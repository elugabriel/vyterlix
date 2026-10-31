# ruff: noqa: E501
"""WooCommerce: connecting with read-only keys, and bringing orders in, against a pretend shop."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import select

from app.integrations import woocommerce
from app.integrations.base import (
    AccountInfo,
    ProviderRejected,
    ProviderUnavailable,
    ReauthRequired,
    SyncContext,
    TokenSet,
)
from app.integrations.registry import PROVIDERS
from app.integrations.woocommerce import WooCommerceProvider
from app.models.data import Sale
from tests.test_health import scoped

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
SHOP = "https://shop.example.co.uk"


@pytest.fixture(autouse=True)
def public_shop(monkeypatch):
    monkeypatch.setattr(woocommerce, "_resolve", lambda host: ["93.184.216.34"])


def order(number, **over):
    base = {
        "id": number, "number": str(number), "status": "completed", "currency": "GBP",
        "date_created": "2026-09-10T10:00:00", "date_paid": "2026-09-10T10:05:00",
        "total": "120.00", "total_tax": "20.00",
        "billing": {"first_name": "Jo", "last_name": "Baker", "email": "Jo@Example.co.uk", "company": ""},
        "line_items": [], "refunds": [],
    }  # fmt: skip
    return {**base, **over}


def shop(orders=(), *, status=200):
    """A pretend shop. `seen` collects the requests."""
    seen = []

    def handler(request):
        seen.append(request)
        path = request.url.path
        if path.endswith("/wp-json"):
            return httpx.Response(200, json={"name": "Baker Street Bakes"})
        if path.endswith("/wc/v3/orders"):
            if status != 200:
                return httpx.Response(status)
            q = parse_qs(request.url.query.decode())
            page, size = int(q.get("page", ["1"])[0]), int(q["per_page"][0])
            return httpx.Response(200, json=list(orders)[(page - 1) * size : page * size])
        return httpx.Response(404)

    provider = WooCommerceProvider()
    provider.transport = httpx.MockTransport(handler)
    provider.seen = seen
    return provider


def tokens():
    return TokenSet("ck_key", "cs_secret", extra={"store_url": SHOP})


def ctx(cursor=None):
    return SyncContext(
        tokens=tokens(), account=AccountInfo(SHOP, "Baker Street Bakes"), cursor=cursor or {},
        settings={}, progress=lambda done, total: None, now=NOW,
    )  # fmt: skip


VALUES = {"store_url": SHOP, "consumer_key": "ck_key", "consumer_secret": "cs_secret"}


# --- what it is --------------------------------------------------------------------------------------------


def test_it_is_always_available_reads_only_and_asks_for_the_shop_address_and_two_keys():
    provider = WooCommerceProvider()
    assert provider.is_configured(None) and "woocommerce" in PROVIDERS
    assert [f.name for f in provider.connect_fields] == [
        "store_url",
        "consumer_key",
        "consumer_secret",
    ]
    assert [f.secret for f in provider.connect_fields] == [False, True, True]
    assert any("never changes anything in your shop" in p for p in provider.permissions)


def test_it_has_no_sign_in_page():
    with pytest.raises(ProviderRejected):
        WooCommerceProvider().authorize_url(state="s", redirect_uri="x", code_challenge="c")
    with pytest.raises(ReauthRequired):
        WooCommerceProvider().refresh(tokens())


# --- the shop address ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "typed",
    [
        "https://shop.example.co.uk",
        "shop.example.co.uk",
        "  https://SHOP.example.co.uk/wp-admin/admin.php?page=wc-admin  ",
        "https://shop.example.co.uk/some/path/",
    ],
)
def test_whatever_is_pasted_becomes_the_shop_address(typed):
    assert woocommerce._shop_address(typed) == SHOP


@pytest.mark.parametrize(
    "typed", ["ftp://shop.example.co.uk", "https://user:pw@shop.example.co.uk", "https://", ""]
)
def test_an_address_that_is_not_a_web_address_is_refused_plainly(typed):
    with pytest.raises(ProviderRejected) as e:
        woocommerce._shop_address(typed)
    assert e.value.code == "bad_address" and "https://" in e.value.message


def test_an_address_inside_a_private_network_is_never_called(monkeypatch):
    monkeypatch.setattr(woocommerce, "_resolve", lambda host: ["10.0.0.5"])
    provider = WooCommerceProvider()  # no pretend shop: it would really call out
    with pytest.raises(ProviderRejected) as e:
        provider.connect_with_keys(VALUES)
    assert e.value.code == "bad_address"
    monkeypatch.setattr(woocommerce, "_resolve", lambda host: ["93.184.216.34", "127.0.0.1"])
    with pytest.raises(ProviderRejected):
        provider.connect_with_keys(VALUES)


def test_http_is_not_allowed_outside_development(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(woocommerce, "get_settings", lambda: SimpleNamespace(env="prod"))
    with pytest.raises(ProviderRejected):
        woocommerce._shop_address("http://shop.example.co.uk")
    assert woocommerce._shop_address("https://shop.example.co.uk") == SHOP


# --- connecting ----------------------------------------------------------------------------------------------


def test_good_keys_connect_and_the_shop_name_is_read_from_the_shop():
    provider = shop()
    got = provider.connect_with_keys(VALUES)
    assert (got.access_token, got.refresh_token, got.extra) == (
        "ck_key",
        "cs_secret",
        {"store_url": SHOP},
    )
    info = provider.account(got)
    assert (info.external_id, info.name) == (SHOP, "Baker Street Bakes")
    orders_call = next(r for r in provider.seen if r.url.path.endswith("/orders"))
    assert orders_call.method == "GET" and orders_call.headers["authorization"].startswith("Basic ")


def test_keys_the_shop_does_not_accept_say_so_plainly():
    for status in (401, 403):
        with pytest.raises(ReauthRequired) as e:
            shop(status=status).connect_with_keys(VALUES)
        assert "did not accept the keys" in e.value.message


def test_a_site_that_is_not_woocommerce_or_is_down_is_said_plainly():
    with pytest.raises(ProviderRejected) as e:
        shop(status=404).connect_with_keys(VALUES)
    assert e.value.code == "not_woocommerce"
    for status in (429, 503):
        with pytest.raises(ProviderUnavailable):
            shop(status=status).connect_with_keys(VALUES)


def test_a_shop_that_cannot_be_reached_is_tried_again_later():
    def down(request):
        raise httpx.ConnectError("no route")

    provider = WooCommerceProvider()
    provider.transport = httpx.MockTransport(down)
    with pytest.raises(ProviderUnavailable):
        provider.connect_with_keys(VALUES)


# --- orders become sales -------------------------------------------------------------------------------------


def test_a_paid_order_becomes_a_sale_with_its_customer_and_tax():
    [row] = shop([order(5)]).sync(ctx()).datasets["sales"]
    assert row["reference"] == "woo-5" and row["kind"] == "sale"
    assert (row["gross_amount"], row["vat_amount"], row["net_amount"]) == (
        D("120"),
        D("20"),
        D("100"),
    )
    assert (row["customer_name"], row["customer_email"]) == ("Jo Baker", "jo@example.co.uk")
    assert str(row["sold_on"]) == "2026-09-10" and str(row["paid_on"]) == "2026-09-10"


def test_only_paid_orders_count():
    out = shop(
        [
            order(1),
            order(2, status="processing"),
            order(3, status="pending"),
            order(4, status="cancelled"),
            order(5, status="failed"),
            order(6, status="draft"),
            order(7, status="on-hold"),
        ]
    ).sync(ctx())
    assert [r["reference"] for r in out.datasets["sales"]] == ["woo-1", "woo-2"]
    assert out.records_fetched == 7


def test_a_refund_comes_off_the_sales_in_proportion_to_the_tax():
    rows = (
        shop([order(8, status="refunded", refunds=[{"id": 90, "reason": "", "total": "-120.00"}])])
        .sync(ctx())
        .datasets["sales"]
    )
    sale, refund = rows
    assert refund["kind"] == "refund" and refund["reference"] == "woo-refund-90"
    assert (refund["gross_amount"], refund["vat_amount"], refund["net_amount"]) == (
        D("-120"),
        D("-20"),
        D("-100"),
    )
    assert sale["gross_amount"] + refund["gross_amount"] == 0
    part = (
        shop([order(9, refunds=[{"id": 91, "total": "-60.00"}])]).sync(ctx()).datasets["sales"][1]
    )
    assert (part["gross_amount"], part["vat_amount"]) == (D("-60"), D("-10"))


def test_a_company_with_no_personal_name_is_used_and_an_unnamed_buyer_is_left_blank():
    rows = (
        shop(
            [
                order(
                    1,
                    billing={"first_name": "", "last_name": "", "company": "Acme Ltd", "email": ""},
                ),
                order(2, billing={}),
            ]
        )
        .sync(ctx())
        .datasets["sales"]
    )
    assert [(r["customer_name"], r["customer_email"]) for r in rows] == [
        ("Acme Ltd", None),
        (None, None),
    ]


def test_one_named_item_says_what_was_sold_and_a_mixed_order_does_not_guess():
    a = {"name": "Sourdough", "quantity": 4}
    b = {"name": "Cake", "quantity": 1}
    one, many = (
        shop([order(1, line_items=[a]), order(2, line_items=[a, b])]).sync(ctx()).datasets["sales"]
    )
    assert (one["product"], one["quantity"]) == ("Sourdough", D("4")) and "product" not in many


def test_other_currencies_and_orders_that_do_not_add_up_are_left_out():
    out = shop(
        [
            order(1),
            order(2, currency="USD"),
            order(3, total="0.00", total_tax="0.00"),
            order(4, total="10.00", total_tax="30.00"),
            order(5, date_created=None),
            order(6),
        ]
    ).sync(ctx())
    assert [r["reference"] for r in out.datasets["sales"]] == ["woo-1", "woo-6"]
    assert out.cursor["left_out_other_currency"] == 1


def test_every_page_is_read_and_only_reads():
    provider = shop([order(i) for i in range(1, 251)])
    out = provider.sync(ctx())
    assert len(out.datasets["sales"]) == 250 and out.records_fetched == 250
    pages = [r for r in provider.seen if r.url.path.endswith("/orders")]
    assert len(pages) == 3 and all(r.method == "GET" for r in pages)


def test_the_first_sync_bounds_the_history_and_later_ones_ask_for_what_changed():
    first = shop()
    out = first.sync(ctx())
    q = parse_qs(first.seen[0].url.query.decode())
    assert (
        q["after"] == [(NOW - timedelta(days=730)).strftime("%Y-%m-%dT%H:%M:%S")]
        and "modified_after" not in q
    )
    later = shop()
    later.sync(ctx({"since": out.cursor["since"]}))
    q = parse_qs(later.seen[0].url.query.decode())
    assert q["modified_after"] == [out.cursor["since"]] and "after" not in q
    assert out.cursor["since"] < NOW.strftime(
        "%Y-%m-%dT%H:%M:%S"
    )  # a margin, so nothing falls in a gap


def test_a_shop_that_stops_accepting_the_keys_during_a_sync_asks_to_be_connected_again():
    with pytest.raises(ReauthRequired):
        shop(status=401).sync(ctx())


# --- the whole connect and sync through the framework --------------------------------------------------------


def test_keys_connect_a_shop_and_the_sync_brings_its_orders_in_without_ever_showing_the_keys(
    api, db, business, storage, monkeypatch
):
    from app.models.integrations import Integration
    from tests.test_integrations import auth, request_sync, run_worker, url

    monkeypatch.setitem(
        PROVIDERS,
        "woocommerce",
        shop(
            [
                order(1),
                order(2, status="refunded", refunds=[{"id": 7, "total": "-120.00"}]),
                order(3, status="pending"),
            ]
        ),
    )
    listed = api.get(url(business, "/providers"), headers=auth(business)).json()
    woo = next(p for p in listed if p["key"] == "woocommerce")
    assert [f["name"] for f in woo["fields"]] == ["store_url", "consumer_key", "consumer_secret"]

    res = api.post(
        url(business, "/connect-keys"),
        json={"provider": "woocommerce", "values": VALUES},
        headers=auth(business),
    )
    assert res.status_code == 200, res.text
    conn = res.json()
    assert conn["provider"] == "woocommerce" and conn["account_name"] == "Baker Street Bakes"
    assert "ck_key" not in json.dumps(conn) and "cs_secret" not in json.dumps(conn)

    request_sync(api, business, conn["id"])
    ran = run_worker(db, storage)
    assert ran.status == "succeeded" and ran.result["records_created"] == 3
    with scoped(db, business):
        sales = list(db.scalars(select(Sale)))
        assert sorted(s.source_ref for s in sales) == ["woo-1", "woo-2", "woo-refund-7"]
        assert {s.source for s in sales} == {"woocommerce"}
        saved = db.scalars(select(Integration)).one()
        assert saved.external_account_id == SHOP
        assert (
            b"ck_key" not in bytes(saved.credentials, "utf8")
            if isinstance(saved.credentials, str)
            else b"ck_key" not in saved.credentials
        )

    again = api.post(
        url(business, "/connect-keys"),
        json={"provider": "woocommerce", "values": VALUES},
        headers=auth(business),
    )
    assert (
        again.status_code == 200 and again.json()["id"] == conn["id"]
    )  # the same shop is not added twice


def test_missing_boxes_and_a_wrong_provider_are_refused_plainly(api, business, monkeypatch):
    from tests.test_integrations import auth, url

    monkeypatch.setitem(PROVIDERS, "woocommerce", shop())
    res = api.post(
        url(business, "/connect-keys"),
        json={"provider": "woocommerce", "values": {"store_url": SHOP}},
        headers=auth(business),
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "missing_fields"
    assert "Consumer key" in res.json()["error"]["message"]
    res = api.post(
        url(business, "/connect-keys"),
        json={"provider": "sandbox", "values": {"a": "b"}},
        headers=auth(business),
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "wrong_provider"


def test_wrong_keys_are_refused_with_the_shops_words_and_nothing_is_saved(
    api, db, business, monkeypatch
):
    from app.models.integrations import Integration
    from tests.test_integrations import auth, url

    monkeypatch.setitem(PROVIDERS, "woocommerce", shop(status=401))
    res = api.post(
        url(business, "/connect-keys"),
        json={"provider": "woocommerce", "values": VALUES},
        headers=auth(business),
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "reauth_required"
    with scoped(db, business):
        assert db.scalars(select(Integration)).first() is None


def test_only_the_owner_can_connect_a_shop(api, business, monkeypatch):
    from tests.test_integrations import auth as owner_auth
    from tests.test_integrations import url

    monkeypatch.setitem(PROVIDERS, "woocommerce", shop())
    res = api.post(
        url(business, "/connect-keys"), json={"provider": "woocommerce", "values": VALUES}
    )
    assert res.status_code in (401, 403)
    assert owner_auth(business)  # the owner's headers exist and are what the other tests use
