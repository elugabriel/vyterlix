# ruff: noqa: E501
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import DB, Meta, VerifiedUser
from app.core.errors import NotFoundError
from app.schemas.admin import (
    AdminAuditPage,
    AdminOrgDetail,
    AdminOrgPage,
    AdminPlan,
    AdminSubscription,
    AdminUserDetail,
    AdminUserPage,
    EntitlementIn,
    ExtendTrialIn,
    PlanChangeIn,
    ReasonIn,
    RoleChangeIn,
    StaffMeOut,
)
from app.services import admin as service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/me", response_model=StaffMeOut)
def me(user: VerifiedUser, db: DB):
    """Whether the caller is platform staff (so the website knows whether to show the admin link)."""
    staff = service.staff_of(db, user)
    return StaffMeOut(is_staff=staff is not None, role=staff.role if staff else None)


def get_staff(user: VerifiedUser, db: DB) -> service.Staff:
    """Platform staff only. Anyone else is told the page does not exist."""
    staff = service.staff_of(db, user)
    if staff is None:
        raise NotFoundError("Not found", code="not_found")
    return staff


Staff = Annotated[service.Staff, Depends(get_staff)]
Page = Annotated[int, Query(ge=1, le=100)]


@router.get("/organizations", response_model=AdminOrgPage)
def organizations(
    staff: Staff,
    db: DB,
    q: Annotated[str | None, Query(max_length=100)] = None,
    status: Annotated[str | None, Query(pattern="^(active|suspended|closed)$")] = None,
    limit: Page = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return service.list_organizations(db, q=q, status=status, limit=limit, offset=offset)


@router.get("/organizations/{organization_id}", response_model=AdminOrgDetail)
def organization(organization_id: uuid.UUID, staff: Staff, db: DB, meta: Meta):
    """One business: its people, its plan and how much of it is used. Never its figures."""
    return service.organization_detail(db, staff, organization_id, meta)


@router.post("/organizations/{organization_id}/suspend", status_code=204)
def suspend(organization_id: uuid.UUID, body: ReasonIn, staff: Staff, db: DB, meta: Meta):
    service.suspend_organization(db, staff, organization_id, body.reason, meta)
    return Response(status_code=204)


@router.post("/organizations/{organization_id}/reactivate", status_code=204)
def reactivate(organization_id: uuid.UUID, body: ReasonIn, staff: Staff, db: DB, meta: Meta):
    service.reactivate_organization(db, staff, organization_id, body.reason, meta)
    return Response(status_code=204)


@router.put("/organizations/{organization_id}/members/{user_id}/role", status_code=204)
def member_role(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    body: RoleChangeIn,
    staff: Staff,
    db: DB,
    meta: Meta,
):
    service.change_member_role(db, staff, organization_id, user_id, body.role, body.reason, meta)
    return Response(status_code=204)


@router.post("/organizations/{organization_id}/extend-trial", response_model=AdminSubscription)
def extend_trial(organization_id: uuid.UUID, body: ExtendTrialIn, staff: Staff, db: DB, meta: Meta):
    return service.extend_trial(db, staff, organization_id, body.days, body.reason, meta)


@router.get("/users", response_model=AdminUserPage)
def users(
    staff: Staff,
    db: DB,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Page = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return service.list_users(db, q=q, limit=limit, offset=offset)


@router.get("/users/{user_id}", response_model=AdminUserDetail)
def user(user_id: uuid.UUID, staff: Staff, db: DB, meta: Meta):
    return service.user_detail(db, staff, user_id, meta)


@router.post("/users/{user_id}/disable", status_code=204)
def disable(user_id: uuid.UUID, body: ReasonIn, staff: Staff, db: DB, meta: Meta):
    service.disable_user(db, staff, user_id, body.reason, meta)
    return Response(status_code=204)


@router.post("/users/{user_id}/enable", status_code=204)
def enable(user_id: uuid.UUID, body: ReasonIn, staff: Staff, db: DB, meta: Meta):
    service.enable_user(db, staff, user_id, body.reason, meta)
    return Response(status_code=204)


@router.get("/audit-log", response_model=AdminAuditPage)
def audit_log(
    staff: Staff,
    db: DB,
    organization_id: uuid.UUID | None = None,
    actor_email: Annotated[str | None, Query(max_length=320)] = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    before: uuid.UUID | None = None,
    limit: Page = 50,
):
    """Everything recorded, across every business, newest first."""
    return service.audit_trail(
        db, organization_id=organization_id, actor_email=actor_email, action=action,
        since=since, until=until, before=before, limit=limit,
    )  # fmt: skip


@router.get("/plans", response_model=list[AdminPlan])
def plans(staff: Staff, db: DB):
    return service.list_plans(db)


@router.patch("/plans/{code}", response_model=AdminPlan)
def change_plan(code: str, body: PlanChangeIn, staff: Staff, db: DB, meta: Meta):
    changes = body.model_dump(exclude_unset=True, exclude={"reason"})
    return service.change_plan(db, staff, code, changes, body.reason, meta)


@router.put("/plans/{code}/features/{feature}", response_model=AdminPlan)
def change_feature(code: str, feature: str, body: EntitlementIn, staff: Staff, db: DB, meta: Meta):
    return service.change_entitlement(
        db, staff, code, feature, body.enabled, body.limit, body.reason, meta
    )
