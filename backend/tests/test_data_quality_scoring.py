"""The data-quality scoring rules, tested on plain figures (no database)."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.services.data_quality import (
    CustomerFacts,
    ExpenseFacts,
    Facts,
    ImportFact,
    ProductFacts,
    SalesFacts,
    StockFacts,
    assess,
)

D = Decimal
TODAY = date(2026, 10, 2)
MONTHLY_SALES = D("1000")
MONTHLY_EXPENSES = D("500")


def months(first, last):
    """['2026-01' .. '2026-09'] as first-of-month dates."""
    (y1, m1), (y2, m2) = (map(int, first.split("-")), map(int, last.split("-")))
    out = []
    y, m = y1, m1
    while (y, m) <= (y2, m2):
        out.append(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def sales(
    span=("2026-01", "2026-09"),
    skip=(),
    per_month=10,
    value=MONTHLY_SALES,
    costed=1.0,
    with_lines=1.0,
    linked=1.0,
    last=None,
):
    present = [m for m in months(*span) if m.strftime("%Y-%m") not in skip]
    count = per_month * len(present)
    total = value * len(present)
    costed_total = (total * D(str(costed))).quantize(D("0.01"))
    return SalesFacts(
        count=count,
        first=present[0].replace(day=5),
        last=last or present[-1].replace(day=28),
        value=total,
        costed_value=costed_total,
        with_lines=round(count * with_lines),
        with_costed_lines=round(count * costed),
        with_customer=round(count * linked),
        month_count={m: per_month for m in present},
        month_value={m: value for m in present},
        month_costed={m: (value * D(str(costed))).quantize(D("0.01")) for m in present},
    )


def expenses(
    span=("2026-01", "2026-09"),
    skip=(),
    per_month=5,
    value=MONTHLY_EXPENSES,
    categorised=1.0,
    last=None,
):
    present = [m for m in months(*span) if m.strftime("%Y-%m") not in skip]
    count = per_month * len(present)
    total = value * len(present)
    return ExpenseFacts(
        count=count,
        first=present[0].replace(day=3),
        last=last or present[-1].replace(day=28),
        value=total,
        categorised_value=(total * D(str(categorised))).quantize(D("0.01")),
        uncategorised=round(count * (1 - categorised)),
        month_count={m: per_month for m in present},
        month_value={m: value for m in present},
        month_categorised={m: (value * D(str(categorised))).quantize(D("0.01")) for m in present},
    )


def good(**kw):
    return Facts(sales=sales(), expenses=expenses(), **kw)


def types(report, dataset=None):
    return [i.issue_type for i in report.issues if dataset in (None, i.dataset)]


def issue(report, issue_type, dataset=None):
    [found] = [
        i for i in report.issues if i.issue_type == issue_type and dataset in (None, i.dataset)
    ]
    return found


def component(report, dataset, key):
    [ds] = [d for d in report.datasets if d.dataset == dataset]
    [c] = [c for c in ds.components if c.key == key]
    return c


# --- nothing yet ------------------------------------------------


def test_no_data_at_all():
    report = assess(Facts(), TODAY)
    assert report.score is None and report.band is None
    assert report.headline == "Add some sales to get a data-quality score."
    assert report.datasets == [] and report.months == [] and report.imports == []
    assert report.missing_datasets == ["Sales", "Expenses"]
    [only] = report.issues
    assert (only.dataset, only.issue_type, only.severity) == ("sales", "no_data", "critical")


def test_expenses_without_any_sales_still_gives_no_score():
    report = assess(Facts(expenses=expenses()), TODAY)
    assert report.score is None
    assert "no_data" in types(report, "sales")


# --- a healthy business ------------------------------------------------


def test_complete_recent_well_labelled_data_scores_100():
    report = assess(good(), TODAY)
    assert report.score == 100 and report.band == "good"
    assert report.issues == [] and report.missing_datasets == []
    assert [d.dataset for d in report.datasets] == ["sales", "expenses"]
    assert all(d.score == 100 for d in report.datasets)
    assert report.headline == "Your data quality is good: 100 out of 100."


def test_the_report_is_repeatable():
    assert assess(good(), TODAY) == assess(good(), TODAY)


@pytest.mark.parametrize("name", ["sales", "expenses"])
def test_each_datasets_check_weights_add_up_to_one(name):
    report = assess(good(), TODAY)
    [ds] = [d for d in report.datasets if d.dataset == name]
    assert sum(c.weight for c in ds.components) == pytest.approx(1.0)


def test_every_check_explains_itself_with_real_figures():
    report = assess(good(), TODAY)
    details = {c.key: c.detail for d in report.datasets for c in d.components}
    assert details["coverage"] in (
        "Sales recorded in 9 of 9 months",
        "Expenses recorded in 9 of 9 months",
    )
    assert details["cost_of_goods"] == "100% of sales value has a cost of goods recorded"
    assert details["line_detail"] == "90 of 90 sales say what was sold"
    assert details["categorised"] == "100% of expense value has a cost category"


# --- missing months ------------------------------------------------


def test_a_missing_month_is_found_and_costs_points():
    facts = Facts(sales=sales(skip=("2026-03",)), expenses=expenses())
    report = assess(facts, TODAY)
    found = issue(report, "missing_period", "sales")
    assert found.severity == "warning" and found.affected_count == 1
    assert (found.period_start, found.period_end) == (date(2026, 3, 1), date(2026, 3, 31))
    assert found.message == "No sales are recorded for March 2026."
    assert found.fix == "Upload the missing month or type it in."
    assert component(report, "sales", "coverage").score == 89  # 8 of 9 months
    assert report.score < 100


def test_a_long_gap_is_one_critical_issue():
    facts = Facts(sales=sales(skip=("2026-04", "2026-05", "2026-06")), expenses=expenses())
    found = issue(assess(facts, TODAY), "missing_period", "sales")
    assert found.severity == "critical" and found.affected_count == 3
    assert found.message == "No sales are recorded for April 2026 to June 2026."
    assert (found.period_start, found.period_end) == (date(2026, 4, 1), date(2026, 6, 30))


def test_separate_gaps_are_separate_issues():
    facts = Facts(sales=sales(skip=("2026-02", "2026-05", "2026-06")), expenses=expenses())
    gaps = [i for i in assess(facts, TODAY).issues if i.issue_type == "missing_period"]
    assert sorted((g.period_start, g.affected_count) for g in gaps) == [
        (date(2026, 2, 1), 1),
        (date(2026, 5, 1), 2),
    ]


def test_a_gap_across_new_year_ends_on_the_right_day():
    facts = Facts(sales=sales(span=("2025-10", "2026-03"), skip=("2025-12", "2026-01")))
    found = issue(assess(facts, date(2026, 4, 10)), "missing_period", "sales")
    assert (found.period_start, found.period_end) == (date(2025, 12, 1), date(2026, 1, 31))


def test_the_month_in_progress_is_never_missing():
    # Sales until September; it's now early October and nothing has been entered yet.
    report = assess(Facts(sales=sales(), expenses=expenses()), date(2026, 10, 2))
    assert "missing_period" not in types(report)


def test_data_only_in_the_current_month_has_no_gaps_to_find():
    only_now = Facts(sales=sales(span=("2026-10", "2026-10"), per_month=3))
    report = assess(only_now, date(2026, 10, 15))
    assert "missing_period" not in types(report)
    assert (
        component(report, "sales", "coverage").detail == "Not enough history yet to look for gaps"
    )
    assert component(report, "sales", "coverage").score == 100


def test_expenses_missing_before_they_started_are_a_gap():
    facts = Facts(sales=sales(), expenses=expenses(span=("2026-07", "2026-09")))
    found = issue(assess(facts, TODAY), "missing_period", "expenses")
    assert found.severity == "critical" and found.affected_count == 6
    assert found.message == "No expenses are recorded for January 2026 to June 2026."


def test_sales_missing_before_they_started_are_a_gap_too():
    facts = Facts(sales=sales(span=("2026-05", "2026-09")), expenses=expenses())
    found = issue(assess(facts, TODAY), "missing_period", "sales")
    assert found.affected_count == 4  # January to April


# --- fresh or stale ------------------------------------------------


@pytest.mark.parametrize(
    ("days", "score"),
    [
        (0, 100),
        (7, 100),
        (8, 75),
        (14, 75),
        (15, 50),
        (30, 50),
        (31, 25),
        (60, 25),
        (61, 0),
        (400, 0),
    ],
)
def test_freshness_steps(days, score):
    last = date.fromordinal(TODAY.toordinal() - days)
    report = assess(Facts(sales=sales(last=last)), TODAY)
    assert component(report, "sales", "freshness").score == score


@pytest.mark.parametrize(
    ("days", "severity"), [(14, None), (15, "warning"), (30, "warning"), (31, "critical")]
)
def test_stale_data_is_flagged_after_two_weeks(days, severity):
    last = date.fromordinal(TODAY.toordinal() - days)
    report = assess(Facts(sales=sales(last=last)), TODAY)
    found = [i for i in report.issues if i.issue_type == "stale_data"]
    if severity is None:
        assert found == []
    else:
        assert found[0].severity == severity and found[0].affected_count == days
        assert (found[0].period_start, found[0].period_end) == (last, TODAY)


def test_stale_message_uses_a_uk_date():
    report = assess(Facts(sales=sales(last=date(2026, 9, 10))), TODAY)
    assert (
        issue(report, "stale_data").message == "The latest sale is dated 10/09/2026, 22 days ago."
    )


def test_today_and_one_day_wording():
    assert (
        "(today)"
        in component(assess(Facts(sales=sales(last=TODAY)), TODAY), "sales", "freshness").detail
    )
    one = date(2026, 10, 1)
    assert (
        "(1 day ago)"
        in component(assess(Facts(sales=sales(last=one)), TODAY), "sales", "freshness").detail
    )


def test_a_future_dated_record_is_not_stale():
    report = assess(Facts(sales=sales(last=date(2026, 10, 3))), TODAY)
    assert component(report, "sales", "freshness").score == 100 and "stale_data" not in types(
        report
    )


# --- cost of goods ------------------------------------------------


@pytest.mark.parametrize(
    ("costed", "severity"),
    [
        (1.0, None),
        (0.9, "info"),
        (0.8, "info"),
        (0.79, "warning"),
        (0.5, "warning"),
        (0.49, "critical"),
        (0.0, "critical"),
    ],
)
def test_missing_cost_severity(costed, severity):
    report = assess(Facts(sales=sales(costed=costed), expenses=expenses()), TODAY)
    found = [i for i in report.issues if i.issue_type == "missing_cost"]
    if severity is None:
        assert found == []
    else:
        assert found[0].severity == severity
        assert component(report, "sales", "cost_of_goods").score == round(costed * 100)


def test_missing_cost_says_how_much_is_unknown():
    report = assess(Facts(sales=sales(costed=0.6), expenses=expenses()), TODAY)
    found = issue(report, "missing_cost")
    assert found.message.startswith("40% of your sales value has no cost of goods")
    assert found.details == {"cost_known_pct": 60} and found.affected_count == 36


# --- line detail and customer links ------------------------------------------------


@pytest.mark.parametrize(
    ("share", "severity"),
    [(1.0, None), (0.5, None), (0.4, "info"), (0.2, "info"), (0.1, "warning"), (0.0, "warning")],
)
def test_sales_that_say_what_was_sold(share, severity):
    report = assess(Facts(sales=sales(with_lines=share), expenses=expenses()), TODAY)
    found = [i for i in report.issues if i.issue_type == "no_line_detail"]
    assert (found[0].severity if found else None) == severity


def test_customer_links_are_only_mentioned_for_enough_sales_and_never_scored():
    few = sales(per_month=1, linked=0.0, span=("2026-01", "2026-09"))  # 9 sales
    assert "no_customer_link" not in types(assess(Facts(sales=few), TODAY))
    many = sales(linked=0.1)
    report = assess(Facts(sales=many, expenses=expenses()), TODAY)
    assert issue(report, "no_customer_link").severity == "info"
    assert component(report, "sales", "coverage").score == 100  # nothing about customers is scored
    assert "no_customer_link" not in types(assess(Facts(sales=sales(linked=0.3)), TODAY))


# --- expenses ------------------------------------------------


@pytest.mark.parametrize(
    ("share", "severity"), [(1.0, None), (0.8, "info"), (0.79, "warning"), (0.0, "warning")]
)
def test_uncategorised_expenses(share, severity):
    report = assess(Facts(sales=sales(), expenses=expenses(categorised=share)), TODAY)
    found = [i for i in report.issues if i.issue_type == "uncategorised_expenses"]
    assert (found[0].severity if found else None) == severity
    assert component(report, "expenses", "categorised").score == round(share * 100)


def test_uncategorised_message_counts_and_percentages():
    report = assess(Facts(sales=sales(), expenses=expenses(categorised=0.5)), TODAY)
    found = issue(report, "uncategorised_expenses")
    assert found.message.startswith("22 expenses (50% of the value) have no cost category")
    assert found.affected_count == 22


def test_a_single_uncategorised_expense_reads_correctly():
    facts = expenses(span=("2026-09", "2026-09"), per_month=1, categorised=0.0)
    report = assess(Facts(sales=sales(span=("2026-09", "2026-09")), expenses=facts), TODAY)
    assert issue(report, "uncategorised_expenses").message.startswith(
        "1 expense (100% of the value)"
    )


def test_no_expenses_at_all_is_a_warning_and_drags_the_score_down():
    report = assess(Facts(sales=sales()), TODAY)
    found = issue(report, "no_data", "expenses")
    assert found.severity == "warning" and "profit can't be worked out" in found.message
    assert report.missing_datasets == ["Expenses"]
    assert report.score == 60 and report.band == "fair"  # perfect sales 3/5 of the weight
    assert "No expenses recorded yet." in report.headline


# --- the overall score ------------------------------------------------


def test_sales_count_for_more_than_expenses():
    weak_sales = assess(Facts(sales=sales(costed=0.0, with_lines=0.0), expenses=expenses()), TODAY)
    weak_expenses = assess(Facts(sales=sales(), expenses=expenses(categorised=0.0)), TODAY)
    assert weak_sales.score < weak_expenses.score


def test_the_overall_score_is_the_weighted_average():
    report = assess(Facts(sales=sales(costed=0.5), expenses=expenses(categorised=0.5)), TODAY)
    sales_score = next(d.score for d in report.datasets if d.dataset == "sales")
    expense_score = next(d.score for d in report.datasets if d.dataset == "expenses")
    assert report.score == round((3 * sales_score + 2 * expense_score) / 5)


@pytest.mark.parametrize(
    ("score", "band"),
    [(100, "good"), (80, "good"), (79, "fair"), (60, "fair"), (59, "poor"), (0, "poor")],
)
def test_bands(score, band):
    from app.services.data_quality import _band

    assert _band(score) == band


def test_optional_datasets_join_the_score_only_when_they_have_records():
    base = assess(good(), TODAY)
    perfect = good(customers=CustomerFacts(count=10), products=ProductFacts(10, 10, 10, 0))
    assert assess(perfect, TODAY).score == base.score == 100
    flawed = good(customers=CustomerFacts(count=10, duplicates=10))
    report = assess(flawed, TODAY)
    assert report.score == round((3 * 100 + 2 * 100 + 1 * 0) / 6) == 83


# --- customers, products, stock ------------------------------------------------


def test_duplicate_customers():
    report = assess(good(customers=CustomerFacts(count=20, duplicates=3)), TODAY)
    found = issue(report, "duplicate_customers")
    assert found.severity == "warning" and found.affected_count == 3
    assert found.message.startswith("3 customer records look like a repeat")
    assert component(report, "customers", "duplicates").score == 85
    one = assess(good(customers=CustomerFacts(count=20, duplicates=1)), TODAY)
    assert issue(one, "duplicate_customers").message.startswith(
        "1 customer record looks like a repeat"
    )


def test_products_without_costs_prices_and_with_repeated_names():
    report = assess(
        good(products=ProductFacts(count=10, with_cost=2, with_price=8, duplicate_names=1)), TODAY
    )
    assert issue(report, "products_without_cost").severity == "warning"  # under half known
    assert issue(report, "products_without_price").severity == "info"
    assert issue(report, "duplicate_products").affected_count == 1
    assert component(report, "products", "unit_cost").score == 20
    assert component(report, "products", "unit_price").score == 80
    assert component(report, "products", "duplicates").score == 90
    [products] = [d for d in report.datasets if d.dataset == "products"]
    assert products.score == round(20 * 0.5 + 80 * 0.3 + 90 * 0.2) == 52


def test_products_with_most_costs_known_is_only_a_hint():
    report = assess(
        good(products=ProductFacts(count=10, with_cost=7, with_price=10, duplicate_names=0)), TODAY
    )
    assert issue(report, "products_without_cost").severity == "info"


def test_negative_stock_names_the_products():
    stock = StockFacts(
        products_with_movements=4,
        negative=[(uuid.uuid4(), "Sourdough", D("-3")), (uuid.uuid4(), "Bun", D("-1"))],
    )
    report = assess(good(stock=stock), TODAY)
    found = issue(report, "negative_stock")
    assert found.severity == "critical" and found.affected_count == 2  # half of the products
    assert "for example Sourdough, Bun" in found.message
    assert [p["name"] for p in found.details["products"]] == ["Sourdough", "Bun"]
    assert found.details["products"][0]["quantity"] == "-3"
    assert component(report, "stock_movements", "negative_stock").score == 50


def test_a_little_negative_stock_is_a_warning():
    stock = StockFacts(products_with_movements=10, negative=[(uuid.uuid4(), "Bun", D("-1"))])
    found = issue(assess(good(stock=stock), TODAY), "negative_stock")
    assert found.severity == "warning" and found.message.startswith(
        "1 product shows less than zero"
    )


def test_stock_that_adds_up_raises_nothing():
    report = assess(good(stock=StockFacts(products_with_movements=5)), TODAY)
    assert "negative_stock" not in types(report)
    assert component(report, "stock_movements", "negative_stock").score == 100


# --- imports ------------------------------------------------


def an_import(imported, invalid, name="till.csv", dataset="sales"):
    return ImportFact(
        uuid.uuid4(), name, dataset, datetime(2026, 9, 12, 10, tzinfo=UTC), imported, invalid
    )


def test_a_clean_import_raises_nothing():
    report = assess(good(imports=[an_import(100, 10)]), TODAY)  # exactly 10/110 = 9%
    assert report.imports[0].clean_rate_pct == 91 and "rows_with_problems" not in types(report)


def test_an_import_with_many_problem_rows_is_flagged():
    fact = an_import(80, 20)
    report = assess(good(imports=[fact]), TODAY)
    found = issue(report, "rows_with_problems")
    assert found.severity == "warning" and found.import_id == fact.id
    assert (
        found.message == "'till.csv' (12/09/2026): 20 of 100 rows had problems and were left out."
    )
    assert found.details == {"clean_rate_pct": 80} and found.affected_count == 20


def test_a_mostly_bad_import_is_critical():
    report = assess(good(imports=[an_import(50, 50)]), TODAY)
    assert issue(report, "rows_with_problems").severity == "critical"


def test_imports_are_listed_in_the_order_given_with_their_clean_rate():
    first, second = an_import(95, 5, "a.csv"), an_import(0, 0, "b.csv")
    rows = assess(good(imports=[first, second]), TODAY).imports
    assert [
        (r.filename, r.rows_imported, r.rows_with_problems, r.clean_rate_pct) for r in rows
    ] == [
        ("a.csv", 95, 5, 95),
        ("b.csv", 0, 0, 100),
    ]


def test_an_import_problem_belongs_to_that_imports_dataset():
    report = assess(good(imports=[an_import(10, 10, "costs.csv", "expenses")]), TODAY)
    assert issue(report, "rows_with_problems").dataset == "expenses"


# --- the month table ------------------------------------------------


def test_months_are_newest_first_with_their_own_scores():
    facts = Facts(
        sales=sales(span=("2026-07", "2026-09"), costed=0.5),
        expenses=expenses(span=("2026-07", "2026-09"), categorised=1.0),
    )
    rows = assess(facts, TODAY).months
    assert [m.label for m in rows] == ["September 2026", "August 2026", "July 2026"]
    assert (rows[0].sales_records, rows[0].expenses_records) == (10, 5)
    assert (rows[0].cost_coverage_pct, rows[0].categorised_pct) == (50, 100)
    assert rows[0].score == 75  # the average of 50 and 100


def test_a_month_with_no_sales_scores_zero_for_sales():
    facts = Facts(sales=sales(skip=("2026-08",)), expenses=expenses())
    row = next(m for m in assess(facts, TODAY).months if m.label == "August 2026")
    assert (row.sales_records, row.cost_coverage_pct, row.score) == (0, None, 50)


def test_recent_silence_shows_as_blank_months():
    facts = Facts(
        sales=sales(span=("2026-01", "2026-06")), expenses=expenses(span=("2026-01", "2026-06"))
    )
    rows = assess(facts, TODAY).months
    assert [m.label for m in rows][:3] == ["September 2026", "August 2026", "July 2026"]
    assert [m.score for m in rows][:3] == [0, 0, 0]


def test_the_month_in_progress_appears_only_when_it_has_records():
    quiet = assess(Facts(sales=sales(), expenses=expenses()), TODAY).months
    assert quiet[0].label == "September 2026"
    busy = sales(span=("2026-01", "2026-10"))
    now = assess(Facts(sales=busy, expenses=expenses()), TODAY).months
    assert now[0].label == "October 2026" and now[0].sales_records == 10


def test_at_most_two_years_of_months_are_shown():
    facts = Facts(
        sales=sales(span=("2023-01", "2026-09")), expenses=expenses(span=("2023-01", "2026-09"))
    )
    rows = assess(facts, TODAY).months
    assert (
        len(rows) == 24 and rows[0].label == "September 2026" and rows[-1].label == "October 2024"
    )


def test_month_scores_are_only_about_the_datasets_that_exist():
    row = assess(Facts(sales=sales(costed=0.5)), TODAY).months[0]
    assert row.score == 50 and row.categorised_pct is None


# --- origins, issues order and the headline ------------------------------------------------


def test_origins_pass_through():
    facts = good()
    facts.origins = [("csv", "sales", 40), ("manual", "sales", 50)]
    rows = assess(facts, TODAY).origins
    assert [(o.source, o.dataset, o.records) for o in rows] == [
        ("csv", "sales", 40),
        ("manual", "sales", 50),
    ]


def test_the_most_serious_problems_come_first():
    facts = good(products=ProductFacts(10, 9, 9, 1))
    facts.sales = sales(costed=0.4, skip=("2026-03",), with_lines=0.4)  # critical + warning + info
    facts.expenses = expenses(categorised=0.9)  # info
    report = assess(facts, TODAY)
    ranks = [{"critical": 0, "warning": 1, "info": 2}[i.severity] for i in report.issues]
    assert ranks == sorted(ranks) and ranks[0] == 0 and ranks[-1] == 2


def test_the_headline_points_at_the_first_real_problem_not_an_info_note():
    report = assess(Facts(sales=sales(costed=0.4), expenses=expenses()), TODAY)
    assert report.headline.startswith("Your data quality is ")
    assert "Start with: 60% of your sales value has no cost of goods" in report.headline
    only_info = assess(Facts(sales=sales(costed=0.9), expenses=expenses()), TODAY)
    assert "Start with" not in only_info.headline


def test_every_issue_says_what_to_do():
    facts = good(customers=CustomerFacts(10, 2), products=ProductFacts(10, 0, 0, 2))
    facts.sales = sales(
        costed=0.3, skip=("2026-02",), with_lines=0.1, linked=0.0, last=date(2026, 8, 1)
    )
    facts.expenses = expenses(categorised=0.3)
    report = assess(facts, TODAY)
    assert len(report.issues) >= 8 and all(i.fix and i.message for i in report.issues)
    assert {i.severity for i in report.issues} == {"critical", "warning", "info"}


# --- exact boundaries ------------------------------------------------


def test_customer_links_start_being_mentioned_at_exactly_20_sales():
    def linked_report(per_month):
        facts = Facts(sales=sales(span=("2026-09", "2026-09"), per_month=per_month, linked=0.0))
        return assess(facts, TODAY)

    assert "no_customer_link" in types(linked_report(20))
    assert "no_customer_link" not in types(linked_report(19))


@pytest.mark.parametrize(
    ("imported", "invalid", "severity"),
    [(90, 10, None), (89, 11, "warning"), (70, 30, "warning"), (69, 31, "critical")],
)
def test_import_problem_thresholds_are_exactly_10_and_30_percent(imported, invalid, severity):
    report = assess(good(imports=[an_import(imported, invalid)]), TODAY)
    found = [i for i in report.issues if i.issue_type == "rows_with_problems"]
    assert (found[0].severity if found else None) == severity


@pytest.mark.parametrize(
    ("negative", "total", "severity"),
    [(1, 5, "warning"), (1, 4, "warning"), (2, 7, "critical"), (2, 8, "warning")],
)
def test_negative_stock_is_critical_only_above_a_quarter_of_products(negative, total, severity):
    stock = StockFacts(
        products_with_movements=total,
        negative=[(uuid.uuid4(), f"P{n}", D("-1")) for n in range(negative)],
    )
    found = issue(assess(good(stock=stock), TODAY), "negative_stock")
    assert found.severity == severity


def test_equally_serious_problems_list_the_bigger_dataset_first():
    facts = good(customers=CustomerFacts(count=20, duplicates=2))  # a customers warning
    facts.sales = sales(with_lines=0.1)  # a sales warning, alphabetically later
    report = assess(facts, TODAY)
    warnings = [i.dataset for i in report.issues if i.severity == "warning"]
    assert warnings == ["sales", "customers"]


def test_a_dataset_with_no_issue_still_scores_from_its_checks():
    from app.services.data_quality import _freshness

    assert [_freshness(d) for d in (7, 8)] == [1.0, 0.75]
