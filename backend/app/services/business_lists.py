"""A business's own lists (offerings, sales channels, customer types, cost categories).

The session must be scoped to the organisation (CurrentTenant).
"""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, NotFoundError
from app.models.business import LIST_KINDS, BusinessListItem
from app.schemas.business_lists import (
    BulkCreateOut,
    BusinessListsOut,
    ListItemCreate,
    ListItemOut,
    ListItemPatch,
)
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta

MAX_ITEMS_PER_LIST = 100

# UK-first starting points shown during onboarding. The business ticks what applies and
# can add its own. Offerings depend entirely on the business, so there are no suggestions.
SUGGESTIONS: dict[str, list[str]] = {
    "offering": [],
    "sales_channel": [
        "Shop (in person)",
        "Own website",
        "Amazon",
        "eBay",
        "Etsy",
        "Instagram / Facebook",
        "TikTok Shop",
        "WhatsApp",
        "Phone orders",
        "Markets & events",
        "Wholesale / trade",
        "Deliveroo / Just Eat / Uber Eats",
    ],
    "customer_type": [
        "Consumers",
        "Sole traders & small businesses",
        "Larger businesses",
        "Trade customers",
        "Public sector",
        "Charities",
    ],
    "cost_category": [
        "Stock & materials",
        "Staff wages",
        "Employer's NI & pensions",
        "Rent",
        "Business rates",
        "Utilities",
        "Marketing & advertising",
        "Delivery & postage",
        "Card & payment fees",
        "Software & subscriptions",
        "Insurance",
        "Accountant & professional fees",
        "Equipment & repairs",
        "Vehicle & travel",
    ],
}
# Suggested cost categories that count as cost of sales (used for gross profit later).
COST_OF_SALES_SUGGESTIONS = {"Stock & materials", "Delivery & postage"}


def _out(item: BusinessListItem) -> ListItemOut:
    return ListItemOut(
        id=item.id,
        kind=item.kind,
        name=item.name,
        is_active=item.is_active,
        sort_order=item.sort_order,
        is_cost_of_sales=item.is_cost_of_sales,
        created_at=item.created_at,
    )


def _ordered(stmt):
    return stmt.order_by(
        BusinessListItem.sort_order.asc().nulls_last(),
        func.lower(BusinessListItem.name),
    )


def _existing_names(db: Session, kind: str) -> dict[str, BusinessListItem]:
    items = db.scalars(select(BusinessListItem).where(BusinessListItem.kind == kind))
    return {item.name.lower(): item for item in items}


def _check_cost_of_sales(kind: str, value: bool | None) -> None:
    if value is not None and kind != "cost_category":
        raise AppError(
            "Only cost categories can be marked as cost of sales",
            code="cost_of_sales_only_costs",
            status_code=422,
        )


def _audit(db: Session, tenant, action: AuditAction, item: BusinessListItem, meta, **details):
    record_audit(
        db,
        action,
        actor_user_id=tenant.user.id,
        organization_id=tenant.organization_id,
        target_type="list_item",
        target_id=item.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        details={"kind": item.kind, **details},
    )


def get_all_lists(db: Session, include_archived: bool = False) -> BusinessListsOut:
    stmt = select(BusinessListItem)
    if not include_archived:
        stmt = stmt.where(BusinessListItem.is_active.is_(True))
    grouped: dict[str, list[ListItemOut]] = {kind: [] for kind in LIST_KINDS}
    for item in db.scalars(_ordered(stmt)):
        grouped[item.kind].append(_out(item))
    return BusinessListsOut(**grouped)


def get_list(db: Session, kind: str, include_archived: bool = False) -> list[ListItemOut]:
    stmt = select(BusinessListItem).where(BusinessListItem.kind == kind)
    if not include_archived:
        stmt = stmt.where(BusinessListItem.is_active.is_(True))
    return [_out(item) for item in db.scalars(_ordered(stmt))]


def _add(db: Session, tenant, kind: str, name: str, is_cost_of_sales: bool | None, meta):
    item = BusinessListItem(kind=kind, name=name, is_cost_of_sales=is_cost_of_sales)
    db.add(item)
    try:
        db.flush()
    except IntegrityError as exc:
        # Someone added the same name at the same moment; the unique index decided.
        db.rollback()
        raise ConflictError(f"'{name}' is already on this list", code="already_exists") from exc
    _audit(db, tenant, AuditAction.LIST_ITEM_ADDED, item, meta)
    return item


def _check_room(existing: dict, adding: int) -> None:
    if len(existing) + adding > MAX_ITEMS_PER_LIST:
        raise AppError(
            f"A list can hold at most {MAX_ITEMS_PER_LIST} items (archived ones included)",
            code="list_full",
            status_code=422,
        )


def add_item(db: Session, tenant, kind: str, body: ListItemCreate, meta: RequestMeta):
    _check_cost_of_sales(kind, body.is_cost_of_sales)
    existing = _existing_names(db, kind)
    match = existing.get(body.name.lower())
    if match is not None:
        hint = " (it's archived: restore it instead)" if not match.is_active else ""
        raise ConflictError(
            f"'{match.name}' is already on this list{hint}",
            code="already_exists",
            details={"id": str(match.id), "is_active": match.is_active},
        )
    _check_room(existing, 1)
    item = _add(db, tenant, kind, body.name, body.is_cost_of_sales, meta)
    db.commit()
    db.refresh(item)
    return _out(item)


def add_items(db: Session, tenant, kind: str, names: list[str], meta: RequestMeta):
    """Add the names that aren't already on the list; report the rest as already there."""
    existing = _existing_names(db, kind)
    to_add, already = [], []
    for name in names:
        key = name.lower()
        if key in existing or key in {n.lower() for n in to_add}:
            already.append(name)
        else:
            to_add.append(name)
    _check_room(existing, len(to_add))
    added = [
        _add(
            db,
            tenant,
            kind,
            name,
            (name in COST_OF_SALES_SUGGESTIONS) if kind == "cost_category" else None,
            meta,
        )
        for name in to_add
    ]
    db.commit()
    for item in added:
        db.refresh(item)
    return BulkCreateOut(added=[_out(i) for i in added], already_there=already)


def update_item(
    db: Session, tenant, kind: str, item_id: uuid.UUID, body: ListItemPatch, meta: RequestMeta
):
    item = db.scalar(
        select(BusinessListItem).where(
            BusinessListItem.id == item_id, BusinessListItem.kind == kind
        )
    )
    if item is None:
        raise NotFoundError("Item not found", code="list_item_not_found")
    changes: dict[str, Any] = body.model_dump(include=body.model_fields_set)
    if "is_cost_of_sales" in changes:
        _check_cost_of_sales(kind, changes["is_cost_of_sales"])
    if "name" in changes and changes["name"].lower() != item.name.lower():
        clash = _existing_names(db, kind).get(changes["name"].lower())
        if clash is not None:
            raise ConflictError(f"'{clash.name}' is already on this list", code="already_exists")

    changed = sorted(k for k, v in changes.items() if getattr(item, k) != v)
    for name in changed:
        setattr(item, name, changes[name])
    if changed:
        details: dict[str, Any] = {"fields": changed}
        if "is_active" in changed:
            details["archived"] = not item.is_active
        _audit(db, tenant, AuditAction.LIST_ITEM_UPDATED, item, meta, **details)
        db.commit()
        db.refresh(item)
    return _out(item)
