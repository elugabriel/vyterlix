# ruff: noqa: E501
"""QuickBooks: signing in, which company is connected, and bringing sales and costs in, against a
pretend QuickBooks."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.integrations.base import (
    AccountInfo,
    ProviderRejected,
    ProviderUnavailable,
    ReauthRequired,
    SyncContext,
    TokenSet,
)
from app.integrations.quickbooks import QuickBooksProvider
from app.integrations.registry import PROVIDERS
from app.models.data import Expense, Sale
from tests.test_health import scoped

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
REALM = "9341458566001658"


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("VYTERLIX_QUICKBOOKS_CLIENT_ID", "qb-client")
    monkeypatch.setenv("VYTERLIX_QUICKBOOKS_CLIENT_SECRET", "qb-secret")
    monkeypatch.delenv("VYTERLIX_QUICKBOOKS_ENVIRONMENT", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def pretend(handler):
    provider = QuickBooksProvider()
    provider.transport = httpx.MockTransport(handler)
    return provider


def tokens(**extra):
    return TokenSet("access-1", "refresh-1", extra={"realmId": REALM, **extra})


def ctx(cursor=None):
    return SyncContext(
        tokens=tokens(), account=AccountInfo(REALM, "Sandbox Co"), cursor=cursor or {},
        settings={}, progress=lambda done, total: None, now=NOW,
    )  # fmt: skip


def invoice(number, **over):
    base = {
        "Id": str(number), "DocNumber": f"INV{number}", "TxnDate": "2026-09-10", "DueDate": "2026-10-10",
        "TotalAmt": 120.0, "Balance": 120.0, "TxnTaxDetail": {"TotalTax": 20.0},
        "CustomerRef": {"value": "1", "name": "Acme Ltd"}, "Line": [],
        "MetaData": {"LastUpdatedTime": "2026-09-12T10:00:00Z"},
    }  # fmt: skip
    return {**base, **over}


def bill(number, **over):
    base = {
        "Id": str(number),
        "DocNumber": f"B{number}",
        "TxnDate": "2026-09-10",
        "TotalAmt": 60.0,
        "TxnTaxDetail": {"TotalTax": 10.0},
        "VendorRef": {"value": "9", "name": "Flour Co"},
    }
    return {**base, **over}


def quickbooks_with(**entities):
    """A pretend QuickBooks that answers the queries: entities={"Invoice": [...], ...}; `seen` collects requests."""
    seen = []

    def handler(request):
        seen.append(request)
        path, query = request.url.path, parse_qs(request.url.query.decode())
        if path.endswith("/query"):
            statement = query["query"][0]
            entity = statement.split("from ")[1].split(" ")[0]
            start = int(statement.split("startposition ")[1].split(" ")[0])
            size = int(statement.split("maxresults ")[1].split(" ")[0])
            rows = entities.get(entity, [])
            return httpx.Response(
                200,
                json={
                    "QueryResponse": {entity: rows[start - 1 : start - 1 + size]}
                    if rows[start - 1 : start - 1 + size]
                    else {"QueryResponse": {}}
                },
            )
        if "/companyinfo/" in path:
            return httpx.Response(
                200, json={"CompanyInfo": {"CompanyName": "Sandbox Co", "Country": "GB"}}
            )
        return httpx.Response(404)

    provider = pretend(handler)
    provider.seen = seen
    return provider


# --- is it set up -------------------------------------------------------------------------------------


def test_it_is_only_available_once_the_app_is_registered_with_intuit():
    provider = QuickBooksProvider()
    assert provider.is_configured(Settings(quickbooks_client_id="a", quickbooks_client_secret="b"))
    assert not provider.is_configured(
        Settings(quickbooks_client_id=None, quickbooks_client_secret=None)
    )
    assert not provider.is_configured(
        Settings(quickbooks_client_id="a", quickbooks_client_secret=None)
    )
    assert "quickbooks" in PROVIDERS


def test_it_reads_only_and_says_so_and_asks_for_the_company_id_back():
    provider = QuickBooksProvider()
    assert provider.scopes == ("com.intuit.quickbooks.accounting",)
    assert any("never changes anything in QuickBooks" in p for p in provider.permissions)
    assert provider.callback_params == ("realmId",)


# --- signing in ----------------------------------------------------------------------------------------


def test_the_sign_in_address_carries_what_intuit_needs():
    url = QuickBooksProvider().authorize_url(
        state="S",
        redirect_uri="https://vyterlix.com/integrations-callback.html",
        code_challenge="C",
    )
    parsed = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert (
        f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        == "https://appcenter.intuit.com/connect/oauth2"
    )
    assert q["client_id"] == "qb-client" and q["state"] == "S" and q["response_type"] == "code"
    assert q["scope"] == "com.intuit.quickbooks.accounting" and q["redirect_uri"].endswith(
        "/integrations-callback.html"
    )
    assert "code_challenge" not in q  # Intuit does not use the extra check


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
                "expires_in": 3600,
                "x_refresh_token_expires_in": 8726400,
            },
        )

    out = pretend(handler).exchange_code(
        code="the-code", redirect_uri="https://x/cb", code_verifier="V"
    )
    assert seen["url"] == "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
    assert (
        seen["auth"].startswith("Basic ")
        and seen["form"]["grant_type"] == ["authorization_code"]
        and seen["form"]["code"] == ["the-code"]
    )
    assert "code_verifier" not in seen["form"]
    assert (out.access_token, out.refresh_token) == ("A", "R")
    assert timedelta(minutes=59) < out.expires_at - datetime.now(UTC) <= timedelta(minutes=60)


def test_a_refused_code_is_said_plainly_and_a_busy_intuit_is_tried_again_later():
    with pytest.raises(ProviderRejected) as bad:
        pretend(lambda r: httpx.Response(400, json={"error": "invalid_grant"})).exchange_code(
            code="x", redirect_uri="https://x/cb", code_verifier="V"
        )
    assert bad.value.code == "invalid_code" and "try connecting again" in bad.value.message
    for status in (429, 503):
        with pytest.raises(ProviderUnavailable):
            pretend(lambda r, s=status: httpx.Response(s)).exchange_code(
                code="x", redirect_uri="https://x/cb", code_verifier="V"
            )


def test_refreshing_keeps_the_new_refresh_token_and_the_company():
    def handler(request):
        assert parse_qs(request.content.decode())["refresh_token"] == ["R1"]
        return httpx.Response(
            200, json={"access_token": "A2", "refresh_token": "R2", "expires_in": 3600}
        )

    fresh = pretend(handler).refresh(
        tokens(realmId=REALM) if False else TokenSet("A1", "R1", extra={"realmId": REALM})
    )
    assert (fresh.access_token, fresh.refresh_token, fresh.extra["realmId"]) == ("A2", "R2", REALM)


def test_when_intuit_no_longer_accepts_the_connection_the_person_is_asked_to_connect_again():
    with pytest.raises(ReauthRequired) as e:
        pretend(lambda r: httpx.Response(400, json={"error": "invalid_grant"})).refresh(tokens())
    assert "connect QuickBooks again" in e.value.message
    with pytest.raises(ReauthRequired):
        QuickBooksProvider().refresh(TokenSet("A", None))


def test_revoking_is_best_effort():
    calls = []
    pretend(lambda r: calls.append(str(r.url)) or httpx.Response(200)).revoke(tokens())
    assert calls == ["https://developer.api.intuit.com/v2/oauth2/tokens/revoke"]

    def down(request):
        raise httpx.ConnectError("no route")

    pretend(down).revoke(tokens())


# --- which company --------------------------------------------------------------------------------------


def test_the_connected_company_is_the_one_in_the_token_extras():
    info = quickbooks_with().account(tokens())
    assert (info.external_id, info.name) == (REALM, "Sandbox Co") and info.settings[
        "country"
    ] == "GB"


def test_without_a_company_id_it_says_so_and_it_never_builds_an_address_from_junk():
    for extra in ({}, {"realmId": ""}, {"realmId": "../../x"}):
        with pytest.raises(ProviderRejected) as e:
            quickbooks_with().account(TokenSet("A", "R", extra=extra))
        assert e.value.code == "no_company"


def test_the_sandbox_is_used_unless_production_is_chosen(monkeypatch):
    provider = quickbooks_with()
    provider.account(tokens())
    assert provider.seen[0].url.host == "sandbox-quickbooks.api.intuit.com"
    monkeypatch.setenv("VYTERLIX_QUICKBOOKS_ENVIRONMENT", "production")
    get_settings.cache_clear()
    live = quickbooks_with()
    live.account(tokens())
    assert live.seen[0].url.host == "quickbooks.api.intuit.com"


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
def test_what_intuit_says_is_turned_into_what_to_do(status, error):
    with pytest.raises(error):
        pretend(lambda r: httpx.Response(status)).account(tokens())


# --- reading the records -------------------------------------------------------------------------------


def test_an_unpaid_invoice_becomes_a_sale_that_is_owed():
    out = quickbooks_with(Invoice=[invoice(1)]).sync(ctx())
    [row] = out.datasets["sales"]
    assert (
        row["kind"],
        row["sold_on"],
        row["net_amount"],
        row["vat_amount"],
        row["gross_amount"],
    ) == ("sale", date(2026, 9, 10), D("100"), D("20"), D("120"))
    assert (row["reference"], row["customer_name"], row["due_on"], row["paid_on"]) == (
        "inv-1",
        "Acme Ltd",
        date(2026, 10, 10),
        None,
    )


def test_a_paid_invoice_is_paid_on_the_day_of_its_payment():
    payment = {
        "Id": "p1",
        "TxnDate": "2026-09-25",
        "Line": [{"LinkedTxn": [{"TxnId": "2", "TxnType": "Invoice"}]}],
    }
    out = quickbooks_with(Invoice=[invoice(2, Balance=0)], Payment=[payment]).sync(ctx())
    assert out.datasets["sales"][0]["paid_on"] == date(2026, 9, 25)


def test_a_paid_invoice_whose_payment_was_not_fetched_is_paid_when_it_last_changed():
    out = quickbooks_with(Invoice=[invoice(3, Balance=0)]).sync(ctx())
    assert out.datasets["sales"][0]["paid_on"] == date(2026, 9, 12)


def test_a_sales_receipt_is_a_sale_with_no_due_day_and_a_credit_memo_is_a_refund():
    receipt = invoice(4, DocNumber="SR1", DueDate=None, Balance=0)
    memo = invoice(5, DocNumber="CM1", TotalAmt=24.0, TxnTaxDetail={"TotalTax": 4.0})
    out = quickbooks_with(SalesReceipt=[receipt], CreditMemo=[memo]).sync(ctx())
    sale, refund = out.datasets["sales"]
    assert sale["reference"] == "sr-4" and "due_on" not in sale and "paid_on" not in sale
    assert (
        refund["kind"],
        refund["net_amount"],
        refund["vat_amount"],
        refund["gross_amount"],
        refund["reference"],
    ) == ("refund", D("-20"), D("-4"), D("-24"), "cm-5")


def test_a_bill_and_an_expense_become_costs_with_their_supplier():
    out = quickbooks_with(
        Bill=[bill(7)], Purchase=[bill(8, EntityRef={"name": "Fuel Co"}, VendorRef=None)]
    ).sync(ctx())
    bill_row, purchase = out.datasets["expenses"]
    assert (
        bill_row["supplier"],
        bill_row["gross_amount"],
        bill_row["net_amount"],
        bill_row["reference"],
    ) == ("Flour Co", D("60"), D("50"), "bill-7")
    assert (purchase["supplier"], purchase["reference"], purchase["kind"]) == (
        "Fuel Co",
        "pur-8",
        "expense",
    )
    assert out.datasets["sales"] == []


def test_every_page_is_read_and_every_request_only_reads():
    many = [invoice(i) for i in range(1, 1201)]
    provider = quickbooks_with(Invoice=many)
    out = provider.sync(ctx())
    assert len(out.datasets["sales"]) == 1200 and out.records_fetched == 1200
    assert all(r.method == "GET" for r in provider.seen)
    assert all(r.headers["authorization"] == "Bearer access-1" for r in provider.seen)


def test_the_first_sync_bounds_the_history_and_later_ones_ask_for_what_changed():
    first = quickbooks_with()
    first.sync(ctx())
    statement = parse_qs(first.seen[0].url.query.decode())["query"][0]
    assert f"TxnDate >= '{(NOW - timedelta(days=730)).date().isoformat()}'" in statement
    later = quickbooks_with()
    out = later.sync(ctx({"since": "2026-10-01T08:00:00+00:00"}))
    assert (
        "MetaData.LastUpdatedTime >= '2026-10-01T08:00:00+00:00'"
        in parse_qs(later.seen[0].url.query.decode())["query"][0]
    )
    assert out.cursor["since"] == NOW.isoformat()


def test_other_currencies_are_left_out_and_counted():
    out = quickbooks_with(
        Invoice=[
            invoice(1),
            invoice(2, CurrencyRef={"value": "USD"}),
            invoice(3, CurrencyRef={"value": "GBP"}),
        ]
    ).sync(ctx())
    assert [r["reference"] for r in out.datasets["sales"]] == ["inv-1", "inv-3"] and out.cursor[
        "left_out_other_currency"
    ] == 1


def test_a_record_for_nothing_or_that_does_not_add_up_is_skipped():
    out = quickbooks_with(
        Invoice=[
            invoice(1, TotalAmt=0),
            invoice(2, TotalAmt=10, TxnTaxDetail={"TotalTax": 30}),
            invoice(3, TxnDate=None),
            invoice(4),
        ]
    ).sync(ctx())
    assert [r["reference"] for r in out.datasets["sales"]] == ["inv-4"]


def test_a_due_day_before_the_sale_is_brought_to_the_sale_day():
    [row] = (
        quickbooks_with(Invoice=[invoice(1, DueDate="2026-08-01")]).sync(ctx()).datasets["sales"]
    )
    assert row["due_on"] == row["sold_on"]


def test_one_named_item_says_what_was_sold_and_a_mixed_invoice_does_not_guess():
    item = {
        "DetailType": "SalesItemLineDetail",
        "SalesItemLineDetail": {"ItemRef": {"name": "Sourdough"}, "Qty": 4},
    }
    other = {
        "DetailType": "SalesItemLineDetail",
        "SalesItemLineDetail": {"ItemRef": {"name": "Cake"}, "Qty": 1},
    }
    [a, b] = (
        quickbooks_with(Invoice=[invoice(1, Line=[item]), invoice(2, Line=[item, other])])
        .sync(ctx())
        .datasets["sales"]
    )
    assert (a["product"], a["quantity"]) == ("Sourdough", D("4")) and "product" not in b


def test_a_permission_problem_reads_in_plain_english():
    with pytest.raises(ProviderRejected) as e:
        pretend(lambda r: httpx.Response(403)).sync(ctx())
    assert e.value.code == "missing_scope" and "connect QuickBooks again" in e.value.message


# --- the whole connect and sync through the framework ---------------------------------------------------


def test_the_company_id_on_the_redirect_is_kept_and_the_sync_brings_the_records_in(
    api, db, business, storage, monkeypatch
):
    from app.models.integrations import Integration
    from tests.test_integrations import auth, request_sync, run_worker, start, url

    def handler(request):
        u = str(request.url)
        if "tokens/bearer" in u:
            return httpx.Response(
                200,
                json={
                    "access_token": "qb-access-xyz",
                    "refresh_token": "qb-refresh-xyz",
                    "expires_in": 3600,
                },
            )
        if "/companyinfo/" in u:
            return httpx.Response(
                200, json={"CompanyInfo": {"CompanyName": "Sandbox Co", "Country": "GB"}}
            )
        statement = parse_qs(request.url.query.decode())["query"][0]
        entity = statement.split("from ")[1].split(" ")[0]
        rows = {"Invoice": [invoice(1), invoice(2, Balance=0)], "Bill": [bill(9)]}.get(entity, [])
        return httpx.Response(200, json={"QueryResponse": {entity: rows} if rows else {}})

    monkeypatch.setitem(PROVIDERS, "quickbooks", pretend(handler))
    res = start(api, business, "quickbooks")
    assert res.status_code == 200, res.text
    state = parse_qs(urlparse(res.json()["authorize_url"]).query)["state"][0]
    done = api.post(
        url(business, "/callback"),
        json={"state": state, "code": "c", "params": {"realmId": REALM, "junk": "ignored"}},
        headers=auth(business),
    )
    assert done.status_code == 200, done.text
    conn = done.json()
    assert conn["provider"] == "quickbooks" and conn["account_name"] == "Sandbox Co"
    assert "qb-access-xyz" not in json.dumps(conn) and "qb-refresh-xyz" not in json.dumps(conn)

    request_sync(api, business, conn["id"])
    ran = run_worker(db, storage)
    assert ran.status == "succeeded" and ran.result["records_created"] == 3
    with scoped(db, business):
        assert sorted(s.source_ref for s in db.scalars(select(Sale))) == ["inv-1", "inv-2"]
        assert {s.source for s in db.scalars(select(Sale))} == {"quickbooks"}
        assert db.scalars(select(Expense)).one().source_ref == "bill-9"
        assert db.scalars(select(Integration)).one().external_account_id == REALM


def test_without_the_company_id_on_the_redirect_connecting_is_refused_plainly(
    api, db, business, monkeypatch
):
    from tests.test_integrations import auth, start, url

    monkeypatch.setitem(
        PROVIDERS,
        "quickbooks",
        pretend(
            lambda r: httpx.Response(
                200, json={"access_token": "A", "refresh_token": "R", "expires_in": 3600}
            )
        ),
    )
    state = parse_qs(urlparse(start(api, business, "quickbooks").json()["authorize_url"]).query)[
        "state"
    ][0]
    res = api.post(
        url(business, "/callback"), json={"state": state, "code": "c"}, headers=auth(business)
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "no_company"
