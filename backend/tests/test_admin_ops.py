# ruff: noqa: E501, E731, F811
"""The admin portal, step 2: feature switches, support cases and notes, and how the system is."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from app.integrations.base import ProviderUnavailable, ReauthRequired
from app.models.admin import AdminNote, FeatureFlag, SupportCase, SystemEvent
from app.models.identity import User
from app.models.jobs import Job
from app.services import flags, scheduler, system_events
from tests.test_admin import A, audit_actions, make_staff
from tests.test_health import ORGS, scoped
from tests.test_integrations import (  # noqa: F401
    connected,
    fake,
    make_due,
    request_sync,
    run_worker,
)
from tests.test_jobs import mapped, start, work

WHY = "A good enough reason"


def new_flag(api, staff, key="new_reports", description="A new kind of report", enabled=False):
    return api.post(
        f"{A}/flags",
        json={"key": key, "description": description, "enabled": enabled, "reason": WHY},
        headers=staff,
    )


def features(api, business, who="owner", org=0):
    res = api.get(f"{ORGS}/{business[org]}/features", headers=business[2][who])
    assert res.status_code == 200, res.text
    return res.json()["flags"]


def events_of(db, kind=None):
    stmt = select(SystemEvent).order_by(SystemEvent.created_at)
    if kind:
        stmt = stmt.where(SystemEvent.kind == kind)
    return db.scalars(stmt.execution_options(populate_existing=True)).all()


# --- everything here is for staff only ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/flags", "/cases", "/health", "/events"])
def test_a_person_who_is_not_staff_is_told_none_of_this_exists(api, db, business, path):
    assert api.get(f"{A}{path}", headers=business[2]["owner"]).status_code == 404
    assert api.get(f"{A}{path}").status_code == 401


def test_every_write_in_this_part_is_closed_to_those_who_are_not_staff(api, db, business):
    org = business[0]
    auth = business[2]["owner"]
    case = uuid.uuid4()
    calls = [
        ("post", "/flags", {"key": "abc", "description": "d d", "reason": WHY}),
        ("post", "/cases", {"subject": "A problem"}),
        ("patch", f"/cases/{case}", {"status": "resolved"}),
        ("post", f"/cases/{case}/notes", {"body": "hello"}),
        ("post", "/notes", {"body": "hello", "organization_id": org}),
        ("post", f"/events/{uuid.uuid4()}/resolve", None),
    ]
    for method, path, body in calls:
        res = getattr(api, method)(
            f"{A}{path}", headers=auth, **({"json": body} if body is not None else {})
        )
        assert res.status_code == 404, path


# --- feature switches ----------------------------------------------------------------------------------------------


def test_an_admin_can_create_a_switch_and_it_starts_off_unless_told_otherwise(
    api, db, signup, business
):
    staff = make_staff(api, db, signup)
    res = new_flag(api, staff)
    assert res.status_code == 201, res.text
    assert res.json() == {
        "key": "new_reports",
        "description": "A new kind of report",
        "enabled": False,
        "overrides": [],
    }
    assert new_flag(api, staff, "on_for_all", enabled=True).json()["enabled"] is True
    assert [f["key"] for f in api.get(f"{A}/flags", headers=staff).json()] == [
        "new_reports",
        "on_for_all",
    ]
    [entry, _] = audit_actions(db, "admin.flag_changed")
    assert entry.target_id == "new_reports" and entry.details == {
        "reason": WHY,
        "what": "created",
        "enabled": False,
    }


def test_a_switch_needs_a_sensible_name_a_description_a_reason_and_a_name_not_already_taken(
    api, db, signup, business
):
    staff = make_staff(api, db, signup)
    for key in ("ab", "Upper", "1starts", "has space", "has-dash", "x" * 42, "../etc"):
        assert new_flag(api, staff, key).status_code == 422, key
    assert (
        api.post(
            f"{A}/flags", json={"key": "valid_one", "description": "", "reason": WHY}, headers=staff
        ).status_code
        == 422
    )
    assert (
        api.post(
            f"{A}/flags",
            json={"key": "valid_one", "description": "Something", "reason": "no"},
            headers=staff,
        ).status_code
        == 422
    )
    assert new_flag(api, staff, "valid_one").status_code == 201
    again = new_flag(api, staff, "valid_one")
    assert again.status_code == 409 and again.json()["error"]["code"] == "flag_exists"


def test_support_staff_can_see_the_switches_but_not_change_them(api, db, signup, business):
    admin = make_staff(api, db, signup)
    support = make_staff(api, db, signup, "support@vyterlix.com", "support")
    new_flag(api, admin)
    assert api.get(f"{A}/flags", headers=support).status_code == 200
    org = business[0]
    for method, path, body in [
        ("post", "/flags", {"key": "another_one", "description": "Another", "reason": WHY}),
        ("patch", "/flags/new_reports", {"enabled": True, "reason": WHY}),
        ("put", f"/flags/new_reports/organizations/{org}", {"enabled": True, "reason": WHY}),
    ]:
        res = getattr(api, method)(f"{A}{path}", json=body, headers=support)
        assert res.status_code == 403 and res.json()["error"]["code"] == "admin_only", path
    for path in ("/flags/new_reports", f"/flags/new_reports/organizations/{org}"):
        assert api.delete(f"{A}{path}", params={"reason": WHY}, headers=support).status_code == 403
    assert flags.enabled(db, "new_reports", uuid.UUID(org)) is False


def test_a_business_sees_which_features_are_on_for_it(api, db, signup, business):
    org, other, auth = business
    staff = make_staff(api, db, signup)
    assert features(api, business) == {}
    new_flag(api, staff, "new_reports")
    new_flag(api, staff, "everyone_gets_it", enabled=True)
    assert features(api, business) == {"everyone_gets_it": True, "new_reports": False}
    assert features(api, business, "viewer") == {"everyone_gets_it": True, "new_reports": False}
    res = api.put(
        f"{A}/flags/new_reports/organizations/{org}",
        json={"enabled": True, "reason": WHY},
        headers=staff,
    )
    assert res.status_code == 200
    assert features(api, business)["new_reports"] is True
    assert features(api, business, "other", 1)["new_reports"] is False  # only the chosen business
    api.put(
        f"{A}/flags/everyone_gets_it/organizations/{other}",
        json={"enabled": False, "reason": WHY},
        headers=staff,
    )
    assert (
        features(api, business, "other", 1)["everyone_gets_it"] is False
        and features(api, business)["everyone_gets_it"] is True
    )
    assert api.get(f"{ORGS}/{org}/features", headers=auth["other"]).status_code in (403, 404)
    assert api.get(f"{ORGS}/{org}/features").status_code == 401


def test_a_businesss_own_entry_wins_over_the_general_setting_until_it_is_cleared(
    api, db, signup, business
):
    org, _, _ = business
    staff = make_staff(api, db, signup)
    new_flag(api, staff, "pilot_thing", enabled=True)
    api.put(
        f"{A}/flags/pilot_thing/organizations/{org}",
        json={"enabled": False, "reason": WHY},
        headers=staff,
    )
    assert features(api, business)["pilot_thing"] is False
    api.patch(f"{A}/flags/pilot_thing", json={"enabled": False, "reason": WHY}, headers=staff)
    api.put(
        f"{A}/flags/pilot_thing/organizations/{org}",
        json={"enabled": True, "reason": WHY},
        headers=staff,
    )
    assert features(api, business)["pilot_thing"] is True
    cleared = api.delete(
        f"{A}/flags/pilot_thing/organizations/{org}", params={"reason": WHY}, headers=staff
    )
    assert cleared.status_code == 200 and cleared.json()["overrides"] == []
    assert features(api, business)["pilot_thing"] is False  # back to the general setting
    again = api.delete(
        f"{A}/flags/pilot_thing/organizations/{org}", params={"reason": WHY}, headers=staff
    )
    assert again.status_code == 404 and again.json()["error"]["code"] == "override_not_found"


def test_the_list_of_switches_names_the_businesses_with_their_own_entry(api, db, signup, business):
    org, other, _ = business
    staff = make_staff(api, db, signup)
    new_flag(api, staff, "pilot_thing")
    api.put(
        f"{A}/flags/pilot_thing/organizations/{other}",
        json={"enabled": True, "reason": WHY},
        headers=staff,
    )
    api.put(
        f"{A}/flags/pilot_thing/organizations/{org}",
        json={"enabled": False, "reason": WHY},
        headers=staff,
    )
    [flag] = api.get(f"{A}/flags", headers=staff).json()
    assert [(o["organization_name"], o["enabled"]) for o in flag["overrides"]] == [
        ("Acme", False),
        ("Rival", True),
    ]


def test_changing_a_switch_is_recorded_with_what_it_was_and_a_change_that_changes_nothing_is_not(
    api, db, signup, business
):
    staff = make_staff(api, db, signup)
    new_flag(api, staff, "pilot_thing")
    res = api.patch(
        f"{A}/flags/pilot_thing",
        json={"enabled": True, "description": "A better description", "reason": WHY},
        headers=staff,
    )
    assert res.status_code == 200 and (res.json()["enabled"], res.json()["description"]) == (
        True,
        "A better description",
    )
    last = audit_actions(db, "admin.flag_changed")[-1]
    assert last.details == {
        "reason": WHY,
        "what": "changed",
        "enabled": {"from": False, "to": True},
        "description": "A better description",
    }
    count = len(audit_actions(db, "admin.flag_changed"))
    api.patch(
        f"{A}/flags/pilot_thing",
        json={"enabled": True, "description": "A better description", "reason": WHY},
        headers=staff,
    )
    assert len(audit_actions(db, "admin.flag_changed")) == count
    assert (
        api.patch(f"{A}/flags/pilot_thing", json={"enabled": True}, headers=staff).status_code
        == 422
    )  # a reason is needed
    assert (
        api.patch(
            f"{A}/flags/pilot_thing", json={"colour": "red", "reason": WHY}, headers=staff
        ).status_code
        == 422
    )


def test_a_switch_can_be_removed_and_unknown_ones_and_businesses_are_not_found(
    api, db, signup, business
):
    org = business[0]
    staff = make_staff(api, db, signup)
    new_flag(api, staff, "pilot_thing", enabled=True)
    assert features(api, business) == {"pilot_thing": True}
    assert (
        api.delete(f"{A}/flags/pilot_thing", params={"reason": WHY}, headers=staff).status_code
        == 204
    )
    assert features(api, business) == {} and api.get(f"{A}/flags", headers=staff).json() == []
    assert audit_actions(db, "admin.flag_changed")[-1].details == {"reason": WHY, "what": "deleted"}
    assert (
        api.delete(f"{A}/flags/pilot_thing", params={"reason": WHY}, headers=staff).status_code
        == 404
    )
    assert api.delete(f"{A}/flags/pilot_thing", headers=staff).status_code == 422
    assert (
        api.patch(
            f"{A}/flags/nothing_here", json={"enabled": True, "reason": WHY}, headers=staff
        ).status_code
        == 404
    )
    new_flag(api, staff, "another_one")
    assert (
        api.put(
            f"{A}/flags/another_one/organizations/{uuid.uuid4()}",
            json={"enabled": True, "reason": WHY},
            headers=staff,
        ).status_code
        == 404
    )
    assert (
        api.put(
            f"{A}/flags/missing_one/organizations/{org}",
            json={"enabled": True, "reason": WHY},
            headers=staff,
        ).status_code
        == 404
    )


def test_a_switch_nobody_created_is_off(db, business):
    assert flags.enabled(db, "never_made", uuid.UUID(business[0])) is False
    db.add(FeatureFlag(key="made_one", description="Made", enabled=True, org_overrides={}))
    db.flush()
    assert flags.enabled(db, "made_one", uuid.UUID(business[0])) is True


# --- support cases and notes -------------------------------------------------------------------------------------------


def open_case(api, staff, **body):
    return api.post(f"{A}/cases", json={"subject": "Cannot find my report", **body}, headers=staff)


def test_support_staff_can_open_a_case_about_a_business_with_a_first_note(
    api, db, signup, business
):
    org = business[0]
    support = make_staff(api, db, signup, "support@vyterlix.com", "support")
    res = open_case(
        api,
        support,
        organization_id=org,
        requester_email="Owner@ACME.co.uk",
        priority="high",
        note="Phoned at 10am",
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert (body["subject"], body["status"], body["priority"], body["organization_name"]) == (
        "Cannot find my report",
        "open",
        "high",
        "Acme",
    )
    assert (
        body["requester_email"] == "owner@acme.co.uk"
        and body["created_by_email"] == "support@vyterlix.com"
    )
    assert [(n["body"], n["author_email"]) for n in body["notes"]] == [
        ("Phoned at 10am", "support@vyterlix.com")
    ]
    assert body["assigned_to_email"] is None and body["resolved_at"] is None
    [entry] = audit_actions(db, "admin.case_created")
    assert str(entry.organization_id) == org and entry.details == {"priority": "high"}


def test_a_case_needs_a_subject_and_a_real_business(api, db, signup, business):
    staff = make_staff(api, db, signup)
    assert open_case(api, staff, subject="Hi").status_code == 422
    assert open_case(api, staff, organization_id=str(uuid.uuid4())).status_code == 404
    assert open_case(api, staff, priority="urgent").status_code == 422
    assert open_case(api, staff, colour="red").status_code == 422
    assert (
        open_case(api, staff).status_code == 201
    )  # a case about no business in particular is fine


def test_cases_are_listed_open_ones_first_and_can_be_narrowed(api, db, signup, business):
    org, other, _ = business
    staff = make_staff(api, db, signup)
    second = make_staff(api, db, signup, "second@vyterlix.com", "support")
    first_id = open_case(api, staff, subject="First problem", organization_id=org).json()["id"]
    open_case(api, staff, subject="Second problem", organization_id=other)
    open_case(api, staff, subject="Third problem")
    api.patch(f"{A}/cases/{first_id}", json={"status": "resolved"}, headers=staff)
    listing = api.get(f"{A}/cases", headers=staff).json()
    assert listing["total"] == 3 and [c["subject"] for c in listing["items"]] == [
        "Third problem",
        "Second problem",
        "First problem",
    ]
    subjects = lambda params: [
        c["subject"] for c in api.get(f"{A}/cases{params}", headers=staff).json()["items"]
    ]
    assert subjects("?status=resolved") == ["First problem"] and sorted(
        subjects("?status=open")
    ) == ["Second problem", "Third problem"]
    assert (
        subjects(f"?organization_id={other}") == ["Second problem"] and subjects("?mine=true") == []
    )
    third_id = api.get(f"{A}/cases?status=open", headers=staff).json()["items"][0]["id"]
    api.patch(
        f"{A}/cases/{third_id}", json={"assigned_to_email": "second@vyterlix.com"}, headers=staff
    )
    assert [
        c["subject"] for c in api.get(f"{A}/cases?mine=true", headers=second).json()["items"]
    ] == ["Third problem"]
    assert subjects("?mine=true") == []
    page = api.get(f"{A}/cases?limit=1&offset=2", headers=staff).json()
    assert page["total"] == 3 and [c["subject"] for c in page["items"]] == ["First problem"]
    assert api.get(f"{A}/cases?status=weird", headers=staff).status_code == 422


def test_a_case_moves_through_open_waiting_and_resolved_and_can_be_reopened(
    api, db, signup, business
):
    staff = make_staff(api, db, signup)
    case_id = open_case(api, staff).json()["id"]
    waiting = api.patch(f"{A}/cases/{case_id}", json={"status": "waiting"}, headers=staff).json()
    assert waiting["status"] == "waiting" and waiting["resolved_at"] is None
    resolved = api.patch(f"{A}/cases/{case_id}", json={"status": "resolved"}, headers=staff).json()
    assert resolved["status"] == "resolved" and resolved["resolved_at"] is not None
    reopened = api.patch(
        f"{A}/cases/{case_id}",
        json={"status": "open", "priority": "low", "subject": "A clearer subject"},
        headers=staff,
    ).json()
    assert (
        reopened["status"],
        reopened["resolved_at"],
        reopened["priority"],
        reopened["subject"],
    ) == ("open", None, "low", "A clearer subject")
    details = [e.details for e in audit_actions(db, "admin.case_updated")]
    assert details[0] == {"status": {"from": "open", "to": "waiting"}}
    assert details[2] == {
        "subject": {"from": "Cannot find my report", "to": "A clearer subject"},
        "priority": {"from": "normal", "to": "low"},
        "status": {"from": "resolved", "to": "open"},
    }
    count = len(details)
    api.patch(f"{A}/cases/{case_id}", json={"status": "open", "priority": "low"}, headers=staff)
    assert (
        len(audit_actions(db, "admin.case_updated")) == count
    )  # nothing changed, nothing recorded


def test_a_case_can_only_be_given_to_staff_and_can_be_taken_back(api, db, signup, business):
    staff = make_staff(api, db, signup)
    make_staff(api, db, signup, "second@vyterlix.com", "support")
    case_id = open_case(api, staff).json()["id"]
    given = api.patch(
        f"{A}/cases/{case_id}", json={"assigned_to_email": "SECOND@vyterlix.com"}, headers=staff
    ).json()
    assert given["assigned_to_email"] == "second@vyterlix.com"
    owner = api.patch(
        f"{A}/cases/{case_id}", json={"assigned_to_email": "owner@acme.co.uk"}, headers=staff
    )
    assert owner.status_code == 422 and owner.json()["error"]["code"] == "not_staff"
    nobody = api.patch(
        f"{A}/cases/{case_id}", json={"assigned_to_email": "nobody@nowhere.example"}, headers=staff
    )
    assert nobody.status_code == 422
    assert (
        api.get(f"{A}/cases/{case_id}", headers=staff).json()["assigned_to_email"]
        == "second@vyterlix.com"
    )
    taken = api.patch(f"{A}/cases/{case_id}", json={"unassign": True}, headers=staff).json()
    assert taken["assigned_to_email"] is None
    assert audit_actions(db, "admin.case_updated")[-1].details == {"assigned_to": {"to": None}}
    again = api.patch(
        f"{A}/cases/{case_id}", json={"assigned_to_email": "second@vyterlix.com"}, headers=staff
    )
    assert again.json()["assigned_to_email"] == "second@vyterlix.com"
    count = len(audit_actions(db, "admin.case_updated"))
    api.patch(
        f"{A}/cases/{case_id}", json={"assigned_to_email": "second@vyterlix.com"}, headers=staff
    )
    assert len(audit_actions(db, "admin.case_updated")) == count


def test_notes_on_a_case_are_kept_newest_first_with_who_wrote_them(api, db, signup, business):
    staff = make_staff(api, db, signup)
    support = make_staff(api, db, signup, "support@vyterlix.com", "support")
    case_id = open_case(api, staff, note="First note").json()["id"]
    api.post(f"{A}/cases/{case_id}/notes", json={"body": "  Second note  "}, headers=support)
    res = api.post(f"{A}/cases/{case_id}/notes", json={"body": "Third note"}, headers=staff)
    assert res.status_code == 201
    notes = res.json()["notes"]
    assert [(n["body"], n["author_email"]) for n in notes] == [
        ("Third note", "staff@vyterlix.com"),
        ("Second note", "support@vyterlix.com"),
        ("First note", "staff@vyterlix.com"),
    ]
    for bad in ("", "   ", "x" * 4001):
        assert (
            api.post(f"{A}/cases/{case_id}/notes", json={"body": bad}, headers=staff).status_code
            == 422
        ), bad[:5]
    assert (
        api.post(f"{A}/cases/{uuid.uuid4()}/notes", json={"body": "hi"}, headers=staff).status_code
        == 404
    )
    assert api.get(f"{A}/cases/{uuid.uuid4()}", headers=staff).status_code == 404
    assert (
        api.patch(f"{A}/cases/{uuid.uuid4()}", json={"status": "open"}, headers=staff).status_code
        == 404
    )
    assert len(audit_actions(db, "admin.note_added")) == 2


def test_staff_can_keep_notes_about_a_business_or_a_person_and_see_them_there(
    api, db, signup, business
):
    org, _, auth = business
    support = make_staff(api, db, signup, "support@vyterlix.com", "support")
    owner_id = db.scalars(select(User.id).where(User.email == "owner@acme.co.uk")).one()
    assert (
        api.post(
            f"{A}/notes",
            json={"body": "Wants a call back", "organization_id": org},
            headers=support,
        ).status_code
        == 201
    )
    assert (
        api.post(
            f"{A}/notes", json={"body": "Prefers email", "user_id": str(owner_id)}, headers=support
        ).status_code
        == 201
    )
    open_case(api, support, organization_id=org)
    detail = api.get(f"{A}/organizations/{org}", headers=support).json()
    assert [n["body"] for n in detail["notes"]] == ["Wants a call back"] and detail[
        "open_cases"
    ] == 1
    person = api.get(f"{A}/users/{owner_id}", headers=support).json()
    assert [n["body"] for n in person["notes"]] == ["Prefers email"]
    other = api.get(f"{A}/organizations/{business[1]}", headers=support).json()
    assert other["notes"] == [] and other["open_cases"] == 0
    # nothing a business can read ever shows a staff note
    assert "call back" not in api.get(f"{ORGS}/{org}/members", headers=auth["owner"]).text
    assert (
        api.post(f"{A}/notes", json={"body": "About nothing"}, headers=support).status_code == 422
    )
    assert (
        api.post(
            f"{A}/notes", json={"body": "x", "organization_id": str(uuid.uuid4())}, headers=support
        ).status_code
        == 404
    )
    assert (
        api.post(
            f"{A}/notes", json={"body": "x", "user_id": str(uuid.uuid4())}, headers=support
        ).status_code
        == 404
    )
    case_id = api.get(f"{A}/cases", headers=support).json()["items"][0]["id"]
    api.patch(f"{A}/cases/{case_id}", json={"status": "resolved"}, headers=support)
    assert api.get(f"{A}/organizations/{org}", headers=support).json()["open_cases"] == 0


def test_a_note_or_case_goes_when_its_business_does_but_the_case_stays_for_the_record(db, business):
    from app.models.identity import Organization

    org = uuid.UUID(business[1])
    case = SupportCase(subject="About a business", organization_id=org)
    db.add(case)
    db.flush()
    db.add(AdminNote(case_id=case.id, body="Case note"))
    db.add(AdminNote(organization_id=org, body="Business note"))
    db.flush()
    db.execute(delete(Organization).where(Organization.id == org))
    db.expire_all()
    case = db.get(SupportCase, case.id)
    assert case.organization_id is None
    assert [n.body for n in db.scalars(select(AdminNote))] == ["Case note"]


# --- things that go wrong are kept for staff -----------------------------------------------------------------------------


def test_work_that_fails_for_good_tells_staff_without_the_details(
    api, db, signup, business, storage
):
    org = business[0]
    import_id = mapped(api, business)  # never checked, so it cannot be imported
    start(api, business, import_id, "import")
    ran = work(db, storage)
    assert ran.status == "failed"
    [event] = events_of(db, "job_failed")
    assert (event.severity, str(event.organization_id), event.message) == (
        "error",
        org,
        "Background work failed for good",
    )
    assert event.details == {
        "job_kind": ran.kind,
        "job_id": str(ran.id),
        "error_code": "not_validated",
    }


def test_a_connection_that_needs_signing_in_again_is_a_warning_and_one_that_keeps_failing_an_error_once(
    api, db, signup, business, storage, fake
):
    conn = connected(api, business)
    fake.refresh_error = None
    fake.sync_error = ProviderUnavailable("Fake Books is down.")
    request_sync(api, business, conn["id"])
    for tries in range(1, 4):
        make_due(db)
        run_worker(db, storage)
        assert len(events_of(db, "integration_failing")) == (1 if tries == 3 else 0), tries
    [failing] = events_of(db, "integration_failing")
    assert (failing.severity, failing.message, str(failing.organization_id)) == (
        "error",
        "A connection keeps failing",
        business[0],
    )
    assert failing.details == {"provider": "fake", "error_code": failing.details["error_code"]}
    fake.sync_error = None
    fake.expires_in = timedelta(seconds=30)
    again = connected(api, business, integration_id=conn["id"])
    fake.refresh_error = ReauthRequired("Fake Books needs you to sign in again.")
    request_sync(api, business, again["id"])
    run_worker(db, storage)
    [reauth] = events_of(db, "integration_needs_signing_in")
    assert reauth.severity == "warning" and reauth.details["provider"] == "fake"
    assert "needs you to sign in" not in str(reauth.details)


def test_an_email_that_cannot_be_sent_tells_staff(api, db, signup, march):
    from app.models.alerts import Notification
    from app.services import notifications
    from tests.test_alerts import evaluate

    class Down:
        def send(self, message):
            raise RuntimeError("the mail server said something private")

    evaluate(api, march)
    with scoped(db, march):
        rows = db.scalars(select(Notification)).all()
        assert rows
        for row in rows:
            row.email_status, row.email_after = "pending", datetime.now(UTC) - timedelta(minutes=1)
        db.flush()
        notifications.send_due_emails(db, sender=Down())
    found = events_of(db, "email_failed")
    assert found and {e.message for e in found} == {"An email could not be sent"}
    assert {str(e.organization_id) for e in found} == {march[0]}
    assert all("private" not in str(e.details) and "private" not in e.message for e in found)


def test_a_timed_round_that_fails_for_a_business_tells_staff_and_carries_on(
    api, db, signup, business, monkeypatch
):
    org = uuid.UUID(business[0])
    monkeypatch.setattr(scheduler, "_businesses_with_work", lambda db, today, now=None: {org})

    def boom(*args, **kwargs):
        raise RuntimeError("internal detail")

    monkeypatch.setattr(scheduler.actions, "refresh_overdue", boom)
    totals = scheduler.tick(db)
    assert totals["businesses"] == 0
    [event] = events_of(db, "scheduled_round_failed")
    assert (event.severity, event.organization_id, event.message) == (
        "error",
        org,
        "The timed round failed for a business",
    )
    assert "internal detail" not in str(event.details)


def test_events_are_listed_newest_first_and_can_be_narrowed_and_paged(api, db, signup, business):
    org, other, _ = business
    staff = make_staff(api, db, signup)
    system_events.record(
        db,
        "email_failed",
        "An email could not be sent",
        severity="warning",
        organization_id=uuid.UUID(org),
    )
    system_events.record(
        db, "job_failed", "Background work failed for good", organization_id=uuid.UUID(other)
    )
    system_events.record(db, "job_failed", "Background work failed for good", severity="info")
    db.flush()
    page = api.get(f"{A}/events", headers=staff).json()
    assert [e["kind"] for e in page["items"]] == [
        "job_failed",
        "job_failed",
        "email_failed",
    ] and page["next_before"] is None
    assert [e["organization_name"] for e in page["items"]] == [None, "Rival", "Acme"]
    assert [
        e["kind"] for e in api.get(f"{A}/events?kind=email_failed", headers=staff).json()["items"]
    ] == ["email_failed"]
    assert [
        e["severity"] for e in api.get(f"{A}/events?severity=error", headers=staff).json()["items"]
    ] == ["error"]
    first = api.get(f"{A}/events?limit=2", headers=staff).json()
    assert len(first["items"]) == 2 and first["next_before"] == first["items"][-1]["id"]
    rest = api.get(f"{A}/events?limit=2&before={first['next_before']}", headers=staff).json()
    assert len(rest["items"]) == 1 and rest["next_before"] is None
    exact = api.get(f"{A}/events?limit=3", headers=staff).json()
    assert len(exact["items"]) == 3 and exact["next_before"] is None
    assert api.get(f"{A}/events?before={uuid.uuid4()}", headers=staff).status_code == 404
    assert api.get(f"{A}/events?severity=bad", headers=staff).status_code == 422


def test_staff_mark_an_event_dealt_with_once(api, db, signup, business):
    support = make_staff(api, db, signup, "support@vyterlix.com", "support")
    system_events.record(
        db, "job_failed", "Background work failed for good", organization_id=uuid.UUID(business[0])
    )
    db.flush()
    event_id = api.get(f"{A}/events", headers=support).json()["items"][0]["id"]
    assert api.post(f"{A}/events/{event_id}/resolve", headers=support).status_code == 204
    shown = api.get(f"{A}/events", headers=support).json()["items"][0]
    assert shown["resolved_at"] is not None
    assert api.get(f"{A}/events?open_only=true", headers=support).json()["items"] == []
    again = api.post(f"{A}/events/{event_id}/resolve", headers=support)
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_resolved"
    assert api.post(f"{A}/events/{uuid.uuid4()}/resolve", headers=support).status_code == 404
    [entry] = audit_actions(db, "admin.event_resolved")
    assert entry.details == {"kind": "job_failed"} and str(entry.organization_id) == business[0]


# --- how the system is --------------------------------------------------------------------------------------------------------


def test_the_health_page_reads_ok_on_a_quiet_system(api, db, signup, business):
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    staff = make_staff(api, db, signup)
    body = api.get(f"{A}/health", headers=staff).json()
    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    assert (body["status"], body["database_ok"], body["migration"]) == ("ok", True, head)
    assert body["environment"] in ("dev", "test", "staging", "prod")
    assert body["email_backend"] in ("console", "smtp") and body["ai_provider"] in (
        "offline",
        "anthropic",
    )
    assert body["jobs"] == {
        "queued": 0,
        "running": 0,
        "stuck": 0,
        "failed_last_day": 0,
        "oldest_waiting_seconds": None,
    }
    assert body["emails"] == {"waiting": 0, "failed_last_day": 0}
    assert body["integrations"] == {"connected": 0, "needing_attention": 0}
    assert (
        body["accounts"]["businesses"] == {"active": 2}
        and body["accounts"]["people"] == 4
        and body["accounts"]["locked_people"] == 0
    )
    assert (body["open_errors"], body["open_warnings"], body["open_cases"]) == (0, 0, 0)


def test_the_health_page_asks_for_attention_while_something_is_unresolved(
    api, db, signup, business
):
    staff = make_staff(api, db, signup)
    system_events.record(db, "job_failed", "Background work failed for good")
    system_events.record(db, "email_failed", "An email could not be sent", severity="warning")
    db.flush()
    body = api.get(f"{A}/health", headers=staff).json()
    assert (body["status"], body["open_errors"], body["open_warnings"]) == ("attention", 1, 1)
    error_id = api.get(f"{A}/events?severity=error", headers=staff).json()["items"][0]["id"]
    api.post(f"{A}/events/{error_id}/resolve", headers=staff)
    after = api.get(f"{A}/health", headers=staff).json()
    assert (after["status"], after["open_errors"], after["open_warnings"]) == (
        "ok",
        0,
        1,
    )  # a warning alone is not a reason to worry


def set_jobs(db, business, where=None, **values):
    with scoped(db, business):
        stmt = update(Job).values(**values)
        if where is not None:
            stmt = stmt.where(where)
        db.execute(stmt)


def test_the_health_page_counts_work_waiting_failing_or_stuck(
    api, db, signup, business, storage, fake
):
    staff = make_staff(api, db, signup)
    import_id = mapped(api, business)
    start(api, business, import_id, "import")
    body = api.get(f"{A}/health", headers=staff).json()
    assert (
        body["jobs"]["queued"] == 1
        and body["jobs"]["oldest_waiting_seconds"] is not None
        and body["jobs"]["oldest_waiting_seconds"] >= 0
    )
    work(db, storage)  # it fails
    body = api.get(f"{A}/health", headers=staff).json()
    assert body["jobs"]["queued"] == 0 and body["jobs"]["failed_last_day"] == 1
    for event in api.get(f"{A}/events", headers=staff).json()[
        "items"
    ]:  # the failure is known about
        api.post(f"{A}/events/{event['id']}/resolve", headers=staff)
    set_jobs(db, business, finished_at=datetime.now(UTC) - timedelta(days=2))
    assert (
        api.get(f"{A}/health", headers=staff).json()["jobs"]["failed_last_day"] == 0
    )  # yesterday's is not today's news
    start(api, business, import_id, "import")
    set_jobs(
        db, business, Job.status == "queued", status="running", locked_by="x",
        heartbeat_at=datetime.now(UTC) - timedelta(minutes=6),
        started_at=datetime.now(UTC) - timedelta(minutes=7),
    )  # fmt: skip
    stuck = api.get(f"{A}/health", headers=staff).json()
    assert (
        stuck["jobs"]["running"] == 1
        and stuck["jobs"]["stuck"] == 1
        and stuck["status"] == "attention"
    )
    set_jobs(
        db, business, Job.status == "running", heartbeat_at=datetime.now(UTC) - timedelta(minutes=4)
    )
    fine = api.get(f"{A}/health", headers=staff).json()
    assert fine["jobs"]["running"] == 1 and fine["jobs"]["stuck"] == 0 and fine["status"] == "ok"


def test_the_health_page_counts_connections_that_need_attention(
    api, db, signup, business, storage, fake
):
    staff = make_staff(api, db, signup)
    conn = connected(api, business)
    body = api.get(f"{A}/health", headers=staff).json()
    assert body["integrations"] == {"connected": 1, "needing_attention": 0}
    fake.sync_error = ProviderUnavailable("Down")
    request_sync(api, business, conn["id"])
    run_worker(db, storage)
    assert api.get(f"{A}/health", headers=staff).json()["integrations"] == {
        "connected": 1,
        "needing_attention": 1,
    }


def test_the_health_page_counts_people_plans_locks_and_open_cases(api, db, signup, business):
    org, _, auth = business
    staff = make_staff(api, db, signup)
    api.get(f"{ORGS}/{org}/billing", headers=auth["owner"])
    viewer = db.scalars(select(User.id).where(User.email == "viewer@acme.co.uk")).one()
    api.post(f"{A}/users/{viewer}/disable", json={"reason": WHY}, headers=staff)
    open_case(api, staff)
    resolved = open_case(api, staff).json()["id"]
    api.patch(f"{A}/cases/{resolved}", json={"status": "resolved"}, headers=staff)
    api.post(f"{A}/organizations/{business[1]}/suspend", json={"reason": WHY}, headers=staff)
    body = api.get(f"{A}/health", headers=staff).json()
    assert body["accounts"]["subscriptions"] == {"trialing": 1}
    assert body["accounts"]["locked_people"] == 1 and body["accounts"]["businesses"] == {
        "active": 1,
        "suspended": 1,
    }
    assert body["open_cases"] == 1


def test_a_waiting_email_is_counted_only_once_it_is_due(api, db, signup, march):
    from sqlalchemy import func

    from app.models.alerts import Notification

    staff = make_staff(api, db, signup)
    before = api.get(f"{A}/health", headers=staff).json()["emails"]
    with scoped(db, march):
        row = db.scalars(select(Notification)).first()
        if row is None:
            from tests.test_alerts import evaluate

            evaluate(api, march)
            row = db.scalars(select(Notification)).first()
        row.email_status, row.email_after = "pending", datetime.now(UTC) - timedelta(minutes=1)
        db.flush()
        pending_now = db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.email_status == "pending")
        )
    waiting = api.get(f"{A}/health", headers=staff).json()["emails"]["waiting"]
    assert waiting == pending_now and waiting >= 1 and waiting > before["waiting"]
    with scoped(db, march):
        db.execute(
            update(Notification)
            .where(Notification.id == row.id)
            .values(email_after=datetime.now(UTC) + timedelta(hours=3))
        )
    later = api.get(f"{A}/health", headers=staff).json()["emails"]["waiting"]
    assert later == pending_now - 1
    with scoped(db, march):
        db.execute(
            update(Notification)
            .where(Notification.id == row.id)
            .values(email_status="failed", email_sent_at=datetime.now(UTC))
        )
    assert api.get(f"{A}/health", headers=staff).json()["emails"]["failed_last_day"] == 1
    with scoped(db, march):
        db.execute(
            update(Notification)
            .where(Notification.id == row.id)
            .values(email_sent_at=datetime.now(UTC) - timedelta(days=2))
        )
    assert api.get(f"{A}/health", headers=staff).json()["emails"]["failed_last_day"] == 0


def test_an_event_needs_a_known_severity_and_survives_its_business_going(db, business):
    from sqlalchemy.exc import IntegrityError

    from app.models.identity import Organization

    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(SystemEvent(kind="x", severity="fatal", message="m"))
        db.flush()
    system_events.record(db, "job_failed", "m", organization_id=uuid.UUID(business[1]))
    db.flush()
    db.execute(delete(Organization).where(Organization.id == uuid.UUID(business[1])))
    db.expire_all()
    [event] = events_of(db, "job_failed")
    assert event.organization_id is None


# --- corners found by the first round of checking -------------------------------------------------------------------------


def test_removing_one_businesss_entry_leaves_the_others(api, db, signup, business):
    org, other, _ = business
    staff = make_staff(api, db, signup)
    new_flag(api, staff, "pilot_thing")
    api.put(
        f"{A}/flags/pilot_thing/organizations/{org}",
        json={"enabled": True, "reason": WHY},
        headers=staff,
    )
    api.put(
        f"{A}/flags/pilot_thing/organizations/{other}",
        json={"enabled": True, "reason": WHY},
        headers=staff,
    )
    api.delete(f"{A}/flags/pilot_thing/organizations/{org}", params={"reason": WHY}, headers=staff)
    [flag] = api.get(f"{A}/flags", headers=staff).json()
    assert [(o["organization_name"], o["enabled"]) for o in flag["overrides"]] == [("Rival", True)]
    assert features(api, business, "other", 1)["pilot_thing"] is True


def test_adding_a_note_marks_the_case_as_touched(api, db, signup, business):
    staff = make_staff(api, db, signup)
    case_id = open_case(api, staff).json()["id"]
    before = api.get(f"{A}/cases/{case_id}", headers=staff).json()["notes"]
    assert before == []
    row = db.get(SupportCase, uuid.UUID(case_id))
    opened = row.updated_at
    db.execute(
        update(SupportCase)
        .where(SupportCase.id == row.id)
        .values(updated_at=opened - timedelta(hours=1))
    )
    db.expire_all()
    api.post(f"{A}/cases/{case_id}/notes", json={"body": "Rang them back"}, headers=staff)
    db.expire_all()
    assert db.get(SupportCase, row.id).updated_at > opened - timedelta(minutes=1)


def test_work_that_is_not_due_yet_is_not_counted_as_waiting(api, db, signup, business):
    staff = make_staff(api, db, signup)
    import_id = mapped(api, business)
    start(api, business, import_id, "import")
    set_jobs(db, business, run_after=datetime.now(UTC) + timedelta(hours=1))
    jobs = api.get(f"{A}/health", headers=staff).json()["jobs"]
    assert jobs["queued"] == 1 and jobs["oldest_waiting_seconds"] is None
    set_jobs(db, business, run_after=datetime.now(UTC) - timedelta(seconds=90))
    assert (
        90 <= api.get(f"{A}/health", headers=staff).json()["jobs"]["oldest_waiting_seconds"] < 150
    )


def test_a_warning_that_has_been_dealt_with_is_no_longer_counted(api, db, signup, business):
    staff = make_staff(api, db, signup)
    system_events.record(db, "email_failed", "An email could not be sent", severity="warning")
    db.flush()
    assert api.get(f"{A}/health", headers=staff).json()["open_warnings"] == 1
    event_id = api.get(f"{A}/events", headers=staff).json()["items"][0]["id"]
    api.post(f"{A}/events/{event_id}/resolve", headers=staff)
    assert api.get(f"{A}/health", headers=staff).json()["open_warnings"] == 0


def test_a_connection_that_goes_on_failing_is_reported_once_not_every_time(
    api, db, signup, business, storage, fake
):
    conn = connected(api, business)
    fake.sync_error = ProviderUnavailable("Fake Books is down.")
    request_sync(api, business, conn["id"])
    for _ in range(3):
        make_due(db)
        run_worker(db, storage)
    assert len(events_of(db, "integration_failing")) == 1
    request_sync(api, business, conn["id"])
    for _ in range(3):
        make_due(db)
        run_worker(db, storage)
    assert (
        len(events_of(db, "integration_failing")) == 1
    )  # the fourth, fifth and sixth failures add nothing


def test_a_resolved_case_goes_below_open_ones_even_when_it_is_newer(api, db, signup, business):
    staff = make_staff(api, db, signup)
    open_case(api, staff, subject="Older but still open")
    newer = open_case(api, staff, subject="Newer but resolved").json()["id"]
    api.patch(f"{A}/cases/{newer}", json={"status": "resolved"}, headers=staff)
    listing = api.get(f"{A}/cases", headers=staff).json()["items"]
    assert [c["subject"] for c in listing] == ["Older but still open", "Newer but resolved"]
