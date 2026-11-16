# ruff: noqa: E501, F811
"""The owner can show or hide the parts of Vyterlix (client feedback 15/11/2026)."""

from sqlalchemy import select

from app.models.health import BusinessHealthComponent
from tests.test_business_type import MINIMAL, put
from tests.test_health import calculate
from tests.test_kpi_engine import scoped
from tests.test_service_figures import (
    salon,  # noqa: F401  (a pretend salon with bookings, hours and sales)
)

ORGS = "/api/v1/organizations"


def modules(api, business, who="owner"):
    res = api.get(f"{ORGS}/{business[0]}/modules", headers=business[2][who])
    assert res.status_code == 200, res.text
    return {m["code"]: m for m in res.json()}


def choose(api, business, code, enabled, who="owner"):
    return api.put(
        f"{ORGS}/{business[0]}/modules/{code}", json={"enabled": enabled}, headers=business[2][who]
    )


def kpi_body(api, business):
    return api.get(f"{ORGS}/{business[0]}/kpis", headers=business[2]["owner"]).json()


def test_every_part_is_listed_and_shown_unless_there_is_a_reason(api, salon):
    states = modules(api, salon)
    assert len(states) == 12
    assert states["bookings"]["status"] == "shown" and states["bookings"]["enabled"] is True
    assert states["workforce"]["status"] == "shown"
    # nothing to show for this one yet (no costs name a supplier), and the owner is told why
    assert (
        states["suppliers"]["status"] == "no_figures" and states["suppliers"]["can_switch"] is False
    )
    assert "who you bought from" in states["suppliers"]["reason"]


def test_a_part_for_a_different_kind_of_business_says_so(api, salon):
    put(api, salon, {**MINIMAL, "sells": "products"})
    bookings = modules(api, salon)["bookings"]
    assert bookings["status"] == "not_for_you" and bookings["can_switch"] is False
    assert "provide a service" in bookings["reason"]
    put(api, salon, {**MINIMAL, "sells": "services"})
    assert "sell products" in modules(api, salon)["inventory"]["reason"]


def test_the_owner_can_hide_a_part_and_it_leaves_the_key_figures(api, salon):
    before = kpi_body(api, salon)
    assert "bookings" in {m["code"] for m in before["modules"]}
    res = choose(api, salon, "bookings", False)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "switched_off" and res.json()["enabled"] is False
    after = kpi_body(api, salon)
    assert "bookings" not in {m["code"] for m in after["modules"]}
    assert not {"no_show_rate_pct", "appointments_kept"} & {k["code"] for k in after["kpis"]}
    assert {"revenue", "net_profit"} <= {k["code"] for k in after["kpis"]}  # everything else stays


def test_a_hidden_part_comes_straight_back_when_switched_on(api, salon):
    choose(api, salon, "bookings", False)
    res = choose(api, salon, "bookings", True)
    assert res.json()["status"] == "shown"
    assert "bookings" in {m["code"] for m in kpi_body(api, salon)["modules"]}


def test_switching_the_same_way_twice_changes_nothing(api, salon):
    assert choose(api, salon, "marketing", True).json()["status"] in ("shown", "no_figures")
    choose(api, salon, "bookings", False)
    assert choose(api, salon, "bookings", False).json()["status"] == "switched_off"


def test_only_the_owner_can_choose_but_everyone_can_see(api, salon):
    assert choose(api, salon, "bookings", False, who="viewer").status_code == 403
    assert modules(api, salon, who="viewer")["bookings"]["enabled"] is True


def test_an_unknown_part_is_refused(api, salon):
    res = choose(api, salon, "nonsense", False)
    assert res.status_code == 404 and res.json()["error"]["code"] == "module_not_found"


def test_the_choice_is_this_businesss_own(api, db, signup):
    owner = signup("owner@acme.co.uk")
    a = api.post(ORGS, json={"name": "Acme Salon"}, headers=owner).json()["id"]
    b = api.post(ORGS, json={"name": "Acme Shop"}, headers=owner).json()["id"]
    api.put(f"{ORGS}/{a}/modules/sales", json={"enabled": False}, headers=owner)
    one = {m["code"]: m for m in api.get(f"{ORGS}/{a}/modules", headers=owner).json()}
    two = {m["code"]: m for m in api.get(f"{ORGS}/{b}/modules", headers=owner).json()}
    assert one["sales"]["enabled"] is False and two["sales"]["enabled"] is True


def test_a_hidden_part_is_left_out_of_the_health_score(api, db, salon):
    calculate(db, salon)

    def judged():
        with scoped(db, salon):
            rows = db.scalars(select(BusinessHealthComponent)).all()
        return {m["kpi_code"] for c in rows for m in c.details}

    assert "no_show_rate_pct" in judged()
    choose(api, salon, "bookings", False)  # the score is worked out again by the choice
    assert "no_show_rate_pct" not in judged()
    assert "gross_margin_pct" in judged()  # the rest is still judged


def test_a_hidden_part_is_left_off_the_today_screen_too(api, salon):
    def today_figures():
        res = api.get(f"{ORGS}/{salon[0]}/dashboard", headers=salon[2]["owner"])
        assert res.status_code == 200, res.text
        return {f["code"] for f in res.json()["figures"]}

    assert "revenue" in today_figures()
    choose(api, salon, "sales", False)
    assert "revenue" not in today_figures()
