# ruff: noqa: E501, F811
"""Alerts and notifications: what needs attention, who is told, and when."""

import smtplib
import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.alerts import rules
from app.core.config import Settings
from app.core.uk import today_uk
from app.integrations.base import utcnow
from app.models.alerts import Alert, AlertEvent, AlertRule, Notification
from app.models.business import BusinessSettings, NotificationPreference
from app.models.identity import AuditLog, User
from app.services import alerts, notifications, scheduler
from app.services.email import EmailDeliveryError, EmailMessage, SmtpEmailSender
from tests.test_actions import accept, act, add_manager, set_dates
from tests.test_health import ORGS, owner_tenant, scoped
from tests.test_recommendations import event_for, recommend

UTC_NIGHT = datetime(2026, 10, 5, 23, 30, tzinfo=UTC)  # half past midnight UK time (BST)
UTC_NOON = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
STALE = (today_uk() - date(2026, 3, 20)).days  # the bakery's last sale was on 20/03/2026

# --- the rules --------------------------------------------------------------------------------------------


def test_every_kind_of_alert_is_well_formed():
    from app.models.alerts import SEVERITIES
    from app.models.business import NOTIFICATION_CATEGORIES

    codes = [r.code for r in rules.RULES]
    assert len(codes) == len(set(codes)) and set(codes) == set(rules.BY_CODE)
    for r in rules.RULES:
        assert (
            r.severity in SEVERITIES
            and r.category in NOTIFICATION_CATEGORIES
            and r.name
            and r.description
        )
        assert set(r.params_help) == set(r.params)
    assert rules.SEVERITIES == SEVERITIES
    assert [r.always_on for r in rules.RULES if r.category == "security"] == [True]
    assert not any(r.always_on for r in rules.RULES if r.category != "security")


def test_every_area_of_the_figures_has_its_own_change_alert():
    for area, code in rules.CHANGE_RULE_FOR.items():
        assert rules.BY_CODE[code].category == area


def test_severity_can_be_lowered_but_not_below_info():
    assert (
        rules.lower("critical") == "high"
        and rules.lower("high", 2) == "low"
        and rules.lower("info") == "info"
    )
    assert (
        rules.at_least("high", "medium")
        and rules.at_least("medium", "medium")
        and not rules.at_least("low", "medium")
    )


@pytest.mark.parametrize(
    ("size", "min_size", "expected"),
    [
        ("major", "major", "high"),
        ("notable", "major", None),
        ("major", "notable", "high"),
        ("notable", "notable", "medium"),
    ],
)
def test_a_major_change_has_the_rules_severity_and_a_notable_one_a_step_less(
    size, min_size, expected
):
    assert rules.change_severity("high", size, min_size) == expected


def test_the_default_settings_apply_until_the_business_changes_them():
    d = rules.BY_CODE["data_stale"]
    assert rules.settings_for(d, None) == (True, "medium", {"days": 14})
    row = AlertRule(enabled=False, severity="low", params={"days": 3})
    assert rules.settings_for(d, row) == (False, "low", {"days": 3})
    assert rules.settings_for(d, AlertRule(enabled=True, severity="low", params={})) == (
        True,
        "low",
        {"days": 14},
    )


def test_a_security_alert_ignores_a_choice_to_turn_it_off():
    d = rules.BY_CODE["security_password_changed"]
    assert rules.settings_for(d, AlertRule(enabled=False, severity="high", params={}))[0] is True


def test_the_same_thing_has_the_same_key():
    assert rules.key_change("revenue", date(2026, 3, 1)) == "change:revenue:2026-03-01"
    assert (
        rules.key_action("a") == "action:a" and rules.key_forecast("revenue") == "forecast:revenue"
    )
    assert (
        rules.key_health(date(2026, 3, 1)) == "health:2026-03-01"
        and rules.key_quality(date(2026, 3, 1)) == "data:quality:2026-03-01"
    )


@pytest.mark.parametrize(
    ("role", "remit", "area", "told"),
    [("owner", None, "sales", True), ("owner", ["customer"], "sales", True), ("manager", None, "sales", True),
     ("manager", ["sales"], "sales", True), ("manager", ["customer"], "sales", False), ("manager", ["customer"], None, True),
     ("viewer", None, "sales", False), ("viewer", None, None, False)],
)  # fmt: skip
def test_who_is_in_the_audience(role, remit, area, told):
    assert rules.in_audience(role, remit, area) is told


NIGHT = (time(22), time(7))
DAY = (time(9), time(17))


def at(hour, minute=0, day=5, month=1):
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)  # January: UK time is UTC


@pytest.mark.parametrize(
    ("now", "quiet", "inside"),
    [(at(23), NIGHT, True), (at(3), NIGHT, True), (at(22), NIGHT, True), (at(7), NIGHT, False), (at(6, 59), NIGHT, True),
     (at(12), NIGHT, False), (at(21, 59), NIGHT, False), (at(9), DAY, True), (at(16, 59), DAY, True), (at(17), DAY, False),
     (at(8, 59), DAY, False)],
)  # fmt: skip
def test_quiet_hours_may_cross_midnight_and_the_end_is_not_quiet(now, quiet, inside):
    assert rules.in_quiet_hours(now, *quiet) is inside


def test_no_quiet_hours_means_never_quiet():
    assert rules.in_quiet_hours(at(3), None, None) is False
    assert rules.in_quiet_hours(at(3), time(7), time(7)) is False


def test_quiet_hours_are_read_in_uk_time_not_utc():
    summer = datetime(2026, 7, 5, 21, 30, tzinfo=UTC)  # 22:30 in the UK
    assert rules.in_quiet_hours(summer, *NIGHT) is True
    assert (
        rules.in_quiet_hours(
            datetime(2026, 7, 5, 21, 30, tzinfo=UTC).replace(month=1, day=5), *NIGHT
        )
        is False
    )


def test_quiet_hours_end_at_the_next_seven_oclock_uk_time():
    assert rules.quiet_ends(at(23), *NIGHT) == datetime(2026, 1, 6, 7, 0, tzinfo=UTC)
    assert rules.quiet_ends(at(3), *NIGHT) == datetime(2026, 1, 5, 7, 0, tzinfo=UTC)
    assert rules.quiet_ends(datetime(2026, 7, 5, 21, 30, tzinfo=UTC), *NIGHT) == datetime(
        2026, 7, 6, 6, 0, tzinfo=UTC
    )  # 07:00 BST


def decide(category="sales", severity="high", wants=True, now=UTC_NIGHT, quiet=NIGHT):
    return rules.email_decision(
        category=category, severity=severity, wants_email=wants, now=now, quiet=quiet
    )


def test_email_goes_at_once_when_it_is_wanted_serious_enough_and_not_quiet():
    assert decide(now=UTC_NOON) == ("send", None)
    assert decide(quiet=None) == ("send", None)


def test_email_waits_out_quiet_hours():
    what, when = decide()
    assert what == "wait" and when == datetime(2026, 10, 6, 6, 0, tzinfo=UTC)


@pytest.mark.parametrize("severity", ["info", "low"])
def test_minor_alerts_are_in_the_app_only(severity):
    assert decide(severity=severity, now=UTC_NOON) == ("none", None)
    assert decide(severity="medium", now=UTC_NOON)[0] == "send"


def test_no_email_if_the_person_does_not_want_it():
    assert decide(wants=False, now=UTC_NOON) == ("none", None)


def test_critical_alerts_ignore_quiet_hours():
    assert decide(severity="critical") == ("send", None)


def test_security_alerts_always_go_at_once_whatever_the_choices():
    assert decide(category="security", severity="info", wants=False) == ("send", None)


# --- finding what needs attention ----------------------------------------------------------------------------


def evaluate(api, business, who="owner"):
    res = api.post(f"{ORGS}/{business[0]}/alerts/evaluate", headers=business[2][who])
    assert res.status_code == 200, res.text
    return res.json()


def alerts_of(api, business, who="owner", **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return api.get(f"{ORGS}/{business[0]}/alerts?{query}", headers=business[2][who]).json()


@pytest.fixture
def raised(api, march, outbox):
    outbox.clear()
    return evaluate(api, march)


def test_a_fall_in_the_figures_becomes_an_alert_with_a_link_to_the_explanation(api, march, raised):
    assert raised == {"raised": 7, "repeated": 0, "resolved": 0, "notified": 7}
    rows = {a["title"].split(" fell")[0].split(" rose")[0]: a for a in alerts_of(api, march)}
    sales = rows["Sales"]
    assert (
        sales["title"]
        == "Sales fell by 32% in March 2026: £410.00, down from £600.00 in February 2026."
    )
    assert (
        sales["rule_code"],
        sales["category"],
        sales["kpi_category"],
        sales["severity"],
        sales["status"],
    ) == ("change_financial", "financial", "financial", "high", "open")
    assert (
        sales["link"] == "changes.html"
        and sales["occurrences"] == 1
        and "What changed" in sales["body"]
    )
    assert (
        rows["Average sale"]["category"] == "sales"
        and rows["Spend per customer"]["category"] == "customer"
    )


def test_stale_data_is_an_alert_too(api, march, raised):
    [stale] = [a for a in alerts_of(api, march) if a["rule_code"] == "data_stale"]
    assert (
        stale["category"] == "data"
        and stale["severity"] == "medium"
        and stale["kpi_category"] is None
    )
    assert stale["title"] == f"No new sales for {STALE} days" and stale["link"] == "import.html"


def test_good_news_and_old_news_are_not_alerts(api, march, raised):
    assert all(
        "rose" not in a["title"] or "Refund rate" in a["title"] for a in alerts_of(api, march)
    )
    assert {a["rule_code"] for a in alerts_of(api, march)} <= {
        "change_sales",
        "change_financial",
        "change_customer",
        "data_stale",
    }


def test_looking_again_counts_the_same_thing_and_raises_nothing_new(api, march, raised, outbox):
    outbox.clear()
    again = evaluate(api, march)
    assert again == {"raised": 0, "repeated": 7, "resolved": 0, "notified": 0}
    assert outbox == [] and len(alerts_of(api, march)) == 7
    assert {a["occurrences"] for a in alerts_of(api, march)} == {
        1
    }  # the same day is not counted twice


def test_seen_again_on_another_day_is_counted_once_for_that_day(api, db, march, raised, outbox):
    outbox.clear()
    with scoped(db, march):
        tenant = owner_tenant(db, march)
        tomorrow = utcnow() + timedelta(days=1)  # the same moment twice: whatever the time of day
        alerts.evaluate(db, tenant, now=tomorrow, sender=outbox)
        alerts.evaluate(db, tenant, now=tomorrow, sender=outbox)
    assert {a["occurrences"] for a in alerts_of(api, march)} == {2} and outbox == []
    detail = api.get(
        f"{ORGS}/{march[0]}/alerts/{alerts_of(api, march)[0]['id']}", headers=march[2]["owner"]
    ).json()
    assert [e["kind"] for e in detail["events"]] == ["raised", "repeated"]


def test_an_alert_that_gets_worse_is_made_more_serious(api, db, march, raised):
    api.put(
        f"{ORGS}/{march[0]}/alerts/rules/data_stale",
        json={"severity": "critical"},
        headers=march[2]["owner"],
    )
    evaluate(api, march)
    [stale] = [a for a in alerts_of(api, march) if a["rule_code"] == "data_stale"]
    assert stale["severity"] == "critical"
    api.put(
        f"{ORGS}/{march[0]}/alerts/rules/data_stale",
        json={"severity": "low"},
        headers=march[2]["owner"],
    )
    evaluate(api, march)
    assert [a for a in alerts_of(api, march) if a["rule_code"] == "data_stale"][0][
        "severity"
    ] == "critical"  # never made less serious by looking again


def test_an_alert_about_a_state_closes_when_it_stops_being_true(api, march, raised):
    api.put(
        f"{ORGS}/{march[0]}/alerts/rules/data_stale",
        json={"params": {"days": 1000}},
        headers=march[2]["owner"],
    )
    result = evaluate(api, march)
    assert result["resolved"] == 1 and result["raised"] == 0
    [stale] = [a for a in alerts_of(api, march) if a["rule_code"] == "data_stale"]
    assert stale["status"] == "resolved" and stale["resolved_at"]
    detail = api.get(f"{ORGS}/{march[0]}/alerts/{stale['id']}", headers=march[2]["owner"]).json()
    assert detail["events"][-1] == {
        "kind": "resolved",
        "user": None,
        "note": "This is no longer the case.",
        "created_at": detail["events"][-1]["created_at"],
    }


def test_a_state_that_comes_back_is_a_new_alert(api, march, raised):
    api.put(
        f"{ORGS}/{march[0]}/alerts/rules/data_stale",
        json={"params": {"days": 1000}},
        headers=march[2]["owner"],
    )
    evaluate(api, march)
    api.put(
        f"{ORGS}/{march[0]}/alerts/rules/data_stale",
        json={"params": {"days": 14}},
        headers=march[2]["owner"],
    )
    assert evaluate(api, march)["raised"] == 1
    assert len([a for a in alerts_of(api, march) if a["rule_code"] == "data_stale"]) == 2


def test_a_change_that_has_been_dealt_with_is_not_raised_again(api, march, raised):
    target = next(a for a in alerts_of(api, march) if a["rule_code"] == "change_financial")
    api.post(f"{ORGS}/{march[0]}/alerts/{target['id']}/resolve", headers=march[2]["owner"])
    result = evaluate(api, march)
    assert result["raised"] == 0 and len(alerts_of(api, march)) == 7
    assert alerts_of(api, march, status="resolved")[0]["id"] == target["id"]


def test_overdue_work_is_an_alert_until_it_is_dealt_with(api, db, march, outbox):
    event = event_for(api, march, "revenue")
    recommend(api, march, event)
    action = accept(api, march, event).json()
    set_dates(
        db, march, action["id"], date.today() - timedelta(days=20), date.today() - timedelta(days=5)
    )
    outbox.clear()
    evaluate(api, march)
    [overdue] = [a for a in alerts_of(api, march) if a["rule_code"] == "action_overdue"]
    assert (
        overdue["category"] == "action"
        and overdue["severity"] == "medium"
        and overdue["link"] == f"actions.html#{action['id']}"
    )
    assert (
        overdue["title"] == f'"{action["title"]}" is 5 days overdue'
        and "Open it to move the date" in overdue["body"]
    )
    assert overdue["kpi_category"] == "financial"
    act(
        api,
        march,
        action["id"],
        "",
        "patch",
        json={"target_date": str(date.today() + timedelta(days=10))},
    )
    assert evaluate(api, march)["resolved"] == 1
    assert [a for a in alerts_of(api, march) if a["rule_code"] == "action_overdue"][0][
        "status"
    ] == "resolved"


def test_work_that_is_only_a_little_late_waits_for_the_days_you_chose(api, db, march):
    event = event_for(api, march, "revenue")
    recommend(api, march, event)
    action = accept(api, march, event).json()
    set_dates(
        db, march, action["id"], date.today() - timedelta(days=20), date.today() - timedelta(days=5)
    )
    api.put(
        f"{ORGS}/{march[0]}/alerts/rules/action_overdue",
        json={"params": {"days": 6}},
        headers=march[2]["owner"],
    )
    evaluate(api, march)
    assert not [a for a in alerts_of(api, march) if a["rule_code"] == "action_overdue"]
    api.put(
        f"{ORGS}/{march[0]}/alerts/rules/action_overdue",
        json={"params": {"days": 5}},
        headers=march[2]["owner"],
    )
    evaluate(api, march)
    assert len([a for a in alerts_of(api, march) if a["rule_code"] == "action_overdue"]) == 1


def test_incomplete_data_is_an_alert(api, db, march):
    from tests.test_detection import Values

    Values(db, march).put("revenue", date(2026, 4, 1), 500, 410, quality=40)
    evaluate(api, march)
    [quality] = [a for a in alerts_of(api, march) if a["rule_code"] == "data_quality"]
    assert (
        quality["title"] == "The data for April 2026 is incomplete"
        and "40 out of 100" in quality["body"]
    )
    api.put(
        f"{ORGS}/{march[0]}/alerts/rules/data_quality",
        json={"params": {"min_score": 40}},
        headers=march[2]["owner"],
    )
    assert evaluate(api, march)["resolved"] == 1


def test_a_fall_in_health_is_an_alert(db, march, monkeypatch):
    from types import SimpleNamespace

    from app.services import health as health_service

    settings = {"health_drop": (True, "high", {"points": 10})}

    def fake(overall, previous):
        return SimpleNamespace(
            overall_score=overall, previous_score=previous, period_start=date(2026, 3, 1)
        )

    with scoped(db, march):
        monkeypatch.setattr(health_service, "latest_health", lambda db: fake(60, 75))
        [alert] = alerts._health(db, settings)
        assert (
            alert.title == "Your business health fell by 15 points"
            and alert.dedupe_key == "health:2026-03-01"
        )
        assert (
            "75 out of 100 the month before and is 60 for March 2026" in alert.body
            and alert.link == "health.html"
        )
        monkeypatch.setattr(health_service, "latest_health", lambda db: fake(66, 75))
        assert alerts._health(db, settings) == []  # 9 points is under the 10 asked for
        monkeypatch.setattr(health_service, "latest_health", lambda db: fake(65, 75))
        assert len(alerts._health(db, settings)) == 1  # exactly 10 counts
        monkeypatch.setattr(health_service, "latest_health", lambda db: fake(60, None))
        assert alerts._health(db, settings) == []
        monkeypatch.setattr(health_service, "latest_health", lambda db: None)
        assert alerts._health(db, settings) == []
        monkeypatch.setattr(health_service, "latest_health", lambda db: fake(60, 75))
        assert alerts._health(db, {"health_drop": (False, "high", {"points": 10})}) == []


def test_a_forecast_of_a_fall_is_an_alert(api, db, business, outbox):
    from app.services import forecast as forecast_service
    from tests.test_detection import Values

    Values(db, business).series("revenue", [1000, 1000, 1000, 1000, 1000, 1000, 1000, 1600])
    with scoped(db, business):
        forecast_service.calculate(db, owner_tenant(db, business), "revenue", 3)
    outbox.clear()
    evaluate(api, business)
    [alert] = [a for a in alerts_of(api, business) if a["rule_code"] == "forecast_decline"]
    assert (
        alert["category"] == "forecast"
        and alert["severity"] == "medium"
        and alert["link"] == "forecast.html"
    )
    assert (
        alert["title"].startswith("Sales are forecast to fall by ")
        and "£1,600.00 in August 2026" in alert["body"]
    )
    put_rule(api, business, "forecast_decline", {"params": {"drop_pct": 90}})
    assert evaluate(api, business)["resolved"] == 1


def test_a_kind_of_alert_that_is_switched_off_raises_nothing(api, march):
    for code in ("change_sales", "change_financial", "change_customer", "data_stale"):
        assert (
            api.put(
                f"{ORGS}/{march[0]}/alerts/rules/{code}",
                json={"enabled": False},
                headers=march[2]["owner"],
            ).status_code
            == 200
        )
    assert evaluate(api, march)["raised"] == 0 and alerts_of(api, march) == []


def test_only_changes_as_big_as_you_asked_for_are_alerts(api, march):
    api.put(
        f"{ORGS}/{march[0]}/alerts/rules/change_sales",
        json={"params": {"min_size": "notable"}},
        headers=march[2]["owner"],
    )
    assert evaluate(api, march)["raised"] >= 7


def test_changes_are_only_news_for_the_latest_two_months(api, db, march):
    from tests.test_detection import Values

    Values(db, march).put("revenue", date(2026, 6, 1), 400, 410)  # the figures now run to June
    evaluate(api, march)
    assert not [a for a in alerts_of(api, march) if a["rule_code"].startswith("change_")]


def test_exactly_two_emails_to_one_person_are_already_a_summary(api, march, outbox):
    for code in ("change_sales", "change_customer", "data_stale"):
        put_rule(api, march, code, {"enabled": False})
    outbox.clear()
    evaluate(api, march)
    [mail] = outbox
    assert mail.subject == "[Vyterlix] 2 things need your attention"


def test_the_end_of_quiet_hours_asked_for_at_the_very_end_is_the_next_one():
    assert rules.quiet_ends(at(7), *NIGHT) == datetime(2026, 1, 6, 7, 0, tzinfo=UTC)


def _event(month, category="sales", severity="major"):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=uuid.uuid4(), category=category, severity=severity, period_start=month, kpi_code="revenue",
        kpi_name="Sales", summary="Sales fell.",
    )  # fmt: skip


def test_only_the_latest_two_months_are_news_and_the_boundary_month_counts(db, march, monkeypatch):
    from app.services import detection

    settings = {
        code: (True, "high", {"min_size": "major"}) for code in rules.CHANGE_RULE_FOR.values()
    }
    events = [_event(date(2026, 3, 1)), _event(date(2026, 2, 1)), _event(date(2026, 1, 1))]
    monkeypatch.setattr(detection, "list_events", lambda db, **kw: events)
    with scoped(db, march):
        found = alerts._changes(db, settings, date(2026, 3, 1))
        assert [c.data["month"] for c in found] == ["2026-03-01", "2026-02-01"]
        assert alerts._changes(db, settings, None) == []


def test_a_forecast_exactly_as_far_down_as_you_asked_is_an_alert(db, march, monkeypatch):
    from types import SimpleNamespace

    from app.services import forecast as forecast_service

    step = SimpleNamespace(value="900", lower="800", upper="1000", period_start=date(2026, 4, 1))
    fake = SimpleNamespace(status="ok", predictions=[step])
    monkeypatch.setattr(forecast_service, "read_latest", lambda db, code: fake)
    monkeypatch.setattr(alerts, "_revenue_value", lambda db, month: SimpleNamespace(value="1000"))
    with scoped(db, march):
        settings = {"forecast_decline": (True, "medium", {"drop_pct": 10})}
        assert len(alerts._forecast(db, settings, date(2026, 3, 1))) == 1
        settings = {"forecast_decline": (True, "medium", {"drop_pct": 11})}
        assert alerts._forecast(db, settings, date(2026, 3, 1)) == []


def test_data_that_is_exactly_as_old_as_you_asked_is_stale(db, march):
    settings = {"data_stale": (True, "medium", {"days": 14})}
    with scoped(db, march):
        last = date(2026, 3, 20)  # the bakery's last sale
        assert len(alerts._stale(db, settings, last + timedelta(days=14))) == 1
        assert alerts._stale(db, settings, last + timedelta(days=13)) == []


def test_a_threshold_of_exactly_one_thousand_is_allowed_and_one_more_is_not(api, march):
    assert put_rule(api, march, "data_stale", {"params": {"days": 1000}}).status_code == 200
    assert put_rule(api, march, "data_stale", {"params": {"days": 1001}}).status_code == 422


def test_a_message_for_nobody_to_see_is_not_kept(api, db, march):
    set_pref(db, march, "owner@acme.co.uk", "action", email=False, in_app=False)
    with scoped(db, march):
        owner = db.scalars(select(User).where(User.email == "owner@acme.co.uk")).one()
        assert (
            notifications.notify_user(
                db,
                uuid.UUID(march[0]),
                owner.id,
                category="action",
                severity="low",
                title="t",
                body="b",
            )
            is None
        )


def test_an_email_that_is_due_at_exactly_this_moment_is_sent(db, march, outbox):
    with scoped(db, march):
        owner = db.scalars(select(User).where(User.email == "owner@acme.co.uk")).one()
        due = datetime(2026, 10, 6, 6, 0, tzinfo=UTC)
        db.add(
            Notification(
                user_id=owner.id,
                category="sales",
                severity="high",
                title="t",
                body="b",
                email_status="pending",
                email_after=due,
                created_at=due,
            )
        )
        db.flush()
        assert notifications.send_due_emails(db, sender=outbox, now=due - timedelta(seconds=1)) == 0
        assert notifications.send_due_emails(db, sender=outbox, now=due) == 1


def test_a_link_to_another_site_is_never_put_in_an_email():
    org = uuid.uuid4()
    assert notifications.link_url(org, "https://evil.example/x") is None
    assert notifications.link_url(org, "//evil.example/x") is None
    assert notifications.link_url(org, None) is None
    assert notifications.link_url(org, "actions.html#abc").endswith(f"/actions.html?org={org}#abc")


def test_a_security_message_is_in_the_inbox_even_if_the_preference_said_otherwise(
    db, march, monkeypatch
):
    from types import SimpleNamespace

    monkeypatch.setattr(
        notifications, "_preference", lambda *a: SimpleNamespace(email=False, in_app=False)
    )
    with scoped(db, march):
        owner = db.scalars(select(User).where(User.email == "owner@acme.co.uk")).one()
        row = notifications.notify_user(
            db,
            uuid.UUID(march[0]),
            owner.id,
            category="security",
            severity="info",
            title="t",
            body="b",
        )
    assert row is not None and row.in_app is True and row.email_status == "sent"


# --- who is told and how ---------------------------------------------------------------------------------------------


def inbox(api, business, who="owner", org=0, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return api.get(f"{ORGS}/{business[org]}/notifications?{query}", headers=business[2][who]).json()


def test_the_owner_is_told_in_the_app_and_by_one_summary_email(api, march, raised, outbox):
    box = inbox(api, march)
    assert box["unread"] == 7 and len(box["items"]) == 7
    first = box["items"][0]
    assert (
        first["read"] is False
        and first["email"] == "sent"
        and first["severity"] in ("high", "medium")
        and first["alert_id"]
    )
    [mail] = outbox
    assert (
        mail.to == "owner@acme.co.uk" and mail.subject == "[Vyterlix] 7 things need your attention"
    )
    assert (
        mail.body.startswith(
            "Hi owner,\n\nHere is what needs your attention:\n\n- Sales fell by 32% in March 2026"
        )
        or "- Sales fell by 32%" in mail.body
    )
    assert (
        f"/changes.html?org={march[0]}" in mail.body and f"/import.html?org={march[0]}" in mail.body
    )


def test_one_alert_makes_one_plain_email(api, db, march, outbox):
    api.put(
        f"{ORGS}/{march[0]}/alerts/rules/change_financial",
        json={"enabled": False},
        headers=march[2]["owner"],
    )
    for code in ("change_sales", "change_customer"):
        api.put(
            f"{ORGS}/{march[0]}/alerts/rules/{code}",
            json={"enabled": False},
            headers=march[2]["owner"],
        )
    outbox.clear()
    evaluate(api, march)
    [mail] = outbox
    assert (
        mail.subject == f"[Vyterlix] No new sales for {STALE} days"
        and f"Hi owner,\n\n- No new sales for {STALE} days:" in mail.body
    )


def test_viewers_are_not_sent_alerts_but_can_read_the_history(api, march, raised):
    assert inbox(api, march, "viewer") == {"unread": 0, "items": []}
    assert len(alerts_of(api, march, "viewer")) == 7


def test_a_manager_is_only_told_about_their_own_area(api, db, signup, march, outbox):
    sales_manager = add_manager(api, db, signup, march, ["sales"])
    customer_manager = add_manager(
        api, db, signup, march, ["customer"], email="manager2@acme.co.uk"
    )
    outbox.clear()
    evaluate(api, march)
    assert {
        n["title"].split(" fell")[0].split(" rose")[0]
        for n in inbox(api, march, sales_manager)["items"]
    } == {"Average sale", "Items sold", "Refund rate", f"No new sales for {STALE} days"}
    assert {n["title"].split(" fell")[0] for n in inbox(api, march, customer_manager)["items"]} == {
        "Spend per customer",
        f"No new sales for {STALE} days",
    }
    assert {m.to for m in outbox} == {
        "owner@acme.co.uk",
        "manager@acme.co.uk",
        "manager2@acme.co.uk",
    }


def test_a_manager_with_no_limits_is_told_everything_and_things_that_belong_to_no_area_go_to_all_managers(
    api, db, signup, march
):
    free = add_manager(api, db, signup, march, None)
    limited = add_manager(api, db, signup, march, ["customer"], email="manager2@acme.co.uk")
    evaluate(api, march)
    assert len(inbox(api, march, free)["items"]) == 7
    assert f"No new sales for {STALE} days" in [
        n["title"] for n in inbox(api, march, limited)["items"]
    ]


def set_pref(db, march, address, category, **channels):
    with scoped(db, march):
        user = db.scalars(select(User).where(User.email == address)).one()
        db.add(
            NotificationPreference(
                user_id=user.id,
                category=category,
                **{"email": True, "in_app": True, "push": True, **channels},
            )
        )
        db.flush()


def test_a_person_can_turn_email_off_for_an_area_and_still_see_it_in_the_app(
    api, db, march, outbox
):
    set_pref(db, march, "owner@acme.co.uk", "sales", email=False)
    outbox.clear()
    evaluate(api, march)
    box = inbox(api, march)
    assert box["unread"] == 7
    by_title = {n["title"].split(" fell")[0].split(" rose")[0]: n for n in box["items"]}
    assert by_title["Average sale"]["email"] == "none" and by_title["Sales"]["email"] == "sent"
    [mail] = outbox
    assert (
        "Average sale" not in mail.body
        and "Sales fell by 32%" in mail.body
        and "4 things" in mail.subject
    )


def test_a_person_can_turn_an_area_off_everywhere(api, db, march):
    set_pref(db, march, "owner@acme.co.uk", "sales", email=False, in_app=False)
    evaluate(api, march)
    titles = [n["title"] for n in inbox(api, march)["items"]]
    assert len(titles) == 4 and not any(
        t.startswith(("Average sale", "Items sold", "Refund rate")) for t in titles
    )


def test_quiet_hours_hold_the_email_until_morning_but_not_the_app_message(api, db, march, outbox):
    with scoped(db, march):
        db.add(BusinessSettings(quiet_hours_start=time(22), quiet_hours_end=time(7)))
        db.flush()
        outbox.clear()
        alerts.evaluate(db, owner_tenant(db, march), now=UTC_NIGHT, sender=outbox)
    assert outbox == []
    box = inbox(api, march)
    assert box["unread"] == 7 and {n["email"] for n in box["items"]} == {"pending"}
    with scoped(db, march):
        assert (
            notifications.send_due_emails(db, sender=outbox, now=UTC_NIGHT + timedelta(hours=2))
            == 0
        )  # still night
        assert (
            notifications.send_due_emails(
                db, sender=outbox, now=datetime(2026, 10, 6, 6, 0, tzinfo=UTC)
            )
            == 7
        )
    [mail] = outbox
    assert mail.subject == "[Vyterlix] 7 things need your attention"
    assert {n["email"] for n in inbox(api, march)["items"]} == {"sent"}


def test_a_critical_alert_is_emailed_straight_through_quiet_hours(api, db, march, outbox):
    api.put(
        f"{ORGS}/{march[0]}/alerts/rules/data_stale",
        json={"severity": "critical"},
        headers=march[2]["owner"],
    )
    for code in ("change_sales", "change_financial", "change_customer"):
        api.put(
            f"{ORGS}/{march[0]}/alerts/rules/{code}",
            json={"enabled": False},
            headers=march[2]["owner"],
        )
    with scoped(db, march):
        db.add(BusinessSettings(quiet_hours_start=time(22), quiet_hours_end=time(7)))
        db.flush()
        outbox.clear()
        alerts.evaluate(db, owner_tenant(db, march), now=UTC_NIGHT, sender=outbox)
    assert len(outbox) == 1


def test_a_failed_email_is_recorded_and_the_rest_carry_on(api, db, march):
    class Broken:
        def send(self, message):
            raise EmailDeliveryError("down")

    with scoped(db, march):
        alerts.evaluate(db, owner_tenant(db, march), sender=Broken())
    assert {n["email"] for n in inbox(api, march)["items"]} == {"failed"}
    assert inbox(api, march)["unread"] == 7


def test_emails_are_sent_once(api, db, march, outbox):
    evaluate(api, march)
    outbox.clear()
    with scoped(db, march):
        assert notifications.send_due_emails(db, sender=outbox) == 0
    assert outbox == []


def test_a_message_that_is_not_an_alert_goes_through_the_same_rules(api, db, march, outbox):
    outbox.clear()
    with scoped(db, march):
        owner = db.scalars(select(User).where(User.email == "owner@acme.co.uk")).one()
        row = notifications.notify_user(
            db,
            uuid.UUID(march[0]),
            owner.id,
            category="action",
            severity="medium",
            title="A direct note",
            body="Body.",
            link="actions.html",
            sender=outbox,
        )
    assert (
        row.alert_id is None
        and row.email_status == "sent"
        and outbox[0].subject == "[Vyterlix] A direct note"
    )
    assert inbox(api, march)["items"][0]["title"] == "A direct note"
    with scoped(db, march):
        assert (
            notifications.notify_user(
                db,
                uuid.UUID(march[0]),
                None,
                category="action",
                severity="medium",
                title="t",
                body="b",
            )
            is None
        )


def test_security_messages_cannot_be_switched_off_and_ignore_quiet_hours(api, db, march, outbox):
    set_pref(db, march, "owner@acme.co.uk", "security", email=True, in_app=True)
    with scoped(db, march):
        db.add(BusinessSettings(quiet_hours_start=time(22), quiet_hours_end=time(7)))
        db.flush()
        owner = db.scalars(select(User).where(User.email == "owner@acme.co.uk")).one()
        outbox.clear()
        notifications.notify_user(
            db,
            uuid.UUID(march[0]),
            owner.id,
            category="security",
            severity="info",
            title="Sign-in from somewhere new",
            body="b",
            now=UTC_NIGHT,
            sender=outbox,
        )
    assert len(outbox) == 1 and inbox(api, march)["items"][0]["category"] == "security"


def test_a_security_note_reaches_the_inbox_in_every_business_the_person_belongs_to(api, db, march):
    with scoped(db, march):
        owner = db.scalars(select(User).where(User.email == "owner@acme.co.uk")).one()
    from app.db.tenant import ACROSS_TENANTS

    assert notifications.record_security(db, owner, "Your password was changed", "Details.") == 1
    box = inbox(api, march)
    assert (
        box["items"][0]["title"] == "Your password was changed"
        and box["items"][0]["severity"] == "high"
    )
    assert box["items"][0]["email"] == "sent" and ACROSS_TENANTS


def test_changing_a_password_sends_the_email_and_leaves_a_note_in_the_inbox(api, db, march, outbox):
    from app.services.password_reset import send_password_changed_alert

    outbox.clear()
    with scoped(db, march):
        owner = db.scalars(select(User).where(User.email == "owner@acme.co.uk")).one()
    send_password_changed_alert(outbox, owner, logged_out="on all devices", db=db)
    assert [m.subject for m in outbox] == ["Your Vyterlix password was changed"]
    item = inbox(api, march)["items"][0]
    assert (item["title"], item["category"], item["severity"]) == (
        "Your password was changed",
        "security",
        "high",
    )
    assert "logged out on all devices" in item["body"]


# --- the inbox ---------------------------------------------------------------------------------------------------------------


def test_notifications_can_be_read_one_at_a_time_or_all_together(api, march, raised):
    box = inbox(api, march)
    one = box["items"][0]["id"]
    assert (
        api.post(
            f"{ORGS}/{march[0]}/notifications/{one}/read", headers=march[2]["owner"]
        ).status_code
        == 204
    )
    assert inbox(api, march)["unread"] == 6
    assert (
        api.post(
            f"{ORGS}/{march[0]}/notifications/{one}/read", headers=march[2]["owner"]
        ).status_code
        == 204
    )  # again: no harm
    assert [n["id"] for n in inbox(api, march, unread_only="true")["items"]] and len(
        inbox(api, march, unread_only="true")["items"]
    ) == 6
    assert (
        api.post(f"{ORGS}/{march[0]}/notifications/read-all", headers=march[2]["owner"]).status_code
        == 204
    )
    assert inbox(api, march)["unread"] == 0 and inbox(api, march, unread_only="true")["items"] == []
    assert len(inbox(api, march)["items"]) == 7  # still there to look back on


def test_nobody_can_read_anothers_notification(api, db, signup, march, raised):
    manager = add_manager(api, db, signup, march, None)
    one = inbox(api, march)["items"][0]["id"]
    assert (
        api.post(
            f"{ORGS}/{march[0]}/notifications/{one}/read", headers=march[2][manager]
        ).status_code
        == 404
    )
    assert (
        api.post(
            f"{ORGS}/{march[0]}/notifications/{uuid.uuid4()}/read", headers=march[2]["owner"]
        ).status_code
        == 404
    )
    assert inbox(api, march)["unread"] == 7


def test_the_inbox_needs_a_login_and_stays_inside_one_business(api, march, raised):
    assert api.get(f"{ORGS}/{march[0]}/notifications").status_code == 401
    assert inbox(api, march, "other", org=1) == {"unread": 0, "items": []}


def test_the_newest_come_first_and_the_limit_is_respected(api, march, raised):
    items = inbox(api, march)["items"]
    assert [n["created_at"] for n in items] == sorted(
        (n["created_at"] for n in items), reverse=True
    )
    assert len(inbox(api, march, limit=3)["items"]) == 3
    assert (
        api.get(f"{ORGS}/{march[0]}/notifications?limit=0", headers=march[2]["owner"]).status_code
        == 422
    )


# --- the alert history ---------------------------------------------------------------------------------------------------------


def test_the_history_shows_the_most_serious_open_ones_first(api, march, raised):
    rows = alerts_of(api, march)
    assert rows[-1]["rule_code"] == "data_stale" and rows[0]["severity"] == "high"
    target = rows[0]
    api.post(f"{ORGS}/{march[0]}/alerts/{target['id']}/resolve", headers=march[2]["owner"])
    rows = alerts_of(api, march)
    assert rows[-1]["id"] == target["id"] and rows[-1]["status"] == "resolved"


def test_the_history_can_be_filtered(api, march, raised):
    assert (
        len(alerts_of(api, march, severity="medium")) == 1
        and len(alerts_of(api, march, category="sales")) == 3
    )
    assert (
        alerts_of(api, march, status="acknowledged") == []
        and len(alerts_of(api, march, limit=2)) == 2
    )
    assert (
        api.get(f"{ORGS}/{march[0]}/alerts?severity=huge", headers=march[2]["owner"]).status_code
        == 422
    )
    assert (
        api.get(f"{ORGS}/{march[0]}/alerts?category=weather", headers=march[2]["owner"]).status_code
        == 422
    )


def test_the_summary_counts_by_state(api, march, raised):
    url = f"{ORGS}/{march[0]}/alerts/summary"
    assert api.get(url, headers=march[2]["viewer"]).json() == {
        "open": 7,
        "acknowledged": 0,
        "resolved": 0,
        "high_or_critical_open": 6,
    }
    first = alerts_of(api, march)
    api.post(f"{ORGS}/{march[0]}/alerts/{first[0]['id']}/acknowledge", headers=march[2]["owner"])
    api.post(f"{ORGS}/{march[0]}/alerts/{first[1]['id']}/resolve", headers=march[2]["owner"])
    assert api.get(url, headers=march[2]["owner"]).json() == {
        "open": 5,
        "acknowledged": 1,
        "resolved": 1,
        "high_or_critical_open": 5,
    }


def test_acknowledging_and_resolving_are_recorded_with_who_and_why(api, march, raised):
    target = alerts_of(api, march)[0]
    base = f"{ORGS}/{march[0]}/alerts/{target['id']}"
    acked = api.post(
        f"{base}/acknowledge", json={"note": "Looking into it"}, headers=march[2]["owner"]
    ).json()
    assert (
        acked["status"] == "acknowledged"
        and acked["acknowledged_by"] == "owner"
        and acked["acknowledged_at"]
    )
    done = api.post(f"{base}/resolve", json={"note": "Fixed"}, headers=march[2]["owner"]).json()
    assert done["status"] == "resolved" and done["resolved_at"]
    assert [(e["kind"], e["user"], e["note"]) for e in done["events"]] == [
        ("raised", None, None),
        ("acknowledged", "owner", "Looking into it"),
        ("resolved", "owner", "Fixed"),
    ]


def test_an_alert_can_only_be_acknowledged_while_open_and_resolved_once(api, march, raised):
    base = f"{ORGS}/{march[0]}/alerts/{alerts_of(api, march)[0]['id']}"
    api.post(f"{base}/acknowledge", headers=march[2]["owner"])
    again = api.post(f"{base}/acknowledge", headers=march[2]["owner"])
    assert again.status_code == 409 and again.json()["error"]["code"] == "not_open"
    api.post(f"{base}/resolve", headers=march[2]["owner"])
    twice = api.post(f"{base}/resolve", headers=march[2]["owner"])
    assert twice.status_code == 409 and twice.json()["error"]["code"] == "already_resolved"
    assert api.post(f"{base}/acknowledge", headers=march[2]["owner"]).status_code == 409


def test_viewers_cannot_deal_with_alerts_and_managers_only_in_their_own_area(
    api, db, signup, march, raised
):
    target = next(a for a in alerts_of(api, march) if a["category"] == "customer")
    base = f"{ORGS}/{march[0]}/alerts/{target['id']}"
    assert api.post(f"{base}/acknowledge", headers=march[2]["viewer"]).status_code == 403
    sales = add_manager(api, db, signup, march, ["sales"])
    outside = api.post(f"{base}/acknowledge", headers=march[2][sales])
    assert outside.status_code == 403 and outside.json()["error"]["code"] == "outside_remit"
    inside = add_manager(api, db, signup, march, ["customer"], email="manager2@acme.co.uk")
    assert api.post(f"{base}/acknowledge", headers=march[2][inside]).status_code == 200
    stale = next(a for a in alerts_of(api, march) if a["rule_code"] == "data_stale")
    assert (
        api.post(
            f"{ORGS}/{march[0]}/alerts/{stale['id']}/resolve", headers=march[2][sales]
        ).status_code
        == 200
    )  # no area: any manager


def test_only_owners_and_managers_can_ask_for_a_look_now(api, march):
    assert (
        api.post(f"{ORGS}/{march[0]}/alerts/evaluate", headers=march[2]["viewer"]).status_code
        == 403
    )
    assert api.post(f"{ORGS}/{march[0]}/alerts/evaluate").status_code == 401


def test_an_unknown_alert_is_not_found_and_other_businesses_see_nothing(api, march, raised):
    assert (
        api.get(f"{ORGS}/{march[0]}/alerts/{uuid.uuid4()}", headers=march[2]["owner"]).status_code
        == 404
    )
    mine = alerts_of(api, march)[0]["id"]
    assert api.get(f"{ORGS}/{march[1]}/alerts/{mine}", headers=march[2]["other"]).status_code == 404
    assert (
        alerts_of(api, march, "other") == []
        if False
        else api.get(f"{ORGS}/{march[1]}/alerts", headers=march[2]["other"]).json() == []
    )


# --- the settings for each kind --------------------------------------------------------------------------------------------------


def put_rule(api, march, code, body, who="owner"):
    return api.put(f"{ORGS}/{march[0]}/alerts/rules/{code}", json=body, headers=march[2][who])


def test_every_kind_is_listed_with_its_defaults(api, march):
    rows = api.get(f"{ORGS}/{march[0]}/alerts/rules", headers=march[2]["viewer"]).json()
    assert [r["code"] for r in rows] == [r.code for r in rules.RULES]
    stale = next(r for r in rows if r["code"] == "data_stale")
    assert stale == {
        "code": "data_stale",
        "name": "No new sales data",
        "category": "data",
        "enabled": True,
        "severity": "medium",
        "params": {"days": 14},
        "params_help": {"days": "Days without a new sale before you are told"},
        "always_on": False,
        "customised": False,
        "description": stale["description"],
    }
    assert next(r for r in rows if r["code"] == "security_password_changed")["always_on"] is True


def test_the_owner_can_change_a_kind_and_it_is_audited(api, db, march):
    res = put_rule(
        api, march, "data_stale", {"enabled": False, "severity": "low", "params": {"days": 30}}
    )
    assert res.status_code == 200 and res.json()["customised"] is True
    assert (res.json()["enabled"], res.json()["severity"], res.json()["params"]) == (
        False,
        "low",
        {"days": 30},
    )
    again = put_rule(api, march, "data_stale", {"enabled": True})
    assert (
        again.json()["params"] == {"days": 30} and again.json()["severity"] == "low"
    )  # what was not sent is kept
    with scoped(db, march):
        entries = db.scalars(select(AuditLog).where(AuditLog.action == "alert.rule_changed")).all()
        assert len(entries) == 2 and entries[0].details["code"] == "data_stale"


def test_only_the_owner_can_change_the_settings(api, db, signup, march):
    assert put_rule(api, march, "data_stale", {"enabled": False}, "viewer").status_code == 403
    manager = add_manager(api, db, signup, march, None)
    assert put_rule(api, march, "data_stale", {"enabled": False}, manager).status_code == 403


def test_security_alerts_cannot_be_switched_off_but_can_be_made_more_serious(api, march):
    refused = put_rule(api, march, "security_password_changed", {"enabled": False})
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "always_on"
    assert (
        put_rule(api, march, "security_password_changed", {"severity": "critical"}).json()[
            "severity"
        ]
        == "critical"
    )


@pytest.mark.parametrize(
    ("code", "body"),
    [("nonsense", {"enabled": False}), ("data_stale", {"params": {"nope": 1}}), ("data_stale", {"params": {"days": 0}}),
     ("data_stale", {"params": {"days": 1001}}), ("data_stale", {"params": {"days": "ten"}}),
     ("change_sales", {"params": {"min_size": "huge"}}), ("change_sales", {"params": {"min_size": 5}}),
     ("data_stale", {"severity": "huge"}), ("data_stale", {"surprise": 1})],
)  # fmt: skip
def test_settings_that_make_no_sense_are_refused(api, march, code, body):
    assert put_rule(api, march, code, body).status_code in (404, 422)


def test_the_limits_of_a_threshold_are_inclusive(api, march):
    assert put_rule(api, march, "data_stale", {"params": {"days": 1}}).status_code == 200
    assert put_rule(api, march, "data_stale", {"params": {"days": 1000}}).status_code == 200


def test_each_business_has_its_own_settings(api, march):
    put_rule(api, march, "data_stale", {"enabled": False})
    other = api.get(f"{ORGS}/{march[1]}/alerts/rules", headers=march[2]["other"]).json()
    assert next(r for r in other if r["code"] == "data_stale")["enabled"] is True


# --- the worker's timed round -----------------------------------------------------------------------------------------------------------


def test_the_timed_round_looks_for_alerts_and_sends_what_was_waiting_for_morning(
    api, db, march, outbox, monkeypatch
):
    with scoped(db, march):
        db.add(BusinessSettings(quiet_hours_start=time(22), quiet_hours_end=time(7)))
        db.flush()
        alerts.evaluate(db, owner_tenant(db, march), now=UTC_NIGHT, sender=outbox)
    sent = []
    monkeypatch.setattr(
        "app.services.notifications.get_email_sender",
        lambda: type("S", (), {"send": lambda self, m: sent.append(m)})(),
    )
    totals = scheduler.tick(
        db, today=date(2026, 10, 6), now=datetime(2026, 10, 6, 6, 0, tzinfo=UTC)
    )
    assert totals["businesses"] >= 1
    assert sent and {n["email"] for n in inbox(api, march)["items"]} == {"sent"}


# --- the email provider -----------------------------------------------------------------------------------------------------------------------


class FakeSmtp:
    log: list = []
    fail: Exception | None = None

    def __init__(self, host, port, timeout=None):
        FakeSmtp.log.append(("connect", host, port, timeout))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        FakeSmtp.log.append(("close",))

    def starttls(self, context=None):
        FakeSmtp.log.append(("starttls", context is not None))

    def login(self, user, password):
        FakeSmtp.log.append(("login", user, password))

    def send_message(self, mime):
        if FakeSmtp.fail:
            raise FakeSmtp.fail
        FakeSmtp.log.append(
            ("send", mime["From"], mime["To"], mime["Subject"], mime.get_content().strip())
        )


@pytest.fixture(autouse=False)
def smtp():
    FakeSmtp.log, FakeSmtp.fail = [], None
    return FakeSmtp


def sender(**kw):
    return SmtpEmailSender(
        "smtp.example.com",
        587,
        kw.pop("username", "me"),
        kw.pop("password", "secret"),
        sender="Vyterlix <no-reply@vyterlix.com>",
        smtp_class=FakeSmtp,
        **kw,
    )


def test_an_email_is_sent_through_smtp_with_tls_and_a_login(smtp):
    sender().send(EmailMessage(to="a@example.com", subject="Hello", body="Body text"))
    assert smtp.log == [
        ("connect", "smtp.example.com", 587, 20.0),
        ("starttls", True),
        ("login", "me", "secret"),
        ("send", "Vyterlix <no-reply@vyterlix.com>", "a@example.com", "Hello", "Body text"),
        ("close",),
    ]


def test_tls_and_login_can_be_left_out_for_a_local_relay(smtp):
    sender(username=None, use_tls=False).send(
        EmailMessage(to="a@example.com", subject="s", body="b")
    )
    assert [e[0] for e in smtp.log] == ["connect", "send", "close"]


@pytest.mark.parametrize(
    "error",
    [
        smtplib.SMTPRecipientsRefused({}),
        smtplib.SMTPAuthenticationError(535, b"no"),
        ConnectionRefusedError("down"),
        TimeoutError(),
    ],
)
def test_a_provider_that_refuses_is_an_error_and_never_a_silent_loss(smtp, error):
    smtp.fail = error
    with pytest.raises(EmailDeliveryError):
        sender().send(EmailMessage(to="a@example.com", subject="s", body="b"))


def test_the_password_is_a_secret_and_smtp_needs_a_host():
    with pytest.raises(ValueError, match="SMTP_HOST"):
        Settings(email_backend="smtp", smtp_host=None)
    s = Settings(email_backend="smtp", smtp_host="smtp.example.com", smtp_password="hunter2")
    assert (
        "hunter2" not in repr(s)
        and "hunter2" not in str(s.model_dump())
        and s.smtp_password.get_secret_value() == "hunter2"
    )


def test_production_still_refuses_the_console_backend_but_accepts_smtp():
    with pytest.raises(ValueError):
        Settings(env="prod", email_backend="console", encryption_key="x" * 44, jwt_secret="j" * 40)
    Settings(
        env="prod",
        email_backend="smtp",
        smtp_host="smtp.example.com",
        encryption_key="Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmE=",
        jwt_secret="j" * 40,
    )


def test_the_email_sender_follows_the_setting(monkeypatch):
    from app.services import email

    monkeypatch.setattr(
        email,
        "get_settings",
        lambda: Settings(
            email_backend="smtp", smtp_host="smtp.example.com", smtp_username="u", smtp_password="p"
        ),
    )
    assert isinstance(email.get_email_sender(), SmtpEmailSender)
    monkeypatch.setattr(email, "get_settings", lambda: Settings())
    assert isinstance(email.get_email_sender(), email.ConsoleEmailSender)


# --- the tables ------------------------------------------------------------------------------------------------------------------------------------


def make_alert(db, march, key="k", **overrides):
    now = datetime.now(UTC)
    fields = dict(
        rule_code="x",
        category="sales",
        severity="high",
        title="t",
        body="b",
        dedupe_key=key,
        status="open",
        first_seen_at=now,
        last_seen_at=now,
    )
    with scoped(db, march):
        alert = Alert(**{**fields, **overrides})
        db.add(alert)
        db.flush()
        return alert.id


def test_the_same_thing_cannot_be_open_twice_but_can_come_back_after_it_is_resolved(db, march):
    make_alert(db, march)
    with pytest.raises(IntegrityError):
        make_alert(db, march)


def test_a_resolved_alert_does_not_block_a_new_one(db, march):
    make_alert(db, march, status="resolved")
    make_alert(db, march)
    make_alert(db, march, key="other")


@pytest.mark.parametrize(
    "bad",
    [
        {"severity": "huge"},
        {"status": "lost"},
        {"occurrences": 0},
        {"category": "weather"},
        {"title": " "},
    ],
)
def test_an_alert_that_makes_no_sense_is_refused(db, march, bad):
    with pytest.raises(IntegrityError):
        make_alert(
            db,
            march,
            key="bad",
            **({"severity": bad["severity"]} if "severity" in bad else {})
            | ({k: v for k, v in bad.items() if k not in ("severity",)}),
        )


def test_a_rule_row_must_be_sensible_and_security_must_stay_on(db, march):
    with scoped(db, march):
        db.add(AlertRule(code="a", category="security", severity="high", enabled=False))
        with pytest.raises(IntegrityError):
            db.flush()


def test_a_business_has_one_row_per_kind(db, march):
    with scoped(db, march):
        db.add(AlertRule(code="data_stale", category="data", severity="low"))
        db.flush()
        db.add(AlertRule(code="data_stale", category="data", severity="low"))
        with pytest.raises(IntegrityError):
            db.flush()


def test_removing_an_alert_removes_its_history_and_notifications(api, db, march, raised):
    with scoped(db, march):
        db.execute(Alert.__table__.delete())
        assert db.scalar(select(func.count()).select_from(AlertEvent)) == 0
        assert db.scalar(select(func.count()).select_from(Notification)) == 0


def test_a_notification_must_have_a_known_email_state(db, march):
    with scoped(db, march):
        user = db.scalars(select(User).where(User.email == "owner@acme.co.uk")).one()
        db.add(
            Notification(
                user_id=user.id,
                category="sales",
                severity="high",
                title="t",
                body="b",
                email_status="maybe",
                created_at=datetime.now(UTC),
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
