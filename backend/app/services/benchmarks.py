"""Sector benchmarks: shared UK reference figures (not any one business's data).

Loaded by staff with `python -m app.cli.benchmarks load <file.csv>` (the admin portal
comes in Phase 17). Businesses only ever read them, through `best_matches()`.
"""

import csv
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.business import BusinessBenchmark, BusinessProfile, Industry
from app.schemas.benchmarks import BenchmarkOut, BenchmarkRow, MatchedBenchmarkOut

SEGMENT = ("industry_code", "sic_code", "region", "size_band", "kpi_code", "period_year")
_REGION_NAMES = {
    "north_east": "North East",
    "north_west": "North West",
    "yorkshire_and_the_humber": "Yorkshire and the Humber",
    "east_midlands": "East Midlands",
    "west_midlands": "West Midlands",
    "east_of_england": "East of England",
    "london": "London",
    "south_east": "South East",
    "south_west": "South West",
    "wales": "Wales",
    "scotland": "Scotland",
    "northern_ireland": "Northern Ireland",
}


# --- loading (staff only, via the CLI) -------------------------------------------------------


@dataclass
class LoadReport:
    rows: int = 0
    added: int = 0
    updated: int = 0
    errors: list[str] = field(default_factory=list)


def _row_errors(line: int, exc: ValidationError) -> list[str]:
    return [
        f"row {line}: {'.'.join(str(p) for p in e['loc']) or 'row'}: {e['msg']}"
        for e in exc.errors()
    ]


def load_csv(db: Session, path: Path, *, dry_run: bool = False) -> LoadReport:
    """Validate every row first; save only if the whole file is valid (all or nothing)."""
    report = LoadReport()
    rows: list[BenchmarkRow] = []
    # utf-8-sig: files saved from Excel start with a byte-order mark.
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {n for n, f in BenchmarkRow.model_fields.items() if f.is_required()}
        missing = required - set(reader.fieldnames or [])
        if missing:
            report.errors.append(f"missing columns: {', '.join(sorted(missing))}")
            return report
        for line, raw in enumerate(reader, start=2):  # line 1 is the header
            report.rows += 1
            try:
                rows.append(BenchmarkRow.model_validate({k: v for k, v in raw.items() if k}))
            except ValidationError as exc:
                report.errors.extend(_row_errors(line, exc))

    known_industries = set(db.scalars(select(Industry.code)))
    seen: set[tuple] = set()
    for line, row in enumerate(rows, start=2):
        if row.industry_code not in known_industries:
            report.errors.append(
                f"row {line}: industry_code: unknown industry '{row.industry_code}'"
            )
        key = tuple(getattr(row, k) for k in SEGMENT)
        if key in seen:
            report.errors.append(f"row {line}: duplicate of an earlier row for the same segment")
        seen.add(key)
    if report.errors:
        return report

    for row in rows:
        existing = db.scalar(
            select(BusinessBenchmark).where(
                *(
                    getattr(BusinessBenchmark, k).is_(None)
                    if getattr(row, k) is None
                    else getattr(BusinessBenchmark, k) == getattr(row, k)
                    for k in SEGMENT
                )
            )
        )
        if existing is None:
            db.add(BusinessBenchmark(**row.model_dump()))
            report.added += 1
        else:
            for name, value in row.model_dump().items():
                setattr(existing, name, value)
            report.updated += 1
    if dry_run:
        db.rollback()
    else:
        db.commit()
    return report


# --- reading ---------------------------------------------------------------------------------


def _out(b: BusinessBenchmark) -> BenchmarkOut:
    return BenchmarkOut(**{k: getattr(b, k) for k in BenchmarkOut.model_fields})


def search(db: Session, **filters) -> list[BenchmarkOut]:
    stmt = select(BusinessBenchmark)
    for name, value in filters.items():
        if value is not None:
            stmt = stmt.where(getattr(BusinessBenchmark, name) == value)
    stmt = stmt.order_by(
        BusinessBenchmark.industry_code,
        BusinessBenchmark.kpi_code,
        BusinessBenchmark.period_year.desc(),
    ).limit(500)
    return [_out(b) for b in db.scalars(stmt)]


def best_matches(db: Session, profile: BusinessProfile) -> list[MatchedBenchmarkOut]:
    """For each KPI, the most specific benchmark that fits this business, newest year first.

    Specificity: SIC code, then UK region, then size band; always the same industry. A
    blank region/size/SIC on a benchmark means "applies to all", so it still fits.
    """

    def fits(column, value):
        return column.is_(None) if value is None else or_(column.is_(None), column == value)

    candidates = db.scalars(
        select(BusinessBenchmark).where(
            BusinessBenchmark.industry_code == profile.industry_code,
            fits(BusinessBenchmark.sic_code, profile.sic_code),
            fits(BusinessBenchmark.region, profile.region),
            fits(BusinessBenchmark.size_band, profile.business_size),
        )
    ).all()

    def rank(b: BusinessBenchmark):
        specificity = (
            (b.sic_code is not None) * 4 + (b.region is not None) * 2 + (b.size_band is not None)
        )
        return (specificity, b.period_year)

    best: dict[str, BusinessBenchmark] = {}
    for b in candidates:
        if b.kpi_code not in best or rank(b) > rank(best[b.kpi_code]):
            best[b.kpi_code] = b

    result = []
    for kpi in sorted(best):
        b = best[kpi]
        matched = ["SIC " + b.sic_code if b.sic_code else "sector"]
        matched.append(_REGION_NAMES[b.region] if b.region else "whole UK")
        matched.append(f"{b.size_band} businesses" if b.size_band else "all sizes")
        result.append(MatchedBenchmarkOut(**_out(b).model_dump(), matched_on=matched))
    return result
