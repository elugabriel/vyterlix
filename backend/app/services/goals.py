"""Business goals. The session must be scoped to the organisation (CurrentTenant)."""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFoundError, PermissionDeniedError
from app.core.permissions import KpiCategory, Perm
from app.models.business import BusinessGoal
from app.models.identity import User
from app.schemas.goals import GoalCreate, GoalOut, GoalPatch, _check_amount
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta

# Which KPI area each goal type belongs to: a Manager may only manage goals in their remit.
GOAL_CATEGORY: dict[str, KpiCategory] = {
    "increase_revenue": KpiCategory.SALES,
    "improve_margin": KpiCategory.FINANCIAL,
    "reduce_costs": KpiCategory.FINANCIAL,
    "improve_cash_flow": KpiCategory.FINANCIAL,
    "grow_customers": KpiCategory.CUSTOMER,
    "improve_retention": KpiCategory.CUSTOMER,
    "reduce_stock_problems": KpiCategory.INVENTORY,
    "other": KpiCategory.OPERATIONAL,
}


def _require_remit(tenant, goal_type: str) -> None:
    category = GOAL_CATEGORY[goal_type]
    if not tenant.can(Perm.GOALS_MANAGE, category):
        raise PermissionDeniedError(
            f"This goal is about {category.value}, which is outside your remit",
            code="outside_remit",
            details={"category": category.value},
        )


def _out(db: Session, goal: BusinessGoal) -> GoalOut:
    creator = db.get(User, goal.created_by_user_id) if goal.created_by_user_id else None
    return GoalOut(
        id=goal.id,
        title=goal.title,
        goal_type=goal.goal_type,
        category=GOAL_CATEGORY[goal.goal_type].value,
        kpi_code=goal.kpi_code,
        baseline_value=goal.baseline_value,
        target_value=goal.target_value,
        target_unit=goal.target_unit,
        target_date=goal.target_date,
        priority=goal.priority,
        status=goal.status,
        notes=goal.notes,
        created_by=creator.full_name if creator else None,
        created_at=goal.created_at,
        updated_at=goal.updated_at,
    )


def _get(db: Session, goal_id: uuid.UUID) -> BusinessGoal:
    goal = db.scalar(select(BusinessGoal).where(BusinessGoal.id == goal_id))  # tenant-scoped
    if goal is None:
        raise NotFoundError("Goal not found", code="goal_not_found")
    return goal


def list_goals(db: Session, status: str | None = None) -> list[GoalOut]:
    """Active goals first, then by priority (1 = highest) and nearest target date."""
    stmt = select(BusinessGoal)
    if status is not None:
        stmt = stmt.where(BusinessGoal.status == status)
    stmt = stmt.order_by(
        case((BusinessGoal.status == "active", 0), else_=1),
        BusinessGoal.priority,
        BusinessGoal.target_date.asc().nulls_last(),
        BusinessGoal.created_at,
    )
    return [_out(db, g) for g in db.scalars(stmt)]


def get_goal(db: Session, goal_id: uuid.UUID) -> GoalOut:
    return _out(db, _get(db, goal_id))


def create_goal(db: Session, tenant, body: GoalCreate, meta: RequestMeta) -> GoalOut:
    _require_remit(tenant, body.goal_type)
    goal = BusinessGoal(**body.model_dump(), created_by_user_id=tenant.user.id)
    db.add(goal)
    db.flush()
    record_audit(
        db,
        AuditAction.GOAL_CREATED,
        actor_user_id=tenant.user.id,
        organization_id=tenant.organization_id,
        target_type="goal",
        target_id=goal.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        details={"goal_type": goal.goal_type},
    )
    db.commit()
    db.refresh(goal)
    return _out(db, goal)


def update_goal(
    db: Session, tenant, goal_id: uuid.UUID, body: GoalPatch, meta: RequestMeta
) -> GoalOut:
    goal = _get(db, goal_id)
    _require_remit(tenant, goal.goal_type)  # may manage this goal at all?
    changes: dict[str, Any] = body.model_dump(include=body.model_fields_set)
    if "goal_type" in changes:
        _require_remit(tenant, changes["goal_type"])  # ...and may move it to the new area?

    # Re-check the target/unit pairing on the merged result.
    value = changes.get("target_value", goal.target_value)
    unit = changes.get("target_unit", goal.target_unit)
    baseline = changes.get("baseline_value", goal.baseline_value)
    if (value is None) != (unit is None):
        raise AppError(
            "Give both a target value and its unit (gbp, percent or count)",
            code="target_needs_unit",
            status_code=422,
        )
    try:
        _check_amount(Decimal(value) if value is not None else None, unit, "The target")
        _check_amount(
            Decimal(baseline) if baseline is not None else None, unit, "The starting value"
        )
    except ValueError as exc:
        raise AppError(str(exc), code="invalid_amount", status_code=422) from exc

    changed = sorted(k for k, v in changes.items() if getattr(goal, k) != v)
    details: dict[str, Any] = {"fields": changed}
    if "status" in changed:
        details["status"] = {"from": goal.status, "to": changes["status"]}
    for name in changed:
        setattr(goal, name, changes[name])
    if changed:
        record_audit(
            db,
            AuditAction.GOAL_UPDATED,
            actor_user_id=tenant.user.id,
            organization_id=tenant.organization_id,
            target_type="goal",
            target_id=goal.id,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
            details=details,
        )
        db.commit()
        db.refresh(goal)
    return _out(db, goal)
