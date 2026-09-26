import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.uk import today_uk
from app.models.business import GOAL_TYPES
from app.models.identity import AuditLog, OrganizationUser, Role, User
from app.services.goals import GOAL_CATEGORY

ORGS = "/api/v1/organizations"
NEXT_YEAR = (today_uk() + timedelta(days=365)).isoformat()
REVENUE_GOAL = {
    "title": "Grow revenue to £250,000",
    "goal_type": "increase_revenue",
    "target_value": "250000.00",
    "target_unit": "gbp",
    "target_date": NEXT_YEAR,
}


@pytest.fixture
def team(api, db, signup):
    """Acme with owner@, manager@ (remit: sales only) and viewer@."""
    auth = {n: signup(f"{n}@acme.co.uk") for n in ("owner", "manager", "viewer")}
    org_id = api.post(ORGS, json={"name": "Acme"}, headers=auth["owner"]).json()["id"]
    for name, scope in (("manager", {"kpi_categories": ["sales"]}), ("viewer", None)):
        db.add(
            OrganizationUser(
                organization_id=uuid.UUID(org_id),
                user_id=db.scalars(select(User.id).where(User.email == f"{name}@acme.co.uk")).one(),
                role_id=db.scalars(select(Role.id).where(Role.code == name)).first(),
                scope=scope,
            )
        )
    db.flush()
    return org_id, auth


def goals_url(org_id, goal_id=None):
    return f"{ORGS}/{org_id}/goals" + (f"/{goal_id}" if goal_id else "")


def create(api, team, body=REVENUE_GOAL, who="owner"):
    org_id, auth = team
    return api.post(goals_url(org_id), json=body, headers=auth[who])


def update(api, team, goal_id, body, who="owner"):
    org_id, auth = team
    return api.patch(goals_url(org_id, goal_id), json=body, headers=auth[who])


def test_every_goal_type_has_a_kpi_area():
    assert set(GOAL_CATEGORY) == set(GOAL_TYPES)


# --- creating --------------------------------------------------------------------------


def test_owner_creates_a_goal(api, team):
    res = create(api, team)
    assert res.status_code == 201
    goal = res.json()
    assert goal["target_value"] == "250000.0000"  # exact decimal, sent as a string
    assert goal["target_unit"] == "gbp"
    assert goal["category"] == "sales"
    assert (goal["priority"], goal["status"]) == (3, "active")
    assert goal["created_by"] == "owner"


def test_goal_without_a_numeric_target(api, team):
    res = create(api, team, {"title": "Open a second shop in Leeds", "goal_type": "other"})
    assert res.status_code == 201
    assert res.json()["target_value"] is None


def test_creation_is_audited(api, db, team):
    goal_id = create(api, team).json()["id"]
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "goal.created")).one()
    assert str(entry.target_id) == goal_id
    assert entry.details == {"goal_type": "increase_revenue"}


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"target_unit": None}, "Give both a target value and its unit"),
        ({"target_value": "-5", "target_unit": "gbp"}, "can't be negative"),
        ({"target_value": "10.555", "target_unit": "gbp"}, "at most 2 decimal places"),
        ({"target_value": "12.5", "target_unit": "count"}, "whole number"),
        ({"target_value": "2000", "target_unit": "percent"}, "between -100% and 1000%"),
        ({"target_unit": "usd"}, "gbp"),
        ({"target_date": (today_uk() - timedelta(days=1)).isoformat()}, "can't be in the past"),
        ({"priority": 6}, "less than or equal to 5"),
        ({"goal_type": "world_domination"}, "increase_revenue"),
        ({"title": "   "}, "at least 1 character"),
        ({"kpi_code": "Gross Margin!"}, "pattern"),
        ({"status": "achieved"}, "Extra inputs"),  # new goals always start active
    ],
)
def test_invalid_goals_rejected_with_clear_messages(api, team, changes, message):
    body = {k: v for k, v in (REVENUE_GOAL | changes).items() if v is not None}
    res = create(api, team, body)
    assert res.status_code == 422
    details = " ".join(str(d["msg"]) for d in res.json()["error"]["details"])
    assert message in details


# --- who may manage which goals ---------------------------------------------------------------


def test_viewer_cannot_create_goals(api, team):
    res = create(api, team, who="viewer")
    assert res.status_code == 403
    assert res.json()["error"]["details"] == {"required_permission": "goals.manage"}


def test_manager_can_create_goals_within_remit(api, team):
    assert create(api, team, who="manager").status_code == 201  # revenue = sales


def test_manager_cannot_create_goals_outside_remit(api, team):
    cash = REVENUE_GOAL | {"title": "Improve cash flow", "goal_type": "improve_cash_flow"}
    res = create(api, team, cash, who="manager")
    assert res.status_code == 403
    err = res.json()["error"]
    assert err["code"] == "outside_remit"
    assert err["details"] == {"category": "financial"}


def test_manager_cannot_edit_a_goal_outside_remit(api, team):
    cash_id = create(api, team, REVENUE_GOAL | {"goal_type": "improve_cash_flow"}).json()["id"]
    res = update(api, team, cash_id, {"priority": 1}, who="manager")
    assert res.json()["error"]["code"] == "outside_remit"


def test_manager_cannot_move_a_goal_out_of_remit(api, team):
    goal_id = create(api, team, who="manager").json()["id"]
    res = update(api, team, goal_id, {"goal_type": "reduce_costs"}, who="manager")
    assert res.json()["error"]["code"] == "outside_remit"


def test_everyone_in_the_business_can_read_goals(api, team):
    org_id, auth = team
    goal_id = create(api, team).json()["id"]
    for who in ("owner", "manager", "viewer"):
        assert api.get(goals_url(org_id, goal_id), headers=auth[who]).status_code == 200
        assert len(api.get(goals_url(org_id), headers=auth[who]).json()) == 1


# --- updating and closing -----------------------------------------------------------------


def test_update_only_what_is_sent(api, db, team):
    goal_id = create(api, team).json()["id"]
    res = update(api, team, goal_id, {"priority": 1, "notes": "Board agreed in September"})
    body = res.json()
    assert (body["priority"], body["title"]) == (1, REVENUE_GOAL["title"])
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "goal.updated")).one()
    assert entry.details == {"fields": ["notes", "priority"]}


def test_mark_achieved_is_audited_with_the_status_change(api, db, team):
    goal_id = create(api, team).json()["id"]
    assert update(api, team, goal_id, {"status": "achieved"}).json()["status"] == "achieved"
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "goal.updated")).one()
    assert entry.details["status"] == {"from": "active", "to": "achieved"}


def test_unit_change_is_checked_against_the_existing_target(api, team):
    goal_id = create(api, team).json()["id"]  # 250000.00 gbp
    res = update(api, team, goal_id, {"target_unit": "percent"})  # 250000% isn't sensible
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid_amount"


def test_clearing_the_target_needs_the_unit_cleared_too(api, team):
    goal_id = create(api, team).json()["id"]
    res = update(api, team, goal_id, {"target_value": None})
    assert res.json()["error"]["code"] == "target_needs_unit"
    ok = update(api, team, goal_id, {"target_value": None, "target_unit": None})
    assert ok.json()["target_value"] is None


@pytest.mark.parametrize("body", [{}, {"title": None}, {"status": None}, {"status": "paused"}])
def test_invalid_updates_rejected(api, team, body):
    goal_id = create(api, team).json()["id"]
    assert update(api, team, goal_id, body).status_code == 422


def test_no_change_is_not_audited(api, db, team):
    goal_id = create(api, team).json()["id"]
    update(api, team, goal_id, {"priority": 3})
    assert db.scalars(select(AuditLog).where(AuditLog.action == "goal.updated")).all() == []


# --- listing ---------------------------------------------------------------------------


def test_list_orders_active_first_then_priority_then_date(api, team):
    soon = (today_uk() + timedelta(days=30)).isoformat()
    later = (today_uk() + timedelta(days=300)).isoformat()
    ids = {}
    for (
        title,
        priority,
        date,
    ) in [("B", 2, later), ("A", 2, soon), ("C", 1, later), ("D", 1, None)]:
        body = {"title": title, "goal_type": "other", "priority": priority}
        if date:
            body["target_date"] = date
        ids[title] = create(api, team, body).json()["id"]
    update(api, team, ids["C"], {"status": "achieved"})

    org_id, auth = team
    titles = [g["title"] for g in api.get(goals_url(org_id), headers=auth["owner"]).json()]
    assert titles == ["D", "A", "B", "C"]
    only_done = api.get(goals_url(org_id), params={"status": "achieved"}, headers=auth["owner"])
    assert [g["title"] for g in only_done.json()] == ["C"]


# --- separation between businesses -----------------------------------------------------------


def test_another_business_cannot_see_or_edit_our_goals(api, team, signup):
    goal_id = create(api, team).json()["id"]
    rival = signup("rival@acme.co.uk")
    rival_org = api.post(ORGS, json={"name": "Rival"}, headers=rival).json()["id"]

    assert api.get(goals_url(rival_org), headers=rival).json() == []
    stolen = api.get(goals_url(rival_org, goal_id), headers=rival)
    assert stolen.status_code == 404
    assert stolen.json()["error"]["code"] == "goal_not_found"
    edit = api.patch(goals_url(rival_org, goal_id), json={"priority": 1}, headers=rival)
    assert edit.status_code == 404
