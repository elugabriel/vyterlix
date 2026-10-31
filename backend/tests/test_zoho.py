# ruff: noqa: E501
"""Zoho Inventory: signing in, which business is connected, and bringing sales and costs in,
against a pretend Zoho."""

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
from app.integrations.registry import PROVIDERS
from app.integrations.zoho import ZohoProvider
from app.models.data import Expense, Sale
from tests.test_health import scoped

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
ORG = "130002676467"


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("VYTERLIX_ZOHO_CLIENT_ID", "zoho-client")
    monkeypatch.setenv("VYTERLIX_ZOHO_CLIENT_SECRET", "zoho-secret")
    monkeypatch.delenv("VYTERLIX_ZOHO_REGION", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def pretend(handler):
    provider = ZohoProvider()
    provider.transport = httpx.MockTransport(handler)
    return provider


def tokens():
    return TokenSet("access-1", "refresh-1")


def ctx(cursor=None):
    return SyncContext(
        tokens=tokens(), account=AccountInfo(ORG, "Baker Street Bakes"), cursor=cursor or {},
        settings={}, progress=lambda done, total: None, now=NOW,
    )  # fmt: skip


def invoice(number, **over):
    base = {
        "invoice_id": str(number), "invoice_number": f"INV-{number}", "status": "sent", "date": "2026-09-10",
        "due_date": "2026-10-10", "total": 120.0, "tax_total": 20.0, "balance": 120.0, "currency_code": "GBP",
        "customer_name": "Acme Ltd", "line_items": [], "payments": [], "last_modified_time": "2026-09-12T10:00:00+0100",
    }  # fmt: skip
    return {**base, **over}


def bill(number, **over):
    base = {
        "bill_id": str(number), "bill_number": f"B{number}", "status": "open", "date": "2026-09-10",
        "total": 60.0, "tax_total": 10.0, "currency_code": "GBP", "vendor_name": "Flour Co",
    }  # fmt: skip
    return {**base, **over}


def credit(number, **over):
    base = {
        "creditnote_id": str(number), "creditnote_number": f"CN-{number}", "status": "open", "date": "2026-09-11",
        "total": 24.0, "tax_total": 4.0, "currency_code": "GBP", "customer_name": "Acme Ltd",
    }  # fmt: skip
    return {**base, **over}


def zoho_with(**kinds):
    """A pretend Zoho: kinds={"invoices": [...], "bills": [...], "creditnotes": [...]}. `seen` collects requests."""
    seen = []

    def handler(request):
        seen.append(request)
        path = request.url.path.removeprefix("/inventory/v1")
        query = parse_qs(request.url.query.decode())
        if path == "/organizations":
            return httpx.Response(
                200,
                json={
                    "organizations": [
                        {"organization_id": "999", "name": "Other"},
                        {
                            "organization_id": ORG,
                            "name": "Baker Street Bakes",
                            "is_default_org": True,
                            "country_code": "GB",
                        },
                    ]
                },
            )
        kind, _, doc_id = path.strip("/").partition("/")
        rows = kinds.get(kind, [])
        key = {"invoices": "invoice_id", "bills": "bill_id", "creditnotes": "creditnote_id"}.get(
            kind
        )
        if doc_id:
            found = next((r for r in rows if r[key] == doc_id), None)
            return httpx.Response(200, json={kind[:-1]: found}) if found else httpx.Response(404)
        page, size = int(query["page"][0]), int(query["per_page"][0])
        chunk = rows[(page - 1) * size : page * size]
        return httpx.Response(
            200, json={kind: chunk, "page_context": {"has_more_page": page * size < len(rows)}}
        )

    provider = pretend(handler)
    provider.seen = seen
    return provider


def listing(provider):
    return [
        r
        for r in provider.seen
        if r.url.path.rsplit("/", 1)[-1] in ("invoices", "bills", "creditnotes")
    ]


# --- is it set up -------------------------------------------------------------------------------------


def test_it_is_only_available_once_the_app_is_registered_with_zoho():
    provider = ZohoProvider()
    assert provider.is_configured(Settings(zoho_client_id="a", zoho_client_secret="b"))
    assert not provider.is_configured(Settings(zoho_client_id=None, zoho_client_secret=None))
    assert not provider.is_configured(Settings(zoho_client_id="a", zoho_client_secret=None))
    assert "zoho" in PROVIDERS


def test_it_only_asks_to_read_and_says_so():
    provider = ZohoProvider()
    assert all(s.endswith(".READ") for s in provider.scopes)
    assert any("never changes anything in Zoho" in p for p in provider.permissions)


# --- signing in ----------------------------------------------------------------------------------------


def test_the_sign_in_address_is_the_uk_one_and_asks_for_a_refresh_token():
    url = ZohoProvider().authorize_url(
        state="S",
        redirect_uri="https://vyterlix.com/integrations-callback.html",
        code_challenge="C",
    )
    parsed = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert (
        f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        == "https://accounts.zoho.uk/oauth/v2/auth"
    )
    assert q["client_id"] == "zoho-client" and q["state"] == "S" and q["access_type"] == "offline"
    assert "ZohoInventory.invoices.READ" in q["scope"] and "code_challenge" not in q


def test_another_region_can_be_chosen_and_an_unknown_one_falls_back_to_the_uk(monkeypatch):
    monkeypatch.setenv("VYTERLIX_ZOHO_REGION", "eu")
    get_settings.cache_clear()
    assert ZohoProvider()._accounts() == "https://accounts.zoho.eu"
    monkeypatch.setenv("VYTERLIX_ZOHO_REGION", "evil.example")
    get_settings.cache_clear()
    assert ZohoProvider()._accounts() == "https://accounts.zoho.uk"


def test_the_code_is_swapped_for_tokens_with_the_apps_credentials():
    seen = {}

    def handler(request):
        seen["url"], seen["form"] = str(request.url), parse_qs(request.content.decode())
        return httpx.Response(
            200, json={"access_token": "A", "refresh_token": "R", "expires_in": 3600}
        )

    out = pretend(handler).exchange_code(
        code="the-code", redirect_uri="https://x/cb", code_verifier="V"
    )
    assert seen["url"] == "https://accounts.zoho.uk/oauth/v2/token"
    assert seen["form"]["grant_type"] == ["authorization_code"] and seen["form"]["code"] == [
        "the-code"
    ]
    assert seen["form"]["client_id"] == ["zoho-client"] and seen["form"]["client_secret"] == [
        "zoho-secret"
    ]
    assert (out.access_token, out.refresh_token) == ("A", "R")
    assert timedelta(minutes=59) < out.expires_at - datetime.now(UTC) <= timedelta(minutes=60)


def test_a_refusal_zoho_says_with_200_ok_is_still_a_refusal():
    with pytest.raises(ProviderRejected) as bad:
        pretend(lambda r: httpx.Response(200, json={"error": "invalid_code"})).exchange_code(
            code="x", redirect_uri="https://x/cb", code_verifier="V"
        )
    assert bad.value.code == "invalid_code" and "try connecting again" in bad.value.message
    for status in (429, 503):
        with pytest.raises(ProviderUnavailable):
            pretend(lambda r, s=status: httpx.Response(s)).exchange_code(
                code="x", redirect_uri="https://x/cb", code_verifier="V"
            )
    with pytest.raises(ProviderUnavailable):
        pretend(lambda r: httpx.Response(200, json={"error": "Access Denied"})).exchange_code(
            code="x", redirect_uri="https://x/cb", code_verifier="V"
        )


def test_refreshing_keeps_the_old_refresh_token_when_zoho_sends_none_and_a_refusal_means_sign_in_again():
    fresh = pretend(
        lambda r: httpx.Response(200, json={"access_token": "A2", "expires_in": 3600})
    ).refresh(tokens())
    assert (
        fresh.access_token == "A2" and fresh.refresh_token is None
    )  # the framework keeps the old one
    with pytest.raises(ReauthRequired):
        pretend(lambda r: httpx.Response(200, json={"error": "invalid_code"})).refresh(tokens())
    with pytest.raises(ReauthRequired):
        ZohoProvider().refresh(TokenSet("a"))


def test_disconnecting_tells_zoho_and_ignores_it_being_unreachable():
    seen = []
    pretend(lambda r: seen.append(r) or httpx.Response(200)).revoke(tokens())
    assert (
        seen
        and "token/revoke" in seen[0].url.path
        and parse_qs(seen[0].url.query.decode())["token"] == ["refresh-1"]
    )

    def down(request):
        raise httpx.ConnectError("no route")

    pretend(down).revoke(tokens())


# --- which business ------------------------------------------------------------------------------------


def test_the_default_business_is_the_account_and_none_at_all_is_refused_plainly():
    info = zoho_with().account(tokens())
    assert (info.external_id, info.name) == (ORG, "Baker Street Bakes")
    with pytest.raises(ProviderRejected) as e:
        pretend(lambda r: httpx.Response(200, json={"organizations": []})).account(tokens())
    assert e.value.code == "no_organization"


# --- bringing the data in ------------------------------------------------------------------------------


def test_an_invoice_becomes_a_sale_with_its_vat_due_day_and_customer():
    [row] = zoho_with(invoices=[invoice(1)]).sync(ctx()).datasets["sales"]
    assert (row["reference"], row["kind"], row["customer_name"]) == ("inv-1", "sale", "Acme Ltd")
    assert (row["gross_amount"], row["vat_amount"], row["net_amount"]) == (
        D("120"),
        D("20"),
        D("100"),
    )
    assert row["due_on"] == date(2026, 10, 10) and row["paid_on"] is None


def test_a_paid_invoice_takes_its_paid_day_from_the_payments():
    paid = invoice(
        2, status="paid", balance=0, payments=[{"date": "2026-09-20"}, {"date": "2026-09-25"}]
    )
    [row] = zoho_with(invoices=[paid]).sync(ctx()).datasets["sales"]
    assert row["paid_on"] == date(2026, 9, 25)
    [no_payments] = (
        zoho_with(invoices=[invoice(3, status="paid", balance=0)]).sync(ctx()).datasets["sales"]
    )
    assert no_payments["paid_on"] == date(2026, 9, 12)


def test_drafts_and_void_documents_are_left_out():
    out = zoho_with(
        invoices=[invoice(1), invoice(2, status="draft"), invoice(3, status="void")],
        bills=[bill(4), bill(5, status="void")],
    ).sync(ctx())
    assert [r["reference"] for r in out.datasets["sales"]] == ["inv-1"]
    assert [r["reference"] for r in out.datasets["expenses"]] == ["bill-4"]


def test_a_credit_note_is_a_refund_and_a_bill_is_a_cost():
    out = zoho_with(creditnotes=[credit(7)], bills=[bill(8)]).sync(ctx())
    [refund] = out.datasets["sales"]
    assert (refund["kind"], refund["reference"], refund["gross_amount"], refund["vat_amount"]) == (
        "refund",
        "cn-7",
        D("-24"),
        D("-4"),
    )
    [cost] = out.datasets["expenses"]
    assert (cost["supplier"], cost["gross_amount"], cost["net_amount"], cost["reference"]) == (
        "Flour Co",
        D("60"),
        D("50"),
        "bill-8",
    )


def test_every_page_is_read_and_every_request_only_reads():
    provider = zoho_with(invoices=[invoice(i) for i in range(1, 451)])
    out = provider.sync(ctx())
    assert len(out.datasets["sales"]) == 450 and out.records_fetched == 450
    assert all(r.method == "GET" for r in provider.seen)
    assert all(r.headers["authorization"] == "Zoho-oauthtoken access-1" for r in provider.seen)
    assert len([r for r in listing(provider) if r.url.path.endswith("/invoices")]) == 3


def test_the_first_sync_bounds_the_history_and_later_ones_ask_for_what_changed():
    first = zoho_with()
    out = first.sync(ctx())
    q = parse_qs(listing(first)[0].url.query.decode())
    assert (
        q["date_start"] == [(NOW - timedelta(days=730)).date().isoformat()]
        and "last_modified_time" not in q
    )
    assert q["organization_id"] == [ORG]
    later = zoho_with()
    later.sync(ctx({"since": out.cursor["since"]}))
    q = parse_qs(listing(later)[0].url.query.decode())
    assert q["last_modified_time"] == [out.cursor["since"]] and "date_start" not in q


def test_other_currencies_are_left_out_and_counted_and_so_are_documents_that_do_not_add_up():
    out = zoho_with(
        invoices=[
            invoice(1),
            invoice(2, currency_code="USD"),
            invoice(3, total=0),
            invoice(4, total=10, tax_total=30),
            invoice(5, date=None),
            invoice(6),
        ]
    ).sync(ctx())
    assert [r["reference"] for r in out.datasets["sales"]] == ["inv-1", "inv-6"] and out.cursor[
        "left_out_other_currency"
    ] == 1


def test_a_due_day_before_the_sale_is_brought_to_the_sale_day():
    [row] = zoho_with(invoices=[invoice(1, due_date="2026-08-01")]).sync(ctx()).datasets["sales"]
    assert row["due_on"] == row["sold_on"]


def test_one_named_item_says_what_was_sold_and_a_mixed_invoice_does_not_guess():
    a, b = {"name": "Sourdough", "quantity": 4}, {"name": "Cake", "quantity": 1}
    one, many = (
        zoho_with(invoices=[invoice(1, line_items=[a]), invoice(2, line_items=[a, b])])
        .sync(ctx())
        .datasets["sales"]
    )
    assert (one["product"], one["quantity"]) == ("Sourdough", D("4")) and "product" not in many


def test_permission_problems_read_in_plain_english():
    with pytest.raises(ProviderRejected) as e:
        pretend(lambda r: httpx.Response(403)).sync(ctx())
    assert e.value.code == "missing_scope" and "connect Zoho again" in e.value.message
    with pytest.raises(ReauthRequired):
        pretend(lambda r: httpx.Response(401)).sync(ctx())
    with pytest.raises(ProviderUnavailable):
        pretend(lambda r: httpx.Response(429)).sync(ctx())


# --- the whole connect and sync through the framework --------------------------------------------------


def test_signing_in_connects_the_business_and_the_sync_brings_the_records_in(
    api, db, business, storage, monkeypatch
):
    from app.models.integrations import Integration
    from tests.test_integrations import auth, request_sync, run_worker, start, url

    inner = zoho_with(invoices=[invoice(1), invoice(2, status="paid", balance=0)], bills=[bill(9)])
    base = inner.transport

    def handler(request):
        if request.url.path == "/oauth/v2/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "zoho-access-xyz",
                    "refresh_token": "zoho-refresh-xyz",
                    "expires_in": 3600,
                },
            )
        return base.handle_request(request)

    provider = pretend(lambda r: None)
    provider.transport = httpx.MockTransport(handler)
    monkeypatch.setitem(PROVIDERS, "zoho", provider)
    res = start(api, business, "zoho")
    assert res.status_code == 200, res.text
    state = parse_qs(urlparse(res.json()["authorize_url"]).query)["state"][0]
    done = api.post(
        url(business, "/callback"), json={"state": state, "code": "c"}, headers=auth(business)
    )
    assert done.status_code == 200, done.text
    conn = done.json()
    assert conn["provider"] == "zoho" and conn["account_name"] == "Baker Street Bakes"
    assert "zoho-access-xyz" not in json.dumps(conn) and "zoho-refresh-xyz" not in json.dumps(conn)

    request_sync(api, business, conn["id"])
    ran = run_worker(db, storage)
    assert ran.status == "succeeded" and ran.result["records_created"] == 3
    with scoped(db, business):
        assert sorted(s.source_ref for s in db.scalars(select(Sale))) == ["inv-1", "inv-2"]
        assert {s.source for s in db.scalars(select(Sale))} == {"zoho"}
        assert db.scalars(select(Expense)).one().source_ref == "bill-9"
        assert db.scalars(select(Integration)).one().external_account_id == ORG
