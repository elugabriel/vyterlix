# ruff: noqa: E501
"""Look after the plans (staff only, until the admin portal can do it).

    python -m app.cli.billing plans                                  # what is for sale and what each includes
    python -m app.cli.billing price growth --month 19900 --year 199000   # prices in pence, excluding VAT
    python -m app.cli.billing provider-price growth stripe month price_123   # the provider's own price id
    python -m app.cli.billing include growth members --limit 10      # what a plan includes
    python -m app.cli.billing include starter ai_assistant --off
    python -m app.cli.billing include corporate members --unlimited

Changing a price never changes what a business already paying is charged.
Every price and limit the system starts with is a placeholder to be confirmed before launch.
"""

import argparse
import sys

from sqlalchemy import select

from app.billing import rules
from app.core.errors import AppError
from app.db.session import get_sessionmaker
from app.models.billing import Plan
from app.services import billing


def _show(db) -> None:
    for plan in db.scalars(select(Plan).order_by(Plan.sort_order)):
        month, year = rules.money(plan.price_month_pence), rules.money(plan.price_year_pence)
        print(
            f"{plan.code:<10} {plan.name:<10} {month:>10} a month  {year:>12} a year  (excluding VAT)"
        )
        print(f"           for sale: {'yes' if plan.self_serve else 'no, by arrangement'}")
        print(f"           provider prices: {plan.provider_prices or 'none set'}")
        for feature, row in billing._rows(db, plan.id).items():
            print(f"           {feature:<18} {billing._feature_text(feature, row)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli.billing")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plans", help="show the plans")
    price = commands.add_parser("price", help="set a plan's prices, in pence")
    price.add_argument("code")
    price.add_argument("--month", type=int)
    price.add_argument("--year", type=int)
    ref = commands.add_parser("provider-price", help="set a payment provider's own price id")
    ref.add_argument("code")
    ref.add_argument("provider", choices=["stripe", "paystack"])
    ref.add_argument("interval", choices=["month", "year"])
    ref.add_argument("ref")
    include = commands.add_parser("include", help="set what a plan includes")
    include.add_argument("code")
    include.add_argument("feature", choices=list(rules.FEATURE_NOUN))
    how = include.add_mutually_exclusive_group(required=True)
    how.add_argument("--limit", type=int)
    how.add_argument("--unlimited", action="store_true")
    how.add_argument("--off", action="store_true")
    args = parser.parse_args(argv)

    with get_sessionmaker()() as db:
        try:
            if args.command == "price":
                billing.set_plan(db, args.code, price_month=args.month, price_year=args.year)
            elif args.command == "provider-price":
                billing.set_plan(
                    db, args.code, provider_price=(args.provider, args.interval, args.ref)
                )
            elif args.command == "include":
                billing.set_entitlement(
                    db, args.code, args.feature, enabled=not args.off, limit=args.limit
                )
            db.commit()
        except AppError as exc:
            db.rollback()
            print(str(exc), file=sys.stderr)
            return 1
        _show(db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
