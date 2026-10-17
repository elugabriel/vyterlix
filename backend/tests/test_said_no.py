# ruff: noqa: E501, F811
"""An action the owner said no to is not put in front of them again soon (client feedback §3:
"dismiss with a reason, remembered for the future")."""

from datetime import timedelta

from sqlalchemy import select

from app.core.uk import today_uk
from app.models.recommendations import Recommendation
from app.services import recommendations as service
from tests.test_actions import ORGS
from tests.test_health import scoped
from tests.test_outcomes import suggested  # noqa: F401
from tests.test_recommendations import recommend


def dismiss(api, march, event, reason="We tried that last year"):
    url = f"{ORGS}/{march[0]}/changes/{event['id']}/recommendation/dismiss"
    assert api.post(url, json={"reason": reason}, headers=march[2]["owner"]).status_code == 204


def test_dismissing_keeps_what_was_said_no_to(api, db, march, suggested):
    event, rec = suggested
    top = next(o for o in rec["options"] if o["is_recommended"])
    dismiss(api, march, event)
    with scoped(db, march):
        basis = db.scalars(select(Recommendation)).one().basis
    assert basis["dismissed_option"]["title"] == top["title"]
    assert basis["dismissed_option"]["code"] == top["intervention"]["code"]
    assert basis["dismissed_on"] == today_uk().isoformat()


def test_it_is_not_suggested_again_and_the_owner_is_told_why(api, march, suggested):
    event, rec = suggested
    top = next(o for o in rec["options"] if o["is_recommended"])
    dismiss(api, march, event)
    again = recommend(api, march, event).json()
    assert again["status"] == "open"  # there are other ways to help
    assert top["title"] not in [o["title"] for o in again["options"]]
    said = [e["statement"] for e in again["evidence"] if "You said no" in e["statement"]]
    assert said and top["title"] in said[0] and "We tried that last year" in said[0]


def test_it_is_remembered_for_a_while_and_then_forgotten(db, march, suggested, api):
    event, rec = suggested
    dismiss(api, march, event)
    with scoped(db, march):
        from app.models.diagnostics import DetectionEvent

        kpi_id = db.get(DetectionEvent, event["id"]).kpi_id
        soon = today_uk() + timedelta(days=service.DECLINED_FOR_DAYS)
        later = soon + timedelta(days=1)
        assert len(service._declined(db, kpi_id, soon)) == 1
        assert service._declined(db, kpi_id, later) == {}


def test_a_recommendation_nobody_turned_down_is_unchanged(api, march, suggested):
    event, rec = suggested
    again = recommend(api, march, event).json()
    assert [o["title"] for o in again["options"]] == [o["title"] for o in rec["options"]]
    assert not any("You said no" in e["statement"] for e in again["evidence"])


def test_the_recommendation_carries_a_priority_with_its_reason(api, march, suggested):
    event, rec = suggested
    assert rec["priority"]["label"] in ("High", "Medium", "Low") and rec["priority"]["reason"]
    assert rec["priority"]["level"] == rec["priority"]["label"].lower()
