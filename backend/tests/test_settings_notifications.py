import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.tenant import tenant_scope
from app.models.business import NOTIFICATION_CATEGORIES, NotificationPreference
from app.models.identity import AuditLog, Organization, OrganizationUser, Role, User

ORGS = "/api/v1/organizations"


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


def settings(api, business, who="owner"):
    org_id, auth = business
    return api.get(f"{ORGS}/{org_id}/settings", headers=auth[who])


def change_settings(api, business, body, who="owner"):
    org_id, auth = business
    return api.patch(f"{ORGS}/{org_id}/settings", json=body, headers=auth[who])


def prefs(api, business, who="owner"):
    org_id, auth = business
    res = api.get(f"{ORGS}/{org_id}/notification-preferences", headers=auth[who])
    return {p["category"]: p for p in res.json()}


def change_prefs(api, business, body, who="owner"):
    org_id, auth = business
    return api.patch(
        f"{ORGS}/{org_id}/notification-preferences", json={"preferences": body}, headers=auth[who]
    )


# --- business settings -------------------------------------------------------------------


def test_settings_default_to_the_uk(api, business):
    res = settings(api, business, who="viewer")  # any member can read
    assert res.status_code == 200
    assert res.json() == {
        "timezone": "Europe/London",
        "locale": "en-GB",
        "currency": "GBP",
        "date_format": "dd/mm/yyyy",
        "week_start_day": 1,
        "week_start_name": "Monday",
        "quiet_hours": None,
    }


def test_owner_sets_quiet_hours_across_midnight(api, db, business):
    res = change_settings(api, business, {"quiet_hours": {"start": "22:00", "end": "07:00"}})
    assert res.status_code == 200
    assert res.json()["quiet_hours"] == {"start": "22:00:00", "end": "07:00:00"}
    assert settings(api, business).json()["quiet_hours"]["end"] == "07:00:00"
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "business.settings_updated")).one()
    assert entry.details == {"fields": ["quiet_hours_end", "quiet_hours_start"]}


def test_quiet_hours_can_be_switched_off(api, business):
    change_settings(api, business, {"quiet_hours": {"start": "21:30", "end": "08:00"}})
    assert change_settings(api, business, {"quiet_hours": None}).json()["quiet_hours"] is None


def test_week_can_start_on_sunday(api, business):
    body = change_settings(api, business, {"week_start_day": 7}).json()
    assert (body["week_start_day"], body["week_start_name"]) == (7, "Sunday")


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"week_start_day": 0},
        {"week_start_day": 8},
        {"week_start_day": None},
        {"quiet_hours": {"start": "22:00", "end": "22:00"}},
        {"quiet_hours": {"start": "22:00"}},
        {"quiet_hours": {"start": "22:00:30", "end": "07:00"}},
        {"timezone": "America/New_York"},  # fixed to the UK, not editable
        {"locale": "en-US"},
        {"currency": "USD"},
    ],
)
def test_invalid_or_non_uk_settings_rejected(api, business, body):
    assert change_settings(api, business, body).status_code == 422


def test_viewers_cannot_change_settings(api, business):
    res = change_settings(api, business, {"week_start_day": 7}, who="viewer")
    assert res.status_code == 403


# --- notification preferences -------------------------------------------------------------


def test_everyone_starts_with_every_alert_on(api, business):
    mine = prefs(api, business)
    assert set(mine) == set(NOTIFICATION_CATEGORIES)
    assert all(p["email"] and p["in_app"] and p["push"] for p in mine.values())
    assert mine["security"]["locked"] is True
    assert mine["sales"]["locked"] is False


def test_turn_off_some_channels(api, business):
    res = change_prefs(
        api, business, {"marketing": {"email": False}, "forecast": {"push": False, "in_app": False}}
    )
    assert res.status_code == 200
    mine = prefs(api, business)
    assert (mine["marketing"]["email"], mine["marketing"]["in_app"]) == (False, True)
    assert (mine["forecast"]["push"], mine["forecast"]["in_app"], mine["forecast"]["email"]) == (
        False,
        False,
        True,
    )
    assert mine["sales"]["email"] is True  # untouched categories keep the defaults


def test_later_changes_only_touch_what_is_sent(api, business):
    change_prefs(api, business, {"sales": {"email": False}})
    change_prefs(api, business, {"sales": {"push": False}})
    sales = prefs(api, business)["sales"]
    assert (sales["email"], sales["in_app"], sales["push"]) == (False, True, False)


@pytest.mark.parametrize("channel", ["email", "in_app"])
def test_security_alerts_cannot_be_switched_off(api, business, channel):
    res = change_prefs(api, business, {"security": {channel: False}})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "security_alerts_required"
    assert prefs(api, business)["security"][channel] is True


def test_security_push_can_be_switched_off(api, business):
    assert change_prefs(api, business, {"security": {"push": False}}).status_code == 200
    assert prefs(api, business)["security"]["push"] is False


def test_database_also_refuses_silencing_security(db):
    org = Organization(name="Acme")
    user = User(email="x@acme.co.uk", password_hash="x", full_name="X")
    db.add_all([org, user])
    db.flush()
    with tenant_scope(db, org.id):
        with pytest.raises(IntegrityError, match="security_always_delivered"):
            with db.begin_nested():
                db.add(NotificationPreference(user_id=user.id, category="security", email=False))
                db.flush()


@pytest.mark.parametrize(
    "body",
    [{}, {"astrology": {"email": False}}, {"sales": {"sms": True}}, {"sales": {"email": "maybe"}}],
)
def test_invalid_preference_changes_rejected(api, business, body):
    assert change_prefs(api, business, body).status_code == 422


def test_each_member_has_their_own_preferences(api, business):
    change_prefs(api, business, {"sales": {"email": False}}, who="viewer")
    assert prefs(api, business, who="viewer")["sales"]["email"] is False
    assert prefs(api, business, who="owner")["sales"]["email"] is True


def test_preferences_are_per_business(api, business, signup):
    """The same person can choose differently in each business they belong to."""
    org_id, auth = business
    other_id = api.post(ORGS, json={"name": "Side Hustle"}, headers=auth["owner"]).json()["id"]
    change_prefs(api, business, {"inventory": {"email": False}})
    other = api.get(f"{ORGS}/{other_id}/notification-preferences", headers=auth["owner"]).json()
    assert {p["category"]: p["email"] for p in other}["inventory"] is True


def test_outsiders_cannot_read_or_change_anything(api, business, signup):
    org_id, _ = business
    stranger = signup("stranger@acme.co.uk")
    assert api.get(f"{ORGS}/{org_id}/settings", headers=stranger).status_code == 404
    assert api.get(f"{ORGS}/{org_id}/notification-preferences", headers=stranger).status_code == 404
