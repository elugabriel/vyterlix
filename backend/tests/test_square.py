# ruff: noqa: E501
"""Square: connecting with an access token, and bringing sales in, against a pretend Square."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.integrations.base import (
    AccountInfo,
    ProviderRejected,
    ProviderUnavailable,
    ReauthRequired,
    SyncContext,
    TokenSet,
)
from app.integrations.registry import PROVIDERS
from app.integrations.square import SquareProvider
from app.models.data import Sale
from tests.test_health import scoped

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
MERCHANT = "MLXYZ123"


def money(pence, currency="GBP"):
    return {"amount": pence, "currency": currency}


def order(order_id, **over):
    base = {
        "id": order_id, "location_id": "L1", "state": "COMPLETED",
        "closed_at": "2026-09-10T10:00:00.000Z", "total_money": money(12000),
        "total_tax_money": money(2000), "total_tip_money": money(0),
        "line_items": [], "refunds": [],
    }  # fmt: skip
    return {**base, **over}


def square(orders=(), *, locations=None, customers=None, status=200):
    """A pretend Square. `seen` collects the requests; `bodies` the order searches."""
    seen, bodies = [], []
    locations = locations if locations is not None else [{"id": "L1", "status": "ACTIVE"}]

    def handler(request):
        seen.append(request)
        path = request.url.path
        if status != 200:
            return httpx.Response(status)
        if path == "/v2/merchants/me":
            return httpx.Response(
                200,
                json={
                    "merchant": {
                        "id": MERCHANT,
                        "business_name": "Baker Street Bakes",
                        "country": "GB",
                    }
                },
            )
        if path == "/v2/locations":
            return httpx.Response(200, json={"locations": locations})
        if path.startswith("/v2/customers/"):
            found = (customers or {}).get(path.rsplit("/", 1)[1])
            return httpx.Response(200, json={"customer": found}) if found else httpx.Response(404)
        if path == "/v2/orders/search":
            body = json.loads(request.content)
            bodies.append(body)
            rows = [o for o in orders if o["location_id"] in body["location_ids"]]
            start = int(body.get("cursor", "0"))
            chunk = rows[start : start + body["limit"]]
            out = {"orders": chunk}
            if start + body["limit"] < len(rows):
                out["cursor"] = str(start + body["limit"])
            return httpx.Response(200, json=out)
        return httpx.Response(404)

    provider = SquareProvider()
    provider.transport = httpx.MockTransport(handler)
    provider.seen, provider.bodies = seen, bodies
    return provider


def tokens():
    return TokenSet("sq-token")


def ctx(cursor=None):
    return SyncContext(
        tokens=tokens(), account=AccountInfo(MERCHANT, "Baker Street Bakes"), cursor=cursor or {},
        settings={}, progress=lambda done, total: None, now=NOW,
    )  # fmt: skip


# --- what it is ------------------------------------------------------------------------------------------


def test_it_is_always_available_reads_only_and_asks_for_one_token():
    provider = SquareProvider()
    assert provider.is_configured(None) and "square" in PROVIDERS
    [field] = provider.connect_fields
    assert field.name == "access_token" and field.secret
    assert any("never changes anything in Square" in p for p in provider.permissions)


def test_the_practice_account_is_used_unless_production_is_chosen(monkeypatch):
    assert SquareProvider._api() == "https://connect.squareupsandbox.com"
    monkeypatch.setenv("VYTERLIX_SQUARE_ENVIRONMENT", "production")
    get_settings.cache_clear()
    try:
        assert SquareProvider._api() == "https://connect.squareup.com"
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


# --- connecting ------------------------------------------------------------------------------------------


def test_a_good_token_connects_and_says_whose_account_it_is():
    provider = square()
    got = provider.connect_with_keys({"access_token": "sq-token"})
    info = provider.account(got)
    assert (info.external_id, info.name) == (MERCHANT, "Baker Street Bakes")
    assert all(r.method == "GET" for r in provider.seen)
    assert provider.seen[0].headers["authorization"] == "Bearer sq-token"


def test_a_token_square_does_not_accept_is_said_plainly():
    with pytest.raises(ReauthRequired) as e:
        square(status=401).connect_with_keys({"access_token": "bad"})
    assert "did not accept the access token" in e.value.message


def test_a_refusal_and_a_busy_square_are_told_apart():
    with pytest.raises(ProviderRejected):
        square(status=403).connect_with_keys({"access_token": "x"})
    for status in (429, 503):
        with pytest.raises(ProviderUnavailable):
            square(status=status).connect_with_keys({"access_token": "x"})


def test_square_being_unreachable_is_tried_again_later():
    def down(request):
        raise httpx.ConnectError("no route")

    provider = SquareProvider()
    provider.transport = httpx.MockTransport(down)
    with pytest.raises(ProviderUnavailable):
        provider.connect_with_keys({"access_token": "x"})


# --- orders become sales ---------------------------------------------------------------------------------


def test_a_completed_order_becomes_a_sale_in_pounds_with_its_tax():
    [row] = square([order("ORD12345678")]).sync(ctx()).datasets["sales"]
    assert row["reference"] == "sq-ORD12345678" and row["kind"] == "sale"
    assert (row["gross_amount"], row["vat_amount"], row["net_amount"]) == (
        D("120"),
        D("20"),
        D("100"),
    )
    assert str(row["sold_on"]) == "2026-09-10" and str(row["paid_on"]) == "2026-09-10"


def test_a_tip_is_not_the_businesss_money():
    [row] = (
        square([order("A", total_money=money(13000), total_tip_money=money(1000))])
        .sync(ctx())
        .datasets["sales"]
    )
    assert (row["gross_amount"], row["net_amount"]) == (D("120"), D("100"))


def test_a_refund_comes_off_the_sales_in_proportion_to_the_tax():
    refunds = [
        {
            "id": "R1",
            "status": "COMPLETED",
            "amount_money": money(6000),
            "created_at": "2026-09-15T09:00:00Z",
        },
        {"id": "R2", "status": "PENDING", "amount_money": money(1000)},
        {"id": "R3", "status": "COMPLETED", "amount_money": money(0)},
    ]
    sale, refund = square([order("A", refunds=refunds)]).sync(ctx()).datasets["sales"]
    assert (
        refund["kind"] == "refund"
        and refund["reference"] == "sq-refund-R1"
        and str(refund["sold_on"]) == "2026-09-15"
    )
    assert (refund["gross_amount"], refund["vat_amount"], refund["net_amount"]) == (
        D("-60"),
        D("-10"),
        D("-50"),
    )


def test_the_customers_name_is_looked_up_once_and_a_missing_one_does_not_stop_the_sale():
    provider = square(
        [
            order("A", customer_id="C1"),
            order("B", customer_id="C1"),
            order("C", customer_id="C9"),
            order("D"),
        ],
        customers={"C1": {"given_name": "Jo", "family_name": "Baker"}},
    )
    rows = provider.sync(ctx()).datasets["sales"]
    assert [r["customer_name"] for r in rows] == ["Jo Baker", "Jo Baker", None, None]
    assert sum(r.url.path == "/v2/customers/C1" for r in provider.seen) == 1


def test_one_named_item_says_what_was_sold_and_a_mixed_order_does_not_guess():
    a, b = {"name": "Sourdough", "quantity": "4"}, {"name": "Cake", "quantity": "1"}
    one, many = (
        square([order("A", line_items=[a]), order("B", line_items=[a, b])])
        .sync(ctx())
        .datasets["sales"]
    )
    assert (one["product"], one["quantity"]) == ("Sourdough", D("4")) and "product" not in many


def test_other_currencies_and_orders_that_do_not_add_up_are_left_out():
    out = square(
        [
            order("A"),
            order("B", total_money=money(5000, "USD")),
            order("C", total_money=money(0)),
            order("D", total_money=money(1000), total_tax_money=money(3000)),
            order("E", closed_at=None, created_at=None),
            order("F"),
        ]
    ).sync(ctx())
    assert [r["reference"] for r in out.datasets["sales"]] == ["sq-A", "sq-F"]
    assert out.records_fetched == 6 and out.cursor["left_out_other_currency"] == 1


def test_every_page_and_every_active_location_is_read_and_only_reads():
    orders = [order(f"O{i}", location_id="L1" if i % 2 else "L2") for i in range(1, 1201)]
    provider = square(
        orders,
        locations=[
            {"id": "L1", "status": "ACTIVE"},
            {"id": "L2", "status": "ACTIVE"},
            {"id": "L3", "status": "INACTIVE"},
        ],
    )
    out = provider.sync(ctx())
    assert out.records_fetched == 1200 and len(out.datasets["sales"]) == 1200
    assert {tuple(b["location_ids"]) for b in provider.bodies} == {("L1", "L2")}
    assert all(
        r.method in ("GET", "POST") and r.url.path in ("/v2/locations", "/v2/orders/search")
        for r in provider.seen
    )
    assert len(provider.bodies) == 3  # 500 + 500 + 200


def test_the_first_sync_bounds_the_history_and_later_ones_ask_for_what_changed():
    first = square()
    out = first.sync(ctx())
    assert first.bodies[0]["query"]["filter"]["date_time_filter"]["updated_at"]["start_at"] == (
        NOW - timedelta(days=730)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert first.bodies[0]["query"]["filter"]["state_filter"] == {"states": ["COMPLETED"]}
    later = square()
    later.sync(ctx({"since": out.cursor["since"]}))
    assert (
        later.bodies[0]["query"]["filter"]["date_time_filter"]["updated_at"]["start_at"]
        == out.cursor["since"]
    )
    assert out.cursor["since"] < NOW.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )  # a margin, so nothing falls in a gap


def test_a_token_that_stops_working_during_a_sync_asks_to_be_connected_again():
    with pytest.raises(ReauthRequired):
        square(status=401).sync(ctx())


# --- the whole connect and sync through the framework ----------------------------------------------------


def test_a_token_connects_square_and_the_sync_brings_its_sales_in_without_ever_showing_the_token(
    api, db, business, storage, monkeypatch
):
    from tests.test_integrations import auth, request_sync, run_worker, url

    refunds = [{"id": "R1", "status": "COMPLETED", "amount_money": money(12000)}]
    monkeypatch.setitem(PROVIDERS, "square", square([order("A"), order("B", refunds=refunds)]))
    listed = api.get(url(business, "/providers"), headers=auth(business)).json()
    assert [f["name"] for f in next(p for p in listed if p["key"] == "square")["fields"]] == [
        "access_token"
    ]

    res = api.post(
        url(business, "/connect-keys"),
        json={"provider": "square", "values": {"access_token": "sq-token-secret"}},
        headers=auth(business),
    )
    assert res.status_code == 200, res.text
    conn = res.json()
    assert conn["provider"] == "square" and conn["account_name"] == "Baker Street Bakes"
    assert "sq-token-secret" not in json.dumps(conn)

    request_sync(api, business, conn["id"])
    ran = run_worker(db, storage)
    assert ran.status == "succeeded" and ran.result["records_created"] == 3
    with scoped(db, business):
        sales = list(db.scalars(select(Sale)))
        assert sorted(s.source_ref for s in sales) == ["sq-A", "sq-B", "sq-refund-R1"]
        assert {s.source for s in sales} == {"square"}

    again = api.post(
        url(business, "/connect-keys"),
        json={"provider": "square", "values": {"access_token": "sq-token-secret"}},
        headers=auth(business),
    )
    assert again.json()["id"] == conn["id"]  # the same account is not added twice


def test_a_wrong_token_is_refused_and_nothing_is_saved(api, db, business, monkeypatch):
    from app.models.integrations import Integration
    from tests.test_integrations import auth, url

    monkeypatch.setitem(PROVIDERS, "square", square(status=401))
    res = api.post(
        url(business, "/connect-keys"),
        json={"provider": "square", "values": {"access_token": "bad"}},
        headers=auth(business),
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "reauth_required"
    with scoped(db, business):
        assert db.scalars(select(Integration)).first() is None
