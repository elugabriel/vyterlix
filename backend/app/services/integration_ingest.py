"""Putting what a connection fetched into the business's data.

A connector (Xero, say) only talks to its own system and hands back rows in the same shape a file
import builds. This writes them the same way a file import does, so a sale from Xero is a sale like
any other: it counts in every figure, and the customers and suppliers on it are made when needed.

Each row carries the provider's own id in `reference`, stored as the record's `source_ref`, so
fetching the same invoice again never makes a second sale. If it has changed (it was paid since),
the record is brought up to date instead.
"""

import uuid
from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.data import BankTransaction, Expense, Sale, WebVisit
from app.services import import_records
from app.services.import_rows import RowOutcome

TABLES = {"sales": Sale, "expenses": Expense}
# What can change on a record after it was first fetched, per dataset.
CHANGEABLE = {
    "sales": (
        ("sold_on", "sold_on"),
        ("net_amount", "net_amount"),
        ("vat_amount", "vat_amount"),
        ("gross_amount", "gross_amount"),
        ("due_on", "due_on"),
        ("paid_on", "paid_on"),
    ),
    "expenses": (
        ("spent_on", "spent_on"),
        ("net_amount", "net_amount"),
        ("vat_amount", "vat_amount"),
        ("gross_amount", "gross_amount"),
    ),
}


WEB_COUNTS = ("sessions", "users", "new_users", "key_events")


def _ingest_web_visits(
    db: Session, organization_id: uuid.UUID, source: str, rows: list[dict[str, Any]]
) -> int:
    """One row per day and kind of source. Analytics revises recent days, so what is already
    stored is brought up to date. Returns how many days were new."""
    refs = [r["reference"] for r in rows]
    existing = {
        v.source_ref: v
        for v in db.scalars(
            select(WebVisit).where(WebVisit.source == source, WebVisit.source_ref.in_(refs))
        )
    }
    made = 0
    for row in rows:
        visit = existing.get(row["reference"])
        if visit is None:
            db.add(
                WebVisit(
                    organization_id=organization_id,
                    source=source,
                    source_ref=row["reference"],
                    day=row["day"],
                    channel=row["channel"],
                    **{k: row[k] for k in WEB_COUNTS},
                )
            )
            made += 1
        else:
            for k in WEB_COUNTS:
                setattr(visit, k, row[k])
    return made


def _ingest_bank_transactions(
    db: Session, organization_id: uuid.UUID, source: str, rows: list[dict[str, Any]]
) -> int:
    """A bank does not change a transaction once it is posted, so only new ones are added."""
    refs = [r["reference"] for r in rows]
    have = set(
        db.scalars(
            select(BankTransaction.source_ref).where(
                BankTransaction.source == source, BankTransaction.source_ref.in_(refs)
            )
        )
    )
    made = 0
    for row in rows:
        if row["reference"] in have:
            continue
        have.add(row["reference"])
        db.add(
            BankTransaction(
                organization_id=organization_id,
                source=source,
                source_ref=row["reference"],
                day=row["day"],
                amount=row["amount"],
                description=row.get("description"),
                counterparty=row.get("counterparty"),
                account_name=row.get("account_name"),
            )
        )
        made += 1
    return made


def ingest(
    db: Session, organization_id: uuid.UUID, source: str, datasets: dict[str, list[dict[str, Any]]]
) -> Counter:
    """Create what is new and update what has changed. Returns how many of each were made."""
    created: Counter = Counter()
    resolver = import_records.Resolver(db, organization_id, None, source)  # type: ignore[arg-type]
    for dataset, rows in datasets.items():
        if dataset == "web_visits" and rows:
            created["web_visits"] += _ingest_web_visits(db, organization_id, source, rows)
            continue
        if dataset == "bank_transactions" and rows:
            created["bank_transactions"] += _ingest_bank_transactions(
                db, organization_id, source, rows
            )
            continue
        model = TABLES.get(dataset)
        if model is None or not rows:
            continue
        refs = [r["reference"] for r in rows if r.get("reference")]
        existing = {
            record.source_ref: record
            for record in db.scalars(
                select(model).where(model.source == source, model.source_ref.in_(refs))
            )
        }
        new: list[RowOutcome] = []
        for row in rows:
            record = existing.get(row.get("reference"))
            if record is None:
                new.append(RowOutcome(values=row, reference=row.get("reference")))
                continue
            for column, key in CHANGEABLE[dataset]:
                if key in row and getattr(record, column) != row[key]:
                    setattr(record, column, row[key])
        if new:
            tables, _ = import_records.build(dataset, resolver, new)
            import_records.insert_records(db, tables, created)
    db.flush()
    created.update(resolver.created)
    return created
