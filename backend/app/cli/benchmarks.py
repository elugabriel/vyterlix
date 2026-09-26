"""Load sector benchmarks from a CSV (staff only).

    python -m app.cli.benchmarks load benchmarks.csv            # validate and save
    python -m app.cli.benchmarks load benchmarks.csv --dry-run  # validate only

Columns: industry_code, sic_code, region, size_band, kpi_code, period_year, value,
unit, source, source_url, notes. Blank region = whole UK; blank size_band = all sizes.
Nothing is saved unless every row is valid. Re-loading a segment updates it.
"""

import argparse
import sys
from pathlib import Path

from app.db.session import get_sessionmaker
from app.services.benchmarks import load_csv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli.benchmarks")
    commands = parser.add_subparsers(dest="command", required=True)
    load = commands.add_parser("load", help="load benchmarks from a CSV file")
    load.add_argument("csv_file", type=Path)
    load.add_argument("--dry-run", action="store_true", help="check the file, save nothing")
    args = parser.parse_args(argv)

    if not args.csv_file.is_file():
        print(f"File not found: {args.csv_file}", file=sys.stderr)
        return 2
    with get_sessionmaker()() as db:
        report = load_csv(db, args.csv_file, dry_run=args.dry_run)
    if report.errors:
        print(f"Nothing saved: {len(report.errors)} problem(s) in {args.csv_file.name}:")
        for error in report.errors:
            print(f"  - {error}")
        return 1
    verb = "Would add" if args.dry_run else "Added"
    print(
        f"{verb} {report.added}, {'would update' if args.dry_run else 'updated'} "
        f"{report.updated} ({report.rows} rows checked)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
