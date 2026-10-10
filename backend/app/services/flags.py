"""Feature flags (Phase 17): a switch for a feature that staff can turn on for everyone, or for
chosen businesses only. Nothing here decides what a flag does; code asks `enabled()`."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.admin import FeatureFlag


def resolve(flag: FeatureFlag, organization_id: uuid.UUID) -> bool:
    """A business's own entry wins; otherwise the flag's general setting."""
    return bool((flag.org_overrides or {}).get(str(organization_id), flag.enabled))


def enabled(db: Session, key: str, organization_id: uuid.UUID) -> bool:
    """Whether a feature is on for a business. A flag nobody has created is off."""
    flag = db.scalars(select(FeatureFlag).where(FeatureFlag.key == key)).first()
    return flag is not None and resolve(flag, organization_id)


def flags_for(db: Session, organization_id: uuid.UUID) -> dict[str, bool]:
    return {
        f.key: resolve(f, organization_id)
        for f in db.scalars(select(FeatureFlag).order_by(FeatureFlag.key))
    }
