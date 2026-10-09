# ruff: noqa: E501
"""The payment providers, each behind the same small interface so a business can use any of them.

Every provider does four things: start a checkout (the provider's own page where the person pays), change
or cancel a subscription, and turn what the provider sends us (a webhook) into the same few plain events.
Nothing here touches the database: services/billing.py decides what to do with an event.

- sandbox: a stand-in for development and testing, with no money involved. It is refused in production.
- stripe, paystack: written from each provider's documentation. They have only been exercised against
  pretend servers; they have never been run against the real services (that needs the business's own
  accounts and keys).

A webhook is only believed if its signature checks out against the secret only we and the provider know.
"""

import base64
import hashlib
import hmac
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx

from app.billing import rules

STRIPE_API = "https://api.stripe.com"
PAYSTACK_API = "https://api.paystack.co"
STRIPE_TOLERANCE_SECONDS = 300
SANDBOX_SESSION_MINUTES = 60
EVENT_TYPES = (
    "checkout_completed",
    "subscription_updated",
    "subscription_canceled",
    "invoice_updated",
    "invoice_failed",
    "ignored",
)


class ProviderError(Exception):
    """The provider did not do what was asked. The message is safe to show."""


class SignatureError(Exception):
    """A webhook that did not carry a valid signature: it is not believed."""


@dataclass
class ProviderEvent:
    id: str
    type: str  # one of EVENT_TYPES
    organization_id: uuid.UUID | None = None
    customer_id: str | None = None
    subscription_id: str | None = None
    plan_code: str | None = None  # our own code, when the provider carried it back to us
    plan_ref: str | None = None  # the provider's own id for the plan or price
    interval: str | None = None
    status: str | None = None  # active, trialing, past_due or canceled
    period_start: datetime | None = None
    period_end: datetime | None = None
    cancel_at_period_end: bool | None = None
    invoice: dict | None = None
    note: str = ""
    replaced_subscription_id: str | None = None  # set by us: the subscription this one replaced


@dataclass
class Checkout:
    url: str
    ref: str


class PaymentProvider(Protocol):
    key: str
    changes_in_place: bool  # a plan can be changed on the existing subscription

    def checkout(
        self,
        *,
        organization_id: uuid.UUID,
        email: str,
        plan,
        interval: str,
        success_url: str,
        cancel_url: str,
    ) -> Checkout: ...
    def change_plan(self, *, subscription_id: str, plan, interval: str, prorate: bool) -> None: ...
    def set_cancel(self, *, subscription_id: str, cancel: bool) -> None: ...
    def parse_webhook(
        self, headers: Mapping[str, str], body: bytes, now: datetime
    ) -> list[ProviderEvent]: ...


def _hex(secret: str, body: bytes, digest) -> str:
    return hmac.new(secret.encode(), body, digest).hexdigest()


def _header(headers: Mapping[str, str], name: str) -> str:
    wanted = name.lower()
    return next((v for k, v in headers.items() if k.lower() == wanted), "")


def _org(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


# --- the sandbox ---------------------------------------------------------------------------------------------


class SandboxProvider:
    """No money moves. A checkout is a signed, short-lived token; "paying" it makes the same events the
    real providers would send, so the whole flow can be tried and tested."""

    key = "sandbox"
    changes_in_place = True

    def __init__(self, secret: str) -> None:
        self._secret = secret

    # a checkout
    def _sign(self, payload: bytes) -> str:
        return _hex(self._secret, payload, hashlib.sha256)

    def checkout(
        self, *, organization_id, email, plan, interval, success_url, cancel_url
    ) -> Checkout:
        expires = int((datetime.now(UTC) + timedelta(minutes=SANDBOX_SESSION_MINUTES)).timestamp())
        payload = json.dumps(
            {"org": str(organization_id), "plan": plan.code, "interval": interval, "exp": expires},
            sort_keys=True,
        ).encode()
        token = base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + self._sign(payload)
        return Checkout(url=f"{success_url}&sandbox={token}", ref=token)

    def complete(self, token: str, now: datetime) -> ProviderEvent:
        """What the provider would send once the person has paid. A bad or old token is refused."""
        try:
            body, signature = token.split(".")
            payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        except ValueError as exc:
            raise ProviderError("That payment link is not valid.") from exc
        if not hmac.compare_digest(self._sign(payload), signature):
            raise ProviderError("That payment link is not valid.")
        data = json.loads(payload)
        if now.timestamp() > data["exp"]:
            raise ProviderError("That payment link has expired. Start again.")
        org = uuid.UUID(data["org"])
        start = now
        end = rules.period_end(start, data["interval"])
        return ProviderEvent(
            id=f"sandbox_evt_{token[-16:]}",
            type="checkout_completed",
            organization_id=org,
            customer_id=f"sandbox_cus_{org.hex[:12]}",
            subscription_id=f"sandbox_sub_{org.hex[:12]}",
            plan_code=data["plan"],
            interval=data["interval"],
            status="active",
            period_start=start,
            period_end=end,
            cancel_at_period_end=False,
        )

    def change_plan(self, *, subscription_id, plan, interval, prorate) -> None:
        return None  # nothing remote to change

    def set_cancel(self, *, subscription_id, cancel) -> None:
        return None

    def parse_webhook(self, headers, body: bytes, now: datetime) -> list[ProviderEvent]:
        if not hmac.compare_digest(self._sign(body), _header(headers, "x-sandbox-signature")):
            raise SignatureError("The signature does not match.")
        data = json.loads(body)
        return [
            ProviderEvent(
                id=data["id"],
                type=data["type"],
                organization_id=_org(data.get("organization_id")),
                customer_id=data.get("customer_id"),
                subscription_id=data.get("subscription_id"),
                plan_code=data.get("plan_code"),
                interval=data.get("interval"),
                status=data.get("status"),
                period_start=rules.from_unix(data.get("period_start")),
                period_end=rules.from_unix(data.get("period_end")),
                cancel_at_period_end=data.get("cancel_at_period_end"),
                invoice=data.get("invoice"),
            )
        ]

    def sign(self, body: bytes) -> str:
        """For tests and the local tools: the signature a genuine sandbox message carries."""
        return self._sign(body)


# --- stripe ----------------------------------------------------------------------------------------------------


def stripe_signature_ok(
    secret: str, header: str, body: bytes, now: datetime, tolerance: int = STRIPE_TOLERANCE_SECONDS
) -> bool:
    """Stripe's scheme: the header is `t=<time>,v1=<hex>,...`; v1 is HMAC-SHA256 of `<time>.<body>`."""
    pairs = [p.split("=", 1) for p in header.split(",") if "=" in p] if header else []
    stamps = [v for k, v in pairs if k.strip() == "t"]
    signatures = [v for k, v in pairs if k.strip() == "v1"]
    try:
        stamp = int(stamps[0])
    except (IndexError, ValueError):
        return False
    if abs(now.timestamp() - stamp) > tolerance:
        return False
    expected = _hex(secret, f"{stamp}.".encode() + body, hashlib.sha256)
    return any(hmac.compare_digest(expected, s) for s in signatures)


STRIPE_STATUS = {
    "active": "active",
    "trialing": "trialing",
    "past_due": "past_due",
    "unpaid": "past_due",
    "canceled": "canceled",
    "incomplete_expired": "canceled",
}


def _stripe_invoice(obj: dict) -> dict:
    net = obj.get("total_excluding_tax", obj.get("subtotal", 0)) or 0
    total = obj.get("total", 0) or 0
    paid_at = (obj.get("status_transitions") or {}).get("paid_at")
    return {
        "id": obj["id"],
        "number": obj.get("number"),
        "status": obj.get("status")
        if obj.get("status") in ("draft", "open", "paid", "void", "uncollectible")
        else "open",
        "currency": str(obj.get("currency", "gbp")).upper(),
        "net_pence": net,
        "vat_pence": total - net,
        "total_pence": total,
        "period_start": obj.get("period_start"),
        "period_end": obj.get("period_end"),
        "paid_at": paid_at,
        "hosted_url": obj.get("hosted_invoice_url"),
        "lines": [
            {"description": line.get("description") or "", "amount_pence": line.get("amount", 0)}
            for line in (obj.get("lines") or {}).get("data", [])
        ],
    }


class StripeProvider:
    key = "stripe"
    changes_in_place = True

    def __init__(
        self,
        secret_key: str,
        webhook_secret: str,
        client: httpx.Client | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._key, self._webhook, self._client, self._timeout = (
            secret_key,
            webhook_secret,
            client,
            timeout,
        )

    def _call(self, method: str, path: str, data: dict | None = None) -> dict:
        try:
            client = self._client or httpx.Client(timeout=self._timeout)
            res = client.request(
                method,
                STRIPE_API + path,
                data=data,
                headers={"Authorization": f"Bearer {self._key}"},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise ProviderError("Could not reach the payment provider. Please try again.") from exc
        if res.status_code >= 400:
            raise ProviderError(
                "The payment provider did not accept that. Please try again, or contact support."
            )
        return res.json()

    @staticmethod
    def _price(plan, interval: str) -> str:
        ref = (plan.provider_prices or {}).get("stripe", {}).get(interval)
        if not ref:
            raise ProviderError(
                f"The {plan.name} plan is not set up for card payments yet. Please contact support."
            )
        return ref

    def checkout(
        self, *, organization_id, email, plan, interval, success_url, cancel_url
    ) -> Checkout:
        meta = {
            "organization_id": str(organization_id),
            "plan_code": plan.code,
            "interval": interval,
        }
        data = {
            "mode": "subscription",
            "line_items[0][price]": self._price(plan, interval),
            "line_items[0][quantity]": "1",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": str(organization_id),
            "customer_email": email,
        }
        for k, v in meta.items():
            data[f"metadata[{k}]"] = v
            data[f"subscription_data[metadata][{k}]"] = v
        session = self._call("POST", "/v1/checkout/sessions", data)
        return Checkout(url=session["url"], ref=session["id"])

    def change_plan(self, *, subscription_id, plan, interval, prorate) -> None:
        current = self._call("GET", f"/v1/subscriptions/{subscription_id}")
        item = current["items"]["data"][0]["id"]
        self._call(
            "POST",
            f"/v1/subscriptions/{subscription_id}",
            {
                "items[0][id]": item,
                "items[0][price]": self._price(plan, interval),
                "proration_behavior": "create_prorations" if prorate else "none",
            },
        )

    def set_cancel(self, *, subscription_id, cancel) -> None:
        self._call(
            "POST",
            f"/v1/subscriptions/{subscription_id}",
            {"cancel_at_period_end": "true" if cancel else "false"},
        )

    def parse_webhook(self, headers, body: bytes, now: datetime) -> list[ProviderEvent]:
        if not stripe_signature_ok(self._webhook, _header(headers, "stripe-signature"), body, now):
            raise SignatureError("The signature does not match.")
        event = json.loads(body)
        kind, obj = event.get("type", ""), (event.get("data") or {}).get("object") or {}
        eid = event["id"]
        meta = obj.get("metadata") or {}
        if kind == "checkout.session.completed":
            return [
                ProviderEvent(
                    id=eid,
                    type="checkout_completed",
                    organization_id=_org(
                        obj.get("client_reference_id") or meta.get("organization_id")
                    ),
                    customer_id=obj.get("customer"),
                    subscription_id=obj.get("subscription"),
                    plan_code=meta.get("plan_code"),
                    interval=meta.get("interval"),
                    status="active",
                )
            ]
        if kind in (
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
        ):
            item = ((obj.get("items") or {}).get("data") or [{}])[0]
            start = obj.get("current_period_start") or item.get("current_period_start")
            end = obj.get("current_period_end") or item.get("current_period_end")
            status = (
                "canceled" if kind.endswith("deleted") else STRIPE_STATUS.get(obj.get("status", ""))
            )
            if status is None:
                return [
                    ProviderEvent(
                        id=eid, type="ignored", note=f"subscription state {obj.get('status')}"
                    )
                ]
            return [ProviderEvent(
                id=eid, type="subscription_canceled" if status == "canceled" else "subscription_updated", organization_id=_org(meta.get("organization_id")),
                customer_id=obj.get("customer"), subscription_id=obj.get("id"), plan_code=meta.get("plan_code"), plan_ref=(item.get("price") or {}).get("id"),
                interval=meta.get("interval"), status=status, period_start=rules.from_unix(start), period_end=rules.from_unix(end), cancel_at_period_end=bool(obj.get("cancel_at_period_end")),
            )]  # fmt: skip
        if kind in (
            "invoice.paid",
            "invoice.payment_succeeded",
            "invoice.payment_failed",
            "invoice.finalized",
        ):
            invoice = _stripe_invoice(obj)
            if kind == "invoice.payment_failed":
                invoice["status"] = "open"
            kind_out = "invoice_failed" if kind == "invoice.payment_failed" else "invoice_updated"
            return [
                ProviderEvent(
                    id=eid,
                    type=kind_out,
                    customer_id=obj.get("customer"),
                    subscription_id=obj.get("subscription"),
                    invoice=invoice,
                )
            ]
        return [ProviderEvent(id=eid, type="ignored", note=kind)]


# --- paystack ------------------------------------------------------------------------------------------------------


def paystack_signature_ok(secret: str, header: str, body: bytes) -> bool:
    """Paystack's scheme: the header is the HMAC-SHA512 of the body, in hex."""
    return bool(header) and hmac.compare_digest(_hex(secret, body, hashlib.sha512), header.strip())


class PaystackProvider:
    key = "paystack"
    changes_in_place = False  # a different plan is a fresh checkout

    def __init__(
        self, secret_key: str, client: httpx.Client | None = None, timeout: float = 20.0
    ) -> None:
        self._key, self._client, self._timeout = secret_key, client, timeout

    def _call(self, method: str, path: str, json_body: dict | None = None) -> dict:
        try:
            client = self._client or httpx.Client(timeout=self._timeout)
            res = client.request(
                method,
                PAYSTACK_API + path,
                json=json_body,
                headers={"Authorization": f"Bearer {self._key}"},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise ProviderError("Could not reach the payment provider. Please try again.") from exc
        body = res.json() if res.content else {}
        if res.status_code >= 400 or not body.get("status", True):
            raise ProviderError(
                "The payment provider did not accept that. Please try again, or contact support."
            )
        return body.get("data") or {}

    def checkout(
        self, *, organization_id, email, plan, interval, success_url, cancel_url
    ) -> Checkout:
        code = (plan.provider_prices or {}).get("paystack", {}).get(interval)
        if not code:
            raise ProviderError(
                f"The {plan.name} plan is not set up for Paystack yet. Please contact support."
            )
        data = self._call(
            "POST", "/transaction/initialize",
            {"email": email, "plan": code, "currency": plan.currency, "callback_url": success_url, "metadata": {"organization_id": str(organization_id), "plan_code": plan.code, "interval": interval, "cancel_action": cancel_url}},
        )  # fmt: skip
        return Checkout(url=data["authorization_url"], ref=data["reference"])

    def change_plan(self, *, subscription_id, plan, interval, prorate) -> None:
        raise ProviderError("A Paystack subscription is changed by choosing the new plan again.")

    def set_cancel(self, *, subscription_id, cancel) -> None:
        if not cancel:
            raise ProviderError(
                "Paystack cannot undo a cancellation. Choose the plan again to carry on."
            )
        sub = self._call("GET", f"/subscription/{subscription_id}")
        self._call(
            "POST", "/subscription/disable", {"code": subscription_id, "token": sub["email_token"]}
        )

    def parse_webhook(self, headers, body: bytes, now: datetime) -> list[ProviderEvent]:
        if not paystack_signature_ok(self._key, _header(headers, "x-paystack-signature"), body):
            raise SignatureError("The signature does not match.")
        event = json.loads(body)
        kind, data = event.get("event", ""), event.get("data") or {}
        meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        customer = (data.get("customer") or {}).get("customer_code")
        eid = f"{kind}:{data.get('reference') or data.get('subscription_code') or data.get('id')}"
        if kind == "charge.success":
            invoice = {
                "id": str(data.get("reference")), "number": str(data.get("reference")), "status": "paid", "currency": str(data.get("currency", "GBP")).upper(),
                "net_pence": data.get("amount", 0), "vat_pence": 0, "total_pence": data.get("amount", 0), "paid_at": data.get("paid_at"), "lines": [],
            }  # fmt: skip
            organization = _org(meta.get("organization_id"))
            if organization is None:
                return [
                    ProviderEvent(
                        id=eid,
                        type="invoice_updated",
                        customer_id=customer,
                        subscription_id=None,
                        invoice=invoice,
                    )
                ]
            return [
                ProviderEvent(
                    id=eid,
                    type="checkout_completed",
                    organization_id=organization,
                    customer_id=customer,
                    plan_code=meta.get("plan_code"),
                    interval=meta.get("interval"),
                    status="active",
                    invoice=invoice,
                )
            ]
        if kind == "subscription.create":
            end = data.get("next_payment_date")
            return [ProviderEvent(
                id=eid, type="subscription_updated", customer_id=customer, subscription_id=data.get("subscription_code"), plan_ref=(data.get("plan") or {}).get("plan_code"),
                status="active", period_end=datetime.fromisoformat(end.replace("Z", "+00:00")) if end else None, cancel_at_period_end=False,
            )]  # fmt: skip
        if kind == "subscription.not_renew":
            return [
                ProviderEvent(
                    id=eid,
                    type="subscription_updated",
                    customer_id=customer,
                    subscription_id=data.get("subscription_code"),
                    status="active",
                    cancel_at_period_end=True,
                )
            ]
        if kind == "subscription.disable":
            return [
                ProviderEvent(
                    id=eid,
                    type="subscription_canceled",
                    customer_id=customer,
                    subscription_id=data.get("subscription_code"),
                    status="canceled",
                )
            ]
        if kind == "invoice.payment_failed":
            return [
                ProviderEvent(
                    id=eid,
                    type="invoice_failed",
                    customer_id=customer,
                    subscription_id=(data.get("subscription") or {}).get("subscription_code"),
                )
            ]
        return [ProviderEvent(id=eid, type="ignored", note=kind)]
