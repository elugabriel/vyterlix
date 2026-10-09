# ruff: noqa: E501
"""Billing (Phase 16): what a business has bought, what that lets it do, and keeping it in step with the
payment provider.

A business starts on a free trial (lazily, the first time anything needs to know). Paying moves it to a
plan; the provider then tells us about every renewal, failure and cancellation, and we only believe
those messages once their signature has checked out, and never act on one twice. The few gated
features (team members, connections, scheduled reports, the AI assistant) are checked here; nothing a
business has already put in is ever held back.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.billing import rules
from app.billing.providers import (
    PaymentProvider,
    PaystackProvider,
    ProviderError,
    ProviderEvent,
    SandboxProvider,
    SignatureError,
    StripeProvider,
)
from app.core.config import get_settings
from app.core.errors import AppError, ConflictError, NotFoundError, PlanLimitError
from app.core.permissions import Perm
from app.db.tenant import ACROSS_TENANTS, tenant_scope
from app.integrations.base import utcnow
from app.models.billing import (
    BillingEvent,
    FeatureEntitlement,
    Invoice,
    Plan,
    Subscription,
    SubscriptionItem,
)
from app.models.identity import OrganizationInvitation, OrganizationUser
from app.models.integrations import Integration
from app.models.reports import ReportSchedule
from app.schemas.billing import (
    BillingOut,
    ChangeOut,
    CheckoutOut,
    FeatureOut,
    InvoiceOut,
    PlanOut,
    SubscriptionOut,
    WebhookOut,
)
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta

logger = logging.getLogger("vyterlix.billing")

PROVIDER_ORDER = ("stripe", "paystack", "sandbox")  # the first one set up is the default
STATUS_LABEL = {
    "trialing": "Free trial",
    "active": "Active",
    "past_due": "Payment overdue",
    "canceled": "Cancelled",
    "expired": "Ended",
}
MAX_ROLLS = 24


class BillingProblem(Exception):
    """A message from the provider that could not be applied (it is recorded as failed, not retried)."""


# --- the ways of paying ------------------------------------------------------------------------------------


def configured_providers() -> dict[str, PaymentProvider]:
    """The providers this installation can use: a real one only if its keys are set, the sandbox only
    outside production."""
    settings = get_settings()
    found: dict[str, PaymentProvider] = {}
    if settings.stripe_secret_key is not None and settings.stripe_webhook_secret is not None:
        found["stripe"] = StripeProvider(
            settings.stripe_secret_key.get_secret_value(),
            settings.stripe_webhook_secret.get_secret_value(),
        )
    if settings.paystack_secret_key is not None:
        found["paystack"] = PaystackProvider(settings.paystack_secret_key.get_secret_value())
    if settings.env != "prod":
        found["sandbox"] = SandboxProvider(settings.jwt_secret)
    return {k: found[k] for k in PROVIDER_ORDER if k in found}


def _provider(providers: dict[str, PaymentProvider], key: str | None) -> PaymentProvider:
    key = key or next(iter(providers), None)
    if key is None or key not in providers:
        raise AppError(
            "That way of paying is not available.", code="provider_unavailable", status_code=422
        )
    return providers[key]


# --- plans ------------------------------------------------------------------------------------------------------


def plan_by_code(db: Session, code: str | None) -> Plan | None:
    if not code:
        return None
    return db.scalars(select(Plan).where(Plan.code == code)).first()


def _rows(db: Session, plan_id: uuid.UUID) -> dict[str, FeatureEntitlement]:
    return {
        r.feature: r
        for r in db.scalars(select(FeatureEntitlement).where(FeatureEntitlement.plan_id == plan_id))
    }


def _feature_text(feature: str, row: FeatureEntitlement | None) -> str:
    if row is None or not row.enabled:
        return "Not included"
    if feature == "ai_assistant":
        return "Included"
    return "No limit" if row.limit is None else f"Up to {row.limit}"


def _feature_out(
    feature: str, row: FeatureEntitlement | None, allow: rules.Allowance, used: int | None
) -> FeatureOut:
    return FeatureOut(
        feature=feature,
        label=rules.FEATURE_LABEL[feature],
        enabled=allow.enabled,
        limit=allow.limit,
        used=used,
        text=_feature_text(feature, row),
    )


def plan_out(db: Session, plan: Plan, current_code: str | None) -> PlanOut:
    rows = _rows(db, plan.id)
    saving = rules.year_saving(plan)
    return PlanOut(
        code=plan.code, name=plan.name, description=plan.description, self_serve=plan.self_serve,
        price_month=None if plan.price_month_pence is None else rules.money(plan.price_month_pence, plan.currency),
        price_year=None if plan.price_year_pence is None else rules.money(plan.price_year_pence, plan.currency),
        price_month_pence=plan.price_month_pence, price_year_pence=plan.price_year_pence,
        year_saving=None if saving is None or saving <= 0 else rules.money(saving, plan.currency),
        vat_note=f"Prices exclude VAT ({int(rules.VAT_RATE * 100)}%), which is added at checkout." if plan.self_serve else "Talk to us about a plan to suit your organisation.",
        features=[_feature_out(f, rows.get(f), rules.Allowance(bool(rows.get(f) and rows[f].enabled), rows[f].limit if rows.get(f) else 0), None) for f in rules.FEATURE_NOUN],
        current=plan.code == current_code,
    )  # fmt: skip


def list_plans(db: Session, org_id: uuid.UUID, now: datetime | None = None) -> list[PlanOut]:
    standing_ = standing(db, org_id, now or utcnow())
    plans = db.scalars(
        select(Plan)
        .where(Plan.is_active.is_(True), Plan.is_public.is_(True))
        .order_by(Plan.sort_order)
    ).all()
    return [
        plan_out(
            db, p, standing_.plan.code if standing_.effective in ("active", "past_due") else None
        )
        for p in plans
    ]


# --- the business's subscription -------------------------------------------------------------------------------------


@dataclass
class Standing:
    subscription: Subscription
    plan: Plan  # the plan whose allowances apply right now
    effective: str
    rows: dict[str, FeatureEntitlement]


def ensure_subscription(db: Session, org_id: uuid.UUID, now: datetime) -> Subscription:
    """The business's subscription, starting its free trial the first time anything needs to know."""
    sub = db.scalars(select(Subscription)).first()
    if sub is not None:
        return sub
    trial = plan_by_code(db, rules.TRIAL_PLAN)
    sub = Subscription(
        organization_id=org_id, plan_id=trial.id, status="trialing", interval="month", provider="none",
        trial_ends_at=now + timedelta(days=get_settings().trial_days),
    )  # fmt: skip
    try:
        with db.begin_nested():
            db.add(sub)
            db.flush()
    except IntegrityError:  # somebody else started it a moment ago
        return db.scalars(select(Subscription)).one()
    return sub


def standing(db: Session, org_id: uuid.UUID, now: datetime) -> Standing:
    sub = ensure_subscription(db, org_id, now)
    effective = rules.effective_status(
        sub.status,
        trial_ends_at=sub.trial_ends_at,
        period_end_at=sub.current_period_end,
        cancel_at_period_end=sub.cancel_at_period_end,
        now=now,
    )
    plan = (
        plan_by_code(db, rules.TRIAL_PLAN)
        if sub.status == "trialing"
        else db.get(Plan, sub.plan_id)
    )
    return Standing(sub, plan, effective, _rows(db, plan.id))


# --- what is in use ---------------------------------------------------------------------------------------------------------


def used(db: Session, feature: str, now: datetime | None = None) -> int:
    now = now or utcnow()
    if feature == "members":
        members = (
            db.scalar(
                select(func.count())
                .select_from(OrganizationUser)
                .where(OrganizationUser.status == "active")
            )
            or 0
        )
        invited = db.scalar(
            select(func.count()).select_from(OrganizationInvitation).where(
                OrganizationInvitation.accepted_at.is_(None), OrganizationInvitation.revoked_at.is_(None), OrganizationInvitation.expires_at > now
            )
        ) or 0  # fmt: skip
        return members + invited
    if feature == "integrations":
        return (
            db.scalar(
                select(func.count())
                .select_from(Integration)
                .where(Integration.status != "disconnected")
            )
            or 0
        )
    if feature == "scheduled_reports":
        return db.scalar(select(func.count()).select_from(ReportSchedule)) or 0
    return 0


def check(db: Session, org_id: uuid.UUID, feature: str, *, now: datetime | None = None) -> None:
    """Refuse (with a plain message) if the business's plan does not let it add one more of this."""
    now = now or utcnow()
    st = standing(db, org_id, now)
    allow = rules.allowance(st.rows.get(feature), st.effective)
    count = used(db, feature, now)
    decision = rules.decide(feature, allow, count, st.plan.name, st.effective)
    if not decision.ok:
        raise PlanLimitError(
            decision.message,
            details={
                "feature": feature,
                "limit": allow.limit,
                "used": count,
                "plan": st.plan.code,
                "status": st.effective,
            },
        )


# --- what it all says, in words --------------------------------------------------------------------------------------------------


def _message(sub: Subscription, plan: Plan, effective: str, now: datetime) -> str:
    interval = "yearly" if sub.interval == "year" else "monthly"
    if effective == "trialing":
        days = rules.trial_days_left(sub.trial_ends_at, now)
        return f"You are on a free trial of the {plan.name} plan: {days} day{'s' if days != 1 else ''} left. Choose a plan before it ends to keep adding to your business."
    if effective == "active":
        end = (
            f"{sub.current_period_end:%d/%m/%Y}"
            if sub.current_period_end
            else "the end of this period"
        )
        if sub.cancel_at_period_end:
            return f"You are on the {plan.name} plan, paid {interval}. It will end on {end} and will not renew."
        text = f"You are on the {plan.name} plan, paid {interval}. It renews on {end}."
        return text
    if effective == "past_due":
        return (
            "Your last payment did not go through. Update your payment details to keep your plan."
        )
    if effective == "canceled":
        return "Your subscription has ended. Choose a plan to carry on adding to your business. Everything you already have stays readable."
    return "Your free trial has ended. Choose a plan to carry on adding to your business. Everything you already have stays readable."


def summary(
    db: Session, tenant, providers: dict[str, PaymentProvider], now: datetime | None = None
) -> BillingOut:
    now = now or utcnow()
    st = standing(db, tenant.organization_id, now)
    sub = st.subscription
    scheduled = db.get(Plan, sub.scheduled_plan_id) if sub.scheduled_plan_id else None
    features = [
        _feature_out(
            f,
            st.rows.get(f),
            rules.allowance(st.rows.get(f), st.effective),
            used(db, f, now) if f != "ai_assistant" else None,
        )
        for f in rules.FEATURE_NOUN
    ]
    return BillingOut(
        subscription=SubscriptionOut(
            plan_code=st.plan.code, plan_name=st.plan.name, status=st.effective, status_label=STATUS_LABEL[st.effective], interval=sub.interval,
            provider=sub.provider, trial_ends_at=sub.trial_ends_at if st.effective == "trialing" else None,
            trial_days_left=rules.trial_days_left(sub.trial_ends_at, now) if st.effective == "trialing" else None,
            current_period_start=sub.current_period_start, current_period_end=sub.current_period_end, cancel_at_period_end=sub.cancel_at_period_end,
            scheduled_plan_code=scheduled.code if scheduled else None, scheduled_plan_name=scheduled.name if scheduled else None,
            message=_message(sub, st.plan, st.effective, now),
        ),
        features=features, providers=list(providers), can_manage=tenant.has(Perm.BILLING_MANAGE),
    )  # fmt: skip


# --- paying -------------------------------------------------------------------------------------------------------------------------


def _links(org_id: uuid.UUID) -> tuple[str, str]:
    base = f"{get_settings().frontend_base_url}/billing.html?org={org_id}"
    return base + "&paid=1", base + "&cancelled=1"


def _buyable(db: Session, code: str, interval: str) -> Plan:
    plan = plan_by_code(db, code)
    if plan is None or not plan.is_active:
        raise NotFoundError("That plan was not found", code="plan_not_found")
    if not plan.self_serve:
        raise AppError(
            "That plan is arranged with us directly. Please get in touch.",
            code="not_for_sale",
            status_code=422,
        )
    if rules.price(plan, interval) is None:
        raise AppError(
            f"The {plan.name} plan is not sold {'yearly' if interval == 'year' else 'monthly'}.",
            code="not_for_sale",
            status_code=422,
        )
    return plan


def start_checkout(
    db: Session,
    tenant,
    plan_code: str,
    interval: str,
    provider_key: str | None,
    providers: dict[str, PaymentProvider],
    meta: RequestMeta,
    now: datetime | None = None,
) -> CheckoutOut:
    now = now or utcnow()
    plan = _buyable(db, plan_code, interval)
    st = standing(db, tenant.organization_id, now)
    if st.effective in ("active", "past_due") and st.subscription.provider != "none":
        raise ConflictError(
            "You already have a plan. Use change plan to move to another.", code="has_a_plan"
        )
    provider = _provider(providers, provider_key)
    ok_url, no_url = _links(tenant.organization_id)
    try:
        session = provider.checkout(
            organization_id=tenant.organization_id,
            email=tenant.user.email,
            plan=plan,
            interval=interval,
            success_url=ok_url,
            cancel_url=no_url,
        )
    except ProviderError as exc:
        raise AppError(str(exc), code="provider_error", status_code=502) from exc
    record_audit(
        db, AuditAction.BILLING_CHECKOUT_STARTED, actor_user_id=tenant.user.id, organization_id=tenant.organization_id, target_type="subscription",
        target_id=st.subscription.id, ip_address=meta.ip_address, user_agent=meta.user_agent, details={"plan": plan.code, "interval": interval, "provider": provider.key},
    )  # fmt: skip
    db.commit()
    return CheckoutOut(url=session.url)


def complete_sandbox(
    db: Session,
    tenant,
    token: str,
    providers: dict[str, PaymentProvider],
    meta: RequestMeta,
    now: datetime | None = None,
) -> BillingOut:
    """Pay a sandbox checkout (development only): it makes the same message the real provider would."""
    now = now or utcnow()
    sandbox = providers.get("sandbox")
    if sandbox is None:
        raise NotFoundError("Not found", code="not_found")
    try:
        event = sandbox.complete(token, now)
    except ProviderError as exc:
        raise AppError(str(exc), code="bad_payment_link", status_code=422) from exc
    if event.organization_id != tenant.organization_id:
        raise AppError(
            "That payment link is for a different business.", code="wrong_business", status_code=403
        )
    apply_event(db, tenant.organization_id, "sandbox", event, now)
    db.commit()
    return summary(db, tenant, providers, now)


# --- messages from the provider --------------------------------------------------------------------------------------------------------


def _lookup_org(db: Session, provider: str, event: ProviderEvent) -> uuid.UUID | None:
    if event.organization_id is not None:
        return event.organization_id
    for column, value in (
        ("provider_subscription_id", event.subscription_id),
        ("provider_customer_id", event.customer_id),
    ):
        if value:
            row = db.scalars(
                select(Subscription)
                .where(Subscription.provider == provider, getattr(Subscription, column) == value)
                .execution_options(**ACROSS_TENANTS)
            ).first()
            if row is not None:
                return row.organization_id
    return None


def handle_webhook(
    db: Session,
    provider_key: str,
    headers,
    body: bytes,
    providers: dict[str, PaymentProvider],
    now: datetime | None = None,
) -> WebhookOut:
    """A message from a provider. It is believed only if its signature is genuine."""
    now = now or utcnow()
    provider = providers.get(provider_key)
    if provider is None:
        raise NotFoundError("Not found", code="not_found")
    try:
        events = provider.parse_webhook(headers, body, now)
    except SignatureError as exc:
        raise AppError(
            "That message was not signed correctly.", code="bad_signature", status_code=400
        ) from exc
    except (ValueError, KeyError) as exc:
        raise AppError(
            "That message could not be read.", code="bad_message", status_code=400
        ) from exc
    counts = {"processed": 0, "ignored": 0, "duplicate": 0, "unmatched": 0, "failed": 0}
    for event in events:
        org = _lookup_org(db, provider.key, event)
        if org is None:
            counts["unmatched"] += 1
            logger.warning("Billing message %s (%s) matches no business", event.id, event.type)
            continue
        with tenant_scope(db, org):
            outcome = apply_event(db, org, provider.key, event, now)
            counts[outcome] += 1
            db.commit()
        if event.replaced_subscription_id and not provider.changes_in_place:
            try:  # the old subscription must stop charging now that a new one has begun
                provider.set_cancel(subscription_id=event.replaced_subscription_id, cancel=True)
            except ProviderError:
                logger.error("Could not stop the replaced subscription", exc_info=True)
    return WebhookOut(
        received=len(events),
        processed=counts["processed"],
        ignored=counts["ignored"],
        duplicates=counts["duplicate"],
        unmatched=counts["unmatched"],
        failed=counts["failed"],
    )


def apply_event(
    db: Session, org_id: uuid.UUID, provider: str, event: ProviderEvent, now: datetime
) -> str:
    """Act on one message, once. Returns processed, ignored or duplicate."""
    if db.scalars(
        select(BillingEvent).where(
            BillingEvent.provider == provider, BillingEvent.event_id == event.id
        )
    ).first():
        return "duplicate"
    status, error = "processed", None
    if event.type == "ignored":
        status = "ignored"
    else:
        try:
            with db.begin_nested():
                HANDLERS[event.type](db, org_id, provider, event, now)
        except BillingProblem as exc:
            status, error = "failed", str(exc)
            logger.warning("Could not apply billing message %s: %s", event.id, exc)
    db.add(
        BillingEvent(
            organization_id=org_id,
            provider=provider,
            event_id=event.id,
            type=event.type,
            status=status,
            error=error,
            detail={"note": event.note} if event.note else {},
            received_at=now,
        )
    )
    db.flush()
    return status


def _plan_for_event(db: Session, provider: str, event: ProviderEvent) -> Plan | None:
    plan = plan_by_code(db, event.plan_code)
    if plan is not None or not event.plan_ref:
        return plan
    for candidate in db.scalars(select(Plan)):
        refs = (candidate.provider_prices or {}).get(provider, {})
        if event.plan_ref in refs.values():
            return candidate
    return None


def _set_items(db: Session, sub: Subscription, plan: Plan, interval: str) -> None:
    db.execute(
        delete(SubscriptionItem).where(
            SubscriptionItem.subscription_id == sub.id, SubscriptionItem.kind == "plan"
        )
    )
    db.add(
        SubscriptionItem(
            organization_id=sub.organization_id, subscription_id=sub.id, kind="plan", plan_id=plan.id, quantity=1,
            description=f"{plan.name} plan, billed {'yearly' if interval == 'year' else 'monthly'}", unit_amount_pence=rules.price(plan, interval) or 0,
        )
    )  # fmt: skip


def _audit_change(db: Session, org_id: uuid.UUID, sub: Subscription, what: str, **details) -> None:
    record_audit(
        db,
        AuditAction.BILLING_SUBSCRIPTION_CHANGED,
        organization_id=org_id,
        target_type="subscription",
        target_id=sub.id,
        details={"what": what, **details},
    )


def _checkout_completed(
    db: Session, org_id, provider: str, event: ProviderEvent, now: datetime
) -> None:
    plan = plan_by_code(db, event.plan_code)
    if plan is None or not plan.is_active or not plan.self_serve:
        raise BillingProblem(f"unknown plan {event.plan_code!r}")
    sub = ensure_subscription(db, org_id, now)
    interval = event.interval if event.interval in ("month", "year") else "month"
    if (
        sub.provider == provider
        and sub.provider_subscription_id
        and sub.provider_subscription_id != event.subscription_id
    ):
        event.replaced_subscription_id = sub.provider_subscription_id
    start = event.period_start or now
    sub.plan_id, sub.scheduled_plan_id, sub.scheduled_interval = plan.id, None, None
    sub.status, sub.interval, sub.provider = "active", interval, provider
    sub.provider_customer_id, sub.provider_subscription_id = (
        event.customer_id,
        event.subscription_id,
    )
    sub.trial_ends_at, sub.current_period_start = None, start
    sub.current_period_end = event.period_end or rules.period_end(start, interval)
    sub.cancel_at_period_end, sub.canceled_at = False, None
    _set_items(db, sub, plan, interval)
    if event.invoice:
        _upsert_invoice(db, org_id, provider, sub, event.invoice, now)
    _audit_change(
        db, org_id, sub, "subscribed", plan=plan.code, interval=interval, provider=provider
    )


def _subscription_updated(
    db: Session, org_id, provider: str, event: ProviderEvent, now: datetime
) -> None:
    sub = ensure_subscription(db, org_id, now)
    if sub.provider in ("none", provider) and not sub.provider_subscription_id:
        sub.provider, sub.provider_subscription_id = provider, event.subscription_id
        sub.provider_customer_id = sub.provider_customer_id or event.customer_id
    plan = _plan_for_event(db, provider, event)
    interval = event.interval if event.interval in ("month", "year") else sub.interval
    if plan is not None and plan.id != sub.plan_id and plan.id != sub.scheduled_plan_id:
        sub.plan_id, sub.interval = plan.id, interval
        _set_items(db, sub, plan, interval)
        _audit_change(db, org_id, sub, "plan_changed", plan=plan.code)
    if event.status in ("active", "trialing", "past_due", "canceled"):
        if event.status == "trialing" and sub.status == "active":
            pass  # a provider's own trial does not move a paying business back to a trial
        else:
            sub.status = event.status
    if event.period_start is not None:
        if sub.current_period_start is None or event.period_start > sub.current_period_start:
            _renewed(db, org_id, sub)
        sub.current_period_start = event.period_start
    if event.period_end is not None:
        sub.current_period_end = event.period_end
    if event.cancel_at_period_end is not None:
        sub.cancel_at_period_end = event.cancel_at_period_end
    sub.trial_ends_at = None if sub.status != "trialing" else sub.trial_ends_at


def _renewed(db: Session, org_id, sub: Subscription) -> None:
    """A new period has begun: a move to a smaller plan that was waiting for it happens now."""
    if sub.scheduled_plan_id:
        plan = db.get(Plan, sub.scheduled_plan_id)
        interval = sub.scheduled_interval or sub.interval
        sub.plan_id, sub.interval = plan.id, interval
        sub.scheduled_plan_id = sub.scheduled_interval = None
        _set_items(db, sub, plan, interval)
        _audit_change(
            db, org_id, sub, "scheduled_change_applied", plan=plan.code, interval=interval
        )


def _subscription_canceled(
    db: Session, org_id, provider: str, event: ProviderEvent, now: datetime
) -> None:
    sub = ensure_subscription(db, org_id, now)
    sub.status, sub.canceled_at, sub.cancel_at_period_end = "canceled", now, False
    sub.scheduled_plan_id = sub.scheduled_interval = None
    _audit_change(db, org_id, sub, "canceled")


def _date_of(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC).date()
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()


def _moment_of(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _upsert_invoice(
    db: Session, org_id, provider: str, sub: Subscription | None, data: dict, now: datetime
) -> Invoice:
    net, total = int(data.get("net_pence", 0)), int(data.get("total_pence", 0))
    net = min(net, total)
    row = db.scalars(
        select(Invoice).where(
            Invoice.provider == provider, Invoice.provider_invoice_id == str(data["id"])
        )
    ).first()
    if row is None:
        row = Invoice(
            organization_id=org_id,
            provider=provider,
            provider_invoice_id=str(data["id"]),
            issued_at=now,
        )
        db.add(row)
    row.subscription_id = sub.id if sub is not None else row.subscription_id
    row.number, row.status = data.get("number"), data.get("status", "open")
    row.currency = data.get("currency", "GBP")
    row.net_pence, row.vat_pence, row.total_pence = net, total - net, total
    row.period_start, row.period_end = (
        _date_of(data.get("period_start")),
        _date_of(data.get("period_end")),
    )
    row.paid_at = _moment_of(data.get("paid_at")) or (
        now if row.status == "paid" and row.paid_at is None else row.paid_at
    )
    url = data.get("hosted_url")
    row.hosted_url = (
        url if isinstance(url, str) and url.startswith("https://") else None
    )  # only a secure page of the provider's
    row.lines = [
        {
            "description": str(line.get("description", ""))[:200],
            "amount_pence": int(line.get("amount_pence", 0)),
        }
        for line in data.get("lines", [])
    ]
    db.flush()
    return row


def _invoice_updated(
    db: Session, org_id, provider: str, event: ProviderEvent, now: datetime
) -> None:
    sub = ensure_subscription(db, org_id, now)
    invoice = _upsert_invoice(db, org_id, provider, sub, event.invoice or {}, now)
    if invoice.status == "paid" and sub.status == "past_due":
        sub.status = "active"
        _audit_change(db, org_id, sub, "payment_recovered")


def _invoice_failed(
    db: Session, org_id, provider: str, event: ProviderEvent, now: datetime
) -> None:
    from app.services import notifications

    sub = ensure_subscription(db, org_id, now)
    if event.invoice:
        _upsert_invoice(db, org_id, provider, sub, {**event.invoice, "status": "open"}, now)
    if sub.status in ("active", "past_due"):
        sub.status = "past_due"
    _audit_change(db, org_id, sub, "payment_failed")
    for member in notifications.members(db):
        if member.role == "owner":
            notifications.notify_user(
                db,
                org_id,
                member.user_id,
                category="data",
                severity="high",
                title="Your payment did not go through",
                body="Update your payment details to keep your plan.",
                link="billing.html",
                now=now,
            )


HANDLERS = {
    "checkout_completed": _checkout_completed,
    "subscription_updated": _subscription_updated,
    "subscription_canceled": _subscription_canceled,
    "invoice_updated": _invoice_updated,
    "invoice_failed": _invoice_failed,
}


# --- changing, cancelling, resuming -------------------------------------------------------------------------------------------------------------


def _paying(st: Standing) -> bool:
    return st.effective in ("active", "past_due") and st.subscription.provider != "none"


def change_plan(
    db: Session,
    tenant,
    plan_code: str,
    interval: str | None,
    providers: dict[str, PaymentProvider],
    meta: RequestMeta,
    now: datetime | None = None,
) -> ChangeOut:
    """Move to another plan. A dearer one starts now; a cheaper one starts when the period ends, and only
    if what the business has now fits in it."""
    now = now or utcnow()
    st = standing(db, tenant.organization_id, now)
    sub = st.subscription
    if not _paying(st):
        raise ConflictError("Choose a plan first.", code="no_plan_yet")
    interval = interval or sub.interval
    target = _buyable(db, plan_code, interval)
    current = st.plan
    if target.id == current.id and interval == sub.interval and not sub.scheduled_plan_id:
        raise ConflictError("You are already on that plan.", code="no_change")
    upgrade = rules.is_upgrade(current, target) or (
        target.id == current.id and interval == "year" and sub.interval == "month"
    )
    if not upgrade:
        problems = rules.downgrade_problems(
            _rows(db, target.id),
            {f: used(db, f, now) for f in ("members", "integrations", "scheduled_reports")},
            target.name,
        )
        if problems:
            raise ConflictError(
                "That plan is too small for what you have now. " + " ".join(problems),
                code="too_big_for_plan",
                details={"problems": problems},
            )
    provider = _provider(providers, sub.provider)
    if not provider.changes_in_place:
        ok_url, no_url = _links(tenant.organization_id)
        try:
            session = provider.checkout(
                organization_id=tenant.organization_id,
                email=tenant.user.email,
                plan=target,
                interval=interval,
                success_url=ok_url,
                cancel_url=no_url,
            )
        except ProviderError as exc:
            raise AppError(str(exc), code="provider_error", status_code=502) from exc
        return ChangeOut(
            result="checkout",
            message="Complete the payment for the new plan.",
            checkout_url=session.url,
            billing=summary(db, tenant, providers, now),
        )
    try:
        provider.change_plan(
            subscription_id=sub.provider_subscription_id,
            plan=target,
            interval=interval,
            prorate=upgrade,
        )
    except ProviderError as exc:
        raise AppError(str(exc), code="provider_error", status_code=502) from exc
    if upgrade:
        sub.plan_id, sub.interval, sub.scheduled_plan_id, sub.scheduled_interval = (
            target.id,
            interval,
            None,
            None,
        )
        _set_items(db, sub, target, interval)
        result, message = "changed_now", f"You are now on the {target.name} plan."
    elif target.id == current.id and interval == sub.interval:  # staying put: drop the waiting move
        sub.scheduled_plan_id = sub.scheduled_interval = None
        result, message = "changed_now", f"You will stay on the {current.name} plan."
    else:
        sub.scheduled_plan_id, sub.scheduled_interval = target.id, interval
        end = (
            f"{sub.current_period_end:%d/%m/%Y}"
            if sub.current_period_end
            else "the end of this period"
        )
        result, message = (
            "scheduled",
            f"You will move to the {target.name} plan on {end}. Until then you keep the {current.name} plan.",
        )
    record_audit(
        db, AuditAction.BILLING_SUBSCRIPTION_CHANGED, actor_user_id=tenant.user.id, organization_id=tenant.organization_id, target_type="subscription", target_id=sub.id,
        ip_address=meta.ip_address, user_agent=meta.user_agent, details={"what": result, "from": current.code, "to": target.code, "interval": interval},
    )  # fmt: skip
    db.commit()
    return ChangeOut(result=result, message=message, billing=summary(db, tenant, providers, now))


def cancel(
    db: Session,
    tenant,
    providers: dict[str, PaymentProvider],
    meta: RequestMeta,
    now: datetime | None = None,
) -> BillingOut:
    now = now or utcnow()
    st = standing(db, tenant.organization_id, now)
    if not _paying(st):
        raise ConflictError("There is no paid plan to cancel.", code="nothing_to_cancel")
    sub = st.subscription
    if sub.cancel_at_period_end:
        raise ConflictError(
            "It is already set to end at the close of this period.", code="already_canceling"
        )
    _toggle_cancel(db, tenant, sub, providers, True, meta)
    return summary(db, tenant, providers, now)


def resume(
    db: Session,
    tenant,
    providers: dict[str, PaymentProvider],
    meta: RequestMeta,
    now: datetime | None = None,
) -> BillingOut:
    now = now or utcnow()
    st = standing(db, tenant.organization_id, now)
    sub = st.subscription
    if not _paying(st) or not sub.cancel_at_period_end:
        raise ConflictError("Nothing is waiting to end.", code="nothing_to_resume")
    _toggle_cancel(db, tenant, sub, providers, False, meta)
    return summary(db, tenant, providers, now)


def _toggle_cancel(
    db: Session, tenant, sub: Subscription, providers, cancel_it: bool, meta: RequestMeta
) -> None:
    provider = _provider(providers, sub.provider)
    try:
        provider.set_cancel(subscription_id=sub.provider_subscription_id, cancel=cancel_it)
    except ProviderError as exc:
        raise AppError(str(exc), code="provider_error", status_code=502) from exc
    sub.cancel_at_period_end = cancel_it
    record_audit(
        db, AuditAction.BILLING_SUBSCRIPTION_CHANGED, actor_user_id=tenant.user.id, organization_id=tenant.organization_id, target_type="subscription", target_id=sub.id,
        ip_address=meta.ip_address, user_agent=meta.user_agent, details={"what": "cancel_requested" if cancel_it else "cancel_withdrawn"},
    )  # fmt: skip
    db.commit()


# --- history ------------------------------------------------------------------------------------------------------------------------------------------------


INVOICE_LABEL = {
    "draft": "Being prepared",
    "open": "Waiting for payment",
    "paid": "Paid",
    "void": "Cancelled",
    "uncollectible": "Could not be collected",
}


def list_invoices(db: Session) -> list[InvoiceOut]:
    return [
        InvoiceOut(
            id=i.id, number=i.number, status=i.status, status_label=INVOICE_LABEL[i.status], currency=i.currency, net=rules.money(i.net_pence, i.currency),
            vat=rules.money(i.vat_pence, i.currency), total=rules.money(i.total_pence, i.currency), period_start=i.period_start, period_end=i.period_end,
            issued_at=i.issued_at, paid_at=i.paid_at, hosted_url=i.hosted_url, lines=[{"description": line["description"], "amount": rules.money(line["amount_pence"], i.currency)} for line in i.lines],
        )
        for i in db.scalars(select(Invoice).order_by(Invoice.issued_at.desc(), Invoice.created_at.desc()))
    ]  # fmt: skip


# --- the sandbox keeps time like a real provider would --------------------------------------------------------------------------------------------------------


def roll_sandbox(db: Session, org_id: uuid.UUID, now: datetime) -> int:
    """A sandbox subscription renews (or ends) when its period does, as a real provider would tell us."""
    sub = db.scalars(select(Subscription)).first()
    if (
        sub is None
        or sub.provider != "sandbox"
        or sub.status != "active"
        or sub.current_period_end is None
    ):
        return 0
    rolled = 0
    while sub.current_period_end <= now and rolled < MAX_ROLLS:
        if sub.cancel_at_period_end:
            sub.status, sub.canceled_at, sub.cancel_at_period_end = (
                "canceled",
                sub.current_period_end,
                False,
            )
            sub.scheduled_plan_id = sub.scheduled_interval = None
            _audit_change(db, org_id, sub, "canceled")
            return rolled + 1
        start = sub.current_period_end
        sub.current_period_start, sub.current_period_end = (
            start,
            rules.period_end(start, sub.interval),
        )
        _renewed(db, org_id, sub)
        plan = db.get(Plan, sub.plan_id)
        net = rules.price(plan, sub.interval) or 0
        _upsert_invoice(
            db, org_id, "sandbox", sub,
            {"id": f"sandbox_inv_{sub.id.hex[:8]}_{start:%Y%m%d}", "number": f"SBX-{start:%Y%m%d}", "status": "paid", "currency": plan.currency, "net_pence": net,
             "total_pence": net + rules.vat(net), "period_start": start.timestamp(), "period_end": sub.current_period_end.timestamp(), "paid_at": start.timestamp(),
             "lines": [{"description": f"{plan.name} plan, billed {'yearly' if sub.interval == 'year' else 'monthly'}", "amount_pence": net}]},
            now,
        )  # fmt: skip
        rolled += 1
    return rolled


# --- looking after the plans (until the admin portal, Phase 17, does it) -----------------------------------------------------------------


def set_plan(
    db: Session,
    code: str,
    *,
    price_month: int | None = None,
    price_year: int | None = None,
    provider_price: tuple[str, str, str] | None = None,
    is_public: bool | None = None,
    is_active: bool | None = None,
) -> Plan:
    """Change a plan's prices (in pence, excluding VAT) or a provider's own price id for it
    (provider, interval, id). Nobody already paying is affected: they pay what they signed up for."""
    plan = plan_by_code(db, code)
    if plan is None:
        raise NotFoundError("That plan was not found", code="plan_not_found")
    if price_month is not None:
        plan.price_month_pence = price_month
    if price_year is not None:
        plan.price_year_pence = price_year
    if is_public is not None:
        plan.is_public = is_public
    if is_active is not None:
        plan.is_active = is_active
    if provider_price is not None:
        provider, interval, ref = provider_price
        if provider not in ("stripe", "paystack") or interval not in ("month", "year"):
            raise AppError(
                "A provider price is for stripe or paystack, by month or year.",
                code="bad_provider_price",
                status_code=422,
            )
        prices = {k: dict(v) for k, v in (plan.provider_prices or {}).items()}
        prices.setdefault(provider, {})[interval] = ref
        plan.provider_prices = prices
    db.flush()
    return plan


def set_entitlement(
    db: Session, code: str, feature: str, *, enabled: bool, limit: int | None
) -> FeatureEntitlement:
    """Set what a plan includes of one feature (a switch, and a limit where something is counted)."""
    plan = plan_by_code(db, code)
    if plan is None:
        raise NotFoundError("That plan was not found", code="plan_not_found")
    if feature not in rules.FEATURE_NOUN:
        raise AppError(
            "That is not a feature a plan controls.", code="bad_feature", status_code=422
        )
    row = _rows(db, plan.id).get(feature)
    if row is None:
        row = FeatureEntitlement(plan_id=plan.id, feature=feature, enabled=enabled, limit=limit)
        db.add(row)
    else:
        row.enabled, row.limit = enabled, limit
    db.flush()
    return row


def grant_demo_plan(db: Session, org_id: uuid.UUID, plan_code: str, now: datetime) -> Subscription:
    """For demonstrations only: put a business on a paid sandbox plan for a year, so every feature is on.
    It moves no money, and is removed with the rest of the demo data before the system is hosted."""
    plan = plan_by_code(db, plan_code)
    if plan is None:
        raise NotFoundError("That plan was not found", code="plan_not_found")
    sub = ensure_subscription(db, org_id, now)
    sub.plan_id, sub.status, sub.interval, sub.provider = plan.id, "active", "year", "sandbox"
    sub.provider_customer_id = f"sandbox_cus_{org_id.hex[:12]}"
    sub.provider_subscription_id = f"sandbox_sub_{org_id.hex[:12]}"
    sub.trial_ends_at, sub.current_period_start = None, now
    sub.current_period_end = rules.period_end(now, "year")
    sub.cancel_at_period_end, sub.canceled_at = False, None
    sub.scheduled_plan_id = sub.scheduled_interval = None
    _set_items(db, sub, plan, "year")
    net = rules.price(plan, "year") or 0
    _upsert_invoice(
        db, org_id, "sandbox", sub,
        {"id": f"sandbox_inv_demo_{org_id.hex[:8]}", "number": f"DEMO-{now:%Y%m%d}", "status": "paid", "currency": plan.currency, "net_pence": net, "total_pence": net + rules.vat(net),
         "period_start": now.timestamp(), "period_end": sub.current_period_end.timestamp(), "paid_at": now.timestamp(),
         "lines": [{"description": f"{plan.name} plan, billed yearly (demo)", "amount_pence": net}]},
        now,
    )  # fmt: skip
    db.flush()
    return sub


def extend_trial(db: Session, org_id: uuid.UUID, days: int, now: datetime) -> Subscription:
    """Give a business more free-trial time (staff only). It also brings back a trial that has ended.
    A business that is already paying has no trial to extend."""
    sub = ensure_subscription(db, org_id, now)
    if sub.status not in ("trialing",):
        raise ConflictError(
            "Only a business on a free trial can be given more trial time.", code="not_on_trial"
        )
    start = max(now, sub.trial_ends_at) if sub.trial_ends_at else now
    sub.trial_ends_at = start + timedelta(days=days)
    db.flush()
    return sub
