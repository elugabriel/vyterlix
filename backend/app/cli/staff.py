"""Who is platform staff (the only way to give or take away staff rights).

    python -m app.cli.staff list
    python -m app.cli.staff create someone@example.com --name "Full Name" --role admin
    python -m app.cli.staff grant someone@example.com --role admin     # admin: look and change
    python -m app.cli.staff grant someone@example.com --role support   # support: look only
    python -m app.cli.staff revoke someone@example.com

`create` makes a new, already-verified account and gives it staff rights in one step: the way to get
the very first admin into a site that has no accounts yet. It asks for the password without
showing it (or reads VYTERLIX_NEW_PASSWORD, for a script); it is never put on the command line.
`grant` and `revoke` are for accounts that already exist. Staff sign in at the ordinary login page;
what they can do is decided here, never from the website.
"""

import argparse
import getpass
import os
import sys

from app.core.errors import AppError
from app.db.session import get_sessionmaker
from app.models.admin import STAFF_ROLES
from app.services import admin


def _ask_password() -> str:
    first = getpass.getpass("New password (not shown): ")
    if first != getpass.getpass("Type it again: "):
        raise AppError("The two passwords are not the same.", code="password_mismatch")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli.staff")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="show platform staff")
    create = commands.add_parser(
        "create", help="make a new verified account that is platform staff"
    )
    create.add_argument("email")
    create.add_argument("--name", required=True, help="the person's full name")
    create.add_argument("--role", choices=STAFF_ROLES, default="admin")
    grant = commands.add_parser("grant", help="make someone platform staff (or change their role)")
    grant.add_argument("email")
    grant.add_argument("--role", choices=STAFF_ROLES, required=True)
    revoke = commands.add_parser("revoke", help="take staff rights away")
    revoke.add_argument("email")
    args = parser.parse_args(argv)

    with get_sessionmaker()() as db:
        try:
            if args.command == "create":
                password = os.environ.get("VYTERLIX_NEW_PASSWORD") or _ask_password()
                admin.create_staff_account(
                    db, email=args.email, full_name=args.name, password=password, role=args.role
                )
                print(f"{args.email} can now sign in, as platform staff ({args.role}).")
            elif args.command == "grant":
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
