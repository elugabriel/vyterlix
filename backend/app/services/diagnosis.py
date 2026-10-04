"""Diagnosis: explain one detected change, with the evidence, and keep the explanation.

`diagnose` takes a change the detection engine found, runs the driver analysis on it, and saves:
a headline and summary in plain English, how sure we are (and why), and every statement behind
it as a separate piece of typed evidence (fact, statistical, AI interpretation, or "cannot tell").
It can be run again at any time: the diagnosis and its evidence are replaced, so it always
matches the current figures.

Nothing here asks a language model anything: the evidence is read from the records or worked out
by arithmetic, the confidence comes from written rules (app/diagnostics/evidence.py), and each
diagnosis records which version of the rules made it and the figures it was based on.
"""

import uuid
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.diagnostics import evidence as rules
from app.integrations.base import utcnow
from app.models.diagnostics import DetectionEvent, Diagnosis, DiagnosticEvidence
from app.models.kpi import KpiDefinition
from app.schemas.diagnostics import DiagnosisOut, EvidenceOut
from app.services.detection import ANOMALY, MATERIAL, get_event
from app.services.drivers import explain
from app.services.segments import METRICS


def _load(db: Session, event_id: uuid.UUID) -> tuple[DetectionEvent, KpiDefinition]:
    row = db.execute(
        select(DetectionEvent, KpiDefinition)
        .join(KpiDefinition, KpiDefinition.id == DetectionEvent.kpi_id)
        .where(DetectionEvent.id == event_id)
    ).first()
    if row is None:
        raise NotFoundError("That change was not found", code="detection_not_found")
    return row


def _moved_on_the_month_before(db: Session, event: DetectionEvent) -> bool:
    """Has this figure also moved by more than normal against the month before? The driver
    analysis explains moves against the month before, so an unusual month that did not move that
    much cannot be explained by it. (A change on the month before is such a move itself.)"""
    return (
        db.scalar(
            select(DetectionEvent.id).where(
                DetectionEvent.kpi_id == event.kpi_id,
                DetectionEvent.kind == MATERIAL,
                DetectionEvent.period_start == event.period_start,
            )
        )
        is not None
    )


def diagnose(db: Session, tenant, event_id: uuid.UUID) -> DiagnosisOut:
    event, definition = _load(db, event_id)
    name, unit = definition.name, definition.unit
    month = f"{event.period_start:%B %Y}"
    before = f"{_before(event):%B %Y}"
    value, reference = Decimal(event.value), Decimal(event.reference_value)

    explainable = definition.code in METRICS and _moved_on_the_month_before(db, event)
    drivers = explain(db, definition.code, event.period_start) if explainable else None
    findings = drivers.findings if drivers else []

    items: list[rules.Item] = rules.figure_facts(
        event.kind, name, unit, month, before, value, reference, event.data_quality
    )
    if event.kind == ANOMALY:
        details = event.details or {}
        items.append(
            rules.history_item(
                details.get("spreads_from_usual", "0"),
                str(reference),
                int(details.get("history_months", 0)),
                month,
            )
        )
    expected = (event.details or {}).get("expected_season_change_pct")
    if event.explained_by_season and expected is not None:
        items.append(rules.season_item(expected, month))
    items.extend(rules.finding_item(f) for f in findings)
    no_detail = _no_detail_share(drivers)
    items.extend(
        rules.limits(explainable=explainable, findings=findings, no_detail_share=no_detail)
    )

    sure = rules.confidence(findings, event.data_quality, explainable)
    lead = rules.lead_clause(
        event.kind, name, unit, month, Decimal(event.change), event.change_unit, event.direction
    )
    headline = rules.headline(lead, findings, sure.status)
    summary = rules.summary_of(headline, findings, sure.note)

    now = utcnow()
    diagnosis = db.scalars(select(Diagnosis).where(Diagnosis.event_id == event.id)).first()
    fields = {
        "status": sure.status,
        "headline": headline,
        "summary": summary,
        "confidence": sure.score,
        "confidence_label": sure.label,
        "confidence_note": sure.note,
        "rules_version": rules.RULES_VERSION,
        "basis": {
            "drivers": None if drivers is None else drivers.model_dump(mode="json"),
            "event": {"kind": event.kind, "period": event.period_start.isoformat()},
        },
        "diagnosed_at": now,
    }
    if diagnosis is None:
        diagnosis = Diagnosis(organization_id=tenant.organization_id, event_id=event.id, **fields)
        db.add(diagnosis)
    else:
        for field, new in fields.items():
            setattr(diagnosis, field, new)
        db.execute(
            delete(DiagnosticEvidence).where(DiagnosticEvidence.diagnosis_id == diagnosis.id)
        )
    db.flush()
    for order, item in enumerate(items):
        db.add(
            DiagnosticEvidence(
                organization_id=tenant.organization_id,
                diagnosis_id=diagnosis.id,
                evidence_type=item.evidence_type,
                statement=item.statement,
                data=item.data,
                sort_order=order,
            )
        )
    if event.status == "open":
        event.status = "diagnosed"
    db.commit()
    return read(db, event_id)


def _before(event: DetectionEvent):
    from app.kpi import periods

    return periods.shift(event.period_start, "month", -1)


def _no_detail_share(drivers) -> Decimal | None:
    """What share of the change sits in sales with no product detail, if any does."""
    if drivers is None:
        return None
    for lens in drivers.lenses:
        for effect in lens.effects:
            if effect.kind == "no_product_detail" and effect.share_pct is not None:
                return abs(Decimal(effect.share_pct))
    return None


# --- reading -------------------------------------------------------------------------------


def read(db: Session, event_id: uuid.UUID) -> DiagnosisOut:
    diagnosis = db.scalars(select(Diagnosis).where(Diagnosis.event_id == event_id)).first()
    if diagnosis is None:
        raise NotFoundError("That change has not been explained yet", code="diagnosis_not_found")
    evidence = db.scalars(
        select(DiagnosticEvidence)
        .where(DiagnosticEvidence.diagnosis_id == diagnosis.id)
        .order_by(DiagnosticEvidence.sort_order)
    ).all()
    return DiagnosisOut(
        id=diagnosis.id,
        event=get_event(db, event_id),
        status=diagnosis.status,
        headline=diagnosis.headline,
        summary=diagnosis.summary,
        confidence=diagnosis.confidence,
        confidence_label=diagnosis.confidence_label,
        confidence_note=diagnosis.confidence_note,
        rules_version=diagnosis.rules_version,
        diagnosed_at=diagnosis.diagnosed_at,
        evidence=[
            EvidenceOut(
                id=e.id,
                evidence_type=e.evidence_type,
                statement=e.statement,
                data=e.data,
                sort_order=e.sort_order,
            )
            for e in evidence
        ],
    )
