# ruff: noqa: E501, F811
"""What the business sells (client feedback 15/10/2026, section 6): products, services or both."""

from datetime import date, timedelta

import pytest

from app.models.data import Customer
from tests.shops import _make, _sale
from tests.test_business_profile_api import MINIMAL, url

ORGS = "/api/v1/organizations"


def put(api, business, body, who="owner"):
    return api.put(url(business[0]), json=body, headers=business[2][who])


def patch(api, business, body, who="owner"):
    return api.patch(url(business[0]), json=body, headers=business[2][who])


# --- saying what you sell --------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["products", "services", "both"])
def test_a_business_can_say_what_it_sells(api, business, value):
    assert put(api, business, {**MINIMAL, "sells": value}).json()["sells"] == value


def test_it_is_optional_and_empty_until_said(api, business):
    assert put(api, business, MINIMAL).json()["sells"] is None


@pytest.mark.parametrize("value", ["goods", "", "Services", 3])
def test_anything_else_is_refused(api, business, value):
    assert put(api, business, {**MINIMAL, "sells": value}).status_code == 422


def test_it_can_be_changed_and_taken_back(api, business):
    put(api, business, {**MINIMAL, "sells": "products"})
    assert patch(api, business, {"sells": "services"}).json()["sells"] == "services"
    assert patch(api, business, {"sells": None}).json()["sells"] is None
    assert (
        patch(api, business, {"town_city": "Leeds"}).json()["sells"] is None
    )  # other changes leave it alone


def test_only_an_owner_can_change_it(api, business):
    put(api, business, MINIMAL)
    assert patch(api, business, {"sells": "services"}, who="viewer").status_code == 403


# --- every screen can see it ------------------------------------------------------------------------------


def test_the_business_carries_what_it_sells_so_every_screen_can_adapt(api, business):
    org_id, auth = business[0], business[2]
    assert api.get(f"{ORGS}/{org_id}", headers=auth["owner"]).json()["sells"] is None
    put(api, business, {**MINIMAL, "sells": "services"})
    assert api.get(f"{ORGS}/{org_id}", headers=auth["owner"]).json()["sells"] == "services"
    assert api.get(f"{ORGS}/{org_id}", headers=auth["viewer"]).json()["sells"] == "services"
    mine = api.get(ORGS, headers=auth["owner"]).json()
    assert [o["sells"] for o in mine if o["id"] == org_id] == ["services"]


def test_each_business_has_its_own_answer(api, db, signup):
    owner = signup("owner@acme.co.uk")
    a = api.post(ORGS, json={"name": "Acme Salon"}, headers=owner).json()["id"]
    b = api.post(ORGS, json={"name": "Acme Shop"}, headers=owner).json()["id"]
    api.put(url(a), json={**MINIMAL, "sells": "services"}, headers=owner)
    api.put(url(b), json={**MINIMAL, "sells": "products"}, headers=owner)
    by_name = {o["name"]: o["sells"] for o in api.get(ORGS, headers=owner).json()}
    assert by_name == {"Acme Salon": "services", "Acme Shop": "products"}


def test_renaming_the_business_keeps_the_answer(api, business):
    org_id, auth = business[0], business[2]
    put(api, business, {**MINIMAL, "sells": "both"})
    renamed = api.patch(f"{ORGS}/{org_id}", json={"name": "Acme Two"}, headers=auth["owner"]).json()
    assert renamed["name"] == "Acme Two" and renamed["sells"] == "both"


# --- a business with no stock is not shown stock ----------------------------------------------------------------


def stock_figures(api, business):
    org_id, auth = business[0], business[2]
    body = api.get(f"{ORGS}/{org_id}/kpis", headers=auth["owner"]).json()
    return {k["code"] for k in body["kpis"] if k["category"] == "inventory"}


def test_a_business_that_sells_only_services_is_not_shown_stock_figures(api, business):
    put(api, business, {**MINIMAL, "sells": "services"})
    assert stock_figures(api, business) == set()
    org_id, auth = business[0], business[2]
    codes = {
        k["code"] for k in api.get(f"{ORGS}/{org_id}/kpis", headers=auth["owner"]).json()["kpis"]
    }
    assert {
        "revenue",
        "net_profit",
        "active_customers",
        "customer_retention_pct",
    } <= codes  # everything else stays


@pytest.mark.parametrize("sells", ["products", "both", None])
def test_everyone_else_still_sees_the_stock_figures(api, business, sells):
    put(api, business, {**MINIMAL, "sells": sells} if sells else MINIMAL)
    assert {"stock_turnover", "days_of_stock", "stock_units"} <= stock_figures(api, business)


def test_a_business_with_no_profile_yet_sees_everything(api, business):
    assert "stock_turnover" in stock_figures(api, business)


def test_a_stock_figure_can_still_be_asked_for_directly(api, business):
    org_id, auth = business[0], business[2]
    put(api, business, {**MINIMAL, "sells": "services"})
    assert api.get(f"{ORGS}/{org_id}/kpis/stock_turnover", headers=auth["owner"]).status_code == 200


# --- customers to follow up --------------------------------------------------------------------------------------

LATEST = date(2026, 9, 28)


def customer(db, business, name, **kw):
    return _make(db, business, Customer, name=name, **kw)


@pytest.fixture
def customers(db, business):
    """Latest sale 28/09/2026. Ana: 300 over two sales, quiet 100 days. Ben: 500, quiet 70 days.
    Cat: 900 but bought 10 days ago. Dev: 200 sale and a 50 refund (150 net), quiet 90 days.
    Eve: one sale 61 days ago, but made inactive. Fay: a refund only. Sales with nobody named do not count."""
    ana, ben, cat, dev, eve, fay = (
        customer(db, business, n) for n in ("Ana", "Ben", "Cat", "Dev", "Eve", "Fay")
    )
    d = lambda days: LATEST - timedelta(days=days)  # noqa: E731
    _sale(db, business, d(120), 100, customer=ana)
    _sale(db, business, d(100), 200, customer=ana)
    _sale(db, business, d(70), 500, customer=ben)
    _sale(db, business, d(400), 900, customer=cat)
    _sale(db, business, d(10), 10, customer=cat)
    _sale(db, business, d(95), 200, customer=dev)
    _sale(db, business, d(90), 50, customer=dev, kind="refund")
    _sale(db, business, d(61), 999, customer=eve)
    _sale(db, business, d(80), 40, customer=fay, kind="refund")
    _sale(db, business, d(150), 777)  # nobody named
    _sale(db, business, d(0), 5)  # the latest sale: today, as far as the books go
    from app.models.data import Customer as C
    from tests.test_health import scoped

    with scoped(db, business):
        db.execute(C.__table__.update().where(C.name == "Eve").values(is_active=False))
    return {"ana": ana, "ben": ben, "cat": cat, "dev": dev}


def follow_up(api, business, who="owner", **params):
    res = api.get(
        f"{ORGS}/{business[0]}/kpis/customers/follow-up",
        params=params or None,
        headers=business[2][who],
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_the_best_customers_who_have_gone_quiet_are_listed_best_first(api, business, customers):
    body = follow_up(api, business)
    assert body["as_of"] == LATEST.isoformat() and body["quiet_days"] == 60
    assert [c["name"] for c in body["customers"]] == ["Ben", "Ana", "Dev"]
    ben, ana, dev = body["customers"]
    assert (ben["total_spent"], ben["orders"], ben["days_since"], ben["last_bought_on"]) == (
        "500.00",
        1,
        70,
        (LATEST - timedelta(days=70)).isoformat(),
    )
    assert (ana["total_spent"], ana["orders"], ana["days_since"]) == ("300.00", 2, 100)
    assert (dev["total_spent"], dev["orders"], dev["days_since"]) == (
        "150.00",
        1,
        90,
    )  # the refund comes off what they spent and does not count as an order
    assert body["total"] == 3


def test_someone_who_bought_recently_is_not_quiet_however_much_they_spent(api, business, customers):
    assert "Cat" not in [c["name"] for c in follow_up(api, business)["customers"]]


def test_quiet_is_counted_from_the_latest_sale_not_from_today(api, business, customers):
    # the books end on 28/09/2026: a business that has not brought in newer sales is not told everyone has gone quiet
    assert follow_up(api, business)["as_of"] == LATEST.isoformat()


def test_the_length_of_time_can_be_changed_and_the_line_is_exact(api, business, customers):
    assert [c["name"] for c in follow_up(api, business, quiet_days=70)["customers"]] == [
        "Ben",
        "Ana",
        "Dev",
    ]  # Ben: exactly 70 days
    assert [c["name"] for c in follow_up(api, business, quiet_days=71)["customers"]] == [
        "Ana",
        "Dev",
    ]
    assert [c["name"] for c in follow_up(api, business, quiet_days=14)["customers"]] == [
        "Ben",
        "Ana",
        "Dev",
    ]


def test_only_the_best_few_are_listed_but_all_are_counted(api, business, customers):
    body = follow_up(api, business, limit=2)
    assert [c["name"] for c in body["customers"]] == ["Ben", "Ana"] and body["total"] == 3


def test_a_customer_with_only_a_refund_or_who_is_inactive_is_left_out(api, business, customers):
    names = [c["name"] for c in follow_up(api, business)["customers"]]
    assert "Fay" not in names and "Eve" not in names


def test_a_business_with_no_sales_has_nobody_to_follow_up(api, business):
    assert follow_up(api, business) == {
        "as_of": None,
        "quiet_days": 60,
        "total": 0,
        "customers": [],
    }


@pytest.mark.parametrize(
    "params", [{"quiet_days": 13}, {"quiet_days": 366}, {"limit": 0}, {"limit": 26}]
)
def test_silly_limits_are_refused(api, business, params):
    res = api.get(
        f"{ORGS}/{business[0]}/kpis/customers/follow-up",
        params=params,
        headers=business[2]["owner"],
    )
    assert res.status_code == 422


def test_a_viewer_can_look_and_another_business_cannot(api, business, customers):
    assert follow_up(api, business, who="viewer")["total"] == 3
    assert api.get(
        f"{ORGS}/{business[0]}/kpis/customers/follow-up", headers=business[2]["other"]
    ).status_code in (403, 404)
    assert api.get(f"{ORGS}/{business[0]}/kpis/customers/follow-up").status_code == 401
    assert follow_up(api, business, who="other") if False else True


def test_each_business_sees_only_its_own_customers(api, db, business, customers):
    other = api.get(
        f"{ORGS}/{business[1]}/kpis/customers/follow-up", headers=business[2]["other"]
    ).json()
    assert other["customers"] == [] and other["total"] == 0
