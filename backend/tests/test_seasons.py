import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.db.tenant import tenant_scope
from app.models.business import BusinessSeason
from app.models.identity import AuditLog, OrganizationUser, Role, User
from app.services.seasons import covers

ORGS = "/api/v1/organizations"
CHRISTMAS = {
    "name": "Christmas rush",
    "start": {"month": 12, "day": 1},
    "end": {"month": 1, "day": 5},
    "expected_change_pct": 40,
}
AUGUST = {
    "name": "August holidays",
    "start": {"month": 8, "day": 1},
    "end": {"month": 8, "day": 31},
    "expected_change_pct": -20,
}


@pytest.fixture
def business(api, db, signup):
    auth = {"owner": signup("owner@acme.co.uk"), "viewer": signup("viewer@acme.co.uk")}
    org_id = api.post(ORGS, json={"name": "Acme"}, headers=auth["owner"]).json()["id"]
    db.add(
        OrganizationUser(
            organization_id=uuid.UUID(org_id),
            user_id=db.scalars(select(User.id).where(User.email == "viewer@acme.co.uk")).one(),
            role_id=db.scalars(select(Role.id).where(Role.code == "viewer")).first(),
        )
    )
    db.flush()
    return org_id, auth


def url(org_id, suffix=""):
    return f"{ORGS}/{org_id}/seasons{suffix}"


def create(api, business, body, who="owner"):
    org_id, auth = business
    return api.post(url(org_id), json=body, headers=auth[who])


def patch(api, business, season_id, body, who="owner"):
    org_id, auth = business
    return api.patch(url(org_id, f"/{season_id}"), json=body, headers=auth[who])


def on(api, business, day):
    org_id, auth = business
    return [
        s["name"]
        for s in api.get(url(org_id, "/on"), params={"date": day}, headers=auth["viewer"]).json()
    ]


def detected_suggestion(db, org_id, name="Summer lull"):
    """What Phase 4+ seasonality detection will create."""
    with tenant_scope(db, uuid.UUID(org_id)):
        s = BusinessSeason(
            name=name,
            start_month=7,
            start_day=15,
            end_month=8,
            end_day=31,
            expected_change_pct=-15,
            source="detected",
            status="suggested",
        )
        db.add(s)
        db.flush()
        return str(s.id)


# --- "does this season include this date?" ------------------------------------------------


def _season(sm, sd, em, ed):
    return BusinessSeason(start_month=sm, start_day=sd, end_month=em, end_day=ed)


@pytest.mark.parametrize(
    ("day", "inside"),
    [
        (date(2026, 12, 1), True),
        (date(2026, 12, 31), True),
        (date(2027, 1, 1), True),
        (date(2027, 1, 5), True),
        (date(2027, 1, 6), False),
        (date(2026, 11, 30), False),
        (date(2026, 7, 1), False),
    ],
)
def test_season_crossing_new_year(day, inside):
    assert covers(_season(12, 1, 1, 5), day) is inside


def test_season_within_one_year():
    august = _season(8, 1, 8, 31)
    assert covers(august, date(2026, 8, 15))
    assert not covers(august, date(2026, 9, 1))


def test_leap_day_falls_inside_a_february_season():
    assert covers(_season(2, 1, 2, 28), date(2028, 2, 29)) is False  # ends on the 28th
    assert covers(_season(2, 14, 3, 1), date(2028, 2, 29)) is True


def test_single_day_season():
    boxing_day = _season(12, 26, 12, 26)
    assert covers(boxing_day, date(2026, 12, 26))
    assert not covers(boxing_day, date(2026, 12, 27))


# --- creating ----------------------------------------------------------------------------


def test_owner_adds_a_season(api, db, business):
    res = create(api, business, CHRISTMAS)
    assert res.status_code == 201
    body = res.json()
    assert body["label"] == "1 Dec – 5 Jan"
    assert body["crosses_new_year"] is True
    assert body["direction"] == "busier"
    assert body["expected_change_pct"] == "40.00"
    assert (body["source"], body["status"]) == ("user", "active")
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "season.created")).one()
    assert str(entry.target_id) == body["id"]


def test_quiet_season_label_and_direction(api, business):
    body = create(api, business, AUGUST).json()
    assert (body["label"], body["crosses_new_year"], body["direction"]) == (
        "1 Aug – 31 Aug",
        False,
        "quieter",
    )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"start": {"month": 2, "day": 30}}, "doesn't exist every year"),
        ({"end": {"month": 2, "day": 29}}, "doesn't exist every year"),
        ({"end": {"month": 13, "day": 1}}, "less than or equal to 12"),
        ({"expected_change_pct": -150}, "greater than or equal to -100"),
        ({"expected_change_pct": "12.345"}, "decimal places"),
        ({"name": "  "}, "at least 1 character"),
        ({"status": "suggested"}, "Extra inputs"),  # only detection can suggest
        ({"source": "detected"}, "Extra inputs"),
    ],
)
def test_invalid_seasons_rejected(api, business, changes, message):
    res = create(api, business, CHRISTMAS | changes)
    assert res.status_code == 422
    assert message in " ".join(str(d["msg"]) for d in res.json()["error"]["details"])


# --- which seasons apply on a date ---------------------------------------------------------


def test_seasons_on_a_date(api, business):
    create(api, business, CHRISTMAS)
    create(api, business, AUGUST)
    create(
        api,
        business,
        {"name": "December", "start": {"month": 12, "day": 1}, "end": {"month": 12, "day": 31}},
    )
    assert on(api, business, "2026-12-20") == ["Christmas rush", "December"]  # overlaps allowed
    assert on(api, business, "2027-01-03") == ["Christmas rush"]
    assert on(api, business, "2026-08-10") == ["August holidays"]
    assert on(api, business, "2026-10-10") == []


def test_suggestions_and_dismissed_seasons_never_apply(api, db, business):
    org_id, _ = business
    detected_suggestion(db, org_id)  # 15 Jul – 31 Aug, still only a suggestion
    august_id = create(api, business, AUGUST).json()["id"]
    patch(api, business, august_id, {"status": "dismissed"})
    assert on(api, business, "2026-08-10") == []


def test_on_defaults_to_today_in_the_uk(api, business):
    org_id, auth = business
    create(
        api,
        business,
        {"name": "All year", "start": {"month": 1, "day": 1}, "end": {"month": 12, "day": 31}},
    )
    res = api.get(url(org_id, "/on"), headers=auth["owner"])
    assert [s["name"] for s in res.json()] == ["All year"]


# --- detected suggestions: confirm or dismiss -------------------------------------------------


def test_suggestions_are_listed_first(api, db, business):
    org_id, auth = business
    create(api, business, CHRISTMAS)
    detected_suggestion(db, org_id)
    listed = api.get(url(org_id), headers=auth["viewer"]).json()
    assert [(s["name"], s["status"]) for s in listed] == [
        ("Summer lull", "suggested"),
        ("Christmas rush", "active"),
    ]


def test_owner_confirms_a_suggestion(api, db, business):
    org_id, _ = business
    suggestion = detected_suggestion(db, org_id)
    res = patch(api, business, suggestion, {"status": "active"})
    assert (res.json()["status"], res.json()["source"]) == ("active", "detected")
    assert on(api, business, "2026-08-01") == ["Summer lull"]
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "season.updated")).one()
    assert entry.details["status"] == {"from": "suggested", "to": "active"}


def test_owner_dismisses_a_suggestion_and_it_is_hidden(api, db, business):
    org_id, auth = business
    suggestion = detected_suggestion(db, org_id)
    patch(api, business, suggestion, {"status": "dismissed"})
    assert api.get(url(org_id), headers=auth["owner"]).json() == []
    dismissed = api.get(url(org_id), params={"status": "dismissed"}, headers=auth["owner"]).json()
    assert [s["name"] for s in dismissed] == ["Summer lull"]


def test_dismissed_season_can_be_restored(api, business):
    season_id = create(api, business, AUGUST).json()["id"]
    patch(api, business, season_id, {"status": "dismissed"})
    assert patch(api, business, season_id, {"status": "active"}).json()["status"] == "active"


# --- editing -----------------------------------------------------------------------------


def test_edit_dates_and_change(api, db, business):
    season_id = create(api, business, CHRISTMAS).json()["id"]
    body = patch(
        api, business, season_id, {"end": {"month": 1, "day": 2}, "expected_change_pct": 55}
    ).json()
    assert (body["label"], body["expected_change_pct"]) == ("1 Dec – 2 Jan", "55.00")
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "season.updated")).one()
    # 5 Jan -> 2 Jan: only the day changed, so only end_day is recorded.
    assert entry.details == {"fields": ["end_day", "expected_change_pct"]}


@pytest.mark.parametrize(
    "body", [{}, {"name": None}, {"start": None}, {"status": "suggested"}, {"status": None}]
)
def test_invalid_edits_rejected(api, business, body):
    season_id = create(api, business, CHRISTMAS).json()["id"]
    assert patch(api, business, season_id, body).status_code == 422


# --- permissions and separation -------------------------------------------------------------


def test_viewers_can_read_but_not_change(api, business):
    season_id = create(api, business, CHRISTMAS).json()["id"]
    assert create(api, business, AUGUST, who="viewer").status_code == 403
    assert patch(api, business, season_id, {"name": "x"}, who="viewer").status_code == 403


def test_another_business_cannot_see_or_change_our_seasons(api, business, signup):
    season_id = create(api, business, CHRISTMAS).json()["id"]
    rival = signup("rival@acme.co.uk")
    rival_org = api.post(ORGS, json={"name": "Rival"}, headers=rival).json()["id"]
    assert api.get(url(rival_org), headers=rival).json() == []
    assert api.get(url(rival_org, "/on"), params={"date": "2026-12-20"}, headers=rival).json() == []
    res = api.patch(url(rival_org, f"/{season_id}"), json={"name": "Stolen"}, headers=rival)
    assert res.json()["error"]["code"] == "season_not_found"
