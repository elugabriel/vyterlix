from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.api.deps import DB, Meta, Tenant, require_permission
from app.billing.providers import PaymentProvider
from app.core.permissions import Perm
from app.schemas.billing import (
    BillingOut,
    ChangeIn,
    ChangeOut,
    CheckoutIn,
    CheckoutOut,
    InvoiceOut,
    PlanOut,
    SandboxCompleteIn,
    WebhookOut,
)
from app.services import billing as service

router = APIRouter(prefix="/organizations/{organization_id}/billing", tags=["billing"])
webhook_router = APIRouter(prefix="/billing", tags=["billing"])

Viewer = Annotated[Tenant, Depends(require_permission(Perm.INSIGHTS_VIEW))]
Owner = Annotated[Tenant, Depends(require_permission(Perm.BILLING_MANAGE))]


def get_payment_providers() -> dict[str, PaymentProvider]:
    """The ways of paying set up on this installation (tests replace this)."""
    return service.configured_providers()


Providers = Annotated[dict[str, PaymentProvider], Depends(get_payment_providers)]


@router.get("/plans", response_model=list[PlanOut])
def plans(tenant: Viewer, db: DB):
    """What can be bought and what each plan includes (prices exclude VAT)."""
    return service.list_plans(db, tenant.organization_id)


@router.get("", response_model=BillingOut)
def billing(tenant: Viewer, db: DB, providers: Providers):
    """The business's plan and state, in words, and what it uses of what the plan allows."""
    return service.summary(db, tenant, providers)


@router.post("/checkout", response_model=CheckoutOut)
def checkout(body: CheckoutIn, tenant: Owner, db: DB, meta: Meta, providers: Providers):
    """Start paying for a plan: returns the page to go to."""
    return service.start_checkout(
        db, tenant, body.plan_code, body.interval, body.provider, providers, meta
    )


@router.post("/sandbox/complete", response_model=BillingOut)
def sandbox_complete(
    body: SandboxCompleteIn, tenant: Owner, db: DB, meta: Meta, providers: Providers
):
    """Pay a sandbox checkout (development only; no money moves)."""
    return service.complete_sandbox(db, tenant, body.token, providers, meta)


@router.post("/change", response_model=ChangeOut)
def change(body: ChangeIn, tenant: Owner, db: DB, meta: Meta, providers: Providers):
    """Move to another plan: a dearer one now, a cheaper one when the period ends."""
    return service.change_plan(db, tenant, body.plan_code, body.interval, providers, meta)


@router.post("/cancel", response_model=BillingOut)
def cancel(tenant: Owner, db: DB, meta: Meta, providers: Providers):
    """Stop the plan renewing. It carries on until the period already paid for ends."""
    return service.cancel(db, tenant, providers, meta)


@router.post("/resume", response_model=BillingOut)
def resume(tenant: Owner, db: DB, meta: Meta, providers: Providers):
    """Change your mind about cancelling, before the period ends."""
    return service.resume(db, tenant, providers, meta)


@router.get("/invoices", response_model=list[InvoiceOut])
def invoices(tenant: Owner, db: DB):
    """What the business has been charged."""
    return service.list_invoices(db)


@webhook_router.post("/webhooks/{provider}", response_model=WebhookOut)
async def webhook(provider: str, request: Request, db: DB, providers: Providers):
    """Messages from a payment provider. Nothing is believed without a genuine signature."""
    body = await request.body()
    return await run_in_threadpool(
        service.handle_webhook, db, provider, dict(request.headers), body, providers
    )
