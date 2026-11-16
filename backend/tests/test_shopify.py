# ruff: noqa: E501
"""Shopify: connecting with a read-only token, and bringing orders in, against a pretend shop."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import select

from app.integrations import shopify
from app.integrations.base import (
    AccountInfo,
    ProviderRejected,
    ProviderUnavailable,
    ReauthRequired,
    SyncContext,
    TokenSet,
)
from app.integrations.registry import PROVIDERS
from app.integrations.shopify import ShopifyProvider
from app.models.data import Sale
from tests.test_health import scoped

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
DOMAIN = "bakes.myshopify.com"
VALUES = {"shop_domain": DOMAIN, "access_token": "shpat_secret"}
ORDERS = f"/admin/api/{shopify.API_VERSION}/orders.json"


def order(number, **over):
    base = {
        "id": number, "name": f"#{1000 + number}", "financial_status": "paid", "currency": "GBP",
        "created_at": "2026-09-10T10:00:00+01:00", "processed_at": "2026-09-10T10:00:00+01:00",
        "total_price": "120.00", "total_tax": "20.00", "test": False,
        "customer": {"first_name": "Jo", "last_name": "Baker", "email": "Jo@Example.co.uk"},
        "line_items": [], "refunds": [],
    }  # fmt: skip
    return {**base, **over}


def refund(rid, amount, when="2026-09-12T10:00:00+01:00", kind="refund", status="success"):
    return {
        "id": rid,
        "created_at": when,
        "transactions": [{"kind": kind, "status": status, "amount": amount}],
    }


def shop(orders=(), *, status=200, page_size=250):
    """A pretend shop. `seen` collects the requests. Pages are linked the way Shopify links them."""
    seen = []

    def handler(request):
        seen.append(request)
        path = request.url.path
        if path.endswith("/shop.json"):
            return httpx.Response(200, json={"shop": {"name": "Baker Street Bakes"}})
        if path.endswith("/orders.json"):
            if status != 200:
                return httpx.Response(status)
            q = parse_qs(request.url.query.decode())
            page = int(q.get("page_info", ["0"])[0] or 0)
            size = int(q["limit"][0]) if "limit" in q else page_size
            size = min(size, page_size)
            chunk = list(orders)[page * size : (page + 1) * size]
            headers = {}
            if (page + 1) * size < len(orders):
                headers["Link"] = (
                    f'<https://{DOMAIN}{ORDERS}?limit={size}&page_info={page + 1}>; rel="next"'
                )
            return httpx.Response(200, json={"orders": chunk}, headers=headers)
        return httpx.Response(404)

    provider = ShopifyProvider()
    provider.transport = httpx.MockTransport(handler)
    provider.seen = seen
    return provider


def tokens():
    return TokenSet("shpat_secret", extra={"shop": DOMAIN})


def ctx(cursor=None):
    return SyncContext(
        tokens=tokens(), account=AccountInfo(DOMAIN, "Baker Street Bakes"), cursor=cursor or {},
        settings={}, progress=lambda done, total: None, now=NOW,
    )  # fmt: skip


# --- what it is --------------------------------------------------------------------------------------------


def test_it_is_always_available_reads_only_and_asks_for_the_shop_address_and_a_token():
    provider = ShopifyProvider()
    assert provider.is_configured(None) and "shopify" in PROVIDERS
    assert [f.name for f in provider.connect_fields] == ["shop_domain", "access_token"]
    assert [f.secret for f in provider.connect_fields] == [False, True]
    assert any("never changes anything in your shop" in p for p in provider.permissions)


def test_it_has_no_sign_in_page():
    with pytest.raises(ProviderRejected):
        ShopifyProvider().authorize_url(state="s", redirect_uri="x", code_challenge="c")
    with pytest.raises(ReauthRequired):
        ShopifyProvider().refresh(tokens())


# --- the shop address ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "typed",
    [
        "bakes.myshopify.com",
        "bakes",
        "  BAKES.MyShopify.com ",
        "https://bakes.myshopify.com/admin/orders",
        "https://admin.shopify.com/store/bakes/orders",
    ],
)
def test_whatever_is_pasted_becomes_the_shop_address(typed):
    assert shopify._shop_domain(typed) == DOMAIN


@pytest.mark.parametrize(
    "typed",
    [
        "",
        "shop.example.co.uk",  # a custom domain is not the Shopify address
        "https://user:pw@bakes.myshopify.com",
        "ftp://bakes.myshopify.com",
        "localhost.evil.com",
        "10.0.0.5",
        "https://admin.shopify.com/",
        "-bad.myshopify.com",
    ],
)
def test_an_address_that_is_not_a_shopify_address_is_refused_plainly(typed):
    with pytest.raises(ProviderRejected) as e:
        shopify._shop_domain(typed)
    assert e.value.code == "bad_address" and "myshopify.com" in e.value.message


# --- connecting ----------------------------------------------------------------------------------------------


def test_a_good_token_connects_and_the_shop_name_is_read_from_the_shop():
    provider = shop()
    got = provider.connect_with_keys(VALUES)
    assert (got.access_token, got.refresh_token, got.extra) == (
        "shpat_secret",
        None,
        {"shop": DOMAIN},
    )
    info = provider.account(got)
    assert (info.external_id, info.name) == (DOMAIN, "Baker Street Bakes")
    call = next(r for r in provider.seen if r.url.path.endswith("/orders.json"))
    assert call.method == "GET" and call.headers["x-shopify-access-token"] == "shpat_secret"
    assert call.url.host == DOMAIN


def test_a_token_shopify_does_not_accept_says_so_plainly():
    with pytest.raises(ReauthRequired) as e:
        shop(status=401).connect_with_keys(VALUES)
    assert "did not accept the access token" in e.value.message


def test_an_app_without_permission_to_read_orders_says_so_plainly():
    with pytest.raises(ProviderRejected) as e:
        shop(status=403).connect_with_keys(VALUES)
    assert e.value.code == "missing_permission" and "Read orders" in e.value.message


def test_a_missing_shop_or_a_busy_one_is_said_plainly():
    with pytest.raises(ProviderRejected) as e:
        shop(status=404).connect_with_keys(VALUES)
    assert e.value.code == "not_shopify"
    for status in (429, 503):
        with pytest.raises(ProviderUnavailable):
            shop(status=status).connect_with_keys(VALUES)


def test_a_shop_that_cannot_be_reached_is_tried_again_later():
    def down(request):
        raise httpx.ConnectError("no route")

    provider = ShopifyProvider()
    provider.transport = httpx.MockTransport(down)
    with pytest.raises(ProviderUnavailable):
        provider.connect_with_keys(VALUES)


def test_the_shop_name_falls_back_to_the_address_when_it_cannot_be_read():
    provider = ShopifyProvider()
    provider.transport = httpx.MockTransport(lambda request: httpx.Response(503))
    assert provider.account(tokens()).name == "bakes"


# --- orders become sales -------------------------------------------------------------------------------------


def test_a_paid_order_becomes_a_sale_with_its_customer_and_tax():
    [row] = shop([order(5)]).sync(ctx()).datasets["sales"]
    assert row["reference"] == "shopify-5" and row["kind"] == "sale"
    assert row["notes"] == "Shopify order #1005"
    assert (row["gross_amount"], row["vat_amount"], row["net_amount"]) == (
        D("120"),
        D("20"),
        D("100"),
    )
    assert (row["customer_name"], row["customer_email"]) == ("Jo Baker", "jo@example.co.uk")
    assert str(row["sold_on"]) == "2026-09-10"


def test_only_paid_orders_count_and_test_orders_never_do():
    out = shop(
        [
            order(1),
            order(2, financial_status="partially_refunded"),
            order(3, financial_status="pending"),
            order(4, financial_status="voided"),
            order(5, financial_status="authorized"),
            order(6, test=True),
            order(7, financial_status="refunded"),
        ]
    ).sync(ctx())
    assert [r["reference"] for r in out.datasets["sales"]] == [
        "shopify-1",
        "shopify-2",
        "shopify-7",
    ]
    assert out.records_fetched == 7


def test_a_refund_comes_off_the_sales_on_the_day_it_was_made_in_proportion_to_the_tax():
    rows = (
        shop([order(8, financial_status="refunded", refunds=[refund(90, "120.00")])])
        .sync(ctx())
        .datasets["sales"]
    )
    sale, back = rows
    assert back["kind"] == "refund" and back["reference"] == "shopify-refund-90"
    assert str(back["sold_on"]) == "2026-09-12"
    assert (back["gross_amount"], back["vat_amount"], back["net_amount"]) == (
        D("-120"),
        D("-20"),
        D("-100"),
    )
    assert sale["gross_amount"] + back["gross_amount"] == 0
    part = shop([order(9, refunds=[refund(91, "60.00")])]).sync(ctx()).datasets["sales"][1]
    assert (part["gross_amount"], part["vat_amount"]) == (D("-60"), D("-10"))


def test_only_money_actually_paid_back_counts_as_a_refund():
    rows = (
        shop(
            [
                order(
                    1,
                    refunds=[
                        refund(1, "50.00", status="failure"),
                        refund(2, "50.00", kind="void"),
                        refund(3, "0.00"),
                    ],
                )
            ]
        )
        .sync(ctx())
        .datasets["sales"]
    )
    assert len(rows) == 1


def test_a_company_is_used_when_there_is_no_name_and_an_unnamed_buyer_is_left_blank():
    rows = (
        shop(
            [
                order(
                    1,
                    customer={},
                    billing_address={"company": "Acme Ltd"},
                    email="Buyer@Acme.co.uk",
                ),
                order(2, customer=None),
            ]
        )
        .sync(ctx())
        .datasets["sales"]
    )
    assert [(r["customer_name"], r["customer_email"]) for r in rows] == [
        ("Acme Ltd", "buyer@acme.co.uk"),
        (None, None),
    ]


def test_one_named_item_says_what_was_sold_and_a_mixed_order_does_not_guess():
    a = {"title": "Sourdough", "quantity": 4}
    b = {"title": "Cake", "quantity": 1}
    one, many = (
        shop([order(1, line_items=[a]), order(2, line_items=[a, b])]).sync(ctx()).datasets["sales"]
    )
    assert (one["product"], one["quantity"]) == ("Sourdough", D("4")) and "product" not in many


def test_other_currencies_and_orders_that_do_not_add_up_are_left_out():
    out = shop(
        [
            order(1),
            order(2, currency="USD"),
            order(3, total_price="0.00", total_tax="0.00"),
            order(4, total_price="10.00", total_tax="30.00"),
            order(5, created_at=None, processed_at=None),
            order(6),
        ]
    ).sync(ctx())
    assert [r["reference"] for r in out.datasets["sales"]] == ["shopify-1", "shopify-6"]
    assert out.cursor["left_out_other_currency"] == 1


def test_every_page_is_followed_and_only_reads():
    provider = shop([order(i) for i in range(1, 6)], page_size=2)
    out = provider.sync(ctx())
    assert len(out.datasets["sales"]) == 5 and out.records_fetched == 5
    pages = [r for r in provider.seen if r.url.path.endswith("/orders.json")]
    assert len(pages) == 3 and all(r.method == "GET" for r in pages)


def test_a_next_page_on_another_address_is_never_followed():
    def handler(request):
        return httpx.Response(
            200,
            json={"orders": [order(1)]},
            headers={"Link": '<https://evil.example.com/steal>; rel="next"'},
        )

    provider = ShopifyProvider()
    provider.transport = httpx.MockTransport(handler)
    out = provider.sync(ctx())
    assert out.records_fetched == 1


def test_the_first_sync_bounds_the_history_and_later_ones_ask_for_what_changed():
    first = shop()
    out = first.sync(ctx())
    q = parse_qs(first.seen[0].url.query.decode())
    assert q["created_at_min"] == [(NOW - timedelta(days=730)).strftime("%Y-%m-%dT%H:%M:%SZ")]
    assert "updated_at_min" not in q and q["status"] == ["any"]
    later = shop()
    later.sync(ctx({"since": out.cursor["since"]}))
    q = parse_qs(later.seen[0].url.query.decode())
    assert q["updated_at_min"] == [out.cursor["since"]] and "created_at_min" not in q
    assert out.cursor["since"] < NOW.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )  # a margin, so nothing falls in a gap


def test_a_shop_that_stops_accepting_the_token_during_a_sync_asks_to_be_connected_again():
    with pytest.raises(ReauthRequired):
        shop(status=401).sync(ctx())


# --- the whole connect and sync through the framework --------------------------------------------------------


def test_a_token_connects_a_shop_and_the_sync_brings_its_orders_in_without_ever_showing_the_token(
    api, db, business, storage, monkeypatch
):
    from app.models.integrations import Integration
    from tests.test_integrations import auth, request_sync, run_worker, url

    monkeypatch.setitem(
        PROVIDERS,
        "shopify",
        shop(
            [
                order(1),
                order(2, financial_status="refunded", refunds=[refund(7, "120.00")]),
                order(3, financial_status="pending"),
            ]
        ),
    )
    listed = api.get(url(business, "/providers"), headers=auth(business)).json()
    entry = next(p for p in listed if p["key"] == "shopify")
    assert [f["name"] for f in entry["fields"]] == ["shop_domain", "access_token"]

    res = api.post(
        url(business, "/connect-keys"),
        json={"provider": "shopify", "values": VALUES},
        headers=auth(business),
    )
    assert res.status_code == 200, res.text
    conn = res.json()
    assert conn["provider"] == "shopify" and conn["account_name"] == "Baker Street Bakes"
    assert "shpat_secret" not in json.dumps(conn)

    request_sync(api, business, conn["id"])
    ran = run_worker(db, storage)
    assert ran.status == "succeeded" and ran.result["records_created"] == 3
    with scoped(db, business):
        sales = list(db.scalars(select(Sale)))
        assert sorted(s.source_ref for s in sales) == ["shopify-1", "shopify-2", "shopify-refund-7"]
        assert {s.source for s in sales} == {"shopify"}
        saved = db.scalars(select(Integration)).one()
        assert saved.external_account_id == DOMAIN
        stored = (
            saved.credentials
            if isinstance(saved.credentials, bytes)
            else bytes(saved.credentials, "utf8")
        )
        assert b"shpat_secret" not in stored

    again = api.post(
        url(business, "/connect-keys"),
        json={"provider": "shopify", "values": VALUES},
        headers=auth(business),
    )
    assert again.status_code == 200 and again.json()["id"] == conn["id"]  # not added twice


def test_missing_boxes_are_refused_plainly(api, business, monkeypatch):
    from tests.test_integrations import auth, url

    monkeypatch.setitem(PROVIDERS, "shopify", shop())
    res = api.post(
        url(business, "/connect-keys"),
        json={"provider": "shopify", "values": {"shop_domain": DOMAIN}},
        headers=auth(business),
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "missing_fields"
    assert "Admin API access token" in res.json()["error"]["message"]


def test_a_wrong_token_is_refused_with_shopifys_words_and_nothing_is_saved(
    api, db, business, monkeypatch
):
    from app.models.integrations import Integration
    from tests.test_integrations import auth, url

    monkeypatch.setitem(PROVIDERS, "shopify", shop(status=401))
    res = api.post(
        url(business, "/connect-keys"),
        json={"provider": "shopify", "values": VALUES},
        headers=auth(business),
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "reauth_required"
    with scoped(db, business):
        assert db.scalars(select(Integration)).first() is None


def test_only_the_owner_can_connect_a_shop(api, business, monkeypatch):
    from tests.test_integrations import url

    monkeypatch.setitem(PROVIDERS, "shopify", shop())
    res = api.post(url(business, "/connect-keys"), json={"provider": "shopify", "values": VALUES})
    assert res.status_code in (401, 403)
