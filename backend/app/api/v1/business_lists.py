import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import DB, Meta, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.business_lists import (
    BulkCreateOut,
    BusinessListsOut,
    ListItemCreate,
    ListItemOut,
    ListItemPatch,
    ListItemsBulkCreate,
    ListKind,
)
from app.services.business_lists import (
    SUGGESTIONS,
    add_item,
    add_items,
    get_all_lists,
    get_list,
    update_item,
)

suggestions_router = APIRouter(prefix="/business-list-suggestions", tags=["business lists"])
router = APIRouter(prefix="/organizations/{organization_id}/lists", tags=["business lists"])

Reader = Annotated[Tenant, Depends(require_permission(Perm.INSIGHTS_VIEW))]
OrgManager = Annotated[Tenant, Depends(require_permission(Perm.ORG_MANAGE))]


@suggestions_router.get("", response_model=dict[str, list[str]])
def suggestions():
    """UK starting points to pick from during onboarding. Public reference data."""
    return SUGGESTIONS


@router.get("", response_model=BusinessListsOut)
def all_lists(tenant: Reader, db: DB, include_archived: bool = False):
    return get_all_lists(db, include_archived)


@router.get("/{kind}", response_model=list[ListItemOut])
def one_list(kind: ListKind, tenant: Reader, db: DB, include_archived: bool = False):
    return get_list(db, kind, include_archived)


@router.post("/{kind}", status_code=status.HTTP_201_CREATED, response_model=ListItemOut)
def add(kind: ListKind, body: ListItemCreate, tenant: OrgManager, db: DB, meta: Meta):
    return add_item(db, tenant, kind, body, meta)


@router.post("/{kind}/bulk", response_model=BulkCreateOut)
def add_many(kind: ListKind, body: ListItemsBulkCreate, tenant: OrgManager, db: DB, meta: Meta):
    """Add several names; any already on the list are skipped, not errors."""
    return add_items(db, tenant, kind, body.names, meta)


@router.patch("/{kind}/{item_id}", response_model=ListItemOut)
def update(
    kind: ListKind,
    item_id: uuid.UUID,
    body: ListItemPatch,
    tenant: OrgManager,
    db: DB,
    meta: Meta,
):
    """Rename, reorder, archive (`is_active: false`) or restore an item. Nothing is deleted."""
    return update_item(db, tenant, kind, item_id, body, meta)
