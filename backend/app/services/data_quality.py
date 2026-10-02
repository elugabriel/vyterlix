"""How complete and trustworthy a business's data is (Phase 4 step 8).

The score is built to be explained. Every dataset (sales, expenses, ...) is judged by a few
named checks; each check has a weight and says, in plain English with the real figures, why it
scored what it did. Nothing is hidden in a model: the same data always gives the same score
(SLA clause 3.4: every number traceable to source data and calculation logic).

Two layers, so the scoring rules can be tested without a database:
- `gather_facts` reads the numbers from the business's data (aggregates only);
- `assess` turns facts into the score, the issues and the per-month table.

The session must be scoped to the organisation.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, extract, func, select, text
from sqlalchemy.orm import Session

from app.core.uk import today_uk
from app.models.data import Customer, Expense, Product, Sale, SaleLine, StockMovement, Supplier
from app.models.imports import DataImport, DataQualityIssue
from app.schemas.data_quality import (
    ComponentOut,
    DataQualityOut,
    DatasetScoreOut,
    ImportQualityOut,
    IssueOut,
    MonthOut,
    OriginOut,
    RefreshOut,
)
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta

LABELS = {
    "sales": "Sales",
    "expenses": "Expenses",
    "customers": "Customers",
    "products": "Products",
    "stock_movements": "Stock",
}
# Share of the overall score. Sales and expenses are needed to say anything about profit, so
# an absent expenses record counts as zero rather than being ignored.
WEIGHTS = {"sales": 3, "expenses": 2, "customers": 1, "products": 1, "stock_movements": 1}
REQUIRED = ("sales", "expenses")
SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}
MONTHS_SHOWN = 24
RECENT_IMPORTS = 10
ZERO = Decimal(0)


# --- facts ----------------------------------------------------------------------------------------


@dataclass
class SalesFacts:
    count: int = 0
    first: date | None = None
    last: date | None = None
    value: Decimal = ZERO  # total of |net| across all sales
    costed_value: Decimal = ZERO  # of which on lines that record a cost
    with_lines: int = 0
    with_costed_lines: int = 0
    with_customer: int = 0
    month_count: dict[date, int] = field(default_factory=dict)
    month_value: dict[date, Decimal] = field(default_factory=dict)
    month_costed: dict[date, Decimal] = field(default_factory=dict)


@dataclass
class ExpenseFacts:
    count: int = 0
    first: date | None = None
    last: date | None = None
    value: Decimal = ZERO  # total of |gross|
    categorised_value: Decimal = ZERO
    uncategorised: int = 0
    month_count: dict[date, int] = field(default_factory=dict)
    month_value: dict[date, Decimal] = field(default_factory=dict)
    month_categorised: dict[date, Decimal] = field(default_factory=dict)


@dataclass
class CustomerFacts:
    count: int = 0
    duplicates: int = 0  # extra records for the same person (same email, or name + postcode)


@dataclass
class ProductFacts:
    count: int = 0
    with_cost: int = 0
    with_price: int = 0
    duplicate_names: int = 0


@dataclass
class StockFacts:
    products_with_movements: int = 0
    negative: list[tuple[uuid.UUID, str, Decimal]] = field(default_factory=list)


@dataclass
class ImportFact:
    id: uuid.UUID
    filename: str
    dataset: str
    imported_at: datetime | None
    imported: int
    invalid: int


@dataclass
class Facts:
    sales: SalesFacts = field(default_factory=SalesFacts)
    expenses: ExpenseFacts = field(default_factory=ExpenseFacts)
    customers: CustomerFacts = field(default_factory=CustomerFacts)
    products: ProductFacts = field(default_factory=ProductFacts)
    stock: StockFacts = field(default_factory=StockFacts)
    imports: list[ImportFact] = field(default_factory=list)
    origins: list[tuple[str, str, int]] = field(default_factory=list)  # (source, dataset, n)


def _by_month(db: Session, date_col, *columns, where=None) -> dict[date, tuple]:
    year, month = extract("year", date_col), extract("month", date_col)
    query = select(year, month, *columns).group_by(year, month)
    if where is not None:
        query = query.where(where)
    return {date(int(y), int(m), 1): tuple(rest) for y, m, *rest in db.execute(query)}


def _sales_facts(db: Session) -> SalesFacts:
    total, first, last, value, with_customer = db.execute(
        select(
            func.count(),
            func.min(Sale.sold_on),
            func.max(Sale.sold_on),
            func.coalesce(func.sum(func.abs(Sale.net_amount)), 0),
            func.count(Sale.customer_id),
        )
    ).one()
    facts = SalesFacts(
        count=total, first=first, last=last, value=Decimal(value), with_customer=with_customer
    )
    if not total:
        return facts
    months = _by_month(
        db, Sale.sold_on, func.count(), func.coalesce(func.sum(func.abs(Sale.net_amount)), 0)
    )
    facts.month_count = {m: v[0] for m, v in months.items()}
    facts.month_value = {m: Decimal(v[1]) for m, v in months.items()}
    facts.with_lines = db.scalar(select(func.count(func.distinct(SaleLine.sale_id)))) or 0
    facts.with_costed_lines = (
        db.scalar(
            select(func.count(func.distinct(SaleLine.sale_id))).where(
                SaleLine.cost_amount.is_not(None)
            )
        )
        or 0
    )
    if facts.with_costed_lines:
        # A join: right after a bulk load Postgres can plan it as a nested loop over the two
        # big tables (minutes). A hash join is always right for this aggregate.
        db.execute(text("SET LOCAL enable_nestloop = off"))
        year, month = extract("year", Sale.sold_on), extract("month", Sale.sold_on)
        costed = db.execute(
            select(year, month, func.coalesce(func.sum(func.abs(SaleLine.net_amount)), 0))
            .select_from(SaleLine)
            .join(
                Sale,
                (Sale.organization_id == SaleLine.organization_id) & (Sale.id == SaleLine.sale_id),
            )
            .where(SaleLine.cost_amount.is_not(None))
            .group_by(year, month)
        ).all()
        db.execute(text("RESET enable_nestloop"))
        facts.month_costed = {date(int(y), int(m), 1): Decimal(v) for y, m, v in costed}
        facts.costed_value = sum(facts.month_costed.values(), ZERO)
    return facts


def _expense_facts(db: Session) -> ExpenseFacts:
    categorised = case(
        (Expense.cost_category_id.is_not(None), func.abs(Expense.gross_amount)), else_=0
    )
    total, first, last, value, cat_value, uncategorised = db.execute(
        select(
            func.count(),
            func.min(Expense.spent_on),
            func.max(Expense.spent_on),
            func.coalesce(func.sum(func.abs(Expense.gross_amount)), 0),
            func.coalesce(func.sum(categorised), 0),
            func.count() - func.count(Expense.cost_category_id),
        )
    ).one()
    facts = ExpenseFacts(
        count=total,
        first=first,
        last=last,
        value=Decimal(value),
        categorised_value=Decimal(cat_value),
        uncategorised=uncategorised,
    )
    if total:
        months = _by_month(
            db,
            Expense.spent_on,
            func.count(),
            func.coalesce(func.sum(func.abs(Expense.gross_amount)), 0),
            func.coalesce(func.sum(categorised), 0),
        )
        facts.month_count = {m: v[0] for m, v in months.items()}
        facts.month_value = {m: Decimal(v[1]) for m, v in months.items()}
        facts.month_categorised = {m: Decimal(v[2]) for m, v in months.items()}
    return facts


def _customer_facts(db: Session) -> CustomerFacts:
    total = db.scalar(select(func.count()).select_from(Customer)) or 0
    if not total:
        return CustomerFacts()
    by_email = db.execute(
        select(func.count(Customer.email), func.count(func.distinct(func.lower(Customer.email))))
    ).one()
    person = func.lower(Customer.name) + "|" + func.upper(Customer.postcode)
    named = Customer.name.is_not(None) & Customer.postcode.is_not(None)
    by_name = db.execute(
        select(func.count(person), func.count(func.distinct(person))).where(named)
    ).one()
    return CustomerFacts(
        count=total, duplicates=(by_email[0] - by_email[1]) + (by_name[0] - by_name[1])
    )


def _product_facts(db: Session) -> ProductFacts:
    count, with_cost, with_price, distinct_names = db.execute(
        select(
            func.count(),
            func.count(Product.unit_cost),
            func.count(Product.unit_price_ex_vat),
            func.count(func.distinct(func.lower(Product.name))),
        )
    ).one()
    return ProductFacts(count, with_cost, with_price, count - distinct_names)


def _stock_facts(db: Session) -> StockFacts:
    rows = db.execute(
        select(StockMovement.product_id, func.sum(StockMovement.quantity)).group_by(
            StockMovement.product_id
        )
    ).all()
    negative = [(pid, Decimal(qty)) for pid, qty in rows if qty < 0]
    names = {}
    if negative:
        names = dict(
            db.execute(
                select(Product.id, Product.name).where(Product.id.in_([p for p, _ in negative]))
            ).all()
        )
    negative.sort(key=lambda row: (row[1], str(row[0])))
    return StockFacts(
        products_with_movements=len(rows),
        negative=[(pid, names.get(pid, "Unknown product"), qty) for pid, qty in negative],
    )


def gather_facts(db: Session) -> Facts:
    facts = Facts(
        sales=_sales_facts(db),
        expenses=_expense_facts(db),
        customers=_customer_facts(db),
        products=_product_facts(db),
        stock=_stock_facts(db),
    )
    imports = db.scalars(
        select(DataImport)
        .where(DataImport.status == "imported")
        .order_by(DataImport.imported_at.desc(), DataImport.id)
        .limit(RECENT_IMPORTS)
    ).all()
    facts.imports = [
        ImportFact(
            i.id, i.original_filename, i.dataset, i.imported_at, i.imported_count, i.invalid_count
        )
        for i in imports
    ]
    for model, dataset in (
        (Sale, "sales"),
        (Expense, "expenses"),
        (Customer, "customers"),
        (Supplier, "suppliers"),
        (Product, "products"),
        (StockMovement, "stock_movements"),
    ):
        for source, count in db.execute(select(model.source, func.count()).group_by(model.source)):
            facts.origins.append((source, dataset, count))
    facts.origins.sort(key=lambda row: (row[1], row[0]))
    return facts


# --- helpers --------------------------------------------------------------------------------------


def _first_of_month(day: date) -> date:
    return day.replace(day=1)


def _next_month(day: date) -> date:
    return date(day.year + (day.month == 12), day.month % 12 + 1, 1)


def _months(start: date, end: date) -> list[date]:
    out, current = [], _first_of_month(start)
    while current <= end:
        out.append(current)
        current = _next_month(current)
    return out


def _month_label(day: date) -> str:
    return f"{day:%B %Y}"


def _uk(day: date) -> str:
    return f"{day:%d/%m/%Y}"


def _pct(part: Decimal | int, whole: Decimal | int) -> int:
    return round(100 * float(part) / float(whole)) if whole else 100


def _band(score: int | None) -> str | None:
    if score is None:
        return None
    return "good" if score >= 80 else "fair" if score >= 60 else "poor"


def _gaps(missing: list[date]) -> list[tuple[date, date, int]]:
    """Runs of consecutive missing months: (first month, last month, how many)."""
    runs: list[list[date]] = []
    for month in missing:
        if runs and _next_month(runs[-1][-1]) == month:
            runs[-1].append(month)
        else:
            runs.append([month])
    return [(run[0], run[-1], len(run)) for run in runs]


def _freshness(days: int) -> float:
    return (
        1.0
        if days <= 7
        else 0.75
        if days <= 14
        else 0.5
        if days <= 30
        else 0.25
        if days <= 60
        else 0.0
    )


def _component(key: str, label: str, share: float, weight: float, detail: str) -> ComponentOut:
    return ComponentOut(
        key=key,
        label=label,
        score=round(100 * max(0.0, min(1.0, share))),
        weight=weight,
        detail=detail,
    )


def _issue(
    dataset,
    issue_type,
    severity,
    message,
    fix,
    *,
    affected=0,
    start=None,
    end=None,
    details=None,
    import_id=None,
) -> IssueOut:
    return IssueOut(
        dataset=dataset,
        issue_type=issue_type,
        severity=severity,
        period_start=start,
        period_end=end,
        affected_count=affected,
        message=message,
        fix=fix,
        details=details or {},
        import_id=import_id,
    )


# --- scoring --------------------------------------------------------------------------------------


def assess(facts: Facts, as_of: date) -> DataQualityOut:
    s, e = facts.sales, facts.expenses
    issues: list[IssueOut] = []
    datasets: list[DatasetScoreOut] = []

    # The months we expect data for: from the first record of either dataset to the last month
    # that has finished. A gap inside that span is a missing period; silence at the end is
    # stale data. The current month is still in progress, so it is never "missing".
    starts = [d for d in (s.first, e.first) if d]
    ends = [d for d in (s.last, e.last) if d]
    expected: list[date] = []
    if starts:
        stop = min(_first_of_month(max(ends)), _prev_month(as_of))
        if stop >= _first_of_month(min(starts)):
            expected = _months(min(starts), stop)

    def coverage(name: str, counts: dict[date, int], noun: str, weight: float):
        missing = [m for m in expected if counts.get(m, 0) == 0]
        total = len(expected)
        share = 1.0 if not total else (total - len(missing)) / total
        detail = (
            f"{noun} recorded in {total - len(missing)} of {total} months"
            if total
            else "Not enough history yet to look for gaps"
        )
        for first, last, n in _gaps(missing):
            span = (
                _month_label(first) if n == 1 else f"{_month_label(first)} to {_month_label(last)}"
            )
            issues.append(
                _issue(
                    name,
                    "missing_period",
                    "critical" if n >= 3 else "warning",
                    f"No {noun.lower()} are recorded for {span}.",
                    "Upload the missing month or type it in."
                    if n == 1
                    else "Upload the missing months or type them in.",
                    affected=n,
                    start=first,
                    end=_next_month(last) - timedelta(days=1),
                )
            )
        return _component("coverage", "No missing months", share, weight, detail)

    def freshness(name: str, last: date, noun: str, weight: float):
        days = max(0, (as_of - last).days)
        share = _freshness(days)
        ago = "today" if days == 0 else f"{days} day{'s' if days != 1 else ''} ago"
        if days > 14:
            issues.append(
                _issue(
                    name,
                    "stale_data",
                    "critical" if days > 30 else "warning",
                    f"The latest {noun} is dated {_uk(last)}, {ago}.",
                    "Add the more recent records so figures and alerts reflect today.",
                    affected=days,
                    start=last,
                    end=as_of,
                )
            )
        return _component(
            "freshness", "Up to date", share, weight, f"Latest {noun} is dated {_uk(last)} ({ago})"
        )

    # --- sales
    if s.count:
        cov = coverage("sales", s.month_count, "Sales", 0.30)
        fresh = freshness("sales", s.last, "sale", 0.20)
        cost_share = float(s.costed_value / s.value) if s.value else 1.0
        cost_pct = _pct(s.costed_value, s.value)
        cost = _component(
            "cost_of_goods",
            "Cost of goods known",
            cost_share,
            0.30,
            f"{cost_pct}% of sales value has a cost of goods recorded",
        )
        if cost_share < 1.0:
            issues.append(
                _issue(
                    "sales",
                    "missing_cost",
                    "critical" if cost_share < 0.5 else "warning" if cost_share < 0.8 else "info",
                    f"{100 - cost_pct}% of your sales value has no cost of goods, so profit "
                    "margins can't be worked out for it.",
                    "Add the cost to each sale's lines, or give your products a cost price.",
                    affected=s.count - s.with_costed_lines,
                    details={"cost_known_pct": cost_pct},
                )
            )
        lines_share = s.with_lines / s.count
        lines = _component(
            "line_detail",
            "Says what was sold",
            lines_share,
            0.20,
            f"{s.with_lines} of {s.count} sales say what was sold",
        )
        if lines_share < 0.5:
            issues.append(
                _issue(
                    "sales",
                    "no_line_detail",
                    "warning" if lines_share < 0.2 else "info",
                    f"{s.count - s.with_lines} of your {s.count} sales don't say what was sold, "
                    "so best-sellers and product margins can't be found.",
                    "Include the product on each sale when you upload or type it in.",
                    affected=s.count - s.with_lines,
                )
            )
        if s.count >= 20 and s.with_customer / s.count < 0.3:
            issues.append(
                _issue(
                    "sales",
                    "no_customer_link",
                    "info",
                    f"Only {s.with_customer} of your {s.count} sales are linked to a customer, so "
                    "repeat-customer and loyalty figures will be thin.",
                    "Include a customer name or email on sales where you have one.",
                    affected=s.count - s.with_customer,
                )
            )
        datasets.append(_dataset("sales", s.count, [cov, fresh, cost, lines]))
    else:
        issues.append(
            _issue(
                "sales",
                "no_data",
                "critical",
                "No sales have been recorded yet.",
                "Upload a sales file or type in your sales.",
            )
        )

    # --- expenses
    if e.count:
        cov = coverage("expenses", e.month_count, "Expenses", 0.35)
        fresh = freshness("expenses", e.last, "expense", 0.20)
        cat_share = float(e.categorised_value / e.value) if e.value else 1.0
        cat_pct = _pct(e.categorised_value, e.value)
        cat = _component(
            "categorised",
            "Costs categorised",
            cat_share,
            0.45,
            f"{cat_pct}% of expense value has a cost category",
        )
        if cat_share < 1.0:
            issues.append(
                _issue(
                    "expenses",
                    "uncategorised_expenses",
                    "warning" if cat_share < 0.8 else "info",
                    f"{e.uncategorised} expense{'s' if e.uncategorised != 1 else ''} "
                    f"({100 - cat_pct}% of the value) have no cost category, so spending can't "
                    "be broken down.",
                    "Give each expense a cost category.",
                    affected=e.uncategorised,
                    details={"categorised_pct": cat_pct},
                )
            )
        datasets.append(_dataset("expenses", e.count, [cov, fresh, cat]))
    elif s.count:
        issues.append(
            _issue(
                "expenses",
                "no_data",
                "warning",
                "No expenses have been recorded, so profit can't be worked out.",
                "Upload your expenses or type them in.",
            )
        )

    # --- customers, products, stock
    c = facts.customers
    if c.count:
        share = 1 - c.duplicates / c.count
        datasets.append(
            _dataset(
                "customers",
                c.count,
                [
                    _component(
                        "duplicates",
                        "No duplicate customers",
                        share,
                        1.0,
                        f"{c.duplicates} possible duplicate{'s' if c.duplicates != 1 else ''} "
                        f"among {c.count} customers",
                    )
                ],
            )
        )
        if c.duplicates:
            issues.append(
                _issue(
                    "customers",
                    "duplicate_customers",
                    "warning",
                    f"{c.duplicates} customer record{'s look' if c.duplicates != 1 else ' looks'} "
                    "like a repeat of someone already listed (same email, or same name and "
                    "postcode).",
                    "Merge or delete the repeats so each customer is counted once.",
                    affected=c.duplicates,
                )
            )
    p = facts.products
    if p.count:
        cost_share, price_share = p.with_cost / p.count, p.with_price / p.count
        dup_share = 1 - p.duplicate_names / p.count
        datasets.append(
            _dataset(
                "products",
                p.count,
                [
                    _component(
                        "unit_cost",
                        "Cost price known",
                        cost_share,
                        0.5,
                        f"{p.with_cost} of {p.count} products have a cost price",
                    ),
                    _component(
                        "unit_price",
                        "Selling price known",
                        price_share,
                        0.3,
                        f"{p.with_price} of {p.count} products have a selling price",
                    ),
                    _component(
                        "duplicates",
                        "No duplicate names",
                        dup_share,
                        0.2,
                        f"{p.duplicate_names} repeated product name"
                        f"{'s' if p.duplicate_names != 1 else ''}",
                    ),
                ],
            )
        )
        if p.with_cost < p.count:
            issues.append(
                _issue(
                    "products",
                    "products_without_cost",
                    "warning" if cost_share < 0.5 else "info",
                    f"{p.count - p.with_cost} of {p.count} products have no cost price, so their "
                    "margins can't be worked out.",
                    "Add a cost price to each product.",
                    affected=p.count - p.with_cost,
                )
            )
        if p.with_price < p.count:
            issues.append(
                _issue(
                    "products",
                    "products_without_price",
                    "info",
                    f"{p.count - p.with_price} of {p.count} products have no selling price.",
                    "Add a selling price to each product.",
                    affected=p.count - p.with_price,
                )
            )
        if p.duplicate_names:
            issues.append(
                _issue(
                    "products",
                    "duplicate_products",
                    "warning",
                    f"{p.duplicate_names} product "
                    f"name{'s are' if p.duplicate_names != 1 else ' is'} "
                    "listed more than once.",
                    "Merge the repeats so stock and sales aren't split across them.",
                    affected=p.duplicate_names,
                )
            )
    st = facts.stock
    if st.products_with_movements:
        neg = len(st.negative)
        datasets.append(
            _dataset(
                "stock_movements",
                st.products_with_movements,
                [
                    _component(
                        "negative_stock",
                        "Stock never below zero",
                        1 - neg / st.products_with_movements,
                        1.0,
                        f"{neg} of {st.products_with_movements} products show less than zero "
                        "in stock",
                    )
                ],
            )
        )
        if neg:
            names = ", ".join(name for _, name, _ in st.negative[:3])
            issues.append(
                _issue(
                    "stock_movements",
                    "negative_stock",
                    "critical" if neg / st.products_with_movements > 0.25 else "warning",
                    f"{neg} product{'s show' if neg != 1 else ' shows'} less than zero in stock "
                    f"(for example {names}). A delivery or opening balance is probably missing.",
                    "Record the missing delivery or an opening stock figure.",
                    affected=neg,
                    details={
                        "products": [
                            {"id": str(pid), "name": name, "quantity": str(qty)}
                            for pid, name, qty in st.negative[:10]
                        ]
                    },
                )
            )

    # --- imports
    import_rows: list[ImportQualityOut] = []
    for imp in facts.imports:
        total = imp.imported + imp.invalid
        clean = _pct(imp.imported, total)
        import_rows.append(
            ImportQualityOut(
                import_id=imp.id,
                filename=imp.filename,
                dataset=imp.dataset,
                imported_at=imp.imported_at,
                rows_imported=imp.imported,
                rows_with_problems=imp.invalid,
                clean_rate_pct=clean,
            )
        )
        if total and imp.invalid / total > 0.10:
            when = f" ({_uk(imp.imported_at.date())})" if imp.imported_at else ""
            issues.append(
                _issue(
                    imp.dataset,
                    "rows_with_problems",
                    "critical" if imp.invalid / total > 0.30 else "warning",
                    f"'{imp.filename}'{when}: {imp.invalid} of {total} rows had problems and were "
                    "left out.",
                    "Download the problem rows from that import, fix them and upload them again.",
                    affected=imp.invalid,
                    import_id=imp.id,
                    details={"clean_rate_pct": clean},
                )
            )

    # --- overall
    scored = {d.dataset: d for d in datasets}
    missing = [LABELS[name] for name in REQUIRED if name not in scored]
    overall: int | None = None
    if "sales" in scored:
        numerator = sum(WEIGHTS[n] * d.score for n, d in scored.items())
        denominator = sum(WEIGHTS[n] for n in scored) + sum(
            WEIGHTS[n] for n in REQUIRED if n not in scored
        )
        overall = round(numerator / denominator)
    issues.sort(
        key=lambda i: (
            SEVERITY_RANK[i.severity],
            -WEIGHTS.get(i.dataset, 0),
            i.period_start or date.min,
            i.issue_type,
        )
    )
    return DataQualityOut(
        as_of=as_of,
        score=overall,
        band=_band(overall),
        headline=_headline(overall, missing, issues),
        datasets=datasets,
        missing_datasets=missing,
        months=_months_table(facts, as_of),
        imports=import_rows,
        origins=[OriginOut(source=a, dataset=b, records=n) for a, b, n in facts.origins],
        issues=issues,
    )


def _prev_month(day: date) -> date:
    first = _first_of_month(day)
    return date(first.year - (first.month == 1), (first.month - 2) % 12 + 1, 1)


def _dataset(name: str, records: int, components: list[ComponentOut]) -> DatasetScoreOut:
    total_weight = sum(c.weight for c in components)
    score = round(sum(c.score * c.weight for c in components) / total_weight)
    return DatasetScoreOut(
        dataset=name,
        label=LABELS[name],
        records=records,
        score=score,
        band=_band(score),
        weight=WEIGHTS[name],
        components=components,
    )


def _headline(score: int | None, missing: list[str], issues: list[IssueOut]) -> str:
    if score is None:
        return "Add some sales to get a data-quality score."
    word = {"good": "good", "fair": "fair", "poor": "poor"}[_band(score)]
    text_ = f"Your data quality is {word}: {score} out of 100."
    if missing:
        text_ += f" No {' or '.join(m.lower() for m in missing)} recorded yet."
    serious = [i for i in issues if i.severity != "info"]
    if serious:
        text_ += f" Start with: {serious[0].message}"
    return text_


def _months_table(facts: Facts, as_of: date) -> list[MonthOut]:
    s, e = facts.sales, facts.expenses
    seen = set(s.month_count) | set(e.month_count)
    if not seen:
        return []
    # From the first month with data to the last finished month (so recent silence shows as
    # blank rows), plus the month in progress if it already has records.
    months = _months(min(seen), max(max(seen), _prev_month(as_of)))
    out = []
    for month in months:
        n_sales, n_exp = s.month_count.get(month, 0), e.month_count.get(month, 0)
        sales_value, exp_value = s.month_value.get(month, ZERO), e.month_value.get(month, ZERO)
        cost_pct = _pct(s.month_costed.get(month, ZERO), sales_value) if sales_value else None
        cat_pct = _pct(e.month_categorised.get(month, ZERO), exp_value) if exp_value else None
        parts = []
        if s.count:  # a month with no sales scores 0 for sales; otherwise how well costed it is
            parts.append(0 if not n_sales else (cost_pct if cost_pct is not None else 100))
        if e.count:
            parts.append(0 if not n_exp else (cat_pct if cat_pct is not None else 100))
        out.append(
            MonthOut(
                month=month,
                label=_month_label(month),
                sales_records=n_sales,
                expenses_records=n_exp,
                cost_coverage_pct=cost_pct,
                categorised_pct=cat_pct,
                score=round(sum(parts) / len(parts)) if parts else None,
            )
        )
    out.sort(key=lambda m: m.month, reverse=True)
    return out[:MONTHS_SHOWN]


# --- the public calls -----------------------------------------------------------------------------


def build_report(db: Session, as_of: date | None = None) -> DataQualityOut:
    return assess(gather_facts(db), as_of or today_uk())


def refresh_report(db: Session, tenant, meta: RequestMeta, as_of: date | None = None) -> RefreshOut:
    """Work the report out and bring the stored issues into line with it: new problems are
    added, ones that have been fixed are marked resolved, ones that continue are updated."""
    report = build_report(db, as_of)
    now = datetime.now(UTC)

    def key(dataset, issue_type, start, import_id):
        # What makes it "the same problem" from one refresh to the next. A gap is identified by
        # which month it starts; a stale-data warning, whose dates move every day, is not.
        return (dataset, issue_type, start if issue_type == "missing_period" else None, import_id)

    open_rows = {
        key(r.dataset, r.issue_type, r.period_start, r.import_id): r
        for r in db.scalars(select(DataQualityIssue).where(DataQualityIssue.resolved_at.is_(None)))
    }
    seen, new = set(), 0
    for issue in report.issues:
        k = key(issue.dataset, issue.issue_type, issue.period_start, issue.import_id)
        seen.add(k)
        row = open_rows.get(k)
        details = issue.details | {"fix": issue.fix}
        if row is None:
            db.add(
                DataQualityIssue(
                    import_id=issue.import_id,
                    dataset=issue.dataset,
                    issue_type=issue.issue_type,
                    severity=issue.severity,
                    period_start=issue.period_start,
                    period_end=issue.period_end,
                    affected_count=issue.affected_count,
                    message=issue.message[:500],
                    details=details,
                )
            )
            new += 1
        else:
            row.severity, row.affected_count = issue.severity, issue.affected_count
            row.period_start, row.period_end = issue.period_start, issue.period_end
            row.message, row.details = issue.message[:500], details
    resolved = 0
    for k, row in open_rows.items():
        if k not in seen:
            row.resolved_at = now
            resolved += 1
    db.flush()
    record_audit(
        db,
        AuditAction.DATA_QUALITY_REFRESHED,
        actor_user_id=tenant.user.id,
        organization_id=tenant.organization_id,
        target_type="data_quality",
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        details={"score": report.score, "new_issues": new, "resolved_issues": resolved},
    )
    db.commit()
    return RefreshOut(**report.model_dump(), new_issues=new, resolved_issues=resolved)


def list_stored_issues(
    db: Session, *, status: str, severity: str | None, limit: int, offset: int
) -> list[DataQualityIssue]:
    query = select(DataQualityIssue)
    if status == "open":
        query = query.where(DataQualityIssue.resolved_at.is_(None))
    elif status == "resolved":
        query = query.where(DataQualityIssue.resolved_at.is_not(None))
    if severity:
        query = query.where(DataQualityIssue.severity == severity)
    query = query.order_by(DataQualityIssue.created_at.desc(), DataQualityIssue.id)
    return list(db.scalars(query.limit(limit).offset(offset)))
