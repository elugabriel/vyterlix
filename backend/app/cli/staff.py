"""Who is platform staff (the only way to give or take away staff rights).

    python -m app.cli.staff list
    python -m app.cli.staff grant someone@example.com --role admin     # admin: look and change
    python -m app.cli.staff grant someone@example.com --role support   # support: look only
    python -m app.cli.staff revoke someone@example.com

The person must already have an account. Staff sign in at the ordinary login page; what they can
do is decided here, never from the website.
"""

import argparse
import sys

from app.core.errors import AppError
from app.db.session import get_sessionmaker
from app.models.admin import STAFF_ROLES
from app.services import admin


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli.staff")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="show platform staff")
    grant = commands.add_parser("grant", help="make someone platform staff (or change their role)")
    grant.add_argument("email")
    grant.add_argument("--role", choices=STAFF_ROLES, required=True)
    revoke = commands.add_parser("revoke", help="take staff rights away")
    revoke.add_argument("email")
    args = parser.parse_args(argv)

    with get_sessionmaker()() as db:
        try:
            if args.command == "grant":
                admin.grant_staff(db, args.email, args.role)
                print(f"{args.email} is now platform staff ({args.role}).")
            elif args.command == "revoke":
                admin.revoke_staff(db, args.email)
                print(f"{args.email} is no longer platform staff.")
            else:
                rows = admin.list_staff(db)
                for email, role, active in rows:
                    print(f"{email:<40} {role:<8} {'active' if active else 'revoked'}")
                if not rows:
                    print("No platform staff yet.")
        except AppError as exc:
            db.rollback()
            print(str(exc), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
