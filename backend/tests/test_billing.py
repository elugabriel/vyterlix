# ruff: noqa: E501, F811
"""Billing: the rules, the providers' signatures and messages, the trial, paying, changing, and the limits."""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.api.v1.billing import get_payment_providers
from app.billing import rules
from app.billing.providers import (
    PaystackProvider,
    ProviderError,
    SandboxProvider,
    SignatureError,
    StripeProvider,
    paystack_signature_ok,
    stripe_signature_ok,
)
from app.models.billing import BillingEvent, Invoice, Plan, Subscription
from app.models.identity import AuditLog
from app.services import billing as service
from tests.test_health import ORGS, scoped
from tests.test_integrations import fake  # noqa: F401  (the fake provider fixture)

UTC_NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
SANDBOX = SandboxProvider("test-secret-for-the-sandbox")


def plan(price_month=None, price_year=None):
    return SimpleNamespace(price_month_pence=price_month, price_year_pence=price_year)


def row(enabled=True, limit=None):
    return SimpleNamespace(enabled=enabled, limit=limit)


# --- the rules ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("net", "vat"),
    [(9900, 1980), (0, 0), (1, 0), (2, 0), (3, 1), (5, 1), (12345, 2469), (19999, 4000)],
)
def test_vat_is_twenty_per_cent_to_the_nearest_penny_with_half_a_penny_rounding_up(net, vat):
    assert rules.vat(net) == vat


def test_money_is_shown_in_pounds_and_other_currencies_by_their_code():
    assert rules.money(9900) == "£99.00"
    assert rules.money(123456789) == "£1,234,567.89"
    assert rules.money(5) == "£0.05"
    assert rules.money(-250) == "-£2.50"
    assert rules.money(1000, "NGN") == "NGN 10.00"
    assert rules.money(None) == "-"


def test_the_price_depends_on_how_it_is_paid_and_a_year_saves_against_twelve_months():
    p = plan(9900, 99000)
    assert rules.price(p, "month") == 9900
    assert rules.price(p, "year") == 99000
    assert rules.year_saving(p) == 9900 * 12 - 99000
    assert rules.year_saving(plan(9900, None)) is None
    assert rules.year_saving(plan(None, 99000)) is None


@pytest.mark.parametrize(
    ("start", "months", "end"),
    [
        (datetime(2026, 1, 31, 9, 30, tzinfo=UTC), 1, datetime(2026, 2, 28, 9, 30, tzinfo=UTC)),
        (datetime(2024, 1, 31, tzinfo=UTC), 1, datetime(2024, 2, 29, tzinfo=UTC)),
        (datetime(2026, 12, 15, tzinfo=UTC), 1, datetime(2027, 1, 15, tzinfo=UTC)),
        (datetime(2026, 3, 31, tzinfo=UTC), 12, datetime(2027, 3, 31, tzinfo=UTC)),
        (datetime(2024, 2, 29, tzinfo=UTC), 12, datetime(2025, 2, 28, tzinfo=UTC)),
    ],
)
def test_a_period_ends_the_same_day_a_month_or_year_later_or_the_last_day_of_a_short_month(
    start, months, end
):
    assert rules.add_months(start, months) == end


def test_a_period_is_a_month_or_a_year():
    start = datetime(2026, 5, 10, tzinfo=UTC)
    assert rules.period_end(start, "month") == datetime(2026, 6, 10, tzinfo=UTC)
    assert rules.period_end(start, "year") == datetime(2027, 5, 10, tzinfo=UTC)


def test_trial_days_left_counts_a_part_of_a_day_as_a_day_and_is_zero_once_over():
    end = UTC_NOW + timedelta(days=3, hours=1)
    assert rules.trial_days_left(end, UTC_NOW) == 4
    assert rules.trial_days_left(UTC_NOW + timedelta(days=3), UTC_NOW) == 3
    assert rules.trial_days_left(UTC_NOW + timedelta(seconds=1), UTC_NOW) == 1
    assert rules.trial_days_left(UTC_NOW, UTC_NOW) == 0
    assert rules.trial_days_left(UTC_NOW - timedelta(days=1), UTC_NOW) == 0
    assert rules.trial_days_left(None, UTC_NOW) == 0


def effective(status, *, trial=None, end=None, cancel=False, now=UTC_NOW):
    return rules.effective_status(
        status, trial_ends_at=trial, period_end_at=end, cancel_at_period_end=cancel, now=now
    )


def test_a_trial_is_over_at_the_moment_it_ends():
    assert effective("trialing", trial=UTC_NOW + timedelta(seconds=1)) == "trialing"
    assert effective("trialing", trial=UTC_NOW) == "expired"
    assert effective("trialing", trial=None) == "expired"


def test_a_paid_plan_set_to_end_ends_when_its_period_does_and_not_before():
    assert (
        effective("active", end=UTC_NOW - timedelta(days=1)) == "active"
    )  # the provider will say if it fails
    assert effective("active", end=UTC_NOW + timedelta(seconds=1), cancel=True) == "active"
    assert effective("active", end=UTC_NOW, cancel=True) == "canceled"
    assert effective("active", end=None, cancel=True) == "active"


def test_a_failed_payment_keeps_the_plan_for_a_week_after_the_period_then_it_ends():
    assert effective("past_due", end=UTC_NOW - timedelta(days=6, hours=23)) == "past_due"
    assert effective("past_due", end=UTC_NOW - timedelta(days=7)) == "expired"
    assert effective("past_due", end=None) == "past_due"
    assert effective("canceled") == "canceled"
    assert effective("expired") == "expired"


def test_only_a_trial_a_paid_plan_or_one_overdue_is_entitled():
    assert [
        rules.entitled(s) for s in ("trialing", "active", "past_due", "canceled", "expired")
    ] == [True, True, True, False, False]


def test_an_allowance_follows_the_plan_row_and_stops_when_not_entitled():
    assert rules.allowance(row(True, 5), "active") == rules.Allowance(True, 5)
    assert rules.allowance(row(True, None), "trialing") == rules.Allowance(True, None)
    assert rules.allowance(row(False, 5), "active") == rules.BLOCKED
    assert rules.allowance(None, "active") == rules.BLOCKED
    assert rules.allowance(row(True, 5), "expired") == rules.BLOCKED
    assert rules.allowance(row(True, 5), "canceled") == rules.BLOCKED


def test_adding_one_more_is_allowed_until_the_limit_is_reached():
    allow = rules.Allowance(True, 3)
    assert rules.decide("members", allow, 2, "Starter", "active").ok
    refused = rules.decide("members", allow, 3, "Starter", "active")
    assert not refused.ok
    assert (
        refused.message
        == "The Starter plan includes up to 3 team members and you already have 3. Upgrade to add more."
    )
    assert rules.decide("members", rules.Allowance(True, None), 10_000, "Scale", "active").ok
    assert rules.decide("members", rules.Allowance(True, 0), 0, "X", "active").ok is False


def test_a_feature_a_plan_leaves_out_and_a_plan_that_has_ended_say_so_in_words():
    out = rules.decide("ai_assistant", rules.BLOCKED, 0, "Starter", "active")
    assert out.message == "The AI assistant is not part of the Starter plan. Upgrade to use it."
    ended = rules.decide("members", rules.BLOCKED, 0, "Scale", "expired")
    assert (
        ended.message == "Your plan has ended. Choose a plan to carry on adding to your business."
    )


def test_a_smaller_plan_is_refused_only_for_what_will_not_fit_and_not_for_what_it_leaves_out():
    target = {
        "members": row(True, 3),
        "integrations": row(True, 1),
        "scheduled_reports": row(True, None),
        "ai_assistant": row(False, None),
    }
    assert (
        rules.downgrade_problems(
            target, {"members": 3, "integrations": 1, "scheduled_reports": 99}, "Starter"
        )
        == []
    )
    problems = rules.downgrade_problems(target, {"members": 4, "integrations": 2}, "Starter")
    assert problems == [
        "You have 4 team members and the Starter plan allows 3.",
        "You have 2 connections to other systems and the Starter plan allows 1.",
    ]
    assert rules.downgrade_problems({"members": row(False, 1)}, {"members": 9}, "X") == []
    assert rules.downgrade_problems({}, {"members": 9}, "X") == []


def test_dearer_is_judged_by_the_monthly_price_and_a_plan_with_no_price_is_the_dearest():
    assert rules.is_upgrade(plan(9900), plan(19900))
    assert not rules.is_upgrade(plan(19900), plan(9900))
    assert not rules.is_upgrade(plan(9900), plan(9900))
    assert rules.is_upgrade(plan(29900), plan(None))
    assert not rules.is_upgrade(plan(None), plan(29900))


# --- signatures ----------------------------------------------------------------------------------------------


def stripe_header(secret, body, stamp, extra=""):
    sig = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={sig}{extra}"


def test_a_stripe_message_is_believed_only_with_the_right_secret_a_fresh_time_and_the_same_body():
    body, now = b'{"id":"evt_1"}', UTC_NOW
    stamp = int(now.timestamp())
    good = stripe_header("whsec_a", body, stamp)
    assert stripe_signature_ok("whsec_a", good, body, now)
    assert not stripe_signature_ok("whsec_b", good, body, now)
    assert not stripe_signature_ok("whsec_a", good, body + b" ", now)
    assert not stripe_signature_ok("whsec_a", "", body, now)
    assert not stripe_signature_ok("whsec_a", "t=abc,v1=00", body, now)
    assert not stripe_signature_ok("whsec_a", f"v1={good.split('v1=')[1]}", body, now)  # no time
    assert not stripe_signature_ok("whsec_a", f"t={stamp}", body, now)  # no signature


def test_a_stripe_message_is_refused_when_older_than_five_minutes_but_not_before():
    body = b"{}"
    stamp = int(UTC_NOW.timestamp())
    header = stripe_header("s", body, stamp)
    assert stripe_signature_ok("s", header, body, UTC_NOW + timedelta(seconds=300))
    assert not stripe_signature_ok("s", header, body, UTC_NOW + timedelta(seconds=301))
    assert stripe_signature_ok("s", header, body, UTC_NOW - timedelta(seconds=300))
    assert not stripe_signature_ok("s", header, body, UTC_NOW - timedelta(seconds=301))


def test_stripe_may_send_several_signatures_and_any_one_genuine_one_will_do():
    body, stamp = b"{}", int(UTC_NOW.timestamp())
    header = stripe_header("s", body, stamp, extra=",v1=" + "0" * 64)
    assert stripe_signature_ok("s", header, body, UTC_NOW)
    wrong_first = f"t={stamp},v1={'0' * 64},v1={hmac.new(b's', f'{stamp}.'.encode() + body, hashlib.sha256).hexdigest()}"
    assert stripe_signature_ok("s", wrong_first, body, UTC_NOW)


def test_a_paystack_message_is_believed_only_with_the_right_secret_and_body():
    body = b'{"event":"charge.success"}'
    sig = hmac.new(b"sk_test", body, hashlib.sha512).hexdigest()
    assert paystack_signature_ok("sk_test", sig, body)
    assert paystack_signature_ok("sk_test", f"  {sig} ", body)
    assert not paystack_signature_ok("sk_other", sig, body)
    assert not paystack_signature_ok("sk_test", sig, body + b"x")
    assert not paystack_signature_ok("sk_test", "", body)


def test_a_sandbox_message_needs_its_signature_too():
    body = json.dumps({"id": "e1", "type": "ignored"}).encode()
    assert (
        SANDBOX.parse_webhook({"X-Sandbox-Signature": SANDBOX.sign(body)}, body, UTC_NOW)[0].id
        == "e1"
    )
    with pytest.raises(SignatureError):
        SANDBOX.parse_webhook({"x-sandbox-signature": "nope"}, body, UTC_NOW)
    with pytest.raises(SignatureError):
        SANDBOX.parse_webhook({}, body, UTC_NOW)


# --- the providers' messages ------------------------------------------------------------------------------------


ORG = uuid.UUID("11111111-1111-1111-1111-111111111111")


def stripe_provider(handler=None):
    client = httpx.Client(transport=httpx.MockTransport(handler)) if handler else None
    return StripeProvider("sk_test", "whsec_test", client=client)


def stripe_event(provider, payload):
    body = json.dumps(payload).encode()
    header = stripe_header("whsec_test", body, int(UTC_NOW.timestamp()))
    return provider.parse_webhook({"Stripe-Signature": header}, body, UTC_NOW)


def test_stripe_says_a_checkout_was_completed_with_what_we_sent_it():
    event = stripe_event(
        stripe_provider(),
        {
            "id": "evt_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "client_reference_id": str(ORG),
                    "customer": "cus_1",
                    "subscription": "sub_1",
                    "metadata": {"plan_code": "growth", "interval": "year"},
                }
            },
        },
    )[0]
    assert (event.type, event.organization_id, event.customer_id, event.subscription_id) == (
        "checkout_completed",
        ORG,
        "cus_1",
        "sub_1",
    )
    assert (event.plan_code, event.interval, event.status) == ("growth", "year", "active")


def test_stripe_subscription_changes_are_understood():
    payload = {
        "id": "evt_2",
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": "sub_1",
                "customer": "cus_1",
                "status": "past_due",
                "cancel_at_period_end": True,
                "metadata": {
                    "organization_id": str(ORG),
                    "plan_code": "growth",
                    "interval": "month",
                },
                "items": {
                    "data": [
                        {
                            "price": {"id": "price_g"},
                            "current_period_start": 1_790_000_000,
                            "current_period_end": 1_792_592_000,
                        }
                    ]
                },
            }
        },
    }
    event = stripe_event(stripe_provider(), payload)[0]
    assert event.type == "subscription_updated"
    assert (event.status, event.cancel_at_period_end, event.plan_ref, event.organization_id) == (
        "past_due",
        True,
        "price_g",
        ORG,
    )
    assert event.period_start == datetime.fromtimestamp(1_790_000_000, tz=UTC)
    assert event.period_end == datetime.fromtimestamp(1_792_592_000, tz=UTC)


def test_stripe_ending_a_subscription_or_leaving_one_unpaid_is_understood_and_odd_states_are_ignored():
    deleted = {
        "id": "e3",
        "type": "customer.subscription.deleted",
        "data": {"object": {"id": "sub_1", "status": "active"}},
    }
    assert stripe_event(stripe_provider(), deleted)[0].type == "subscription_canceled"
    unpaid = {
        "id": "e4",
        "type": "customer.subscription.updated",
        "data": {"object": {"id": "sub_1", "status": "unpaid"}},
    }
    assert stripe_event(stripe_provider(), unpaid)[0].status == "past_due"
    odd = {
        "id": "e5",
        "type": "customer.subscription.updated",
        "data": {"object": {"id": "sub_1", "status": "paused"}},
    }
    assert stripe_event(stripe_provider(), odd)[0].type == "ignored"
    other = {"id": "e6", "type": "customer.created", "data": {"object": {}}}
    assert stripe_event(stripe_provider(), other)[0].type == "ignored"


def test_stripe_invoices_carry_the_net_the_vat_and_the_total():
    payload = {
        "id": "e7",
        "type": "invoice.paid",
        "data": {
            "object": {
                "id": "in_1",
                "number": "A-0001",
                "status": "paid",
                "currency": "gbp",
                "total_excluding_tax": 9900,
                "total": 11880,
                "customer": "cus_1",
                "subscription": "sub_1",
                "hosted_invoice_url": "https://pay.stripe.com/in_1",
                "status_transitions": {"paid_at": 1_790_000_100},
                "lines": {"data": [{"description": "Starter plan", "amount": 9900}]},
            }
        },
    }
    event = stripe_event(stripe_provider(), payload)[0]
    assert event.type == "invoice_updated"
    assert (
        event.invoice["net_pence"] == 9900
        and event.invoice["vat_pence"] == 1980
        and event.invoice["total_pence"] == 11880
    )
    assert event.invoice["currency"] == "GBP" and event.invoice["paid_at"] == 1_790_000_100
    assert event.invoice["lines"] == [{"description": "Starter plan", "amount_pence": 9900}]
    failed = stripe_event(
        stripe_provider(), {**payload, "id": "e8", "type": "invoice.payment_failed"}
    )[0]
    assert failed.type == "invoice_failed" and failed.invoice["status"] == "open"
    odd_status = {
        **payload,
        "id": "e9",
        "data": {"object": {**payload["data"]["object"], "status": "weird"}},
    }
    assert stripe_event(stripe_provider(), odd_status)[0].invoice["status"] == "open"


def test_a_stripe_message_without_a_genuine_signature_is_refused():
    body = b'{"id":"e","type":"x"}'
    with pytest.raises(SignatureError):
        stripe_provider().parse_webhook({"stripe-signature": "t=1,v1=00"}, body, UTC_NOW)


def stripe_plan(**prices):
    return SimpleNamespace(
        code="growth", name="Growth", currency="GBP", provider_prices={"stripe": prices}
    )


def test_a_stripe_checkout_asks_for_the_right_price_and_carries_our_ids_back():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        seen["form"] = dict(httpx.QueryParams(request.content.decode()))
        return httpx.Response(200, json={"id": "cs_1", "url": "https://checkout.stripe.com/cs_1"})

    out = stripe_provider(handler).checkout(
        organization_id=ORG,
        email="a@b.co.uk",
        plan=stripe_plan(month="price_m", year="price_y"),
        interval="year",
        success_url="https://x/ok",
        cancel_url="https://x/no",
    )
    assert out.url == "https://checkout.stripe.com/cs_1" and out.ref == "cs_1"
    assert (
        seen["url"] == "https://api.stripe.com/v1/checkout/sessions"
        and seen["auth"] == "Bearer sk_test"
    )
    form = seen["form"]
    assert form["line_items[0][price]"] == "price_y" and form["mode"] == "subscription"
    assert form["client_reference_id"] == str(ORG) and form["customer_email"] == "a@b.co.uk"
    assert (
        form["metadata[plan_code]"] == "growth"
        and form["subscription_data[metadata][interval]"] == "year"
    )
    assert form["success_url"] == "https://x/ok" and form["cancel_url"] == "https://x/no"


def test_a_plan_with_no_stripe_price_cannot_be_checked_out_and_a_stripe_failure_is_plain_words():
    with pytest.raises(ProviderError, match="not set up for card payments"):
        stripe_provider(lambda r: httpx.Response(200, json={})).checkout(
            organization_id=ORG,
            email="a@b.co.uk",
            plan=stripe_plan(month="price_m"),
            interval="year",
            success_url="s",
            cancel_url="c",
        )
    with pytest.raises(ProviderError, match="did not accept"):
        stripe_provider(
            lambda r: httpx.Response(402, json={"error": {"message": "card secret details"}})
        ).set_cancel(subscription_id="sub_1", cancel=True)

    def down(request):
        raise httpx.ConnectError("boom")

    with pytest.raises(ProviderError, match="Could not reach"):
        stripe_provider(down).set_cancel(subscription_id="sub_1", cancel=True)


def test_stripe_changes_a_plan_on_the_same_subscription_and_cancels_at_the_period_end():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, request.content.decode()))
        if request.method == "GET":
            return httpx.Response(200, json={"items": {"data": [{"id": "si_1"}]}})
        return httpx.Response(200, json={})

    provider = stripe_provider(handler)
    provider.change_plan(
        subscription_id="sub_1", plan=stripe_plan(month="price_m"), interval="month", prorate=True
    )
    provider.change_plan(
        subscription_id="sub_1", plan=stripe_plan(month="price_m"), interval="month", prorate=False
    )
    provider.set_cancel(subscription_id="sub_1", cancel=True)
    provider.set_cancel(subscription_id="sub_1", cancel=False)
    forms = [dict(httpx.QueryParams(c[2])) for c in calls if c[0] == "POST"]
    assert forms[0]["items[0][id]"] == "si_1" and forms[0]["items[0][price]"] == "price_m"
    assert (
        forms[0]["proration_behavior"] == "create_prorations"
        and forms[1]["proration_behavior"] == "none"
    )
    assert forms[2] == {"cancel_at_period_end": "true"} and forms[3] == {
        "cancel_at_period_end": "false"
    }
    assert all(c[1] == "/v1/subscriptions/sub_1" for c in calls)


def paystack_provider(handler=None):
    client = httpx.Client(transport=httpx.MockTransport(handler)) if handler else None
    return PaystackProvider("sk_test", client=client)


def paystack_event(payload):
    body = json.dumps(payload).encode()
    sig = hmac.new(b"sk_test", body, hashlib.sha512).hexdigest()
    return paystack_provider().parse_webhook({"x-paystack-signature": sig}, body, UTC_NOW)


def test_a_paystack_payment_for_a_plan_starts_it_and_one_without_our_ids_only_records_the_invoice():
    charge = {
        "event": "charge.success",
        "data": {
            "reference": "ref_1",
            "amount": 9900,
            "currency": "gbp",
            "paid_at": "2026-10-09T12:00:00Z",
            "customer": {"customer_code": "CUS_1"},
            "metadata": {"organization_id": str(ORG), "plan_code": "starter", "interval": "month"},
        },
    }
    first = paystack_event(charge)[0]
    assert (first.id, first.type, first.organization_id, first.plan_code) == (
        "charge.success:ref_1",
        "checkout_completed",
        ORG,
        "starter",
    )
    assert (
        first.invoice["status"] == "paid"
        and first.invoice["currency"] == "GBP"
        and first.invoice["total_pence"] == 9900
    )
    charge["data"]["metadata"] = None
    renewal = paystack_event(charge)[0]
    assert (
        renewal.type == "invoice_updated"
        and renewal.customer_id == "CUS_1"
        and renewal.organization_id is None
    )


def test_paystack_subscription_messages_are_understood():
    created = paystack_event(
        {
            "event": "subscription.create",
            "data": {
                "subscription_code": "SUB_1",
                "customer": {"customer_code": "CUS_1"},
                "plan": {"plan_code": "PLN_1"},
                "next_payment_date": "2026-11-09T12:00:00Z",
            },
        }
    )[0]
    assert (created.type, created.subscription_id, created.plan_ref, created.status) == (
        "subscription_updated",
        "SUB_1",
        "PLN_1",
        "active",
    )
    assert created.period_end == datetime(2026, 11, 9, 12, tzinfo=UTC)
    not_renew = paystack_event(
        {"event": "subscription.not_renew", "data": {"subscription_code": "SUB_1"}}
    )[0]
    assert not_renew.type == "subscription_updated" and not_renew.cancel_at_period_end is True
    disabled = paystack_event(
        {"event": "subscription.disable", "data": {"subscription_code": "SUB_1"}}
    )[0]
    assert disabled.type == "subscription_canceled" and disabled.status == "canceled"
    failed = paystack_event(
        {
            "event": "invoice.payment_failed",
            "data": {"subscription": {"subscription_code": "SUB_1"}},
        }
    )[0]
    assert failed.type == "invoice_failed" and failed.subscription_id == "SUB_1"
    assert paystack_event({"event": "transfer.success", "data": {}})[0].type == "ignored"


def test_a_paystack_message_without_a_genuine_signature_is_refused():
    with pytest.raises(SignatureError):
        paystack_provider().parse_webhook({"x-paystack-signature": "00"}, b"{}", UTC_NOW)


def test_paystack_checkout_cancel_and_the_things_it_cannot_do():
    seen = []

    def handler(request):
        seen.append(
            (
                request.method,
                request.url.path,
                json.loads(request.content) if request.content else None,
            )
        )
        if request.url.path == "/transaction/initialize":
            return httpx.Response(
                200,
                json={
                    "status": True,
                    "data": {
                        "authorization_url": "https://checkout.paystack.com/x",
                        "reference": "ref_9",
                    },
                },
            )
        if request.url.path.startswith("/subscription/SUB"):
            return httpx.Response(200, json={"status": True, "data": {"email_token": "tok"}})
        return httpx.Response(200, json={"status": True, "data": {}})

    provider = paystack_provider(handler)
    pl = SimpleNamespace(
        code="growth",
        name="Growth",
        currency="GBP",
        provider_prices={"paystack": {"month": "PLN_m"}},
    )
    out = provider.checkout(
        organization_id=ORG,
        email="a@b.co.uk",
        plan=pl,
        interval="month",
        success_url="https://x/ok",
        cancel_url="https://x/no",
    )
    assert out.url == "https://checkout.paystack.com/x" and out.ref == "ref_9"
    sent = seen[0][2]
    assert (
        sent["plan"] == "PLN_m"
        and sent["currency"] == "GBP"
        and sent["callback_url"] == "https://x/ok"
    )
    assert (
        sent["metadata"]["organization_id"] == str(ORG)
        and sent["metadata"]["plan_code"] == "growth"
    )
    provider.set_cancel(subscription_id="SUB_1", cancel=True)
    assert seen[-1] == ("POST", "/subscription/disable", {"code": "SUB_1", "token": "tok"})
    with pytest.raises(ProviderError, match="cannot undo"):
        provider.set_cancel(subscription_id="SUB_1", cancel=False)
    with pytest.raises(ProviderError, match="choosing the new plan"):
        provider.change_plan(subscription_id="SUB_1", plan=pl, interval="month", prorate=True)
    with pytest.raises(ProviderError, match="not set up for Paystack"):
        provider.checkout(
            organization_id=ORG,
            email="a@b.co.uk",
            plan=pl,
            interval="year",
            success_url="s",
            cancel_url="c",
        )
    refused = paystack_provider(
        lambda r: httpx.Response(200, json={"status": False, "message": "private detail"})
    )
    with pytest.raises(ProviderError, match="did not accept") as caught:
        refused.set_cancel(subscription_id="SUB_1", cancel=True)
    assert "private" not in str(caught.value)


def test_the_sandbox_link_is_signed_short_lived_and_refuses_tampering():
    pl = SimpleNamespace(code="growth")
    link = SANDBOX.checkout(
        organization_id=ORG,
        email="a@b.co.uk",
        plan=pl,
        interval="month",
        success_url="https://x/b?org=1&paid=1",
        cancel_url="c",
    )
    token = link.url.split("sandbox=")[1]
    event = SANDBOX.complete(token, datetime.now(UTC))
    assert (event.type, event.organization_id, event.plan_code, event.interval) == (
        "checkout_completed",
        ORG,
        "growth",
        "month",
    )
    assert event.period_end == rules.period_end(event.period_start, "month")
    with pytest.raises(ProviderError, match="expired"):
        SANDBOX.complete(token, datetime.now(UTC) + timedelta(minutes=61))
    SANDBOX.complete(token, datetime.now(UTC) + timedelta(minutes=59))
    body, sig = token.split(".")
    with pytest.raises(ProviderError, match="not valid"):
        SANDBOX.complete(body + "." + "0" * 64, datetime.now(UTC))
    with pytest.raises(ProviderError, match="not valid"):
        SandboxProvider("another secret").complete(token, datetime.now(UTC))
    with pytest.raises(ProviderError, match="not valid"):
        SANDBOX.complete("nodothere", datetime.now(UTC))


def test_the_real_providers_exist_only_when_their_keys_are_set_and_the_sandbox_never_in_production(
    monkeypatch,
):
    from pydantic import SecretStr

    from app.core.config import get_settings

    def with_settings(**changes):
        base = get_settings()
        monkeypatch.setattr(service, "get_settings", lambda: base.model_copy(update=changes))
        return list(service.configured_providers())

    assert with_settings(
        env="dev", stripe_secret_key=None, stripe_webhook_secret=None, paystack_secret_key=None
    ) == ["sandbox"]
    assert (
        with_settings(
            env="prod", stripe_secret_key=None, stripe_webhook_secret=None, paystack_secret_key=None
        )
        == []
    )
    assert with_settings(
        env="prod",
        stripe_secret_key=SecretStr("k"),
        stripe_webhook_secret=SecretStr("w"),
        paystack_secret_key=SecretStr("p"),
    ) == ["stripe", "paystack"]
    assert with_settings(
        env="dev",
        stripe_secret_key=SecretStr("k"),
        stripe_webhook_secret=None,
        paystack_secret_key=SecretStr("p"),
    ) == ["paystack", "sandbox"]


# --- the web side ------------------------------------------------------------------------------------------------


@pytest.fixture
def pay_with_sandbox(app):
    app.dependency_overrides[get_payment_providers] = lambda: {"sandbox": SANDBOX}
    yield
    app.dependency_overrides.pop(get_payment_providers, None)


def base(org):
    return f"{ORGS}/{org}/billing"


def get_billing(api, business, who="owner"):
    res = api.get(base(business[0]), headers=business[2][who])
    assert res.status_code == 200, res.text
    return res.json()


def pay(api, business, plan_code="growth", interval="month"):
    org, _, auth = business
    started = api.post(
        f"{base(org)}/checkout",
        json={"plan_code": plan_code, "interval": interval, "provider": "sandbox"},
        headers=auth["owner"],
    )
    assert started.status_code == 200, started.text
    token = started.json()["url"].split("sandbox=")[1]
    done = api.post(f"{base(org)}/sandbox/complete", json={"token": token}, headers=auth["owner"])
    assert done.status_code == 200, done.text
    return done.json()


def webhook(api, payload, *, secret_body=None):
    body = json.dumps(payload).encode()
    return api.post(
        "/api/v1/billing/webhooks/sandbox",
        content=body,
        headers={
            "x-sandbox-signature": SANDBOX.sign(secret_body or body),
            "content-type": "application/json",
        },
    )


def message(business, kind, event_id, **fields):
    return {"id": event_id, "type": kind, "organization_id": business[0], **fields}


def subscription(db, business):
    with scoped(db, business):
        sub = db.scalars(select(Subscription)).one()
        db.refresh(sub)
        return sub


def invite(api, business, email):
    return api.post(
        f"{ORGS}/{business[0]}/invitations",
        json={"email": email, "role": "viewer"},
        headers=business[2]["owner"],
    )


def test_a_new_business_is_on_a_free_trial_of_the_scale_plan_for_fourteen_days(
    api, db, business, pay_with_sandbox
):
    body = get_billing(api, business)
    sub = body["subscription"]
    assert (sub["plan_code"], sub["status"], sub["status_label"], sub["trial_days_left"]) == (
        "scale",
        "trialing",
        "Free trial",
        14,
    )
    assert sub["message"].startswith("You are on a free trial of the Scale plan: 14 days left.")
    assert body["can_manage"] is True and body["providers"] == ["sandbox"]
    features = {f["feature"]: f for f in body["features"]}
    assert features["members"]["limit"] == 25 and features["members"]["used"] == 2
    assert features["integrations"]["limit"] == 10 and features["integrations"]["used"] == 0
    assert features["scheduled_reports"]["limit"] == 20
    assert features["ai_assistant"]["enabled"] is True and features["ai_assistant"]["used"] is None
    assert features["members"]["text"] == "Up to 25"


def test_the_trial_length_comes_from_the_settings_and_counts_down(
    api, db, business, pay_with_sandbox
):
    get_billing(api, business)
    sub = subscription(db, business)
    assert (
        timedelta(days=13, hours=23) < sub.trial_ends_at - datetime.now(UTC) <= timedelta(days=14)
    )
    with scoped(db, business):
        db.execute(
            update(Subscription).values(
                trial_ends_at=datetime.now(UTC) + timedelta(days=2, hours=3)
            )
        )
    assert get_billing(api, business)["subscription"]["trial_days_left"] == 3


def test_anyone_in_the_business_can_see_the_plan_but_only_the_owner_can_change_it(
    api, db, business, pay_with_sandbox
):
    org, other, auth = business
    seen = api.get(base(org), headers=auth["viewer"])
    assert seen.status_code == 200 and seen.json()["can_manage"] is False
    assert api.get(f"{base(org)}/plans", headers=auth["viewer"]).status_code == 200
    for method, path, body in (
        ("post", "checkout", {"plan_code": "growth"}),
        ("post", "change", {"plan_code": "growth"}),
        ("post", "cancel", None),
        ("post", "resume", None),
        ("get", "invoices", None),
    ):
        res = getattr(api, method)(
            f"{base(org)}/{path}", headers=auth["viewer"], **({"json": body} if body else {})
        )
        assert res.status_code == 403, (path, res.text)
    assert api.get(base(org), headers=auth["other"]).status_code in (403, 404)
    assert api.get(base(org)).status_code == 401


def test_the_plans_are_listed_with_prices_before_vat_and_what_each_includes(
    api, business, pay_with_sandbox
):
    res = api.get(f"{base(business[0])}/plans", headers=business[2]["owner"])
    plans = {p["code"]: p for p in res.json()}
    assert list(plans) == ["starter", "growth", "scale", "corporate"]
    starter = plans["starter"]
    assert (starter["price_month"], starter["price_year"], starter["year_saving"]) == (
        "£99.00",
        "£990.00",
        "£198.00",
    )
    assert starter["vat_note"] == "Prices exclude VAT (20%), which is added at checkout."
    assert [f["text"] for f in starter["features"]] == [
        "Up to 3",
        "Up to 1",
        "Up to 1",
        "Not included",
    ]
    assert plans["growth"]["features"][3]["text"] == "Included"
    corporate = plans["corporate"]
    assert corporate["self_serve"] is False and corporate["price_month"] is None
    assert [f["text"] for f in corporate["features"]] == [
        "No limit",
        "No limit",
        "No limit",
        "Included",
    ]
    assert corporate["vat_note"] == "Talk to us about a plan to suit your organisation."
    assert not any(p["current"] for p in plans.values())  # a trial is not a plan bought


def test_paying_through_the_sandbox_moves_the_business_onto_the_plan_and_writes_an_invoice(
    api, db, business, pay_with_sandbox
):
    body = pay(api, business, "growth", "month")
    sub = body["subscription"]
    assert (sub["plan_code"], sub["status"], sub["interval"], sub["provider"]) == (
        "growth",
        "active",
        "month",
        "sandbox",
    )
    assert sub["trial_days_left"] is None and sub["trial_ends_at"] is None
    assert sub["message"].startswith("You are on the Growth plan, paid monthly. It renews on ")
    assert {f["feature"]: f["limit"] for f in body["features"]} == {
        "members": 10,
        "integrations": 3,
        "scheduled_reports": 5,
        "ai_assistant": None,
    }
    plans = api.get(f"{base(business[0])}/plans", headers=business[2]["owner"]).json()
    assert [p["code"] for p in plans if p["current"]] == ["growth"]
    stored = subscription(db, business)
    assert stored.provider_subscription_id.startswith(
        "sandbox_sub_"
    ) and stored.current_period_end == rules.period_end(stored.current_period_start, "month")
    audits = db.scalars(select(AuditLog.action)).all()
    assert "billing.checkout_started" in audits and "billing.subscription_changed" in audits


def test_a_yearly_plan_lasts_a_year(api, db, business, pay_with_sandbox):
    pay(api, business, "starter", "year")
    sub = subscription(db, business)
    assert sub.interval == "year" and sub.current_period_end == rules.add_months(
        sub.current_period_start, 12
    )


def test_a_plan_that_is_not_for_sale_or_does_not_exist_cannot_be_bought(
    api, business, pay_with_sandbox
):
    org, _, auth = business
    corporate = api.post(
        f"{base(org)}/checkout", json={"plan_code": "corporate"}, headers=auth["owner"]
    )
    assert corporate.status_code == 422 and corporate.json()["error"]["code"] == "not_for_sale"
    missing = api.post(
        f"{base(org)}/checkout", json={"plan_code": "nothing"}, headers=auth["owner"]
    )
    assert missing.status_code == 404
    bad = api.post(
        f"{base(org)}/checkout",
        json={"plan_code": "growth", "interval": "week"},
        headers=auth["owner"],
    )
    assert bad.status_code == 422
    unknown_provider = api.post(
        f"{base(org)}/checkout",
        json={"plan_code": "growth", "provider": "stripe"},
        headers=auth["owner"],
    )
    assert (
        unknown_provider.status_code == 422
        and unknown_provider.json()["error"]["code"] == "provider_unavailable"
    )


def test_a_business_that_already_pays_is_told_to_change_plan_not_buy_again(
    api, business, pay_with_sandbox
):
    pay(api, business, "starter")
    again = api.post(
        f"{base(business[0])}/checkout",
        json={"plan_code": "growth", "provider": "sandbox"},
        headers=business[2]["owner"],
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "has_a_plan"


def test_a_payment_link_for_another_business_is_refused_and_so_is_rubbish(
    api, db, business, pay_with_sandbox
):
    org, other, auth = business
    started = api.post(
        f"{base(other)}/checkout",
        json={"plan_code": "growth", "provider": "sandbox"},
        headers=auth["other"],
    )
    token = started.json()["url"].split("sandbox=")[1]
    stolen = api.post(f"{base(org)}/sandbox/complete", json={"token": token}, headers=auth["owner"])
    assert stolen.status_code == 403 and stolen.json()["error"]["code"] == "wrong_business"
    assert get_billing(api, business)["subscription"]["status"] == "trialing"
    junk = api.post(
        f"{base(org)}/sandbox/complete", json={"token": "x" * 20}, headers=auth["owner"]
    )
    assert junk.status_code == 422 and junk.json()["error"]["code"] == "bad_payment_link"
    short = api.post(
        f"{base(org)}/sandbox/complete", json={"token": "short"}, headers=auth["owner"]
    )
    assert short.status_code == 422


def test_there_is_no_sandbox_to_pay_in_when_it_is_not_one_of_the_providers(app, api, business):
    app.dependency_overrides[get_payment_providers] = lambda: {}
    try:
        res = api.post(
            f"{base(business[0])}/sandbox/complete",
            json={"token": "x" * 20},
            headers=business[2]["owner"],
        )
        assert res.status_code == 404
    finally:
        app.dependency_overrides.pop(get_payment_providers, None)


def test_one_business_cannot_see_or_change_another_ones_plan(api, db, business, pay_with_sandbox):
    org, other, auth = business
    pay(api, business, "scale")
    assert api.get(base(org), headers=auth["other"]).status_code in (403, 404)
    assert api.post(f"{base(org)}/cancel", headers=auth["other"]).status_code in (403, 404)
    assert api.get(f"{base(org)}/invoices", headers=auth["other"]).status_code in (403, 404)
    theirs = api.get(base(other), headers=auth["other"]).json()["subscription"]
    assert theirs["status"] == "trialing"
    with scoped(db, business, 1):
        assert db.scalars(select(Subscription.status)).all() == ["trialing"]


# --- changing, cancelling, resuming ------------------------------------------------------------------------------


def test_moving_to_a_dearer_plan_takes_effect_now(api, db, business, pay_with_sandbox):
    pay(api, business, "starter")
    res = api.post(
        f"{base(business[0])}/change", json={"plan_code": "scale"}, headers=business[2]["owner"]
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["result"] == "changed_now" and body["message"] == "You are now on the Scale plan."
    assert body["billing"]["subscription"]["plan_code"] == "scale"
    assert subscription(db, business).scheduled_plan_id is None


def test_moving_to_a_cheaper_plan_waits_for_the_end_of_the_period_that_was_paid_for(
    api, db, business, pay_with_sandbox
):
    pay(api, business, "scale")
    res = api.post(
        f"{base(business[0])}/change", json={"plan_code": "growth"}, headers=business[2]["owner"]
    )
    assert res.status_code == 200, res.text
    body = res.json()
    sub = body["billing"]["subscription"]
    assert body["result"] == "scheduled" and sub["plan_code"] == "scale"
    assert (sub["scheduled_plan_code"], sub["scheduled_plan_name"]) == ("growth", "Growth")
    end = f"{subscription(db, business).current_period_end:%d/%m/%Y}"
    assert (
        body["message"]
        == f"You will move to the Growth plan on {end}. Until then you keep the Scale plan."
    )


def test_a_cheaper_plan_that_is_too_small_for_what_the_business_has_is_refused_in_words(
    api, db, business, pay_with_sandbox
):
    org, _, auth = business
    pay(api, business, "scale")
    for n in range(2):  # the two members plus two invitations is four, more than Starter's three
        assert invite(api, business, f"new{n}@acme.co.uk").status_code == 201
    res = api.post(f"{base(org)}/change", json={"plan_code": "starter"}, headers=auth["owner"])
    assert res.status_code == 409 and res.json()["error"]["code"] == "too_big_for_plan"
    assert (
        "You have 4 team members and the Starter plan allows 3." in res.json()["error"]["message"]
    )
    assert get_billing(api, business)["subscription"]["scheduled_plan_code"] is None


def test_a_move_to_the_same_plan_or_before_choosing_one_is_refused(api, business, pay_with_sandbox):
    org, _, auth = business
    first = api.post(f"{base(org)}/change", json={"plan_code": "growth"}, headers=auth["owner"])
    assert first.status_code == 409 and first.json()["error"]["code"] == "no_plan_yet"
    pay(api, business, "growth")
    same = api.post(f"{base(org)}/change", json={"plan_code": "growth"}, headers=auth["owner"])
    assert same.status_code == 409 and same.json()["error"]["code"] == "no_change"
    corporate = api.post(
        f"{base(org)}/change", json={"plan_code": "corporate"}, headers=auth["owner"]
    )
    assert corporate.status_code == 422


def test_the_same_plan_paid_yearly_is_a_change_that_starts_now_but_back_to_monthly_waits(
    api, db, business, pay_with_sandbox
):
    org, _, auth = business
    pay(api, business, "growth", "month")
    yearly = api.post(
        f"{base(org)}/change",
        json={"plan_code": "growth", "interval": "year"},
        headers=auth["owner"],
    ).json()
    assert (
        yearly["result"] == "changed_now"
        and yearly["billing"]["subscription"]["interval"] == "year"
    )
    monthly = api.post(
        f"{base(org)}/change",
        json={"plan_code": "growth", "interval": "month"},
        headers=auth["owner"],
    ).json()
    assert monthly["result"] == "scheduled"
    assert (
        subscription(db, business).interval == "year"
        and subscription(db, business).scheduled_interval == "month"
    )


def test_cancelling_keeps_the_plan_until_the_period_ends_and_can_be_taken_back(
    api, db, business, pay_with_sandbox
):
    org, _, auth = business
    pay(api, business, "growth")
    cancelled = api.post(f"{base(org)}/cancel", headers=auth["owner"]).json()["subscription"]
    assert cancelled["status"] == "active" and cancelled["cancel_at_period_end"] is True
    assert "will not renew" in cancelled["message"]
    again = api.post(f"{base(org)}/cancel", headers=auth["owner"])
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_canceling"
    resumed = api.post(f"{base(org)}/resume", headers=auth["owner"]).json()["subscription"]
    assert resumed["cancel_at_period_end"] is False and "renews on" in resumed["message"]
    nothing = api.post(f"{base(org)}/resume", headers=auth["owner"])
    assert nothing.status_code == 409 and nothing.json()["error"]["code"] == "nothing_to_resume"


def test_there_is_nothing_to_cancel_during_the_trial(api, business, pay_with_sandbox):
    res = api.post(f"{base(business[0])}/cancel", headers=business[2]["owner"])
    assert res.status_code == 409 and res.json()["error"]["code"] == "nothing_to_cancel"


def test_a_cancelled_plan_ends_when_its_period_does(api, db, business, pay_with_sandbox):
    org, _, auth = business
    pay(api, business, "growth")
    api.post(f"{base(org)}/cancel", headers=auth["owner"])
    with scoped(db, business):
        db.execute(
            update(Subscription).values(current_period_end=datetime.now(UTC) - timedelta(hours=1))
        )
    body = get_billing(api, business)
    assert body["subscription"]["status"] == "canceled"
    assert body["subscription"]["message"].startswith("Your subscription has ended.")
    assert all(f["limit"] == 0 or f["enabled"] is False for f in body["features"])


# --- messages from the provider -----------------------------------------------------------------------------------


def test_a_message_without_a_genuine_signature_changes_nothing(api, db, business, pay_with_sandbox):
    pay(api, business, "growth")
    forged = message(business, "subscription_canceled", "evt_forged")
    res = webhook(api, forged, secret_body=b"some other body")
    assert res.status_code == 400 and res.json()["error"]["code"] == "bad_signature"
    assert subscription(db, business).status == "active"
    with scoped(db, business):
        assert "evt_forged" not in db.scalars(select(BillingEvent.event_id)).all()
    unsigned = api.post(
        "/api/v1/billing/webhooks/sandbox",
        content=b"{}",
        headers={"content-type": "application/json"},
    )
    assert unsigned.status_code == 400


def test_a_message_that_cannot_be_read_is_refused_and_an_unknown_provider_is_not_found(
    api, pay_with_sandbox
):
    junk = b"not json"
    res = api.post(
        "/api/v1/billing/webhooks/sandbox",
        content=junk,
        headers={"x-sandbox-signature": SANDBOX.sign(junk)},
    )
    assert res.status_code == 400 and res.json()["error"]["code"] == "bad_message"
    missing_field = b'{"type": "ignored"}'
    res = api.post(
        "/api/v1/billing/webhooks/sandbox",
        content=missing_field,
        headers={"x-sandbox-signature": SANDBOX.sign(missing_field)},
    )
    assert res.status_code == 400 and res.json()["error"]["code"] == "bad_message"
    assert api.post("/api/v1/billing/webhooks/stripe", content=b"{}").status_code == 404


def test_a_message_is_acted_on_once_however_many_times_it_is_sent(
    api, db, business, pay_with_sandbox
):
    pay(api, business, "growth")
    later = int((datetime.now(UTC) + timedelta(days=31)).timestamp())
    renewal = message(
        business,
        "subscription_updated",
        "evt_renew",
        status="active",
        period_start=later,
        period_end=later + 2_592_000,
    )
    first = webhook(api, renewal).json()
    again = webhook(api, renewal).json()
    assert (first["received"], first["processed"], first["duplicates"]) == (1, 1, 0)
    assert (again["processed"], again["duplicates"]) == (0, 1)
    with scoped(db, business):
        assert (
            len(db.scalars(select(BillingEvent).where(BillingEvent.event_id == "evt_renew")).all())
            == 1
        )


def test_a_renewal_moves_the_period_on_and_applies_a_waiting_move_to_a_cheaper_plan(
    api, db, business, pay_with_sandbox
):
    pay(api, business, "scale")
    api.post(
        f"{base(business[0])}/change", json={"plan_code": "growth"}, headers=business[2]["owner"]
    )
    assert subscription(db, business).scheduled_plan_id is not None
    start = int((datetime.now(UTC) + timedelta(days=31)).timestamp())
    res = webhook(
        api,
        message(
            business,
            "subscription_updated",
            "evt_r1",
            status="active",
            period_start=start,
            period_end=start + 2_592_000,
        ),
    )
    assert res.json()["processed"] == 1
    sub = get_billing(api, business)["subscription"]
    assert sub["plan_code"] == "growth" and sub["scheduled_plan_code"] is None
    stored = subscription(db, business)
    assert (
        stored.current_period_start == datetime.fromtimestamp(start, tz=UTC)
        and stored.scheduled_interval is None
    )


def test_an_update_inside_the_same_period_does_not_apply_a_waiting_move_early(
    api, db, business, pay_with_sandbox
):
    pay(api, business, "scale")
    api.post(
        f"{base(business[0])}/change", json={"plan_code": "growth"}, headers=business[2]["owner"]
    )
    stored = subscription(db, business)
    same = int(stored.current_period_start.timestamp())
    webhook(
        api,
        message(
            business,
            "subscription_updated",
            "evt_same",
            status="active",
            period_start=same,
            period_end=same + 2_592_000,
        ),
    )
    assert get_billing(api, business)["subscription"]["plan_code"] == "scale"
    webhook(
        api,
        message(business, "subscription_updated", "evt_same2", plan_code="growth", status="active"),
    )
    after = get_billing(api, business)["subscription"]
    assert after["plan_code"] == "scale" and after["scheduled_plan_code"] == "growth"


def test_the_provider_changing_the_plan_by_its_own_price_id_is_followed(
    api, db, business, pay_with_sandbox
):
    pay(api, business, "starter")
    with scoped(db, business):
        db.execute(
            update(Plan)
            .where(Plan.code == "growth")
            .values(provider_prices={"sandbox": {"month": "price_growth_m"}})
        )
    res = webhook(
        api,
        {
            **message(business, "subscription_updated", "evt_ref", status="active"),
            "plan_ref": "ignored-field",
        },
    )
    assert res.json()["processed"] == 1
    sub = subscription(db, business)
    assert sub.plan_id == db.scalars(select(Plan.id).where(Plan.code == "starter")).one()
    assert (
        service._plan_for_event(
            db, "sandbox", SimpleNamespace(plan_code=None, plan_ref="price_growth_m")
        ).code
        == "growth"
    )
    assert (
        service._plan_for_event(db, "sandbox", SimpleNamespace(plan_code=None, plan_ref="nothing"))
        is None
    )
    assert (
        service._plan_for_event(db, "sandbox", SimpleNamespace(plan_code=None, plan_ref=None))
        is None
    )


def test_a_failed_payment_makes_the_plan_overdue_tells_the_owner_and_a_paid_invoice_recovers_it(
    api, db, business, pay_with_sandbox
):
    pay(api, business, "growth")
    failed = webhook(
        api,
        message(
            business,
            "invoice_failed",
            "evt_f1",
            invoice={"id": "inv_1", "total_pence": 11880, "net_pence": 9900},
        ),
    )
    assert failed.json()["processed"] == 1
    body = get_billing(api, business)["subscription"]
    assert body["status"] == "past_due" and body["status_label"] == "Payment overdue"
    assert (
        body["message"]
        == "Your last payment did not go through. Update your payment details to keep your plan."
    )
    from app.models.alerts import Notification

    with scoped(db, business):
        titles = db.scalars(select(Notification.title)).all()
    assert titles == ["Your payment did not go through"]
    paid = webhook(
        api,
        message(
            business,
            "invoice_updated",
            "evt_f2",
            invoice={"id": "inv_1", "status": "paid", "total_pence": 11880, "net_pence": 9900},
        ),
    )
    assert paid.json()["processed"] == 1
    assert get_billing(api, business)["subscription"]["status"] == "active"


def test_an_overdue_plan_still_works_for_a_week_after_its_period_and_then_ends(
    api, db, business, pay_with_sandbox
):
    pay(api, business, "starter")
    webhook(api, message(business, "invoice_failed", "evt_f"))
    with scoped(db, business):
        db.execute(
            update(Subscription).values(current_period_end=datetime.now(UTC) - timedelta(days=6))
        )
    assert invite(api, business, "still@acme.co.uk").status_code == 201
    with scoped(db, business):
        db.execute(
            update(Subscription).values(current_period_end=datetime.now(UTC) - timedelta(days=8))
        )
    ended = invite(api, business, "late@acme.co.uk")
    assert ended.status_code == 402 and ended.json()["error"]["details"]["status"] == "expired"


def test_the_provider_ending_the_subscription_ends_the_plan(api, db, business, pay_with_sandbox):
    pay(api, business, "growth")
    res = webhook(api, message(business, "subscription_canceled", "evt_end", status="canceled"))
    assert res.json()["processed"] == 1
    sub = get_billing(api, business)["subscription"]
    assert sub["status"] == "canceled"
    stored = subscription(db, business)
    assert stored.canceled_at is not None and stored.cancel_at_period_end is False


def test_a_message_that_cannot_be_applied_is_recorded_as_failed_and_does_not_stop_the_rest(
    api, db, business, pay_with_sandbox
):
    bad = webhook(
        api,
        message(business, "checkout_completed", "evt_bad", plan_code="corporate", status="active"),
    ).json()
    assert (bad["received"], bad["processed"], bad["failed"]) == (1, 0, 1)
    assert get_billing(api, business)["subscription"]["status"] == "trialing"
    with scoped(db, business):
        event = db.scalars(select(BillingEvent).where(BillingEvent.event_id == "evt_bad")).one()
    assert event.status == "failed" and "unknown plan" in event.error
    unknown = webhook(
        api, message(business, "checkout_completed", "evt_bad2", plan_code="nonsense")
    ).json()
    assert unknown["failed"] == 1
    ignored = webhook(api, message(business, "ignored", "evt_ign")).json()
    assert (ignored["ignored"], ignored["processed"]) == (1, 0)


def test_a_message_for_a_business_we_do_not_know_is_set_aside_and_one_found_by_the_providers_ids_is_matched(
    api, db, business, pay_with_sandbox
):
    stranger = webhook(
        api,
        {
            "id": "evt_x",
            "type": "subscription_updated",
            "subscription_id": "nobody",
            "customer_id": "nobody",
        },
    ).json()
    assert (stranger["received"], stranger["unmatched"], stranger["processed"]) == (1, 1, 0)
    pay(api, business, "growth")
    stored = subscription(db, business)
    sub_id, customer_id = stored.provider_subscription_id, stored.provider_customer_id
    found = webhook(
        api,
        {
            "id": "evt_y",
            "type": "subscription_updated",
            "subscription_id": sub_id,
            "status": "past_due",
        },
    ).json()
    assert found["processed"] == 1
    by_customer = webhook(
        api,
        {
            "id": "evt_z",
            "type": "subscription_updated",
            "customer_id": customer_id,
            "status": "active",
        },
    ).json()
    assert by_customer["processed"] == 1
    assert get_billing(api, business)["subscription"]["status"] == "active"
    with scoped(db, business, 1):
        assert db.scalars(select(BillingEvent)).all() == []  # the other business heard nothing


def test_a_message_about_one_business_never_touches_another(api, db, business, pay_with_sandbox):
    pay(api, business, "growth")
    webhook(api, message(business, "subscription_canceled", "evt_c", status="canceled"))
    with scoped(db, business, 1):
        assert (
            db.scalars(select(Subscription.status)).all() == []
        )  # nobody has looked at its plan yet
    other = (business[1], None, business[2])
    assert (
        api.get(base(other[0]), headers=business[2]["other"]).json()["subscription"]["status"]
        == "trialing"
    )


def test_invoices_are_kept_once_listed_newest_first_and_the_hosted_link_must_be_secure(
    api, db, business, pay_with_sandbox
):
    pay(api, business, "growth")
    first = {
        "id": "inv_a",
        "number": "INV-1",
        "status": "paid",
        "currency": "GBP",
        "net_pence": 19900,
        "total_pence": 23880,
        "period_start": 1_790_000_000,
        "period_end": 1_792_592_000,
        "paid_at": 1_790_000_100,
        "hosted_url": "https://pay.example.com/inv_a",
        "lines": [{"description": "Growth plan", "amount_pence": 19900}],
    }
    webhook(api, message(business, "invoice_updated", "evt_i1", invoice=first))
    insecure = {
        **first,
        "id": "inv_b",
        "number": "INV-2",
        "hosted_url": "http://pay.example.com/inv_b",
        "net_pence": 5000,
        "total_pence": 6000,
    }
    webhook(api, message(business, "invoice_updated", "evt_i2", invoice=insecure))
    webhook(
        api, message(business, "invoice_updated", "evt_i3", invoice={**first, "number": "INV-1A"})
    )
    res = api.get(f"{base(business[0])}/invoices", headers=business[2]["owner"])
    assert res.status_code == 200
    rows = {r["number"]: r for r in res.json()}
    assert set(rows) == {"INV-1A", "INV-2"}  # the repeat updated the first, it was not added again
    one = rows["INV-1A"]
    assert (one["net"], one["vat"], one["total"], one["status_label"]) == (
        "£199.00",
        "£39.80",
        "£238.80",
        "Paid",
    )
    assert (
        one["hosted_url"] == "https://pay.example.com/inv_a" and rows["INV-2"]["hosted_url"] is None
    )
    assert one["lines"] == [{"description": "Growth plan", "amount": "£199.00"}]
    assert str(one["period_start"]) == "2026-09-21" and str(one["period_end"]) == "2026-10-21"
    assert one["paid_at"] is not None
    with scoped(db, business, 1):
        assert db.scalars(select(Invoice)).all() == []
    assert api.get(f"{base(business[1])}/invoices", headers=business[2]["owner"]).status_code in (
        403,
        404,
    )


def test_an_invoices_net_never_goes_above_its_total(api, db, business, pay_with_sandbox):
    pay(api, business, "growth")
    webhook(
        api,
        message(
            business,
            "invoice_updated",
            "evt_odd",
            invoice={"id": "inv_odd", "status": "paid", "net_pence": 5000, "total_pence": 4000},
        ),
    )
    with scoped(db, business):
        row = db.scalars(select(Invoice).where(Invoice.provider_invoice_id == "inv_odd")).one()
    assert (row.net_pence, row.vat_pence, row.total_pence) == (4000, 0, 4000)


# --- the sandbox keeps time ---------------------------------------------------------------------------------------


def test_a_sandbox_plan_renews_when_its_period_ends_with_an_invoice_and_keeps_renewing(
    api, db, business, pay_with_sandbox
):
    pay(api, business, "growth")
    org = uuid.UUID(business[0])
    start = subscription(db, business).current_period_start
    with scoped(db, business):
        rolled = service.roll_sandbox(db, org, start + timedelta(days=95))
        sub = db.scalars(select(Subscription)).one()
        invoices = db.scalars(select(Invoice).order_by(Invoice.created_at)).all()
    assert rolled == 3
    assert sub.current_period_start == rules.add_months(
        start, 3
    ) and sub.current_period_end == rules.add_months(start, 4)
    assert len(invoices) == 3 and {i.status for i in invoices} == {"paid"}
    assert (
        invoices[0].net_pence == 19900
        and invoices[0].vat_pence == 3980
        and invoices[0].total_pence == 23880
    )
    assert invoices[0].number == f"SBX-{rules.add_months(start, 1):%Y%m%d}"
    with scoped(db, business):
        assert (
            service.roll_sandbox(db, org, start + timedelta(days=95)) == 0
        )  # nothing more is due yet


def test_a_sandbox_plan_set_to_end_ends_and_does_not_renew(api, db, business, pay_with_sandbox):
    pay(api, business, "growth")
    api.post(f"{base(business[0])}/cancel", headers=business[2]["owner"])
    org = uuid.UUID(business[0])
    end = subscription(db, business).current_period_end
    with scoped(db, business):
        assert service.roll_sandbox(db, org, end + timedelta(hours=1)) == 1
        sub = db.scalars(select(Subscription)).one()
        assert (sub.status, sub.cancel_at_period_end, sub.canceled_at) == ("canceled", False, end)
        assert service.roll_sandbox(db, org, end + timedelta(days=40)) == 0
        assert db.scalars(select(Invoice)).all() == []


def test_a_sandbox_renewal_applies_a_waiting_move_to_a_cheaper_plan(
    api, db, business, pay_with_sandbox
):
    pay(api, business, "scale", "month")
    api.post(
        f"{base(business[0])}/change",
        json={"plan_code": "starter", "interval": "year"},
        headers=business[2]["owner"],
    )
    org = uuid.UUID(business[0])
    end = subscription(db, business).current_period_end
    with scoped(db, business):
        service.roll_sandbox(db, org, end + timedelta(minutes=1))
        sub = db.scalars(select(Subscription)).one()
        assert (db.get(Plan, sub.plan_id).code, sub.interval, sub.scheduled_plan_id) == (
            "starter",
            "year",
            None,
        )


def test_rolling_does_nothing_for_a_trial_a_real_provider_or_no_subscription(
    api, db, business, pay_with_sandbox
):
    org = uuid.UUID(business[0])
    with scoped(db, business):
        assert service.roll_sandbox(db, org, datetime.now(UTC)) == 0  # no subscription yet
    get_billing(api, business)
    with scoped(db, business):
        assert (
            service.roll_sandbox(db, org, datetime.now(UTC) + timedelta(days=400)) == 0
        )  # a trial
    pay(api, business, "growth")
    with scoped(db, business):
        db.execute(update(Subscription).values(provider="stripe"))
        assert service.roll_sandbox(db, org, datetime.now(UTC) + timedelta(days=400)) == 0
        db.execute(update(Subscription).values(provider="sandbox", status="past_due"))
        assert service.roll_sandbox(db, org, datetime.now(UTC) + timedelta(days=400)) == 0


def test_rolling_never_loops_for_ever_when_a_very_old_plan_is_picked_up(
    api, db, business, pay_with_sandbox
):
    pay(api, business, "starter")
    org = uuid.UUID(business[0])
    start = subscription(db, business).current_period_start
    with scoped(db, business):
        assert service.roll_sandbox(db, org, start + timedelta(days=365 * 10)) == service.MAX_ROLLS


# --- what the plan lets the business add -------------------------------------------------------------------------------------------------


def expire_trial(db, business):
    with scoped(db, business):
        service.ensure_subscription(db, uuid.UUID(business[0]), datetime.now(UTC))
        db.execute(
            update(Subscription).values(trial_ends_at=datetime.now(UTC) - timedelta(minutes=1))
        )


def test_when_the_trial_ends_people_cannot_be_invited_but_what_is_already_there_stays_readable(
    api, db, business, pay_with_sandbox
):
    org, _, auth = business
    get_billing(api, business)
    assert invite(api, business, "during@acme.co.uk").status_code == 201
    expire_trial(db, business)
    res = invite(api, business, "after@acme.co.uk")
    assert res.status_code == 402
    error = res.json()["error"]
    assert (
        error["code"] == "plan_limit"
        and error["message"]
        == "Your plan has ended. Choose a plan to carry on adding to your business."
    )
    assert error["details"]["feature"] == "members" and error["details"]["status"] == "expired"
    body = get_billing(api, business)
    assert body["subscription"]["status"] == "expired" and body["subscription"][
        "message"
    ].startswith("Your free trial has ended.")
    assert api.get(f"{ORGS}/{org}/invitations", headers=auth["owner"]).status_code == 200
    assert api.get(f"{ORGS}/{org}/members", headers=auth["owner"]).status_code == 200


def test_buying_a_plan_after_the_trial_turns_the_gates_back_on(api, db, business, pay_with_sandbox):
    expire_trial(db, business)
    assert invite(api, business, "x@acme.co.uk").status_code == 402
    pay(api, business, "growth")
    assert invite(api, business, "x@acme.co.uk").status_code == 201


def test_the_members_limit_counts_people_and_open_invitations_and_a_new_invite_replaces_an_old_one(
    api, db, business, pay_with_sandbox
):
    pay(api, business, "starter")  # three team members: the owner and viewer, plus one more
    assert invite(api, business, "third@acme.co.uk").status_code == 201
    refused = invite(api, business, "fourth@acme.co.uk")
    assert refused.status_code == 402
    assert (
        refused.json()["error"]["message"]
        == "The Starter plan includes up to 3 team members and you already have 3. Upgrade to add more."
    )
    assert refused.json()["error"]["details"] == {
        "feature": "members",
        "limit": 3,
        "used": 3,
        "plan": "starter",
        "status": "active",
    }
    assert (
        invite(
            api,
            business,
            "third@acme.co.uk",
        ).status_code
        == 201
    )  # re-inviting the same person takes the old invitation's place
    api.post(
        f"{base(business[0])}/change", json={"plan_code": "growth"}, headers=business[2]["owner"]
    )
    assert invite(api, business, "fourth@acme.co.uk").status_code == 201


def test_a_plan_that_leaves_a_feature_out_says_so_and_a_bigger_one_gives_it(
    api, db, business, pay_with_sandbox
):
    org, _, auth = business
    pay(api, business, "starter")
    ask = api.post(
        f"{ORGS}/{org}/assistant/ask",
        json={"message": "How is my business doing?"},
        headers=auth["owner"],
    )
    assert ask.status_code == 402
    assert (
        ask.json()["error"]["message"]
        == "The AI assistant is not part of the Starter plan. Upgrade to use it."
    )
    api.post(f"{base(org)}/change", json={"plan_code": "growth"}, headers=auth["owner"])
    assert (
        api.post(
            f"{ORGS}/{org}/assistant/ask",
            json={"message": "How is my business doing?"},
            headers=auth["owner"],
        ).status_code
        != 402
    )


def test_the_assistant_is_off_when_the_trial_ends(api, db, business, pay_with_sandbox):
    org, _, auth = business
    expire_trial(db, business)
    res = api.post(
        f"{ORGS}/{org}/assistant/ask",
        json={"message": "How is my business doing?"},
        headers=auth["owner"],
    )
    assert res.status_code == 402 and res.json()["error"]["details"]["feature"] == "ai_assistant"


def test_check_names_the_feature_the_limit_and_what_is_used(db, business):
    org = uuid.UUID(business[0])
    with scoped(db, business):
        service.check(db, org, "members")
        service.check(db, org, "ai_assistant")
        db.execute(update(Subscription).values(status="expired"))
        for feature in ("members", "integrations", "scheduled_reports", "ai_assistant"):
            with pytest.raises(service.PlanLimitError) as caught:
                service.check(db, org, feature)
            assert caught.value.details["feature"] == feature


def test_use_is_counted_for_people_connections_and_schedules(db, business):
    with scoped(db, business):
        assert service.used(db, "members") == 2
        assert service.used(db, "integrations") == 0
        assert service.used(db, "scheduled_reports") == 0
        assert service.used(db, "ai_assistant") == 0
    with scoped(db, business, 1):
        assert service.used(db, "members") == 1


# --- the rows themselves --------------------------------------------------------------------------------------------------


def test_the_starting_plans_are_in_the_database_with_what_each_includes(db):
    plans = {p.code: p for p in db.scalars(select(Plan))}
    assert set(plans) == {"starter", "growth", "scale", "corporate"}
    assert (plans["starter"].price_month_pence, plans["starter"].price_year_pence) == (9900, 99000)
    assert plans["corporate"].self_serve is False and plans["corporate"].price_month_pence is None
    from app.models.billing import FeatureEntitlement

    rows = {
        (db.get(Plan, r.plan_id).code, r.feature): (r.enabled, r.limit)
        for r in db.scalars(select(FeatureEntitlement))
    }
    assert rows[("starter", "members")] == (True, 3) and rows[("starter", "ai_assistant")] == (
        False,
        None,
    )
    assert rows[("scale", "scheduled_reports")] == (True, 20) and rows[
        ("corporate", "members")
    ] == (True, None)
    assert len(rows) == 16


@pytest.mark.parametrize(
    "bad",
    [
        {"code": "Bad Code"},
        {"name": "  "},
        {"price_month_pence": -1},
        {"price_year_pence": -1},
        {"self_serve": True, "price_month_pence": None},
        {"currency": "gbp"},
    ],
)
def test_a_plan_cannot_be_written_with_nonsense(db, bad):
    fields = {
        "code": "extra",
        "name": "Extra",
        "description": "d",
        "price_month_pence": 100,
        "price_year_pence": 1000,
        "currency": "GBP",
        "self_serve": True,
    } | bad
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(Plan(**fields))
        db.flush()


def test_two_plans_cannot_share_a_code_and_a_business_has_one_subscription(db, business):
    existing = db.scalars(select(Plan)).first()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(
            Plan(
                code=existing.code,
                name="Again",
                description="d",
                price_month_pence=1,
                self_serve=True,
            )
        )
        db.flush()
    with scoped(db, business):
        db.add(
            Subscription(
                organization_id=uuid.UUID(business[0]), plan_id=existing.id, status="trialing"
            )
        )
        db.flush()
        with pytest.raises(IntegrityError), db.begin_nested():
            db.add(
                Subscription(
                    organization_id=uuid.UUID(business[0]), plan_id=existing.id, status="active"
                )
            )
            db.flush()


def test_an_invoice_must_add_up_and_the_same_provider_invoice_cannot_be_kept_twice(db, business):
    org = uuid.UUID(business[0])
    with scoped(db, business):
        with pytest.raises(IntegrityError), db.begin_nested():
            db.add(
                Invoice(
                    organization_id=org,
                    provider="sandbox",
                    provider_invoice_id="a",
                    status="paid",
                    net_pence=100,
                    vat_pence=20,
                    total_pence=130,
                    issued_at=datetime.now(UTC),
                )
            )
            db.flush()
        db.add(
            Invoice(
                organization_id=org,
                provider="sandbox",
                provider_invoice_id="a",
                status="paid",
                net_pence=100,
                vat_pence=20,
                total_pence=120,
                issued_at=datetime.now(UTC),
            )
        )
        db.flush()
        with pytest.raises(IntegrityError), db.begin_nested():
            db.add(
                Invoice(
                    organization_id=org,
                    provider="sandbox",
                    provider_invoice_id="a",
                    status="paid",
                    net_pence=100,
                    vat_pence=20,
                    total_pence=120,
                    issued_at=datetime.now(UTC),
                )
            )
            db.flush()
        with pytest.raises(IntegrityError), db.begin_nested():
            db.add(
                Invoice(
                    organization_id=org,
                    provider="sandbox",
                    provider_invoice_id="b",
                    status="owing",
                    net_pence=0,
                    vat_pence=0,
                    total_pence=0,
                    issued_at=datetime.now(UTC),
                )
            )
            db.flush()


def test_the_same_provider_message_cannot_be_recorded_twice(db, business):
    org = uuid.UUID(business[0])
    with scoped(db, business):
        db.add(
            BillingEvent(
                organization_id=org,
                provider="sandbox",
                event_id="e",
                type="x",
                status="processed",
                received_at=datetime.now(UTC),
            )
        )
        db.flush()
        with pytest.raises(IntegrityError), db.begin_nested():
            db.add(
                BillingEvent(
                    organization_id=org,
                    provider="sandbox",
                    event_id="e",
                    type="x",
                    status="processed",
                    received_at=datetime.now(UTC),
                )
            )
            db.flush()


# --- being told, and the sandbox renewing by itself ---------------------------------------------------------------------


def billing_alerts(api, business):
    from tests.test_alerts import alerts_of

    return [a for a in alerts_of(api, business) if a["rule_code"] == "billing_status"]


def set_trial_left(db, business, **delta):
    with scoped(db, business):
        service.ensure_subscription(db, uuid.UUID(business[0]), datetime.now(UTC))
        db.execute(
            update(Subscription).values(trial_ends_at=datetime.now(UTC) + timedelta(**delta))
        )


def test_a_trial_with_plenty_of_time_left_raises_nothing(api, db, business, pay_with_sandbox):
    from tests.test_alerts import evaluate

    set_trial_left(db, business, days=5, hours=1)  # six days left
    evaluate(api, business)
    assert billing_alerts(api, business) == []


def test_a_trial_in_its_last_five_days_raises_one_alert_that_goes_when_a_plan_is_bought(
    api, db, business, pay_with_sandbox
):
    from tests.test_alerts import evaluate

    set_trial_left(db, business, days=4, hours=12)  # five days left
    evaluate(api, business)
    [alert] = billing_alerts(api, business)
    assert alert["title"] == "Your free trial ends in 5 days" and alert["severity"] == "high"
    assert alert["link"] == "billing.html" and alert["status"] == "open"
    evaluate(api, business)
    assert len(billing_alerts(api, business)) == 1  # a repeat is counted, not added
    pay(api, business, "growth")
    evaluate(api, business)
    assert billing_alerts(api, business)[0]["status"] == "resolved"


def test_the_last_day_is_worded_in_the_singular_and_the_threshold_can_be_changed(
    api, db, business, pay_with_sandbox
):
    from tests.test_alerts import evaluate

    set_trial_left(db, business, hours=3)
    evaluate(api, business)
    assert billing_alerts(api, business)[0]["title"] == "Your free trial ends in 1 day"
    res = api.put(
        f"{ORGS}/{business[0]}/alerts/rules/billing_status",
        json={"enabled": True, "severity": "medium", "params": {"days": 1}},
        headers=business[2]["owner"],
    )
    assert res.status_code == 200, res.text


def test_an_overdue_payment_and_an_ended_plan_each_raise_their_own_alert(
    api, db, business, pay_with_sandbox
):
    from tests.test_alerts import evaluate

    pay(api, business, "growth")
    webhook(api, message(business, "invoice_failed", "evt_late"))
    evaluate(api, business)
    assert [a["title"] for a in billing_alerts(api, business)] == [
        "Your last payment did not go through"
    ]
    webhook(api, message(business, "subscription_canceled", "evt_gone", status="canceled"))
    evaluate(api, business)
    by_title = {a["title"]: a["status"] for a in billing_alerts(api, business)}
    assert by_title == {
        "Your last payment did not go through": "resolved",
        "Your plan has ended": "open",
    }


def test_a_business_can_switch_the_billing_alert_off(api, db, business, pay_with_sandbox):
    from tests.test_alerts import evaluate

    api.put(
        f"{ORGS}/{business[0]}/alerts/rules/billing_status",
        json={"enabled": False, "severity": "high", "params": {"days": 5}},
        headers=business[2]["owner"],
    )
    expire_trial(db, business)
    evaluate(api, business)
    assert billing_alerts(api, business) == []


def test_the_scheduled_round_renews_a_sandbox_plan_that_is_due_and_leaves_others_alone(
    api, db, business, pay_with_sandbox
):
    from app.services import scheduler

    pay(api, business, "growth")
    start = subscription(db, business).current_period_start
    due = start + timedelta(days=32)
    totals = scheduler.tick(db, today=due.date(), now=due)
    assert totals["renewed"] == 1 and totals["businesses"] >= 1
    sub = subscription(db, business)
    assert sub.current_period_start == rules.add_months(start, 1)
    with scoped(db, business):
        assert len(db.scalars(select(Invoice)).all()) == 1
    again = scheduler.tick(db, today=due.date(), now=due)
    assert again["renewed"] == 0
    with scoped(db, business, 1):
        assert db.scalars(select(Invoice)).all() == []


# --- looking after the plans ---------------------------------------------------------------------------------------------


def test_a_plans_prices_and_provider_price_ids_can_be_changed_without_touching_those_already_paying(
    api, db, business, pay_with_sandbox
):
    pay(api, business, "growth")
    before = subscription(db, business).current_period_end
    plan = service.set_plan(db, "growth", price_month=24900, price_year=249000)
    assert (plan.price_month_pence, plan.price_year_pence) == (24900, 249000)
    service.set_plan(db, "growth", provider_price=("stripe", "month", "price_m"))
    service.set_plan(db, "growth", provider_price=("stripe", "year", "price_y"))
    service.set_plan(db, "growth", provider_price=("paystack", "month", "PLN_m"))
    assert db.scalars(select(Plan.provider_prices).where(Plan.code == "growth")).one() == {
        "stripe": {"month": "price_m", "year": "price_y"},
        "paystack": {"month": "PLN_m"},
    }
    service.set_plan(db, "growth", price_month=100)
    assert (
        db.scalars(select(Plan.price_year_pence).where(Plan.code == "growth")).one() == 249000
    )  # what was not mentioned is kept
    assert subscription(db, business).current_period_end == before
    plans = {
        p["code"]: p
        for p in api.get(f"{base(business[0])}/plans", headers=business[2]["owner"]).json()
    }
    assert plans["growth"]["price_month"] == "£1.00"


def test_a_plan_price_change_is_refused_for_a_plan_that_is_not_there_or_a_provider_that_is_not_known(
    db,
):
    with pytest.raises(service.NotFoundError):
        service.set_plan(db, "nothing", price_month=1)
    with pytest.raises(service.AppError) as caught:
        service.set_plan(db, "growth", provider_price=("paypal", "month", "x"))
    assert caught.value.code == "bad_provider_price"
    with pytest.raises(service.AppError):
        service.set_plan(db, "growth", provider_price=("stripe", "week", "x"))


def test_what_a_plan_includes_can_be_changed_and_it_changes_what_a_business_can_do(
    api, db, business, pay_with_sandbox
):
    pay(api, business, "starter")
    assert invite(api, business, "third@acme.co.uk").status_code == 201
    assert invite(api, business, "fourth@acme.co.uk").status_code == 402
    row = service.set_entitlement(db, "starter", "members", enabled=True, limit=4)
    assert (row.enabled, row.limit) == (True, 4)
    assert invite(api, business, "fourth@acme.co.uk").status_code == 201
    service.set_entitlement(db, "starter", "members", enabled=True, limit=None)
    assert invite(api, business, "fifth@acme.co.uk").status_code == 201
    service.set_entitlement(db, "starter", "members", enabled=False, limit=None)
    assert invite(api, business, "sixth@acme.co.uk").status_code == 402
    from app.models.billing import FeatureEntitlement

    db.execute(
        FeatureEntitlement.__table__.delete().where(FeatureEntitlement.feature == "integrations")
    )
    added = service.set_entitlement(db, "starter", "integrations", enabled=True, limit=2)
    assert (added.enabled, added.limit) == (True, 2)
    with pytest.raises(service.AppError):
        service.set_entitlement(db, "starter", "everything", enabled=True, limit=None)
    with pytest.raises(service.NotFoundError):
        service.set_entitlement(db, "nothing", "members", enabled=True, limit=None)


def test_a_demo_business_can_be_given_a_paid_sandbox_plan_so_every_feature_is_on(
    api, db, business, pay_with_sandbox
):
    org = uuid.UUID(business[0])
    with scoped(db, business):
        sub = service.grant_demo_plan(db, org, "scale", datetime.now(UTC))
        assert (sub.status, sub.provider, sub.interval) == ("active", "sandbox", "year")
        assert sub.current_period_end == rules.add_months(sub.current_period_start, 12)
        again = service.grant_demo_plan(db, org, "growth", datetime.now(UTC))
        assert again.id == sub.id and db.get(Plan, again.plan_id).code == "growth"
        with pytest.raises(service.NotFoundError):
            service.grant_demo_plan(db, org, "nothing", datetime.now(UTC))
    body = get_billing(api, business)
    assert (
        body["subscription"]["plan_code"] == "growth" and body["subscription"]["status"] == "active"
    )
    [demo] = api.get(f"{base(business[0])}/invoices", headers=business[2]["owner"]).json()
    assert demo["status"] == "paid" and demo["number"].startswith("DEMO-")
    assert demo["lines"][0]["description"] == "Growth plan, billed yearly (demo)"
    assert (demo["net"], demo["vat"], demo["total"]) == ("£1,990.00", "£398.00", "£2,388.00")


def test_the_plans_command_shows_and_changes_the_plans(db, capsys, monkeypatch):
    from app.cli import billing as cli

    class Session:
        def __enter__(self):
            return db

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(cli, "get_sessionmaker", lambda: lambda: Session())
    assert cli.main(["plans"]) == 0
    shown = capsys.readouterr().out
    assert "starter" in shown and "£99.00 a month" in shown and "Not included" in shown
    assert cli.main(["price", "starter", "--month", "10500", "--year", "105000"]) == 0
    assert "£105.00 a month" in capsys.readouterr().out
    assert cli.main(["provider-price", "starter", "stripe", "month", "price_abc"]) == 0
    assert "price_abc" in capsys.readouterr().out
    assert cli.main(["include", "starter", "members", "--limit", "4"]) == 0
    assert cli.main(["include", "starter", "members", "--unlimited"]) == 0
    assert "No limit" in capsys.readouterr().out
    assert cli.main(["include", "starter", "ai_assistant", "--off"]) == 0
    assert cli.main(["price", "nothing", "--month", "1"]) == 1
    assert "not found" in capsys.readouterr().err


# --- the corners the first round of checking found ---------------------------------------------------------------------


class RecordingSandbox(SandboxProvider):
    """The sandbox that remembers what it was asked, and can behave like a provider that changes a plan
    by a new checkout (as Paystack does)."""

    def __init__(self, in_place=True):
        super().__init__("test-secret-for-the-sandbox")
        self.changes_in_place = in_place
        self.cancelled, self.prorated = [], []

    def set_cancel(self, *, subscription_id, cancel):
        self.cancelled.append((subscription_id, cancel))

    def change_plan(self, *, subscription_id, plan, interval, prorate):
        self.prorated.append(prorate)


def use_provider(app, provider):
    app.dependency_overrides[get_payment_providers] = lambda: {"sandbox": provider}


def test_a_sandbox_link_is_good_up_to_the_second_it_expires_and_no_longer():
    import base64

    pl = SimpleNamespace(code="growth")
    link = SANDBOX.checkout(
        organization_id=ORG,
        email="a@b.co.uk",
        plan=pl,
        interval="month",
        success_url="https://x/b?org=1&paid=1",
        cancel_url="c",
    )
    token = link.url.split("sandbox=")[1]
    payload = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))
    last = datetime.fromtimestamp(payload["exp"], tz=UTC)
    SANDBOX.complete(token, last)
    with pytest.raises(ProviderError, match="expired"):
        SANDBOX.complete(token, last + timedelta(seconds=1))


def test_a_plan_is_changed_with_proration_only_when_it_is_a_move_up(app, api, db, business):
    recorder = RecordingSandbox()
    use_provider(app, recorder)
    pay(api, business, "starter")
    api.post(
        f"{base(business[0])}/change", json={"plan_code": "scale"}, headers=business[2]["owner"]
    )
    api.post(
        f"{base(business[0])}/change", json={"plan_code": "growth"}, headers=business[2]["owner"]
    )
    assert recorder.prorated == [True, False]


def test_choosing_the_plan_you_are_on_drops_a_move_that_was_waiting(
    api, db, business, pay_with_sandbox
):
    org, _, auth = business
    pay(api, business, "scale")
    api.post(f"{base(org)}/change", json={"plan_code": "growth"}, headers=auth["owner"])
    assert get_billing(api, business)["subscription"]["scheduled_plan_code"] == "growth"
    res = api.post(f"{base(org)}/change", json={"plan_code": "scale"}, headers=auth["owner"])
    assert res.status_code == 200, res.text
    assert res.json()["message"] == "You will stay on the Scale plan."
    after = get_billing(api, business)["subscription"]
    assert (after["plan_code"], after["scheduled_plan_code"]) == ("scale", None)
    assert subscription(db, business).scheduled_interval is None
    again = api.post(f"{base(org)}/change", json={"plan_code": "scale"}, headers=auth["owner"])
    assert again.status_code == 409 and again.json()["error"]["code"] == "no_change"


def test_a_providers_own_trial_does_not_put_a_paying_business_back_on_a_trial(
    api, db, business, pay_with_sandbox
):
    pay(api, business, "growth")
    webhook(api, message(business, "subscription_updated", "evt_t", status="trialing"))
    assert get_billing(api, business)["subscription"]["status"] == "active"


def test_only_a_paid_invoice_brings_an_overdue_plan_back(api, db, business, pay_with_sandbox):
    pay(api, business, "growth")
    webhook(api, message(business, "invoice_failed", "evt_f"))
    webhook(
        api,
        message(
            business,
            "invoice_updated",
            "evt_open",
            invoice={"id": "inv_o", "status": "open", "total_pence": 100, "net_pence": 100},
        ),
    )
    assert get_billing(api, business)["subscription"]["status"] == "past_due"
    webhook(
        api,
        message(
            business,
            "invoice_updated",
            "evt_paid",
            invoice={"id": "inv_o", "status": "paid", "total_pence": 100, "net_pence": 100},
        ),
    )
    assert get_billing(api, business)["subscription"]["status"] == "active"


def test_a_provider_that_changes_plans_by_a_new_checkout_has_the_old_subscription_stopped(
    app, api, db, business
):
    recorder = RecordingSandbox(in_place=False)
    use_provider(app, recorder)
    pay(api, business, "starter")
    old = subscription(db, business).provider_subscription_id
    new = message(
        business,
        "checkout_completed",
        "evt_new",
        plan_code="growth",
        interval="month",
        subscription_id="sandbox_sub_replacement",
        status="active",
    )
    assert webhook(api, new).json()["processed"] == 1
    assert recorder.cancelled == [(old, True)]
    assert subscription(db, business).provider_subscription_id == "sandbox_sub_replacement"
    same = message(
        business,
        "checkout_completed",
        "evt_same",
        plan_code="growth",
        interval="month",
        subscription_id="sandbox_sub_replacement",
        status="active",
    )
    webhook(api, same)
    assert recorder.cancelled == [(old, True)]  # the same subscription again stops nothing


def test_a_provider_that_changes_plans_in_place_never_has_a_subscription_stopped_for_it(
    app, api, db, business
):
    recorder = RecordingSandbox(in_place=True)
    use_provider(app, recorder)
    pay(api, business, "starter")
    new = message(
        business,
        "checkout_completed",
        "evt_new",
        plan_code="growth",
        interval="month",
        subscription_id="sandbox_sub_other",
        status="active",
    )
    webhook(api, new)
    assert recorder.cancelled == []


def test_a_business_with_no_provider_yet_takes_the_ids_of_the_first_message_and_keeps_them(
    api, db, business, pay_with_sandbox
):
    first = message(
        business,
        "subscription_updated",
        "evt_a",
        subscription_id="sub_first",
        customer_id="cus_first",
        status="active",
    )
    assert webhook(api, first).json()["processed"] == 1
    sub = subscription(db, business)
    assert (sub.provider, sub.provider_subscription_id, sub.provider_customer_id) == (
        "sandbox",
        "sub_first",
        "cus_first",
    )
    assert sub.status == "active" and sub.trial_ends_at is None
    second = message(
        business,
        "subscription_updated",
        "evt_b",
        subscription_id="sub_second",
        customer_id="cus_second",
        status="active",
    )
    webhook(api, second)
    sub = subscription(db, business)
    assert (sub.provider_subscription_id, sub.provider_customer_id) == ("sub_first", "cus_first")


def test_a_trial_that_is_still_a_trial_keeps_its_end_date_through_an_update(
    api, db, business, pay_with_sandbox
):
    get_billing(api, business)
    before = subscription(db, business).trial_ends_at
    webhook(
        api,
        message(
            business, "subscription_updated", "evt_keep", status="trialing", subscription_id="sub_t"
        ),
    )
    sub = subscription(db, business)
    assert sub.status == "trialing" and sub.trial_ends_at == before


def test_paying_again_after_cancelling_starts_a_fresh_plan_that_is_not_set_to_end(
    api, db, business, pay_with_sandbox
):
    pay(api, business, "growth")
    api.post(f"{base(business[0])}/cancel", headers=business[2]["owner"])
    assert subscription(db, business).cancel_at_period_end is True
    again = message(
        business,
        "checkout_completed",
        "evt_again",
        plan_code="growth",
        interval="month",
        subscription_id="sandbox_sub_again",
        status="active",
    )
    webhook(api, again)
    sub = subscription(db, business)
    assert sub.cancel_at_period_end is False and sub.canceled_at is None


def test_connections_are_limited_by_the_plan_and_a_disconnected_one_does_not_count(
    api, db, business, pay_with_sandbox, fake
):
    from tests.test_integrations import connected

    org, _, auth = business
    service.set_entitlement(db, "scale", "integrations", enabled=True, limit=1)
    first = connected(api, business)
    with scoped(db, business):
        assert service.used(db, "integrations") == 1
    refused = api.post(
        f"{ORGS}/{org}/integrations/connect", json={"provider": "fake"}, headers=auth["owner"]
    )
    assert (
        refused.status_code == 402
        and refused.json()["error"]["details"]["feature"] == "integrations"
    )
    assert "up to 1 connections to other systems" in refused.json()["error"]["message"]
    again = api.post(
        f"{ORGS}/{org}/integrations/connect",
        json={"provider": "fake", "integration_id": first["id"]},
        headers=auth["owner"],
    )
    assert again.status_code == 200  # signing in again to one already there is not a new connection
    assert (
        api.post(
            f"{ORGS}/{org}/integrations/{first['id']}/disconnect", headers=auth["owner"]
        ).status_code
        == 200
    )
    with scoped(db, business):
        assert service.used(db, "integrations") == 0
    assert (
        api.post(
            f"{ORGS}/{org}/integrations/connect", json={"provider": "fake"}, headers=auth["owner"]
        ).status_code
        == 200
    )


def test_scheduled_reports_are_limited_by_the_plan_and_stop_when_the_trial_ends(
    api, db, march, pay_with_sandbox
):
    from tests.test_reports import schedule, uid

    service.set_entitlement(db, "scale", "scheduled_reports", enabled=True, limit=1)
    who = [uid(db, march, "owner@acme.co.uk")]
    assert schedule(api, march, recipients=who).status_code == 201
    refused = schedule(api, march, kind="kpi", recipients=who)
    assert (
        refused.status_code == 402
        and refused.json()["error"]["details"]["feature"] == "scheduled_reports"
    )
    expire_trial(db, march)
    service.set_entitlement(db, "scale", "scheduled_reports", enabled=True, limit=None)
    assert schedule(api, march, kind="kpi", recipients=who).status_code == 402
