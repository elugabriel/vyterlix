from fastapi import APIRouter, status

from app.api.deps import DB, CurrentTenant, Meta, VerifiedUser
from app.schemas.organizations import CreateOrganizationRequest, MemberOut, OrganizationOut
from app.services.organizations import (
    create_organization,
    list_members,
    list_organizations,
    organization_out,
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


@router.get("/{organization_id}/members", response_model=list[MemberOut])
def members(tenant: CurrentTenant, db: DB):
    return list_members(db)
