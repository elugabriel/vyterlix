import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import DB, CurrentTenant, Meta, Tenant, VerifiedUser, require_permission
from app.core.permissions import Perm
from app.schemas.organizations import (
    CreateOrganizationRequest,
    MemberOut,
    OrganizationOut,
    UpdateMemberRequest,
    UpdateOrganizationRequest,
)
from app.services.organizations import (
    create_organization,
    list_members,
    list_organizations,
    organization_out,
    rename_organization,
    update_member,
)

# Everything from here on needs a verified email (unverified users have limited access).
router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=OrganizationOut)
def create(body: CreateOrganizationRequest, user: VerifiedUser, db: DB, meta: Meta):
    return create_organization(db, user, body.name, meta)


@router.get("", response_model=list[OrganizationOut])
def list_mine(user: VerifiedUser, db: DB):
    return list_organizations(db, user)


# --- inside one organisation: every route below takes CurrentTenant ----------------


@router.get("/{organization_id}", response_model=OrganizationOut)
def read(tenant: CurrentTenant):
    return organization_out(tenant.organization, tenant.role)


@router.patch("/{organization_id}", response_model=OrganizationOut)
def rename(
    body: UpdateOrganizationRequest,
    tenant: Annotated[Tenant, Depends(require_permission(Perm.ORG_MANAGE))],
    db: DB,
    meta: Meta,
):
    org = rename_organization(db, tenant.organization, tenant.user, body.name, meta)
    return organization_out(org, tenant.role)


@router.get("/{organization_id}/members", response_model=list[MemberOut])
def members(tenant: CurrentTenant, db: DB):
    return list_members(db)


@router.patch("/{organization_id}/members/{user_id}", response_model=MemberOut)
def change_member(
    user_id: uuid.UUID,
    body: UpdateMemberRequest,
    tenant: Annotated[Tenant, Depends(require_permission(Perm.MEMBERS_MANAGE))],
    db: DB,
    meta: Meta,
):
    changes = body.model_dump(mode="json", include=body.model_fields_set)
    return update_member(db, tenant.organization_id, tenant.user, user_id, changes, meta)
