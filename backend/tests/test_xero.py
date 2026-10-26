# ruff: noqa: E501
"""Xero: signing in, who is connected, and bringing invoices and bills in, against a pretend Xero."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.integrations import xero as xero_module
from app.integrations.base import (
    AccountInfo,
    ProviderRejected,
    ProviderUnavailable,
    ReauthRequired,
    SyncContext,
    TokenSet,
)
from app.integrations.registry import PROVIDERS
from app.integrations.xero import XeroProvider
from app.models.data import Customer, Expense, Sale, SaleLine, Supplier
from app.services import integration_ingest
from tests.test_health import owner_tenant, scoped

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
TENANT = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("VYTERLIX_XERO_CLIENT_ID", "client-123")
    monkeypatch.setenv("VYTERLIX_XERO_CLIENT_SECRET", "secret-456")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def pretend(handler):
    """A Xero provider that talks to `handler(request) -> httpx.Response` instead of the internet."""
    provider = XeroProvider()
    provider.transport = httpx.MockTransport(handler)
    return provider


def invoice(number, kind="ACCREC", **over):
    base = {
        "InvoiceID": f"id-{number}", "InvoiceNumber": f"INV-{number}", "Type": kind, "Status": "AUTHORISED",
        "CurrencyCode": "GBP", "DateString": "2026-09-10T00:00:00", "DueDateString": "2026-10-10T00:00:00",
        "SubTotal": 100.0, "TotalTax": 20.0, "Total": 120.0, "Contact": {"Name": "Acme Ltd"}, "LineItems": [],
    }  # fmt: skip
    return {**base, **over}


def ctx(cursor=None, tokens=None):
    return SyncContext(
        tokens=tokens or TokenSet("access-1", "refresh-1"),
        account=AccountInfo(TENANT, "Demo Bakery"),
        cursor=cursor or {}, settings={}, progress=lambda done, total: None, now=NOW,
    )  # fmt: skip


# --- is it set up -----------------------------------------------------------------------------------


def test_it_is_only_available_once_the_app_is_registered_with_xero():
    provider = XeroProvider()
    assert provider.is_configured(Settings(xero_client_id="a", xero_client_secret="b"))
    assert not provider.is_configured(Settings(xero_client_id=None, xero_client_secret=None))
    assert not provider.is_configured(Settings(xero_client_id="a", xero_client_secret=None))
    assert "xero" in PROVIDERS


def test_it_asks_only_to_read_and_says_so():
    provider = XeroProvider()
    assert all(".read" in s or s == "offline_access" for s in provider.scopes)
    assert any("never changes anything in Xero" in p for p in provider.permissions)


# --- signing in -------------------------------------------------------------------------------------


def test_the_sign_in_address_carries_what_xero_needs():
    url = XeroProvider().authorize_url(
        state="S",
        redirect_uri="https://vyterlix.com/integrations-callback.html",
        code_challenge="C",
    )
    parsed = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert (
        f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        == "https://login.xero.com/identity/connect/authorize"
    )
    assert q["client_id"] == "client-123" and q["state"] == "S" and q["response_type"] == "code"
    assert q["redirect_uri"] == "https://vyterlix.com/integrations-callback.html"
    assert q["code_challenge"] == "C" and q["code_challenge_method"] == "S256"
    assert (
        "offline_access" in q["scope"].split() and "accounting.invoices.read" in q["scope"].split()
    )


def test_the_code_is_swapped_for_tokens_with_the_apps_credentials():
    seen = {}

    def handler(request):
        seen["url"], seen["auth"], seen["form"] = (
            str(request.url),
            request.headers["authorization"],
            parse_qs(request.content.decode()),
        )
        return httpx.Response(
            200,
            json={
                "access_token": "A",
                "refresh_token": "R",
                "expires_in": 1800,
                "scope": "offline_access accounting.invoices.read",
            },
        )

    tokens = pretend(handler).exchange_code(
        code="the-code", redirect_uri="https://x/cb", code_verifier="V"
    )
    assert seen["url"] == "https://identity.xero.com/connect/token"
    assert seen["auth"].startswith("Basic ") and seen["form"]["grant_type"] == [
        "authorization_code"
    ]
    assert seen["form"]["code"] == ["the-code"] and seen["form"]["code_verifier"] == ["V"]
    assert (tokens.access_token, tokens.refresh_token) == ("A", "R")
    assert tokens.scopes == ("offline_access", "accounting.invoices.read")
    assert timedelta(minutes=29) < tokens.expires_at - datetime.now(UTC) <= timedelta(minutes=30)


def test_a_refused_code_is_said_plainly_and_a_busy_xero_is_tried_again_later():
    with pytest.raises(ProviderRejected) as bad:
        pretend(lambda r: httpx.Response(400, json={"error": "invalid_grant"})).exchange_code(
            code="x", redirect_uri="https://x/cb", code_verifier="V"
        )
    assert bad.value.code == "invalid_code" and "try connecting again" in bad.value.message
    for status in (429, 500, 503):
        with pytest.raises(ProviderUnavailable):
            pretend(lambda r, s=status: httpx.Response(s)).exchange_code(
                code="x", redirect_uri="https://x/cb", code_verifier="V"
            )


def test_a_network_failure_is_a_temporary_problem():
    def down(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(ProviderUnavailable):
        pretend(down).exchange_code(code="x", redirect_uri="https://x/cb", code_verifier="V")


def test_refreshing_uses_the_refresh_token_and_keeps_the_new_one():
    seen = {}

    def handler(request):
        seen["form"] = parse_qs(request.content.decode())
        return httpx.Response(
            200, json={"access_token": "A2", "refresh_token": "R2", "expires_in": 1800}
        )

    fresh = pretend(handler).refresh(TokenSet("A1", "R1"))
    assert seen["form"]["grant_type"] == ["refresh_token"] and seen["form"]["refresh_token"] == [
        "R1"
    ]
    assert (fresh.access_token, fresh.refresh_token) == ("A2", "R2")


def test_when_xero_no_longer_accepts_the_connection_the_person_is_asked_to_connect_again():
    with pytest.raises(ReauthRequired) as e:
        pretend(lambda r: httpx.Response(400, json={"error": "invalid_grant"})).refresh(
            TokenSet("A", "R")
        )
    assert "connect Xero again" in e.value.message
    with pytest.raises(ReauthRequired):
        XeroProvider().refresh(TokenSet("A", None))


def test_the_connection_can_be_revoked_and_a_failure_is_ignored():
    calls = []
    pretend(lambda r: calls.append(str(r.url)) or httpx.Response(200)).revoke(TokenSet("A", "R"))
    assert calls == ["https://identity.xero.com/connect/revocation"]

    def down(request):
        raise httpx.ConnectError("no route")

    pretend(down).revoke(TokenSet("A", "R"))  # never raises


# --- which organisation ------------------------------------------------------------------------------


def test_the_connected_organisation_is_the_one_xero_shared():
    provider = pretend(
        lambda r: httpx.Response(
            200,
            json=[{"tenantId": TENANT, "tenantType": "ORGANISATION", "tenantName": "Demo Bakery"}],
        )
    )
    info = provider.account(TokenSet("A", "R"))
    assert (info.external_id, info.name) == (TENANT, "Demo Bakery")


def test_with_several_shared_the_most_recent_is_used_and_with_none_it_says_so():
    two = [
        {
            "tenantId": "old",
            "tenantType": "ORGANISATION",
            "tenantName": "Old",
            "updatedDateUtc": "2026-01-01T00:00:00",
        },
        {
            "tenantId": "new",
            "tenantType": "ORGANISATION",
            "tenantName": "New",
            "updatedDateUtc": "2026-09-01T00:00:00",
        },
    ]
    assert (
        pretend(lambda r: httpx.Response(200, json=two)).account(TokenSet("A")).external_id == "new"
    )
    with pytest.raises(ProviderRejected) as e:
        pretend(lambda r: httpx.Response(200, json=[])).account(TokenSet("A"))
    assert e.value.code == "no_organisation"


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, ReauthRequired),
        (403, ProviderRejected),
        (429, ProviderUnavailable),
        (503, ProviderUnavailable),
        (404, ProviderRejected),
    ],
)
def test_what_xero_says_is_turned_into_what_to_do(status, error):
    with pytest.raises(error):
        pretend(lambda r: httpx.Response(status)).account(TokenSet("A"))


# --- reading the invoices ----------------------------------------------------------------------------


def xero_with(sales=(), bills=(), seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        kind = parse_qs(request.url.query.decode())["where"][0]
        rows = sales if "ACCREC" in kind else bills
        page = int(parse_qs(request.url.query.decode())["page"][0])
        size = int(parse_qs(request.url.query.decode())["pageSize"][0])
        return httpx.Response(200, json={"Invoices": list(rows)[(page - 1) * size : page * size]})

    return pretend(handler)


def test_a_sales_invoice_becomes_a_sale_with_its_due_and_paid_days():
    paid = invoice(1, Status="PAID", FullyPaidOnDate="2026-09-20T00:00:00")
    out = xero_with([invoice(2), paid]).sync(ctx())
    first, second = out.datasets["sales"]
    assert (
        first["kind"],
        first["sold_on"],
        first["net_amount"],
        first["vat_amount"],
        first["gross_amount"],
    ) == ("sale", date(2026, 9, 10), D("100"), D("20"), D("120"))
    assert (first["reference"], first["customer_name"], first["due_on"], first["paid_on"]) == (
        "id-2",
        "Acme Ltd",
        date(2026, 10, 10),
        None,
    )
    assert second["paid_on"] == date(2026, 9, 20) and "INV-1" in second["notes"]
    assert out.records_fetched == 2


def test_a_bill_becomes_a_cost_with_its_supplier():
    out = xero_with(bills=[invoice(7, "ACCPAY", Contact={"Name": "Flour Co"})]).sync(ctx())
    [cost] = out.datasets["expenses"]
    assert (
        cost["kind"],
        cost["spent_on"],
        cost["supplier"],
        cost["gross_amount"],
        cost["reference"],
    ) == ("expense", date(2026, 9, 10), "Flour Co", D("120"), "id-7")
    assert out.datasets["sales"] == []


def test_only_approved_and_paid_invoices_are_asked_for_and_the_request_is_read_only():
    seen = []
    xero_with([invoice(1)], seen=seen).sync(ctx())
    first = seen[0]
    q = parse_qs(first.url.query.decode())
    assert q["Statuses"] == ["AUTHORISED,PAID"] and first.method == "GET"
    assert (
        first.headers["xero-tenant-id"] == TENANT
        and first.headers["authorization"] == "Bearer access-1"
    )
    assert all(r.method == "GET" for r in seen)


def test_the_first_sync_goes_back_two_years_and_later_ones_only_ask_for_what_changed():
    seen = []
    xero_with(seen=seen).sync(ctx())
    assert seen[0].headers["if-modified-since"] == (NOW - timedelta(days=730)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    seen.clear()
    out = xero_with(seen=seen).sync(ctx({"since": "2026-10-01T08:00:00+00:00"}))
    assert seen[0].headers["if-modified-since"] == "2026-10-01T08:00:00"
    assert out.cursor["since"] == NOW.isoformat()


def test_every_page_is_read():
    many = [invoice(i) for i in range(230)]
    out = xero_with(many).sync(ctx())
    assert len(out.datasets["sales"]) == 230 and out.records_fetched == 230


def test_other_currencies_are_left_out_and_counted_not_guessed_at():
    out = xero_with(
        [invoice(1), invoice(2, CurrencyCode="USD"), invoice(3, CurrencyCode="EUR")]
    ).sync(ctx())
    assert len(out.datasets["sales"]) == 1 and out.cursor["left_out_other_currency"] == 2


def test_an_invoice_for_nothing_or_with_no_day_is_skipped():
    out = xero_with(
        [
            invoice(1, SubTotal=0, TotalTax=0, Total=0),
            invoice(2, DateString=None, Date=None),
            invoice(3),
        ]
    ).sync(ctx())
    assert [r["reference"] for r in out.datasets["sales"]] == ["id-3"]


def test_dates_written_the_old_way_are_understood():
    assert xero_module._day("/Date(1789000000000+0000)/") == date(2026, 9, 10)
    assert xero_module._day("2026-09-10T00:00:00") == date(2026, 9, 10)
    assert xero_module._day(None) is None and xero_module._day("garbage") is None


def test_a_due_day_before_the_sale_is_brought_to_the_sale_day_so_it_can_be_kept():
    [row] = (
        xero_with([invoice(1, DueDateString="2026-08-01T00:00:00")]).sync(ctx()).datasets["sales"]
    )
    assert row["due_on"] == row["sold_on"]


def test_one_clearly_coded_item_says_what_was_sold_and_a_mixed_invoice_does_not_guess():
    one = invoice(1, LineItems=[{"ItemCode": "SD1", "Description": "Sourdough", "Quantity": 4}])
    mixed = invoice(
        2,
        LineItems=[
            {"ItemCode": "SD1", "Description": "Sourdough", "Quantity": 4},
            {"Description": "Delivery", "Quantity": 1},
        ],
    )
    [a, b] = xero_with([one, mixed]).sync(ctx()).datasets["sales"]
    assert (a["sku"], a["product"], a["quantity"]) == ("SD1", "Sourdough", D("4"))
    assert "sku" not in b and "product" not in b


def test_a_permission_problem_reads_in_plain_english():
    with pytest.raises(ProviderRejected) as e:
        pretend(lambda r: httpx.Response(403)).sync(ctx())
    assert e.value.code == "missing_scope" and "connect Xero again" in e.value.message


# --- putting it into the business's data ---------------------------------------------------------------


def test_fetched_rows_become_real_sales_costs_customers_and_suppliers(db, business):
    out = xero_with(
        [invoice(1), invoice(2, Contact={"Name": "Beta Ltd"})],
        [invoice(9, "ACCPAY", Contact={"Name": "Flour Co"})],
    ).sync(ctx())
    with scoped(db, business):
        made = integration_ingest.ingest(
            db, owner_tenant(db, business).organization_id, "xero", out.datasets
        )
        assert (made["sales"], made["expenses"], made["customers"], made["suppliers"]) == (
            2,
            1,
            2,
            1,
        )
        sales = db.scalars(select(Sale).order_by(Sale.source_ref)).all()
        assert [(s.source, s.source_ref, s.net_amount, s.due_on) for s in sales] == [
            ("xero", "id-1", D("100"), date(2026, 10, 10)),
            ("xero", "id-2", D("100"), date(2026, 10, 10)),
        ]
        assert {c.name for c in db.scalars(select(Customer))} == {"Acme Ltd", "Beta Ltd"}
        assert [s.name for s in db.scalars(select(Supplier))] == ["Flour Co"]
        assert db.scalars(select(Expense)).one().source_ref == "id-9"


def test_fetching_again_makes_nothing_twice_and_a_payment_updates_the_sale(db, business):
    org = owner_tenant(db, business).organization_id
    first = xero_with([invoice(1)]).sync(ctx())
    later = xero_with([invoice(1, Status="PAID", FullyPaidOnDate="2026-09-25T00:00:00")]).sync(
        ctx()
    )
    with scoped(db, business):
        integration_ingest.ingest(db, org, "xero", first.datasets)
        again = integration_ingest.ingest(db, org, "xero", later.datasets)
        assert again["sales"] == 0
        [sale] = db.scalars(select(Sale)).all()
        assert (
            sale.paid_on == date(2026, 9, 25) and db.scalars(select(Customer)).all().__len__() == 1
        )


def test_a_coded_item_makes_a_line_and_a_product(db, business):
    out = xero_with(
        [invoice(1, LineItems=[{"ItemCode": "SD1", "Description": "Sourdough", "Quantity": 4}])]
    ).sync(ctx())
    with scoped(db, business):
        made = integration_ingest.ingest(
            db, owner_tenant(db, business).organization_id, "xero", out.datasets
        )
        assert made["sale_lines"] == 1 and made["products"] == 1
        assert db.scalars(select(SaleLine)).one().quantity == D("4")


def fake_xero(invoices_by_kind):
    """One pretend Xero that answers sign-in, the list of organisations and the invoices."""

    def handler(request):
        url = str(request.url)
        if url.startswith("https://identity.xero.com/connect/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "access-token-xyz",
                    "refresh_token": "refresh-token-xyz",
                    "expires_in": 1800,
                    "scope": "offline_access accounting.invoices.read",
                },
            )
        if url.startswith("https://api.xero.com/connections"):
            return httpx.Response(
                200,
                json=[
                    {"tenantId": TENANT, "tenantType": "ORGANISATION", "tenantName": "Demo Bakery"}
                ],
            )
        kind = parse_qs(request.url.query.decode())["where"][0]
        return httpx.Response(
            200, json={"Invoices": invoices_by_kind["ACCREC" if "ACCREC" in kind else "ACCPAY"]}
        )

    return pretend(handler)


def test_connecting_and_syncing_brings_the_invoices_into_the_business(
    api, db, business, storage, monkeypatch
):
    from app.models.integrations import Integration
    from tests.test_integrations import auth, request_sync, run_worker, start, url

    monkeypatch.setitem(
        PROVIDERS,
        "xero",
        fake_xero(
            {
                "ACCREC": [
                    invoice(1),
                    invoice(2, Status="PAID", FullyPaidOnDate="2026-09-25T00:00:00"),
                ],
                "ACCPAY": [invoice(9, "ACCPAY", Contact={"Name": "Flour Co"})],
            }
        ),
    )
    res = start(api, business, "xero")
    assert res.status_code == 200, res.text
    state = parse_qs(urlparse(res.json()["authorize_url"]).query)["state"][0]
    done = api.post(
        url(business, "/callback"),
        json={"state": state, "code": "the-code"},
        headers=auth(business),
    )
    assert done.status_code == 200, done.text
    conn = done.json()
    assert conn["provider"] == "xero" and conn["account_name"] == "Demo Bakery"
    assert "token-xyz" not in json.dumps(conn)  # no tokens shown

    assert request_sync(api, business, conn["id"]).status_code == 202
    ran = run_worker(db, storage)
    assert (
        ran.status == "succeeded"
        and ran.result["records_fetched"] == 3
        and ran.result["records_created"] == 3
    )
    with scoped(db, business):
        assert sorted((s.source_ref, s.paid_on) for s in db.scalars(select(Sale))) == [
            ("id-1", None),
            ("id-2", date(2026, 9, 25)),
        ]
        assert db.scalars(select(Expense)).one().source_ref == "id-9"
        assert db.scalars(select(Integration)).one().sync_cursor["since"]

    # the figures are worked out again, and a second sync later changes nothing already there
    assert run_worker(db, storage).kind == "kpi.calculate"
    request_sync(api, business, conn["id"])
    again = run_worker(db, storage)
    assert again.status == "succeeded" and again.result["records_created"] == 0
    with scoped(db, business):
        assert db.scalars(select(Sale)).all().__len__() == 2


def test_a_sync_that_brings_new_records_queues_a_recalculation_of_the_figures(
    api, db, business, storage, monkeypatch
):
    from app.models.jobs import Job
    from tests.test_integrations import auth, request_sync, run_worker, start, url

    monkeypatch.setitem(PROVIDERS, "xero", fake_xero({"ACCREC": [invoice(1)], "ACCPAY": []}))
    state = parse_qs(urlparse(start(api, business, "xero").json()["authorize_url"]).query)["state"][
        0
    ]
    conn = api.post(
        url(business, "/callback"), json={"state": state, "code": "c"}, headers=auth(business)
    ).json()
    request_sync(api, business, conn["id"])
    run_worker(db, storage)
    with scoped(db, business):
        kinds = [j.kind for j in db.scalars(select(Job))]
    assert "kpi.calculate" in kinds
