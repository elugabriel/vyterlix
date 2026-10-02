"""Fake demo data for trying Vyterlix. Nothing here is real, and it only ever goes into a
business whose name ends with "(demo data)", so it can't be mixed into a real business.

    python -m app.cli.demo files --out ./demo-files   # write the CSV files, to upload by hand
    python -m app.cli.demo load --org <business id>   # bring a fake year of trading in
    python -m app.cli.demo clear --org <business id>  # take it all out again (undoes the imports)

The year is a small invented UK bakery, 1 October 2025 to 30 September 2026 (see
app/demo/milestone.py). `load` uses the same import pipeline as the Upload page.
"""

import argparse
import io
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_sessionmaker
from app.db.tenant import ACROSS_TENANTS, tenant_scope
from app.demo import milestone
from app.models.business import BusinessListItem
from app.models.identity import Organization, OrganizationUser, Role, User
from app.models.imports import DataImport
from app.schemas.import_mapping import MappingIn, MappingOptions
from app.services.auth import RequestMeta
from app.services.import_mapping import get_mapping, save_mapping
from app.services.import_runner import run_import, undo_import
from app.services.import_validation import validate_import
from app.services.imports import upload_import
from app.services.jobs import JobTenant
from app.services.storage import FileStorage, get_file_storage

DEMO_SUFFIX = "(demo data)"


class DemoError(Exception):
    """Something the person running the command can fix; shown without a stack trace."""


def _owner_and_org(db: Session, org_id: uuid.UUID) -> tuple[Organization, JobTenant]:
    org = db.get(Organization, org_id)
    if org is None:
        raise DemoError("No business with that id.")
    if not org.name.rstrip().endswith(DEMO_SUFFIX):
        raise DemoError(
            f'Refusing: "{org.name}" is not a demo business. Demo data only goes into a business '
            f'whose name ends with "{DEMO_SUFFIX}" (rename it, or create a new one).'
        )
    owner = db.scalars(
        select(User)
        .join(OrganizationUser, OrganizationUser.user_id == User.id)
        .join(Role, Role.id == OrganizationUser.role_id)
        .where(OrganizationUser.organization_id == org_id, Role.code == "owner")
        .limit(1)
        .execution_options(**ACROSS_TENANTS)  # looking up who owns this one business
    ).first()
    if owner is None:
        raise DemoError("That business has no owner to import as.")
    return org, JobTenant(organization_id=org_id, user=owner)


def _demo_imports(db: Session) -> list[DataImport]:
    names = [name for name, _, _ in milestone.FILES]
    return list(
        db.scalars(
            select(DataImport)
            .where(DataImport.original_filename.in_(names))
            .order_by(DataImport.created_at)
        )
    )


def load_year(
    db: Session,
    org_id: uuid.UUID,
    storage: FileStorage | None = None,
    say: Callable[[str], None] = print,
) -> None:
    storage, meta = storage or get_file_storage(), RequestMeta()
    dataset = milestone.build()
    org, tenant = _owner_and_org(db, org_id)
    with tenant_scope(db, org_id):
        if any(i.status == "imported" for i in _demo_imports(db)):
            raise DemoError("The demo year is already loaded. Run `clear` first to reload it.")
        say(f"Loading the demo year into {org.name}...")
        for filename, kind, options in dataset.plan:
            uploaded = upload_import(
                db,
                storage,
                tenant,
                stream=io.BytesIO(dataset.files[filename]),
                filename=filename,
                dataset=kind,
                meta=meta,
            )
            suggested = get_mapping(db, storage, uploaded.id).suggested_mapping
            body = MappingIn(mapping=suggested, options=MappingOptions(**options))
            save_mapping(db, storage, tenant, uploaded.id, body, meta)
            checked = validate_import(db, storage, tenant, uploaded.id, meta)
            if checked.invalid or checked.duplicate:
                raise DemoError(
                    f"{filename}: {checked.invalid} bad rows and {checked.duplicate} repeats, "
                    "so nothing from it was imported."
                )
            done = run_import(db, tenant, uploaded.id, meta)
            say(f"  {filename}: {done.data_import.imported_count:,} rows imported")
        # What an owner says in onboarding: buying stock is a "cost of sales", not a running cost
        # (the goods are already counted as cost of goods sold), so profit isn't counted twice.
        stock = db.scalars(
            select(BusinessListItem).where(
                BusinessListItem.kind == "cost_category", BusinessListItem.name == "Stock"
            )
        ).first()
        if stock is not None:
            stock.is_cost_of_sales = True
            db.commit()
    say("Done. Open the business in the browser to see it.")


def clear_year(db: Session, org_id: uuid.UUID, say: Callable[[str], None] = print) -> None:
    meta = RequestMeta()
    _org, tenant = _owner_and_org(db, org_id)
    with tenant_scope(db, org_id):
        imports = [i for i in _demo_imports(db) if i.status == "imported"]
        if not imports:
            say("Nothing to clear: the demo year isn't loaded.")
            return
        for data_import in reversed(imports):  # newest first, so nothing points at a removed row
            name = data_import.original_filename
            result = undo_import(db, tenant, data_import.id, meta)
            say(f"  undid {name}: {sum(result.removed.values()):,} records")
    say("Cleared.")


def write_files(out: Path, say: Callable[[str], None] = print) -> None:
    for path in milestone.build().write(out):
        say(str(path))
    say(f"Wrote the files to {out.resolve()}.")
    say("Upload them in this order: customers, products, sales, expenses, stock.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli.demo")
    commands = parser.add_subparsers(dest="command", required=True)
    files = commands.add_parser("files", help="write the demo CSV files")
    files.add_argument("--out", type=Path, required=True)
    for name, help_ in (("load", "import the demo year"), ("clear", "undo the demo imports")):
        commands.add_parser(name, help=help_).add_argument("--org", type=uuid.UUID, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "files":
            write_files(args.out)
        else:
            with get_sessionmaker()() as db:
                (load_year if args.command == "load" else clear_year)(db, args.org)
    except DemoError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
