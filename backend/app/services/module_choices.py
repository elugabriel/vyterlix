"""Which parts of Vyterlix a business shows (client feedback 15/11/2026).

What a business sells and what data it has decide which parts can be shown (app/modules/rules.py).
On top of that the owner can hide a part they do not want to look at. A part the owner has hidden
disappears from Key figures, Today and the health score; nothing is deleted, and switching it back
on brings it straight back.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models.business import BusinessModule, BusinessProfile
from app.modules.rules import BY_CODE, MODULES, applies
from app.schemas.kpi import ModuleStateOut
from app.services.audit import AuditAction, record_audit

# What a part waits for, when it is for this business but has nothing to show yet.
NEEDS = {
    "suppliers": "It needs costs that say who you bought from, typed in or imported.",
    "demand": "It needs a few months of sales or appointments to compare.",
}


NEEDS_DEFAULT = "It needs information you have not added yet, such as appointments or hours worked."


def switched_off(db: Session) -> set[str]:
    """The modules the owner has hidden."""
    return set(
        db.scalars(select(BusinessModule.module_code).where(BusinessModule.enabled.is_(False)))
    )


def _reason(code: str, status: str, sells: str | None) -> str | None:
    module = BY_CODE[code]
    if status == "not_for_you":
        wants = "sell products" if module.needs == "products" else "provide a service"
        return f"This is for businesses that {wants}."
    if status == "no_figures":
        if not module.kpis:
            return "The figures for this are still being built."
        return NEEDS.get(code, NEEDS_DEFAULT)
    if status == "switched_off":
        return "You have hidden this. Switch it on to see it again."
    return None


def list_states(db: Session) -> list[ModuleStateOut]:
    """All twelve parts, and where each stands for this business."""
    from app.services import kpi  # here, because the figures list asks this module what is hidden

    sells = db.scalar(select(BusinessProfile.sells))
    hidden = switched_off(db)
    codes = {k.code for k in kpi.list_kpis(db, "month", respect_choices=False).kpis}
    out = []
    for module in MODULES:
        has_figures = any(code in codes for code in module.kpis)
        if not applies(module, sells):
            status = "not_for_you"
        elif not has_figures:
            status = "no_figures"
        elif module.code in hidden:
            status = "switched_off"
        else:
            status = "shown"
        out.append(
            ModuleStateOut(
                code=module.code,
                name=module.name,
                summary=module.summary,
                enabled=module.code not in hidden,
                status=status,
                reason=_reason(module.code, status, sells),
                can_switch=status in ("shown", "switched_off"),
            )
        )
    return out


def set_choice(db: Session, tenant, code: str, enabled: bool, meta) -> ModuleStateOut:
    """Show or hide one part. The health score is worked out again, so it follows the choice."""
    if code not in BY_CODE:
        raise NotFoundError("There is no such part.", code="module_not_found")
    row = db.scalars(select(BusinessModule).where(BusinessModule.module_code == code)).first()
    if row is None:
        row = BusinessModule(module_code=code, enabled=enabled)
        db.add(row)
    elif row.enabled == enabled:
        return next(s for s in list_states(db) if s.code == code)
    else:
        row.enabled = enabled
    db.flush()
    record_audit(
        db,
        AuditAction.MODULE_CHOICE_CHANGED,
        actor_user_id=tenant.user.id,
        organization_id=tenant.organization_id,
        target_type="business_modules",
        target_id=row.id if isinstance(row.id, uuid.UUID) else None,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        details={"module": code, "enabled": enabled},
    )
    db.commit()
    from app.services import health

    health.calculate(db, tenant)  # the score is judged only on the parts that are shown
    return next(s for s in list_states(db) if s.code == code)
