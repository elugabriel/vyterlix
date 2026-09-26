import uuid

import pytest
from sqlalchemy import select

from app.models.identity import AuditLog, OrganizationUser, Role, User
from app.services.onboarding import SECTION_KEYS

ORGS = "/api/v1/organizations"
PROFILE = {"industry_code": "retail", "financial_year_start": {"month": 4, "day": 1}}


@pytest.fixture
def business(api, db, signup):
    auth = {"owner": signup("owner@acme.co.uk"), "viewer": signup("viewer@acme.co.uk")}
    org_id = api.post(ORGS, json={"name": "Acme"}, headers=auth["owner"]).json()["id"]
    return org_id, auth


def add_viewer(db, org_id):
    db.add(
        OrganizationUser(
            organization_id=uuid.UUID(org_id),
            user_id=db.scalars(select(User.id).where(User.email == "viewer@acme.co.uk")).one(),
            role_id=db.scalars(select(Role.id).where(Role.code == "viewer")).first(),
        )
    )
    db.flush()


def state(api, business, who="owner"):
    org_id, auth = business
    res = api.get(f"{ORGS}/{org_id}/onboarding", headers=auth[who])
    assert res.status_code == 200
    return res.json()


def statuses(api, business):
    return {s["key"]: s["status"] for s in state(api, business)["sections"]}


def post(api, business, path, body=None, who="owner"):
    org_id, auth = business
    return api.post(f"{ORGS}/{org_id}{path}", json=body, headers=auth[who])


def set_profile(api, business, **extra):
    org_id, auth = business
    api.put(f"{ORGS}/{org_id}/profile", json=PROFILE | extra, headers=auth["owner"])


# --- starting point --------------------------------------------------------------------------


def test_new_business_starts_with_everything_to_do(api, business):
    s = state(api, business)
    assert [x["key"] for x in s["sections"]] == list(SECTION_KEYS)
    assert s["ready_for_dashboard"] is False
    assert (s["done"], s["total"]) == (0, 9)
    assert (s["required_done"], s["required_total"]) == (0, 1)
    assert s["next_section"] == "business_details"
    assert s["completed_at"] is None
    details = s["sections"][0]
    assert (details["title"], details["required"]) == ("Business details", True)


# --- each section is done by doing the real thing --------------------------------------------


def test_business_details_unlock_the_dashboard(api, business):
    set_profile(api, business)
    s = state(api, business)
    assert s["ready_for_dashboard"] is True
    assert s["next_section"] == "location"  # first optional section still to do


def test_location_needs_region_and_town_or_postcode(api, business):
    set_profile(api, business, region="london")
    assert statuses(api, business)["location"] == "to_do"
    set_profile(api, business, region="london", postcode="SW1A 1AA")
    assert statuses(api, business)["location"] == "done"


def test_every_optional_section_completes_from_its_own_data(api, db, business):
    org_id, auth = business
    h = auth["owner"]
    set_profile(api, business, region="scotland", town_city="Glasgow")
    api.post(
        f"{ORGS}/{org_id}/goals", json={"title": "Grow", "goal_type": "increase_revenue"}, headers=h
    )
    for kind, name in [
        ("offering", "Whisky"),
        ("sales_channel", "Own website"),
        ("customer_type", "Consumers"),
        ("cost_category", "Stock & materials"),
    ]:
        api.post(f"{ORGS}/{org_id}/lists/{kind}", json={"name": name}, headers=h)
    api.post(
        f"{ORGS}/{org_id}/seasons",
        json={
            "name": "Burns Night",
            "start": {"month": 1, "day": 20},
            "end": {"month": 1, "day": 25},
        },
        headers=h,
    )
    api.post(
        f"{ORGS}/{org_id}/invitations",
        json={"email": "new@acme.co.uk", "role": "viewer"},
        headers=h,
    )

    s = state(api, business)
    assert set(statuses(api, business).values()) == {"done"}
    assert (s["done"], s["optional_done"], s["next_section"]) == (9, 8, None)


def test_archived_or_closed_items_do_not_count(api, business):
    org_id, auth = business
    h = auth["owner"]
    set_profile(api, business)
    item = api.post(f"{ORGS}/{org_id}/lists/offering", json={"name": "Bread"}, headers=h).json()
    goal = api.post(
        f"{ORGS}/{org_id}/goals", json={"title": "Grow", "goal_type": "other"}, headers=h
    ).json()
    api.patch(f"{ORGS}/{org_id}/lists/offering/{item['id']}", json={"is_active": False}, headers=h)
    api.patch(f"{ORGS}/{org_id}/goals/{goal['id']}", json={"status": "abandoned"}, headers=h)
    assert (statuses(api, business)["offerings"], statuses(api, business)["goals"]) == (
        "to_do",
        "to_do",
    )


def test_team_is_done_when_someone_else_has_joined(api, db, business):
    org_id, _ = business
    set_profile(api, business)
    add_viewer(db, org_id)
    assert statuses(api, business)["team"] == "done"


# --- skipping --------------------------------------------------------------------------------


def test_skip_and_unskip_an_optional_section(api, business):
    set_profile(api, business)
    s = post(api, business, "/onboarding/skip", {"section": "seasons"}).json()
    assert {x["key"]: x["status"] for x in s["sections"]}["seasons"] == "skipped"
    assert s["optional_skipped"] == 1
    back = post(api, business, "/onboarding/skip", {"section": "seasons", "skip": False}).json()
    assert {x["key"]: x["status"] for x in back["sections"]}["seasons"] == "to_do"


def test_skipped_sections_are_not_suggested_next(api, business):
    set_profile(api, business)
    post(api, business, "/onboarding/skip", {"section": "location"})
    assert state(api, business)["next_section"] == "goals"


def test_doing_a_skipped_section_later_marks_it_done(api, business):
    org_id, auth = business
    set_profile(api, business)
    post(api, business, "/onboarding/skip", {"section": "goals"})
    api.post(
        f"{ORGS}/{org_id}/goals",
        json={"title": "Grow", "goal_type": "other"},
        headers=auth["owner"],
    )
    assert statuses(api, business)["goals"] == "done"


def test_required_section_cannot_be_skipped(api, business):
    set_profile(api, business)
    assert (
        post(api, business, "/onboarding/skip", {"section": "business_details"}).status_code == 422
    )


def test_skipping_needs_business_details_first(api, business):
    res = post(api, business, "/onboarding/skip", {"section": "seasons"})
    assert res.json()["error"]["code"] == "business_details_required"


def test_unknown_section_rejected(api, business):
    set_profile(api, business)
    assert post(api, business, "/onboarding/skip", {"section": "astrology"}).status_code == 422


# --- finishing ------------------------------------------------------------------------------------


def test_cannot_finish_without_the_required_section(api, business):
    res = post(api, business, "/onboarding/complete")
    assert res.status_code == 409
    err = res.json()["error"]
    assert err["code"] == "onboarding_incomplete"
    assert err["details"] == {"missing": ["Business details"]}


def test_finish_with_optional_sections_left_for_later(api, db, business):
    set_profile(api, business)
    s = post(api, business, "/onboarding/complete").json()
    assert s["completed_at"] is not None
    assert s["next_section"] == "location"  # optional ones still offered afterwards
    entry = db.scalars(
        select(AuditLog).where(AuditLog.action == "business.onboarding_completed")
    ).one()
    assert entry.details == {"optional_done": 0, "optional_total": 8}


def test_finishing_twice_keeps_the_first_time(api, db, business):
    set_profile(api, business)
    first = post(api, business, "/onboarding/complete").json()["completed_at"]
    assert post(api, business, "/onboarding/complete").json()["completed_at"] == first
    assert (
        len(
            db.scalars(
                select(AuditLog).where(AuditLog.action == "business.onboarding_completed")
            ).all()
        )
        == 1
    )


# --- permissions and separation ------------------------------------------------------------------


def test_members_can_see_progress_but_only_owners_change_it(api, db, business):
    org_id, _ = business
    set_profile(api, business)
    add_viewer(db, org_id)
    assert state(api, business, who="viewer")["ready_for_dashboard"] is True
    assert (
        post(api, business, "/onboarding/skip", {"section": "seasons"}, who="viewer").status_code
        == 403
    )
    assert post(api, business, "/onboarding/complete", who="viewer").status_code == 403


def test_progress_is_per_business(api, business, signup):
    set_profile(api, business)
    rival = signup("rival@acme.co.uk")
    rival_org = api.post(ORGS, json={"name": "Rival"}, headers=rival).json()["id"]
    theirs = api.get(f"{ORGS}/{rival_org}/onboarding", headers=rival).json()
    assert theirs["ready_for_dashboard"] is False
    org_id, _ = business
    assert api.get(f"{ORGS}/{org_id}/onboarding", headers=rival).status_code == 404
