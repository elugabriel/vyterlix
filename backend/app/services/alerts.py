# ruff: noqa: E501
"""Alerts (Phase 14): finding the things that need attention and keeping their history.

Each round (after the figures are worked out, and on the worker's timed round) looks at the business's
own results with the rules in alerts/rules.py. A thing that needs attention becomes an alert once: if it
is still open when it is seen again, that is counted on the same alert instead of being raised again. An
alert about a state of affairs (work that is overdue, data that is stale) closes itself when that stops
being true; an alert about a change in a figure stays until someone deals with it. Telling people is
left entirely to the notification service.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.alerts import rules
from app.core.errors import AppError, ConflictError, NotFoundError, PermissionDeniedError
from app.core.permissions import KpiCategory
from app.core.uk import today_uk
from app.integrations.base import utcnow
from app.models.alerts import Alert, AlertEvent, AlertRule
from app.models.data import Sale
from app.models.identity import User
from app.models.kpi import KpiDefinition, KpiValue
from app.schemas.alerts import (
    AlertCountsOut,
    AlertDetailOut,
    AlertEventOut,
    AlertOut,
    EvaluateOut,
    RuleIn,
    RuleOut,
)
from app.services import actions as actions_service
from app.services import detection, forecast, health, notifications
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta
from app.services.email import EmailSender

logger = logging.getLogger("vyterlix.alerts")

STATE_BASED = ("forecast_decline", "action_overdue", "data_stale", "data_quality", "health_drop")
RECENT_MONTHS = 2  # changes older than this are history, not news


@dataclass
class Candidate:
    rule_code: str
    category: str
    severity: str
    title: str
    body: str
    dedupe_key: str
    link: str | None = None
    kpi_category: str | None = None
    data: dict = field(default_factory=dict)


# --- the business's own settings for each kind --------------------------------------------------------


def _rows(db: Session) -> dict[str, AlertRule]:
    return {r.code: r for r in db.scalars(select(AlertRule))}


def rules_out(db: Session) -> list[RuleOut]:
    rows = _rows(db)
    out = []
    for d in rules.RULES:
        enabled, severity, params = rules.settings_for(d, rows.get(d.code))
        out.append(
            RuleOut(
                code=d.code,
                name=d.name,
                category=d.category,
                description=d.description,
                enabled=enabled,
                severity=severity,
                params=params,
                params_help=d.params_help,
                always_on=d.always_on,
                customised=d.code in rows,
            )  # fmt: skip
        )
    return out


def _check_params(definition: rules.RuleDef, params: dict) -> dict:
    for key, value in params.items():
        if key not in definition.params:
            raise AppError(
                f"{key!r} is not a setting of this alert.", code="unknown_setting", status_code=422
            )
        default = definition.params[key]
        if isinstance(default, int):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1000:
                raise AppError(
                    f"{key!r} must be a whole number from 1 to 1000.",
                    code="bad_setting",
                    status_code=422,
                )
        elif value not in ("notable", "major"):
            raise AppError(
                f"{key!r} must be notable or major.", code="bad_setting", status_code=422
            )
    return params


def set_rule(db: Session, tenant, code: str, body: RuleIn, meta: RequestMeta) -> RuleOut:
    definition = rules.BY_CODE.get(code)
    if definition is None:
        raise NotFoundError("That kind of alert was not found", code="alert_rule_not_found")
    if body.enabled is False and definition.always_on:
        raise AppError("Security alerts cannot be switched off.", code="always_on", status_code=422)
    row = _rows(db).get(code)
    if row is None:
        row = AlertRule(
            organization_id=tenant.organization_id,
            code=code,
            category=definition.category,
            severity=definition.severity,
            enabled=True,
            params={},
        )
        db.add(row)
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.severity is not None:
        row.severity = body.severity
    if body.params is not None:
        row.params = {**row.params, **_check_params(definition, body.params)}
    row.updated_by_user_id = tenant.user.id
    db.flush()
    record_audit(
        db, AuditAction.ALERT_RULE_CHANGED, actor_user_id=tenant.user.id, organization_id=tenant.organization_id,
        target_type="alert_rule", target_id=row.id, ip_address=meta.ip_address, user_agent=meta.user_agent,
        details={"code": code, "enabled": row.enabled, "severity": row.severity, "params": row.params},
    )  # fmt: skip
    db.commit()
    return next(r for r in rules_out(db) if r.code == code)


# --- raising, repeating, closing --------------------------------------------------------------------------


def _event(db: Session, alert: Alert, kind: str, user_id=None, note=None, now=None) -> None:
    db.add(
        AlertEvent(
            organization_id=alert.organization_id,
            alert_id=alert.id,
            kind=kind,
            user_id=user_id,
            note=note,
            created_at=now or utcnow(),
        )
    )


def raise_alert(
    db: Session, organization_id: uuid.UUID, c: Candidate, now: datetime
) -> tuple[Alert, bool]:
    """(the alert, whether it is new). Something already open is counted, not raised again."""
    previous = db.scalars(
        select(Alert).where(Alert.dedupe_key == c.dedupe_key).order_by(Alert.created_at.desc())
    ).first()
    if previous is not None and previous.status == "resolved" and c.rule_code not in STATE_BASED:
        return (
            previous,
            False,
        )  # a change in a figure is one alert for good: dealt with, it stays so
    existing = previous if previous is not None and previous.status != "resolved" else None
    if existing is not None:
        if (
            existing.last_seen_at.astimezone(rules.UK_TZ).date()
            != now.astimezone(rules.UK_TZ).date()
        ):
            existing.occurrences += (
                1  # seen again, counted once a day however often it is looked at
            )
            _event(db, existing, "repeated", now=now)
        existing.last_seen_at = now
        if rules.RANK[c.severity] > rules.RANK[existing.severity]:
            existing.severity = c.severity  # it has got worse
        existing.body = c.body
        return existing, False
    alert = Alert(
        organization_id=organization_id, rule_code=c.rule_code, category=c.category, kpi_category=c.kpi_category,
        severity=c.severity, title=c.title[:200], body=c.body, link=c.link, dedupe_key=c.dedupe_key, status="open",
        occurrences=1, data=c.data, first_seen_at=now, last_seen_at=now,
    )  # fmt: skip
    db.add(alert)
    db.flush()
    _event(db, alert, "raised", now=now)
    return alert, True


# --- finding what needs attention --------------------------------------------------------------------------


def _latest_month(db: Session) -> date | None:
    return db.scalar(
        select(func.max(KpiValue.period_start)).where(
            KpiValue.granularity == "month", KpiValue.is_complete.is_(True), KpiValue.status == "ok"
        )
    )


def _month_back(month: date, by: int) -> date:
    index = month.year * 12 + month.month - 1 - by
    return date(index // 12, index % 12 + 1, 1)


def _revenue_value(db: Session, month: date) -> KpiValue | None:
    return db.scalars(
        select(KpiValue)
        .join(KpiDefinition, KpiDefinition.id == KpiValue.kpi_id)
        .where(
            KpiDefinition.code == "revenue",
            KpiValue.granularity == "month",
            KpiValue.period_start == month,
            KpiValue.status == "ok",
        )
    ).first()


def _changes(db: Session, settings: dict, latest: date | None) -> list[Candidate]:
    found = []
    if latest is None:
        return found
    floor = _month_back(latest, RECENT_MONTHS - 1)
    for event in detection.list_events(db, effect="bad", include_expected=False, limit=500):
        code = rules.CHANGE_RULE_FOR.get(event.category)
        if code is None or event.period_start < floor:
            continue
        enabled, severity, params = settings[code]
        level = (
            rules.change_severity(severity, event.severity, params["min_size"]) if enabled else None
        )
        if level is None:
            continue
        found.append(
            Candidate(
                code,
                event.category,
                level,
                event.summary,
                f"{event.kpi_name} needs a look. Open What changed to see why it happened and what to do about it.",
                rules.key_change(event.kpi_code, event.period_start),
                "changes.html",
                event.category,
                {
                    "event_id": str(event.id),
                    "kpi": event.kpi_code,
                    "month": event.period_start.isoformat(),
                },
            )  # fmt: skip
        )
    return found


def _forecast(db: Session, settings: dict, latest: date | None) -> list[Candidate]:
    enabled, severity, params = settings["forecast_decline"]
    if not enabled or latest is None:
        return []
    f = forecast.read_latest(db, "revenue")
    now_value = _revenue_value(db, latest)
    if (
        f is None
        or f.status != "ok"
        or not f.predictions
        or now_value is None
        or Decimal(now_value.value) <= 0
    ):
        return []
    now, expected = Decimal(now_value.value), Decimal(f.predictions[0].value)
    drop = (now - expected) / now * 100
    if drop < params["drop_pct"]:
        return []
    return [
        Candidate(
            "forecast_decline",
            "forecast",
            severity,
            f"Sales are forecast to fall by {drop:.0f}% in {f.predictions[0].period_start:%B %Y}",
            f"Sales were £{now:,.2f} in {latest:%B %Y} and are expected to be about £{expected:,.2f} in {f.predictions[0].period_start:%B %Y} (most likely between £{Decimal(f.predictions[0].lower):,.2f} and £{Decimal(f.predictions[0].upper):,.2f}).",
            rules.key_forecast("revenue"),
            "forecast.html",
            "financial",
            {"drop_pct": f"{drop:.1f}"},
        )  # fmt: skip
    ]


def _overdue(db: Session, tenant, settings: dict, today: date) -> list[Candidate]:
    enabled, severity, params = settings["action_overdue"]
    if not enabled:
        return []
    return [
        Candidate(
            "action_overdue",
            "action",
            severity,
            f'"{a.title}" is {a.days_late} days overdue',
            f"It was due on {a.target_date:%d/%m/%Y}. Open it to move the date, finish it or cancel it.",
            rules.key_action(a.id),
            f"actions.html#{a.id}",
            a.category,
            {"action_id": str(a.id), "days_late": a.days_late},
        )  # fmt: skip
        for a in actions_service.list_actions(db, tenant, status="overdue", today=today)
        if a.days_late >= params["days"] and a.target_date
    ]


def _stale(db: Session, settings: dict, today: date) -> list[Candidate]:
    enabled, severity, params = settings["data_stale"]
    last = db.scalar(select(func.max(Sale.sold_on)))
    if not enabled or last is None or (today - last).days < params["days"]:
        return []
    days = (today - last).days
    return [
        Candidate(
            "data_stale",
            "data",
            severity,
            f"No new sales for {days} days",
            f"The most recent sale you have is from {last:%d/%m/%Y}. Bring in your latest sales so your figures stay up to date.",
            rules.KEY_STALE,
            "import.html",
            None,
            {"last_sale": last.isoformat(), "days": days},
        )
    ]


def _quality(db: Session, settings: dict, latest: date | None) -> list[Candidate]:
    enabled, severity, params = settings["data_quality"]
    value = None if latest is None else _revenue_value(db, latest)
    if (
        not enabled
        or value is None
        or value.data_quality is None
        or value.data_quality >= params["min_score"]
    ):
        return []
    return [
        Candidate(
            "data_quality",
            "data",
            severity,
            f"The data for {latest:%B %Y} is incomplete",
            f"Its data quality is {value.data_quality} out of 100, below the {params['min_score']} you asked to be told about, so figures for that month may not be reliable.",
            rules.key_quality(latest),
            "health.html",
            None,
            {"score": value.data_quality},
        )
    ]


def _health(db: Session, settings: dict) -> list[Candidate]:
    enabled, severity, params = settings["health_drop"]
    h = health.latest_health(db)
    if (
        not enabled
        or h is None
        or h.overall_score is None
        or h.previous_score is None
        or h.previous_score - h.overall_score < params["points"]
    ):
        return []
    fell = h.previous_score - h.overall_score
    return [
        Candidate(
            "health_drop",
            "financial",
            severity,
            f"Your business health fell by {fell} points",
            f"It was {h.previous_score} out of 100 the month before and is {h.overall_score} for {h.period_start:%B %Y}. See which area pulled it down.",
            rules.key_health(h.period_start),
            "health.html",
            None,
            {"fell": fell},
        )
    ]


def candidates(db: Session, tenant, today: date) -> list[Candidate]:
    rows = _rows(db)
    settings = {d.code: rules.settings_for(d, rows.get(d.code)) for d in rules.RULES}
    latest = _latest_month(db)
    return [
        *_changes(db, settings, latest), *_forecast(db, settings, latest), *_overdue(db, tenant, settings, today),
        *_stale(db, settings, today), *_quality(db, settings, latest), *_health(db, settings),
    ]  # fmt: skip


def evaluate(
    db: Session,
    tenant,
    *,
    today: date | None = None,
    sender: EmailSender | None = None,
    now: datetime | None = None,
) -> EvaluateOut:
    """Look at everything and raise, count or close alerts. Safe to run as often as you like."""
    today, now = today or today_uk(), now or utcnow()
    current = candidates(db, tenant, today)
    raised = repeated = notified = 0
    for c in current:
        alert, new = raise_alert(db, tenant.organization_id, c, now)
        if new:
            raised += 1
            notified += len(notifications.deliver(db, alert, sender=sender, now=now, send=False))
        else:
            repeated += 1
    notifications.send_due_emails(db, sender=sender, now=now)
    keys = {c.dedupe_key for c in current}
    resolved = 0
    for alert in db.scalars(
        select(Alert).where(Alert.rule_code.in_(STATE_BASED), Alert.status != "resolved")
    ):
        if alert.dedupe_key not in keys:
            alert.status, alert.resolved_at = "resolved", now
            _event(db, alert, "resolved", note="This is no longer the case.", now=now)
            resolved += 1
    db.commit()
    return EvaluateOut(raised=raised, repeated=repeated, resolved=resolved, notified=notified)


# --- reading and dealing with alerts ---------------------------------------------------------------------------


def _names(db: Session, ids: set) -> dict:
    ids = {i for i in ids if i is not None}
    return (
        {i: n for i, n in db.execute(select(User.id, User.full_name).where(User.id.in_(ids)))}
        if ids
        else {}
    )


def _out(a: Alert, names: dict) -> dict:
    return dict(
        id=a.id, rule_code=a.rule_code, category=a.category, kpi_category=a.kpi_category, severity=a.severity,
        title=a.title, body=a.body, link=a.link, status=a.status, occurrences=a.occurrences, first_seen_at=a.first_seen_at,
        last_seen_at=a.last_seen_at, acknowledged_by=names.get(a.acknowledged_by_user_id), acknowledged_at=a.acknowledged_at,
        resolved_at=a.resolved_at,
    )  # fmt: skip


def list_alerts(
    db: Session,
    *,
    status: str | None = None,
    severity: str | None = None,
    category: str | None = None,
    limit: int = 100,
) -> list[AlertOut]:
    query = select(Alert)
    if status:
        query = query.where(Alert.status == status)
    if severity:
        query = query.where(Alert.severity == severity)
    if category:
        query = query.where(Alert.category == category)
    rows = db.scalars(query).all()
    # Open ones first, the most serious first, then the most recent
    rows.sort(
        key=lambda a: (a.status == "resolved", -rules.RANK[a.severity], -a.last_seen_at.timestamp())
    )
    names = _names(db, {a.acknowledged_by_user_id for a in rows})
    return [AlertOut(**_out(a, names)) for a in rows[:limit]]


def counts(db: Session) -> AlertCountsOut:
    by = dict(db.execute(select(Alert.status, func.count()).group_by(Alert.status)).all())
    serious = db.scalar(
        select(func.count())
        .select_from(Alert)
        .where(Alert.status != "resolved", Alert.severity.in_(("high", "critical")))
    )
    return AlertCountsOut(
        open=by.get("open", 0),
        acknowledged=by.get("acknowledged", 0),
        resolved=by.get("resolved", 0),
        high_or_critical_open=serious or 0,
    )


def _get(db: Session, alert_id: uuid.UUID) -> Alert:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise NotFoundError("That alert was not found", code="alert_not_found")
    return alert


def get_alert(db: Session, alert_id: uuid.UUID) -> AlertDetailOut:
    alert = _get(db, alert_id)
    events = db.scalars(
        select(AlertEvent)
        .where(AlertEvent.alert_id == alert.id)
        .order_by(AlertEvent.created_at, AlertEvent.id)
    ).all()
    names = _names(db, {alert.acknowledged_by_user_id, *(e.user_id for e in events)})
    return AlertDetailOut(
        **_out(alert, names),
        events=[
            AlertEventOut(
                kind=e.kind, user=names.get(e.user_id), note=e.note, created_at=e.created_at
            )
            for e in events
        ],
    )


def _require(tenant, alert: Alert) -> None:
    if alert.kpi_category is not None and not tenant.within_remit(KpiCategory(alert.kpi_category)):
        raise PermissionDeniedError("This alert is outside your area.", code="outside_remit")


def acknowledge(
    db: Session, tenant, alert_id: uuid.UUID, note: str | None, meta: RequestMeta
) -> AlertDetailOut:
    alert = _get(db, alert_id)
    _require(tenant, alert)
    if alert.status != "open":
        raise ConflictError(
            "Only an alert that is still open can be acknowledged.", code="not_open"
        )
    now = utcnow()
    alert.status, alert.acknowledged_by_user_id, alert.acknowledged_at = (
        "acknowledged",
        tenant.user.id,
        now,
    )
    _event(db, alert, "acknowledged", tenant.user.id, note, now)
    db.commit()
    return get_alert(db, alert.id)


def resolve(
    db: Session, tenant, alert_id: uuid.UUID, note: str | None, meta: RequestMeta
) -> AlertDetailOut:
    alert = _get(db, alert_id)
    _require(tenant, alert)
    if alert.status == "resolved":
        raise ConflictError("That alert is already resolved.", code="already_resolved")
    now = utcnow()
    alert.status, alert.resolved_at = "resolved", now
    _event(db, alert, "resolved", tenant.user.id, note, now)
    db.commit()
    return get_alert(db, alert.id)
