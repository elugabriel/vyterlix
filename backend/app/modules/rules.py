"""The twelve parts ("modules") of Vyterlix, and which of them a business sees.

A module is a group of figures with a plain name ("Bookings & Appointments"). Which modules a
business sees depends on what it sells: a business that only provides a service has no stock
module, a business that only sells things has no bookings module, and one that does both sees
both. A module also stays out of sight until it has figures to show, so nobody sees an empty box.

Kept free of the database so the rules can be tested exhaustively. Nothing here changes how a
figure is worked out; it only decides where a figure is shown and who sees it.
"""

from dataclasses import dataclass

SELLS = ("products", "services", "both")


@dataclass(frozen=True)
class Module:
    code: str
    name: str
    summary: str  # one plain sentence for the owner
    kpis: tuple[str, ...]  # the figures that belong here, each in exactly one module
    needs: str = "both"  # who sees it: "products", "services" or "both"


MODULES: tuple[Module, ...] = (
    Module(
        "sales",
        "Sales & Revenue",
        "What you sold and how it is moving.",
        (
            "revenue", "gross_sales", "sales_count", "average_order_value", "revenue_growth_pct",
            "revenue_vs_last_year_pct", "units_sold", "refund_rate_pct", "new_leads",
            "proposals_sent", "win_rate_pct", "work_won",
        ),
    ),  # fmt: skip
    Module(
        "financial",
        "Financial Intelligence",
        "Profit, running costs, money owed and money in the bank.",
        (
            "net_profit", "net_margin_pct", "operating_expenses", "money_owed", "overdue_invoices",
            "days_to_get_paid", "bank_money_in", "bank_money_out",
        ),
    ),  # fmt: skip
    Module(
        "profitability",
        "Service & Product Profitability",
        "How much you keep from each sale after what it costs you to deliver.",
        ("gross_profit", "gross_margin_pct", "cost_of_goods_sold"),
    ),
    Module(
        "retention",
        "Customer Retention & Loyalty",
        "Whether customers come back, and whether they are happy.",
        (
            "customer_retention_pct", "customer_churn_pct", "repeat_customer_rate_pct",
            "returning_customers", "rebooking_rate_pct", "regular_clients", "renewal_rate_pct",
            "happy_ratings_pct", "unhappy_ratings_pct", "visits_per_customer",
        ),
    ),  # fmt: skip
    Module(
        "customers",
        "Customer Insight",
        "Who your customers are and what each is worth.",
        (
            "active_customers", "new_customers", "new_clients", "average_customer_value",
            "customers_identified_pct",
        ),
    ),
    Module(
        "bookings",
        "Bookings & Appointments",
        "Appointments kept, cancelled and missed.",
        (
            "appointments_booked", "appointments_kept", "cancellation_rate_pct", "no_show_rate_pct",
            "late_cancellations",
        ),
        needs="services",
    ),
    Module(
        "inventory",
        "Stock & Inventory",
        "What is on the shelf, what is selling and what to reorder.",
        (
            "days_of_stock", "out_of_stock_pct", "stock_turnover", "stock_units", "stock_value",
            "stock_purchases",
        ),
        needs="products",
    ),  # fmt: skip
    Module(
        "marketing",
        "Marketing & Acquisition",
        "How many people find you, and what they do next.",
        (
            "website_visits", "website_new_visitors", "website_actions", "ad_spend", "ad_clicks",
            "ad_cost_per_click", "ad_results", "ad_cost_per_result", "cost_per_new_customer",
            "sales_per_ad_pound",
        ),
    ),
    # Figures for these are not worked out yet, so they stay out of sight until they are.
    Module(
        "workforce",
        "Workforce & Productivity",
        "How your team's time is used.",
        ("hours_worked", "revenue_per_hour", "utilisation_pct", "appointments_per_hour"),
    ),
    Module(
        "operations",
        "Operational Performance",
        "How well the work gets done.",
        ("completion_rate_pct", "average_appointment_minutes", "overrun_pct"),
    ),
    Module("suppliers", "Supplier Performance", "How reliable and fair your suppliers are.", ()),
    Module("demand", "Demand Forecasting", "What to expect next, so you can be ready.", ()),
)  # fmt: skip

BY_CODE = {m.code: m for m in MODULES}
_MODULE_OF = {code: m.code for m in MODULES for code in m.kpis}


def module_of(kpi_code: str) -> str | None:
    """The module a figure is shown in, or None for a figure that is not part of any."""
    return _MODULE_OF.get(kpi_code)


def applies(module: Module, sells: str | None) -> bool:
    """Is this module for a business that sells `sells`? One that has not said sees both kinds."""
    if sells is None or sells == "both" or module.needs == "both":
        return True
    return module.needs == sells


def visible_modules(sells: str | None, kpi_codes: set[str]) -> list[Module]:
    """The modules to show, in order: right for the business and with a figure to show."""
    return [m for m in MODULES if applies(m, sells) and any(code in kpi_codes for code in m.kpis)]


# --- the health score ---------------------------------------------------------------------------
# The health score is made of areas (financial, sales, customer, marketing, inventory, operational).
# Which areas count depends on what the business sells, so a salon is not marked down for having no
# stock, and a shop is not marked for having no bookings.


def health_area_counts(area: str, sells: str | None, has_bookings: bool) -> bool:
    """Does this area count towards this business's health score?"""
    if area == "inventory":
        return sells != "services"
    if area == "operational":  # appointments and bookings
        if sells == "products":
            return False
        return sells in ("services", "both") or has_bookings
    return True
