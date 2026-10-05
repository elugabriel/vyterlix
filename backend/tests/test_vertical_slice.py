# ruff: noqa: E501
"""The vertical slice: one fake UK shop goes round the whole loop, through the real API.

Upload data -> work out the KPIs -> see a fall in sales -> explain it -> forecast sales -> suggest
actions -> pick one and say why -> accept it -> do it -> follow up -> measure the outcome -> keep what
was learned. Every step is checked against figures worked out by hand from the generated files.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal as D

from sqlalchemy import select

from app.db.tenant import tenant_scope
from app.demo import milestone
from app.models.actions import BusinessAction
from app.models.identity import User
from app.models.outcomes import FollowUpSchedule
from app.services import jobs, outcomes, track_record
from app.services.jobs import JobTenant
from tests.test_milestone_dataset import ORGS, bring_in, business  # noqa: F401


def drain(db, storage):
    """Run everything waiting in the background queue (the KPI calculation after the imports...)."""
    ran = 0
    while jobs.work_once(db, "slice-worker", storage=storage) is not None:
        ran += 1
    return ran


def test_one_shop_goes_round_the_whole_loop(api, db, storage, business):  # noqa: F811
    org_id, auth = business
    base = f"{ORGS}/{org_id}"
    dataset = milestone.build()
    months = dataset.expected["sales"]["months"]

    # 1. Upload data: five files, every row accepted, nothing left over
    bring_in(api, db, storage, org_id, auth, dataset)
    assert drain(db, storage) >= 1

    # 2. Calculate KPIs: sales per month are exactly what the generator worked out
    history = api.get(f"{base}/kpis/revenue?granularity=month", headers=auth).json()
    by_month = {p["period_start"][:7]: p["value"] for p in history["values"]}
    for month in ("2025-12", "2026-01", "2026-03"):
        assert D(by_month[month]) == D(months[month]["net"])

    # 3. Detect a revenue decline: January fell by a third on the Christmas month
    changes = api.get(
        f"{base}/changes?kind=material_change&effect=bad&limit=200", headers=auth
    ).json()
    fall = next(
        e for e in changes if e["kpi_code"] == "revenue" and e["period_start"] == "2026-01-01"
    )
    assert fall["direction"] == "down" and D(fall["value"]) == D(months["2026-01"]["net"])
    assert D(fall["reference_value"]) == D(months["2025-12"]["net"])
    drop = (D(months["2026-01"]["net"]) / D(months["2025-12"]["net"]) - 1) * 100
    assert D(fall["change"]) == drop.quantize(D("0.01")) or abs(D(fall["change"]) - drop) < D("0.1")

    # 4. Explain the cause: a diagnosis with evidence, every line saying what kind of statement it is
    diagnosis = api.post(f"{base}/changes/{fall['id']}/diagnosis", headers=auth)
    assert diagnosis.status_code == 200, diagnosis.text
    body = diagnosis.json()
    assert body["status"] != "insufficient_evidence" and body["evidence"]
    assert {e["evidence_type"] for e in body["evidence"]} <= {
        "fact", "statistical", "ai_interpretation", "insufficient"
    }  # fmt: skip

    # 5. Forecast revenue: three months ahead, each with a range around it
    forecast = api.post(f"{base}/forecasts/revenue?horizon=3", headers=auth)
    assert forecast.status_code == 200, forecast.text
    predicted = forecast.json()
    assert predicted["status"] == "ok" and len(predicted["predictions"]) == 3
    assert predicted["as_of"] == "2026-09-01"
    for step in predicted["predictions"]:
        assert D(step["lower"]) <= D(step["value"]) <= D(step["upper"])

    # 6. Generate candidate actions: ranked, each scored on six things
    made = api.post(f"{base}/changes/{fall['id']}/recommendation", headers=auth)
    assert made.status_code == 200, made.text
    recommendation = made.json()
    options = recommendation["options"]
    assert recommendation["status"] == "open" and len(options) >= 3
    assert [o["rank"] for o in options] == list(range(1, len(options) + 1))
    assert all(len(o["scores"]) == 6 for o in options)
    assert [o["total_score"] for o in options] == sorted(
        (o["total_score"] for o in options), reverse=True
    )

    # 7. Select and explain a recommendation: the best one, and why it came first
    best = options[0]
    assert best["is_recommended"] and not any(o["is_recommended"] for o in options[1:])
    assert (
        "Next best is" in recommendation["rationale"]
        and best["title"] in recommendation["rationale"]
    )

    # 8. The owner accepts it
    accepted = api.post(f"{base}/changes/{fall['id']}/recommendation/accept", json={}, headers=auth)
    assert accepted.status_code == 200, accepted.text
    action = accepted.json()
    assert action["status"] == "accepted" and action["title"] == best["title"]
    assert action["decision"]["baseline_value"] == str(
        D(months["2026-01"]["net"]).quantize(D("0.01"))
    )
    assert action["decision"]["expected_impact_value"] == best["impact_value"]

    # 9. Track the action: start it, tick the steps off, note it, finish it
    path = f"{base}/actions/{action['id']}"
    assert (
        api.post(f"{path}/status", json={"status": "in_progress"}, headers=auth).status_code == 200
    )
    steps = [{"text": s["text"], "done": True} for s in action["steps"]]
    assert api.patch(path, json={"steps": steps}, headers=auth).json()["progress"]["percent"] == 100
    assert (
        api.post(f"{path}/notes", json={"note": "Ran it for four weeks"}, headers=auth).status_code
        == 200
    )
    finished = api.post(f"{path}/status", json={"status": "completed"}, headers=auth).json()
    assert finished["status"] == "completed" and finished["follow_up"]["status"] == "scheduled"

    # 10. Run the follow-up: pretend the work was finished in mid-February, so its follow-up falls
    # in April and the month it measures is March, which the data covers.
    with tenant_scope(db, uuid.UUID(org_id)):
        row = db.get(BusinessAction, uuid.UUID(action["id"]))
        row.completed_at = datetime(2026, 2, 16, 12, tzinfo=UTC)
        db.query(FollowUpSchedule).delete()
        owner = db.scalars(select(User).where(User.email == "owner@fakeham.co.uk")).one()
        tenant = JobTenant(organization_id=uuid.UUID(org_id), user=owner)
        plan = outcomes.schedule(db, tenant, row)
        assert plan.measure_month == date(2026, 3, 1)
        swept = outcomes.sweep(db, tenant, today=plan.due_date)
    assert swept["due"] == 1 and swept["told"] == 1 and swept["measured"] == 1

    # 11. Measure the outcome: March against January, counted in pounds, with the arithmetic shown
    result = api.get(path, headers=auth).json()
    outcome = result["outcome"]
    assert outcome is not None and result["follow_up"]["status"] == "done"
    gained = D(months["2026-03"]["net"]) - D(months["2026-01"]["net"])
    assert D(outcome["actual_change"]) == gained.quantize(D("0.01"))
    assert D(outcome["expected_change"]) == D(best["impact_value"])
    assert outcome["seasonal_change"] is None  # no figures from the year before to take out
    assert outcome["outcome"] == "successful" and outcome["achieved_pct"] == 101  # 877.10 of 868.48
    assert outcome["reason"] and outcome["achieved_pct"] is not None
    report = api.get(f"{path}/report", headers=auth).json()
    assert (
        report["outcome"]["outcome"] == outcome["outcome"]
        and "Ran it for four weeks" in report["notes"][0]
    )

    # 12. Store the learning: the result is in the track record, and the next recommendation uses it
    with tenant_scope(db, uuid.UUID(org_id)):
        record = track_record.load(db)[action["decision"]["library_code"]]
    assert record.decided == 1
    summary = api.get(f"{base}/actions/outcomes", headers=auth).json()
    assert (
        summary[outcome["outcome"]] == 1
        and summary["track_record"][0]["code"] == action["decision"]["library_code"]
    )
    again = api.post(f"{base}/changes/{fall['id']}/recommendation", headers=auth).json()
    tried = next(
        o
        for o in again["options"]
        if o["intervention"]["code"] == action["decision"]["library_code"]
    )
    track = next(line["score"] for line in tried["scores"] if line["key"] == "history")
    assert track == 75  # one success, so it counts for more than the neutral 50
    assert any(
        "tried 1 time in your business: 1 worked" in e["statement"] for e in again["evidence"]
    )
