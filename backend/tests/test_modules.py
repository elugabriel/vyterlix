# ruff: noqa: E501, F811
"""The twelve parts of Vyterlix: where each figure is shown, who sees which part, and which areas
count towards a business's health score (client feedback 15/11/2026)."""

import pytest
from sqlalchemy import select

from app.models.health import BusinessHealthComponent
from app.models.kpi import KpiDefinition
from app.modules.rules import (
    BY_CODE,
    MODULES,
    applies,
    health_area_counts,
    module_of,
    visible_modules,
)
from tests.test_booking_figures import (
    salon,  # noqa: F401  (a pretend salon with a year of bookings)
)
from tests.test_business_type import MINIMAL, put
from tests.test_health import calculate
from tests.test_kpi_engine import scoped

ORGS = "/api/v1/organizations"


# --- the rules ---------------------------------------------------------------------------------


def test_there_are_twelve_modules_with_plain_names():
    assert len(MODULES) == 12
    assert {m.name for m in MODULES} >= {
        "Sales & Revenue",
        "Bookings & Appointments",
        "Stock & Inventory",
        "Workforce & Productivity",
        "Operational Performance",
        "Supplier Performance",
        "Demand Forecasting",
    }
    assert all(m.summary.endswith(".") for m in MODULES)


def test_every_figure_belongs_to_exactly_one_module(db):
    codes = set(db.scalars(select(KpiDefinition.code)))
    placed = [code for m in MODULES for code in m.kpis]
    assert len(placed) == len(set(placed)), "a figure is in two modules"
    assert codes <= set(placed), f"not in any module: {sorted(codes - set(placed))}"
    assert set(placed) <= codes, (
        f"modules name figures that do not exist: {sorted(set(placed) - codes)}"
    )


def test_a_figure_knows_its_module():
    assert module_of("no_show_rate_pct") == "bookings"
    assert module_of("stock_turnover") == "inventory"
    assert module_of("website_visits") == "marketing"
    assert module_of("nonsense") is None


@pytest.mark.parametrize(
    ("code", "sells", "expected"),
    [
        ("bookings", "services", True),
        ("bookings", "both", True),
        ("bookings", "products", False),
        ("bookings", None, True),  # not said yet: shown if it has figures
        ("inventory", "products", True),
        ("inventory", "both", True),
        ("inventory", "services", False),
        ("sales", "services", True),
        ("sales", "products", True),
    ],
)
def test_who_sees_which_module(code, sells, expected):
    assert applies(BY_CODE[code], sells) is expected


def test_a_module_with_no_figures_stays_out_of_sight():
    every_figure = {code for m in MODULES for code in m.kpis}
    shown = {m.code for m in visible_modules("both", every_figure)}
    assert shown == {m.code for m in MODULES if m.kpis}
    assert not {"suppliers", "demand"} & shown  # nothing to show for these yet


def test_a_salon_sees_bookings_not_stock_and_a_shop_the_other_way_round():
    every_figure = {code for m in MODULES for code in m.kpis}
    salon_sees = {m.code for m in visible_modules("services", every_figure)}
    shop_sees = {m.code for m in visible_modules("products", every_figure)}
    assert "bookings" in salon_sees and "inventory" not in salon_sees
    assert "inventory" in shop_sees and "bookings" not in shop_sees


@pytest.mark.parametrize(
    ("area", "sells", "has_bookings", "counts"),
    [
        ("inventory", "services", False, False),
        ("inventory", "products", False, True),
        ("inventory", "both", False, True),
        ("inventory", None, False, True),
        ("operational", "products", True, False),
        ("operational", "services", False, True),
        ("operational", "both", False, True),
        ("operational", None, True, True),
        ("operational", None, False, False),
        ("financial", "services", False, True),
        ("customer", "products", False, True),
    ],
)
def test_which_areas_count_towards_health(area, sells, has_bookings, counts):
    assert health_area_counts(area, sells, has_bookings) is counts


# --- through the website's API -----------------------------------------------------------------


def kpis(api, business):
    return api.get(f"{ORGS}/{business[0]}/kpis", headers=business[2]["owner"]).json()


def test_the_figures_come_with_their_module_and_the_modules_in_order(api, salon):
    body = kpis(api, salon)
    by_code = {k["code"]: k["module"] for k in body["kpis"]}
    assert by_code["no_show_rate_pct"] == "bookings"
    assert by_code["revenue"] == "sales"
    codes = [m["code"] for m in body["modules"]]
    assert codes == [m.code for m in MODULES if m.code in codes]  # same order as the registry
    assert all(m["name"] and m["summary"] for m in body["modules"])
    # every module listed has at least one figure shown
    shown = {k["module"] for k in body["kpis"]}
    assert set(codes) <= shown


def test_a_services_business_is_not_given_a_stock_module(api, salon):
    put(api, salon, {**MINIMAL, "sells": "services"})
    codes = [m["code"] for m in kpis(api, salon)["modules"]]
    assert "bookings" in codes and "inventory" not in codes


def test_a_products_business_is_not_given_a_bookings_module(api, salon):
    put(api, salon, {**MINIMAL, "sells": "products"})
    codes = [m["code"] for m in kpis(api, salon)["modules"]]
    assert "inventory" in codes and "bookings" not in codes


# --- health ------------------------------------------------------------------------------------


def areas(db, business):
    with scoped(db, business):
        return {c.category: c for c in db.scalars(select(BusinessHealthComponent))}


def test_a_business_with_appointments_is_judged_on_them(api, db, salon):
    calculate(db, salon)
    found = areas(db, salon)
    assert "operational" in found
    with scoped(db, salon):
        every_month = db.scalars(select(BusinessHealthComponent)).all()
    judged = {m["kpi_code"] for c in every_month for m in c.details}
    assert {"no_show_rate_pct", "cancellation_rate_pct"} <= judged


def test_a_service_business_is_not_marked_down_for_having_no_stock(api, db, salon):
    put(api, salon, {**MINIMAL, "sells": "services"})
    calculate(db, salon)
    found = areas(db, salon)
    assert "inventory" not in found and "operational" in found


def test_a_shop_is_not_judged_on_appointments(api, db, salon):
    put(api, salon, {**MINIMAL, "sells": "products"})
    calculate(db, salon)
    assert "operational" not in areas(db, salon)
