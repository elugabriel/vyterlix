"""Invitations: owner-side routes live under the organisation; invitee-side routes don't,
because the invitee isn't a member yet."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import DB, CurrentUser, Meta, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.invitations import (
    AcceptInvitationOut,
    CreateInvitationRequest,
    InvitationOut,
    InvitationPreviewOut,
    InvitationTokenRequest,
)
from app.services.email import EmailSender, get_email_sender
from app.services.invitations import (
    accept_invitation,
    create_invitation,
    list_invitations,
    preview_invitation,
    revoke_invitation,
)

MembersManager = Annotated[Tenant, Depends(require_permission(Perm.MEMBERS_MANAGE))]
Sender = Annotated[EmailSender, Depends(get_email_sender)]

org_router = APIRouter(prefix="/organizations/{organization_id}/invitations", tags=["invitations"])
invitee_router = APIRouter(prefix="/invitations", tags=["invitations"])


@org_router.post("", status_code=status.HTTP_201_CREATED, response_model=InvitationOut)
def invite(
    body: CreateInvitationRequest, tenant: MembersManager, db: DB, meta: Meta, sender: Sender
):
    remit = body.remit.model_dump(mode="json") if body.remit else None
    return create_invitation(
        db, tenant.organization, tenant.user, body.email, body.role, remit, sender, meta
    )


@org_router.get("", response_model=list[InvitationOut])
def list_(tenant: MembersManager, db: DB):
    return list_invitations(db)


@org_router.delete("/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke(invitation_id: uuid.UUID, tenant: MembersManager, db: DB, meta: Meta) -> None:
    revoke_invitation(db, tenant.organization, tenant.user, invitation_id, meta)


# POST (not GET) so the token never appears in URLs or access logs.
@invitee_router.post("/preview", response_model=InvitationPreviewOut)
def preview(body: InvitationTokenRequest, db: DB):
    return preview_invitation(db, body.token)


@invitee_router.post("/accept", response_model=AcceptInvitationOut)
def accept(body: InvitationTokenRequest, user: CurrentUser, db: DB, meta: Meta):
    # CurrentUser (not VerifiedUser): accepting an emailed link verifies the address.
    return AcceptInvitationOut(organization=accept_invitation(db, user, body.token, meta))
