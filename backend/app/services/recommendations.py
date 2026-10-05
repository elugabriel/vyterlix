"""Recommendations: what to do about a change that has been explained.

`generate` takes a detected change and its diagnosis (making the diagnosis first if there is not
one), turns the causes found into candidate actions from the intervention library, scores each on
the same seven things, ranks them, and keeps the options with the best one marked as recommended
and a plain-English reason for it. Safe to run again: the recommendation is replaced.

Ranking is rules and arithmetic (app/recommend/rules.py), never a language model.
"""

import uuid
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.diagnostics import evidence as diagnosis_evidence
from app.integrations.base import utcnow
from app.memory import rules as memory_rules
from app.models.actions import BusinessIntervention
from app.models.diagnostics import DetectionEvent, Diagnosis
from app.models.kpi import KpiDefinition
from app.models.outcomes import InterventionOutcome
from app.models.recommendations import (
    Intervention,
    Recommendation,
    RecommendationEvidence,
    RecommendationOption,
)
from app.recommend import rules
from app.schemas.diagnostics import EvidenceOut
from app.schemas.recommendations import (
    InterventionOut,
    OptionOut,
    RecommendationOut,
    RecommendationSummaryOut,
    ScoreLineOut,
)
from app.services import diagnosis as diagnosis_service
from app.services import memory, track_record
from app.services.detection import get_event
from app.services.goals import list_goals


def _load(db: Session, event_id: uuid.UUID) -> tuple[DetectionEvent, KpiDefinition]:
    row = db.execute(
        select(DetectionEvent, KpiDefinition)
        .join(KpiDefinition, KpiDefinition.id == DetectionEvent.kpi_id)
        .where(DetectionEvent.id == event_id)
    ).first()
    if row is None:
        raise NotFoundError("That change was not found", code="detection_not_found")
    return row


def _library(db: Session) -> list[rules.Action]:
    return [
        rules.Action(
            code=i.code,
            name=i.name,
            summary=i.summary,
            steps=list(i.steps),
            addresses=list(i.addresses),
            kpis=list(i.kpis),
            goal_types=list(i.goal_types),
            effort=i.effort,
            cost_level=i.cost_level,
            days=i.typical_days_to_effect,
            impact_share=Decimal(i.impact_share),
        )
        for i in db.scalars(
            select(Intervention).where(Intervention.is_active.is_(True)).order_by(Intervention.code)
        )
    ]


def _tried(db: Session, event_id: uuid.UUID) -> set[tuple[str | None, str | None]]:
    """Actions already tried for this change that did not fully work (code, what it was aimed at):
    a different one is suggested next time."""
    rows = db.execute(
        select(BusinessIntervention.library_code, BusinessIntervention.target_label)
        .join(InterventionOutcome, InterventionOutcome.intervention_id == BusinessIntervention.id)
        .where(
            BusinessIntervention.event_id == event_id,
            InterventionOutcome.outcome.in_(("partially_successful", "unsuccessful")),
        )
    ).all()
    return {(code, target) for code, target in rows}


def _history(recalled, record: dict, code: str) -> int:
    """An action's track record score: its record on this figure, else in the business."""
    score = recalled.history(code, record)
    return rules.NEUTRAL_HISTORY if score is None else score


def _remembered(recalled, record, candidates, left_out, kpi_name):
    """What memory added to this recommendation: as evidence lines, and as the record of use."""
    items: list[diagnosis_evidence.Item] = []
    used: list[dict] = []
    names = {c.action.code: c.action.name for c, _ in left_out} | {
        c.action.code: c.action.name for c in candidates
    }
    seen = set()
    for c, why in left_out:
        if c.action.code in seen:
            continue
        seen.add(c.action.code)
        text = f'"{c.action.name}" was left out because {why}.'
        items.append(
            diagnosis_evidence.Item("fact", text, {"reason": "your_limits", "code": c.action.code})
        )
        used.append({"kind": "limit", "statement": text})
    for code, here in sorted(recalled.records.items()):
        if here.decided and code in names:
            text = (
                f'On {kpi_name}, "{names[code]}" has been tried {here.decided} '
                f"time{'s' if here.decided != 1 else ''} in your business: "
                f"{here.successful} worked, {here.partially_successful} partly worked and "
                f"{here.unsuccessful} did not."
            )
            items.append(diagnosis_evidence.Item("fact", text, {"reason": "pattern", "code": code}))
            used.append({"kind": "pattern", "statement": text})
    for case in recalled.cases:
        text = f"Last time: {case.lesson}"
        items.append(
            diagnosis_evidence.Item(
                "fact", text, {"reason": "similar_case", "outcome": case.outcome}
            )
        )
        used.append({"kind": "case", "statement": text})
    if recalled.constraints.any:
        used.append(
            {"kind": "limits", "statement": "Your limits on cost, effort and speed were applied."}
        )
    return items, used


def _findings(drivers: dict | None) -> list[rules.Finding]:
    if not drivers:
        return []
    return [
        rules.Finding(
            kind=f["kind"],
            lens=f["lens"],
            label=f["label"],
            amount=Decimal(f["amount"]),
            share_pct=None if f["share_pct"] is None else Decimal(f["share_pct"]),
            text=f["text"],
        )
        for f in drivers.get("findings", [])
    ]


def _goals(db: Session) -> list[rules.Goal]:
    return [
        rules.Goal(g.kpi_code, g.goal_type, g.title, g.priority) for g in list_goals(db, "active")
    ]


def _ensure_diagnosis(db: Session, tenant, event_id: uuid.UUID) -> Diagnosis:
    found = db.scalars(select(Diagnosis).where(Diagnosis.event_id == event_id)).first()
    if found is None:
        diagnosis_service.diagnose(db, tenant, event_id)
        found = db.scalars(select(Diagnosis).where(Diagnosis.event_id == event_id)).one()
    return found


# --- making one ---------------------------------------------------------------------------------


def generate(db: Session, tenant, event_id: uuid.UUID) -> RecommendationOut:
    event, kpi = _load(db, event_id)
    diagnosis = _ensure_diagnosis(db, tenant, event_id)
    month = f"{event.period_start:%B %Y}"
    drivers = (diagnosis.basis or {}).get("drivers")
    findings = _findings(drivers)
    unit = kpi.unit if not drivers else drivers["unit"]
    scored: list[rules.Scored] = []
    record: dict[str, track_record.Record] = {}
    remembered: list[diagnosis_evidence.Item] = []  # what memory added to this recommendation
    used: list[dict] = []

    if event.effect != "bad":
        status = "no_action_needed"
        headline = f"{kpi.name} in {month} is good news: nothing needs fixing."
        rationale = (
            "This change is in your favour, so there is nothing to put right. Keep doing what you "
            "have been doing, and read the explanation to see what is helping."
        )
    else:
        goals = _goals(db)
        tried = _tried(db, event.id)
        recalled = memory.recall(db, kpi.code)
        candidates, left_out = [], []
        for c in rules.generate(_library(db), findings, kpi.code):
            if (c.action.code, c.target) in tried:
                continue
            why = memory_rules.broken_limit(
                recalled.constraints, code=c.action.code, effort=c.action.effort,
                cost_level=c.action.cost_level, days=c.action.days,
            )  # fmt: skip
            if why:
                left_out.append((c, why))
            else:
                candidates.append(c)
        record = track_record.load(db)
        remembered, used = _remembered(recalled, record, candidates, left_out, kpi.name)
        change = Decimal(drivers["total_change"]) if drivers else Decimal(0)
        scored = rules.rank(
            [
                rules.evaluate(
                    c,
                    kpi_code=kpi.code,
                    event_change=change,
                    severity=event.severity,
                    diagnosis_confidence=diagnosis.confidence,
                    goals=goals,
                    history=_history(recalled, record, c.action.code),
                )
                for c in candidates
            ]
        )[: rules.MAX_OPTIONS]
        if not scored:
            status = "insufficient_evidence"
            headline = f"We can't recommend an action for {kpi.name} in {month} yet."
            rationale = (
                "We could not say why this happened, so we cannot say what to do about it."
                if diagnosis.status == "insufficient_evidence" or not findings
                else "No action in our library answers the causes we found yet."
            )
            if left_out and findings:
                rationale = (
                    "Every action that answers the causes we found breaks a limit you set "
                    f"({left_out[0][1]}). You can change your limits on the What we know page."
                )
        else:
            status = "open"
            headline = (
                f"Best next step for {kpi.name} in {month}: {rules.title_for(scored[0].candidate)}."
            )
            rationale = rules.rationale(scored, unit)

    now = utcnow()
    db.execute(delete(Recommendation).where(Recommendation.event_id == event.id))
    db.flush()
    recommendation = Recommendation(
        organization_id=tenant.organization_id,
        event_id=event.id,
        diagnosis_id=diagnosis.id,
        status=status,
        headline=headline,
        rationale=rationale,
        rules_version=rules.RULES_VERSION,
        basis={
            "weights": rules.WEIGHTS,
            "findings": [
                {
                    "label": f.label,
                    "kind": f.kind,
                    "amount": str(f.amount),
                    "share_pct": None if f.share_pct is None else str(f.share_pct),
                }
                for f in findings
            ],
            "diagnosis_confidence": diagnosis.confidence,
        },
        generated_at=now,
    )
    db.add(recommendation)
    db.flush()
    library = {i.code: i for i in db.scalars(select(Intervention))}
    for item in scored:
        candidate = item.candidate
        db.add(
            RecommendationOption(
                organization_id=tenant.organization_id,
                recommendation_id=recommendation.id,
                intervention_id=library[candidate.action.code].id,
                rank=item.rank,
                is_recommended=item.rank == 1,
                title=rules.title_for(candidate),
                description=rules.description_for(candidate, item.impact_value, unit),
                target_label=candidate.target,
                impact_value=item.impact_value.quantize(Decimal("0.01")),
                impact_unit=unit,
                impact_score=item.scores["impact"],
                confidence=item.scores["confidence"],
                goal_fit=item.scores["goal_fit"],
                urgency=item.scores["urgency"],
                ease=item.scores["ease"],
                history_score=item.scores["history"],
                total_score=item.total,
                effort=candidate.action.effort,
                cost_level=candidate.action.cost_level,
                days_to_effect=candidate.action.days,
                breakdown={"lines": item.breakdown, "goal": item.goal_match},
            )
        )
    memory.log_use(db, tenant, event.id, used)
    for order, piece in enumerate([*_evidence(event, diagnosis, scored, record), *remembered]):
        db.add(
            RecommendationEvidence(
                organization_id=tenant.organization_id,
                recommendation_id=recommendation.id,
                evidence_type=piece.evidence_type,
                statement=piece.statement,
                data=piece.data,
                sort_order=order,
            )
        )
    db.commit()
    return read(db, event_id)


def _evidence(
    event, diagnosis, scored: list[rules.Scored], record: dict[str, track_record.Record]
) -> list[diagnosis_evidence.Item]:
    items = [
        diagnosis_evidence.Item("fact", event.summary, {"event_id": str(event.id)}),
        diagnosis_evidence.Item(
            "statistical",
            f"Why it happened: {diagnosis.headline}",
            {"confidence": diagnosis.confidence, "label": diagnosis.confidence_label},
        ),
    ]
    if scored:
        best = scored[0]
        cause = best.candidate.finding
        items.append(
            diagnosis_evidence.finding_item(
                SimpleNamespace(
                    kind=cause.kind,
                    lens=cause.lens,
                    label=cause.label,
                    amount=str(cause.amount),
                    share_pct=None if cause.share_pct is None else str(cause.share_pct),
                    text=cause.text,
                )
            )
        )
        share = (best.candidate.action.impact_share * 100).quantize(Decimal("1"))
        items.append(
            diagnosis_evidence.Item(
                "statistical",
                f"The estimate assumes this kind of action wins back about {share}% of a "
                f"{best.candidate.gap:.2f} gap. That {share}% is Vyterlix's own starting estimate "
                "and has not yet been measured on your business.",
                {
                    "impact_share": str(best.candidate.action.impact_share),
                    "gap": str(best.candidate.gap),
                },
            )
        )
        seen = record.get(best.candidate.action.code)
        if seen is None or not seen.decided:
            items.append(
                diagnosis_evidence.Item(
                    "insufficient",
                    "We have no record yet of how this kind of action worked for your business, so "
                    "its track record counts as neutral.",
                    {"reason": "no_history"},
                )
            )
        else:
            items.append(
                diagnosis_evidence.Item(
                    "fact",
                    f"This kind of action has been tried {seen.decided} "
                    f"time{'s' if seen.decided != 1 else ''} in your business: {seen.successful} "
                    f"worked, {seen.partially_successful} partly worked and {seen.unsuccessful} "
                    "did not. That moves its score up or down a little.",
                    {
                        "successful": seen.successful,
                        "partial": seen.partially_successful,
                        "unsuccessful": seen.unsuccessful,
                    },
                )
            )
        items.append(
            diagnosis_evidence.Item(
                "insufficient",
                "Vyterlix does not yet know your budget, staffing or other limits, so check the "
                "effort and cost suit you before acting.",
                {"reason": "no_constraints"},
            )
        )
    return items


# --- reading ------------------------------------------------------------------------------------


def _intervention_out(i: Intervention) -> InterventionOut:
    return InterventionOut(
        code=i.code,
        name=i.name,
        category=i.category,
        summary=i.summary,
        steps=list(i.steps),
        effort=i.effort,
        cost_level=i.cost_level,
        typical_days_to_effect=i.typical_days_to_effect,
        impact_share=str(Decimal(i.impact_share).normalize()),
        impact_basis=i.impact_basis,
    )


def interventions(db: Session) -> list[InterventionOut]:
    """The actions Vyterlix knows how to suggest."""
    return [
        _intervention_out(i)
        for i in db.scalars(
            select(Intervention).where(Intervention.is_active.is_(True)).order_by(Intervention.name)
        )
    ]


def read(db: Session, event_id: uuid.UUID) -> RecommendationOut:
    recommendation = db.scalars(
        select(Recommendation).where(Recommendation.event_id == event_id)
    ).first()
    if recommendation is None:
        raise NotFoundError(
            "Nothing has been recommended for that change yet", code="recommendation_not_found"
        )
    rows = db.execute(
        select(RecommendationOption, Intervention)
        .join(Intervention, Intervention.id == RecommendationOption.intervention_id)
        .where(RecommendationOption.recommendation_id == recommendation.id)
        .order_by(RecommendationOption.rank)
    ).all()
    evidence = db.scalars(
        select(RecommendationEvidence)
        .where(RecommendationEvidence.recommendation_id == recommendation.id)
        .order_by(RecommendationEvidence.sort_order)
    ).all()
    return RecommendationOut(
        id=recommendation.id,
        event=get_event(db, event_id),
        status=recommendation.status,
        headline=recommendation.headline,
        rationale=recommendation.rationale,
        rules_version=recommendation.rules_version,
        generated_at=recommendation.generated_at,
        options=[
            OptionOut(
                id=o.id,
                rank=o.rank,
                is_recommended=o.is_recommended,
                title=o.title,
                description=o.description,
                target=o.target_label,
                intervention=_intervention_out(i),
                impact_value=str(Decimal(o.impact_value).quantize(Decimal("0.01"))),
                impact_unit=o.impact_unit,
                total_score=o.total_score,
                scores=[ScoreLineOut(**line) for line in o.breakdown["lines"]],
                effort=o.effort,
                cost_level=o.cost_level,
                days_to_effect=o.days_to_effect,
            )
            for o, i in rows
        ],
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


def list_recommendations(db: Session, status: str | None = None) -> list[RecommendationSummaryOut]:
    """Every recommendation made, newest first."""
    query = (
        select(Recommendation, DetectionEvent, KpiDefinition)
        .join(
            DetectionEvent,
            (DetectionEvent.organization_id == Recommendation.organization_id)
            & (DetectionEvent.id == Recommendation.event_id),
        )
        .join(KpiDefinition, KpiDefinition.id == DetectionEvent.kpi_id)
        .order_by(Recommendation.generated_at.desc(), DetectionEvent.period_start.desc())
    )
    if status is not None:
        query = query.where(Recommendation.status == status)
    out = []
    for recommendation, event, kpi in db.execute(query).all():
        top = db.scalars(
            select(RecommendationOption).where(
                RecommendationOption.recommendation_id == recommendation.id,
                RecommendationOption.is_recommended.is_(True),
            )
        ).first()
        out.append(
            RecommendationSummaryOut(
                id=recommendation.id,
                event_id=event.id,
                kpi_name=kpi.name,
                period_start=event.period_start,
                status=recommendation.status,
                headline=recommendation.headline,
                recommended=None if top is None else top.title,
                score=None if top is None else top.total_score,
                generated_at=recommendation.generated_at,
            )
        )
    return out
