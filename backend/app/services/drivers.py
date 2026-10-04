"""Driver identification: what actually caused a change?

Builds on segment analysis (which part moved) with causes that cut across the parts:

- days: a month with more days sells more, whatever else happens;
- orders: more sales, or a bigger average sale;
- price and volume: more items sold, or higher prices actually charged;
- parts: one product, channel, customer, day or cost that accounts for most of the change.

Each lens splits the same total change into two effects that add up to it exactly (the
arithmetic is in app/diagnostics/drivers.py). Everything is read straight from the records when
asked, with the same conventions as the KPI measures, so the totals match the Key figures page.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.diagnostics import drivers as rules
from app.health.scoring import format_value
from app.kpi import periods
from app.kpi.measures import _hash_joins
from app.models.data import Sale, SaleLine
from app.schemas.diagnostics import DriversOut, FindingOut, LensOut
from app.services.segments import AGAINST, METRICS, _pounds_or_count, segment_change

ZERO = Decimal("0")
TOP_FINDINGS = 6
# Figures whose total depends on how many days there were.
PER_DAY = {"revenue", "sales_count", "units_sold", "gross_profit"}

TITLES = {
    "calendar_days": "Different number of days",
    "daily_rate": "Busier or slower days",
    "sales_count": "Number of sales",
    "sale_value": "Size of the average sale",
    "volume": "Items sold",
    "price": "Prices charged",
    "no_product_detail": "Sales with no product detail",
}


def _total(db: Session, metric: str, start: date, end: date) -> Decimal:
    """The whole figure for a period: the sum of the parts of any one way of splitting it."""
    _, read = next(iter(METRICS[metric].dimensions.values()))
    return sum((value for _, value in read(db, start, end).values()), ZERO)


def _sales_count(db: Session, start: date, end: date) -> int:
    return int(
        db.scalar(
            select(func.count()).where(
                Sale.kind == "sale", Sale.sold_on >= start, Sale.sold_on <= end
            )
        )
        or 0
    )


def _lines_by_product(db: Session, start: date, end: date) -> dict[str, tuple[Decimal, Decimal]]:
    """{product: (items, sales)} from the sale lines; refunded items count back."""
    query = (
        select(SaleLine.product_id, func.sum(SaleLine.quantity), func.sum(SaleLine.net_amount))
        .select_from(SaleLine)
        .join(
            Sale,
            (Sale.organization_id == SaleLine.organization_id) & (Sale.id == SaleLine.sale_id),
        )
        .where(Sale.sold_on >= start, Sale.sold_on <= end)
        .group_by(SaleLine.product_id)
    )
    with _hash_joins(db):
        found = db.execute(query).all()
    return {str(pid): (Decimal(q or 0), Decimal(net or 0)) for pid, q, net in found}


def _impact(amount: Decimal, unit: str) -> str:
    verb = "adds" if amount > 0 else "takes off"
    return f"{verb} {format_value(abs(amount), unit)}"


def _finding(kind: str, lens: str, amount: Decimal, change: Decimal, text: str, unit: str):
    share = rules.share_of(amount, change)
    return FindingOut(
        kind=kind,
        lens=lens,
        label=TITLES[kind],
        amount=_pounds_or_count(amount, unit),
        share_pct=None if share is None else str(share.quantize(Decimal("0.1"))),
        text=text,
    )


def _strength(finding: FindingOut) -> Decimal:
    return abs(Decimal(finding.share_pct)) if finding.share_pct is not None else ZERO


def explain(
    db: Session,
    metric: str,
    month: date,
    against: str = "previous_month",
) -> DriversOut:
    if metric not in METRICS:
        raise AppError("That figure can't be explained yet.", code="bad_metric", status_code=422)
    if against not in AGAINST:
        raise AppError(
            "Compare with the month before or last year.", code="bad_against", status_code=422
        )
    spec = METRICS[metric]
    unit = spec.unit
    month = periods.start_of(month, "month")
    before = periods.shift(month, "month", AGAINST[against])
    end, before_end = periods.end_of(month, "month"), periods.end_of(before, "month")
    this_name, before_name = f"{month:%B %Y}", f"{before:%B %Y}"

    now, then = _total(db, metric, month, end), _total(db, metric, before, before_end)
    change = now - then
    pct = None if then == 0 else change / abs(then) * rules.HUNDRED

    lenses: list[LensOut] = []
    if metric in PER_DAY:
        lens = _days_lens(metric, unit, now, then, month, end, before, before_end, change,
                          this_name, before_name)  # fmt: skip
        if lens:
            lenses.append(lens)
    if metric == "revenue":
        lenses.extend(_sales_lenses(db, unit, now, then, month, end, before, before_end, change,
                                    this_name, before_name))  # fmt: skip

    findings = [effect for lens in lenses for effect in lens.effects if Decimal(effect.amount) != 0]
    for dimension in spec.dimensions:
        part = segment_change(db, metric, dimension, month, against, limit=1)
        top = part.rows[0] if part.rows else None
        if top is None or top.share_of_change_pct is None:
            continue
        if abs(Decimal(top.share_of_change_pct)) < rules.MIN_CONTRIBUTOR_SHARE:
            continue
        findings.append(
            FindingOut(
                kind="contributor",
                lens="parts",
                label=f"{part.dimension_label}: {top.label}",
                amount=top.change,
                share_pct=top.share_of_change_pct,
                text=top.text,
            )
        )
    findings.sort(key=lambda f: (-_strength(f), f.label))

    if change == 0:
        headline = f"{spec.label} in {this_name} was the same as in {before_name}."
    else:
        verb = "rose" if change > 0 else "fell"
        size = format_value(abs(change), unit)
        shown = "" if pct is None else f" ({abs(pct):.0f}%)"
        headline = (
            f"{spec.label} {verb} by {size}{shown} in {this_name} compared with {before_name}."
        )
    return DriversOut(
        metric=metric,
        metric_label=spec.label,
        unit=unit,
        month=month,
        compared_with=before,
        against=against,
        total_previous=_pounds_or_count(then, unit),
        total_current=_pounds_or_count(now, unit),
        total_change=_pounds_or_count(change, unit),
        total_change_pct=None if pct is None else str(pct.quantize(Decimal("0.1"))),
        headline=headline,
        lenses=lenses,
        findings=findings[:TOP_FINDINGS],
    )


def _days_lens(
    metric, unit, now, then, month, end, before, before_end, change, this_name, before_name
):
    days, days_before = rules.days_in(month, end), rules.days_in(before, before_end)
    if days == days_before:
        return None
    rate, rate_before = now / days, then / days_before
    day_effect, rate_effect = rules.two_factors(days_before, days, rate_before, rate)
    gap = abs(days - days_before)
    more = "more" if days > days_before else "fewer"
    calendar = _finding(
        "calendar_days", "days", day_effect, change,
        f"{this_name} has {gap} {more} days than {before_name}, which on its own "
        f"{_impact(day_effect, unit)}.", unit,
    )  # fmt: skip
    busier = _finding(
        "daily_rate", "days", rate_effect, change,
        f"On an average day it came to {format_value(rate, unit)} against "
        f"{format_value(rate_before, unit)}, which {_impact(rate_effect, unit)}.", unit,
    )  # fmt: skip
    return LensOut(key="days", title="The number of days in the month", effects=[calendar, busier])


def _sales_lenses(
    db, unit, now, then, month, end, before, before_end, change, this_name, before_name
):
    lenses = []
    n, n_before = _sales_count(db, month, end), _sales_count(db, before, before_end)
    if n or n_before:
        rate = now / n if n else ZERO
        rate_before = then / n_before if n_before else ZERO
        count_effect, _ = rules.two_factors(n_before, n, rate_before, rate)
        value_effect = change - count_effect
        more = "more" if n > n_before else "fewer"
        if n_before == 0:
            count_text = (
                f"There were {n} sales in {this_name} and none in {before_name}, which "
                f"{_impact(count_effect, unit)}."
            )
        else:
            count_text = (
                f"{abs(n - n_before)} {more} sales ({n} against {n_before}), valued at the average "
                f"sale in {before_name} of {format_value(rate_before, unit)}, "
                f"{_impact(count_effect, unit)}."
            )
        bigger = "A bigger" if value_effect > 0 else "A smaller"
        value_text = (
            f"{bigger} average sale ({format_value(rate, unit)} against "
            f"{format_value(rate_before, unit)}) {_impact(value_effect, unit)}."
        )
        lenses.append(
            LensOut(
                key="orders",
                title="Number of sales and size of the average sale",
                effects=[
                    _finding("sales_count", "orders", count_effect, change, count_text, unit),
                    _finding("sale_value", "orders", value_effect, change, value_text, unit),
                ],
            )
        )

    lines, lines_before = (
        _lines_by_product(db, month, end),
        _lines_by_product(db, before, before_end),
    )
    if lines or lines_before:
        rows = {
            key: (
                *lines_before.get(key, (ZERO, ZERO)),
                *lines.get(key, (ZERO, ZERO)),
            )
            for key in lines.keys() | lines_before.keys()
        }
        split = rules.price_volume(rows)
        lines_net = sum((net for _, net in lines.values()), ZERO)
        lines_net_before = sum((net for _, net in lines_before.values()), ZERO)
        no_detail = (now - lines_net) - (then - lines_net_before)
        items = sum((q for q, _ in lines.values()), ZERO)
        items_before = sum((q for q, _ in lines_before.values()), ZERO)
        more = "more" if items > items_before else "fewer"
        volume_text = (
            f"Selling {abs(items - items_before):.0f} {more} items ({items:.0f} against "
            f"{items_before:.0f}), at the prices of {before_name}, {_impact(split.volume, unit)}."
        )
        price_text = (
            f"{'Higher' if split.price > 0 else 'Lower'} prices (what customers actually paid per "
            f"item, after discounts) {_impact(split.price, unit)}."
        )
        effects = [
            _finding("volume", "price_volume", split.volume, change, volume_text, unit),
            _finding("price", "price_volume", split.price, change, price_text, unit),
        ]
        if no_detail != 0:
            effects.append(
                _finding(
                    "no_product_detail",
                    "price_volume",
                    no_detail,
                    change,
                    f"Sales recorded without product detail changed, which "
                    f"{_impact(no_detail, unit)}.",
                    unit,
                )  # fmt: skip
            )
        lenses.append(
            LensOut(key="price_volume", title="Items sold and the prices charged", effects=effects)
        )
    return lenses
