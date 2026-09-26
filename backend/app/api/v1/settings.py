from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DB, CurrentTenant, Meta, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.settings import (
    NotificationPreferenceOut,
    NotificationPreferencesPatch,
    SettingsOut,
    SettingsPatch,
)
from app.services.settings import (
    effective_preferences,
    get_business_settings,
    update_preferences,
    update_settings,
)

router = APIRouter(prefix="/organizations/{organization_id}", tags=["settings"])

OrgManager = Annotated[Tenant, Depends(require_permission(Perm.ORG_MANAGE))]


@router.get("/settings", response_model=SettingsOut)
def read_settings(tenant: CurrentTenant, db: DB):
    """UK defaults until the business changes something."""
    return get_business_settings(db)


@router.patch("/settings", response_model=SettingsOut)
def change_settings(body: SettingsPatch, tenant: OrgManager, db: DB, meta: Meta):
    return update_settings(db, tenant, body, meta)


@router.get("/notification-preferences", response_model=list[NotificationPreferenceOut])
def my_preferences(tenant: CurrentTenant, db: DB):
    """Your own choices in this business; every member manages their own."""
    return effective_preferences(db, tenant.user.id)


@router.patch("/notification-preferences", response_model=list[NotificationPreferenceOut])
def change_my_preferences(body: NotificationPreferencesPatch, tenant: CurrentTenant, db: DB):
    return update_preferences(db, tenant.user.id, body)
