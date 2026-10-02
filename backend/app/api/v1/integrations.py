import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import DB, Meta, Tenant, require_permission
from app.core.permissions import Perm
from app.integrations.registry import available_providers
from app.schemas.integrations import (
    CallbackIn,
    ConnectIn,
    ConnectOut,
    IntegrationOut,
    ProviderOut,
    SyncOut,
)
from app.schemas.jobs import JobOut
from app.services import job_handlers  # noqa: F401  (registers the handlers)
from app.services.integrations import (
    complete_connect,
    disconnect,
    get_integration,
    integration_out,
    list_integrations,
    list_syncs,
    request_sync,
    start_connect,
)

router = APIRouter(prefix="/organizations/{organization_id}/integrations", tags=["integrations"])

# Seeing connections and asking for a sync need data.manage; connecting and disconnecting hand
# access to another system, so they need integrations.manage. Both belong to the Owner only.
DataManager = Annotated[Tenant, Depends(require_permission(Perm.DATA_MANAGE))]
IntegrationManager = Annotated[Tenant, Depends(require_permission(Perm.INTEGRATIONS_MANAGE))]


@router.get("/providers", response_model=list[ProviderOut])
def providers(tenant: DataManager):
    """What can be connected, and what we would read."""
    return [
        ProviderOut(key=p.key, label=p.label, permissions=list(p.permissions))
        for p in available_providers()
    ]


@router.get("", response_model=list[IntegrationOut])
def list_(tenant: DataManager, db: DB):
    return list_integrations(db)


@router.post("/connect", response_model=ConnectOut)
def connect(body: ConnectIn, tenant: IntegrationManager, db: DB):
    """Step 1: returns the address of the provider's approval page to send the person to."""
    return start_connect(db, tenant, body.provider, body.integration_id)


@router.post("/callback", response_model=IntegrationOut)
def callback(body: CallbackIn, tenant: IntegrationManager, db: DB, meta: Meta):
    """Step 2: the page the provider sent the person back to passes on `state` and `code`."""
    return complete_connect(db, tenant, body.state, body.code, meta)


@router.get("/{integration_id}", response_model=IntegrationOut)
def get(integration_id: uuid.UUID, tenant: DataManager, db: DB):
    return integration_out(get_integration(db, integration_id))


@router.post("/{integration_id}/sync", status_code=status.HTTP_202_ACCEPTED, response_model=JobOut)
def sync(integration_id: uuid.UUID, tenant: DataManager, db: DB, meta: Meta):
    """Fetch the latest from the other system, in the background. Poll GET .../jobs/{id}."""
    return request_sync(db, tenant, integration_id, meta)


@router.get("/{integration_id}/syncs", response_model=list[SyncOut])
def syncs(integration_id: uuid.UUID, tenant: DataManager, db: DB):
    """How each sync went, newest first."""
    return list_syncs(db, integration_id)


@router.post("/{integration_id}/disconnect", response_model=IntegrationOut)
def disconnect_(integration_id: uuid.UUID, tenant: IntegrationManager, db: DB, meta: Meta):
    """Forget our access. What was already imported stays."""
    return disconnect(db, tenant, integration_id, meta)
