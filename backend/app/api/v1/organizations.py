import uuid

from fastapi import APIRouter, status

from app.api.deps import DB, Meta, VerifiedUser
from app.schemas.organizations import CreateOrganizationRequest, OrganizationOut
from app.services.organizations import (
    create_organization,
    get_organization,
    list_organizations,
)

# Everything from here on needs a verified email (unverified users have limited access).
router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=OrganizationOut)
def create(body: CreateOrganizationRequest, user: VerifiedUser, db: DB, meta: Meta):
    return create_organization(db, user, body.name, meta)


@router.get("", response_model=list[OrganizationOut])
def list_mine(user: VerifiedUser, db: DB):
    return list_organizations(db, user)


@router.get("/{organization_id}", response_model=OrganizationOut)
def read(organization_id: uuid.UUID, user: VerifiedUser, db: DB):
    return get_organization(db, user, organization_id)
