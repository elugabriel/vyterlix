# ruff: noqa: E501
"""The mobile apps' side of the server: signing in from a phone, the list of devices, the app-version
check and where to send push notifications."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.models.identity import AuditLog, User, UserSession
from app.models.mobile import PushDevice
from app.services import mobile
from tests.conftest import TEST_PASSWORD

AUTH = "/api/v1/auth"
ME = "/api/v1/me"
LONG = "t" * 40  # a push address is long


def bearer(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def phone_login(api, email="owner@acme.co.uk", **extra):
    res = api.post(
        f"{AUTH}/login",
        json={"email": email, "password": TEST_PASSWORD, "client": "mobile", **extra},
    )
    assert res.status_code == 200, res.text
    return res.json()


def of_kind(db, client, email="owner@acme.co.uk"):
    return [s for s in sessions_of(db, email) if s.client == client]


def sessions_of(db, email="owner@acme.co.uk"):
    uid = db.scalars(select(User.id).where(User.email == email)).one()
    return db.scalars(
        select(UserSession).where(UserSession.user_id == uid).order_by(UserSession.created_at)
    ).all()


@pytest.fixture
def owner(signup):
    return signup("owner@acme.co.uk")


# --- signing in from a phone ---------------------------------------------------------------------------------------


def test_a_phone_gets_its_refresh_token_in_the_answer_and_no_cookie(api, db, owner):
    api.cookies.clear()
    res = api.post(
        f"{AUTH}/login",
        json={
            "email": "owner@acme.co.uk",
            "password": TEST_PASSWORD,
            "client": "mobile",
            "device_name": "  Jo's iPhone  ",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert (
        len(body["refresh_token"]) >= 20
        and body["access_token"]
        and body["user"]["email"] == "owner@acme.co.uk"
    )
    assert "vyterlix_refresh" not in res.headers.get("set-cookie", "") and not api.cookies
    [row] = of_kind(db, "mobile")
    assert (row.client, row.device_name) == ("mobile", "Jo's iPhone")


def test_a_browser_still_gets_its_refresh_token_in_a_cookie_and_not_in_the_answer(api, db, owner):
    api.cookies.clear()
    res = api.post(f"{AUTH}/login", json={"email": "owner@acme.co.uk", "password": TEST_PASSWORD})
    assert "refresh_token" not in res.json() and "vyterlix_refresh" in res.headers["set-cookie"]
    assert [x.device_name for x in of_kind(db, "web")] == [
        None,
        None,
    ]  # the sign-in made for the test, and this one
    assert of_kind(db, "mobile") == []


def test_a_phone_refreshes_with_the_token_in_the_body_and_the_old_one_stops_working(api, db, owner):
    api.cookies.clear()
    first = phone_login(api)
    res = api.post(f"{AUTH}/refresh", json={"refresh_token": first["refresh_token"]})
    assert res.status_code == 200, res.text
    second = res.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert "set-cookie" not in res.headers and not api.cookies
    assert (
        api.post(f"{AUTH}/refresh", json={"refresh_token": first["refresh_token"]}).status_code
        == 401
    )
    assert api.get(f"{ME}", headers=bearer(second)).status_code == 200
    third = api.post(f"{AUTH}/refresh", json={"refresh_token": second["refresh_token"]})
    assert third.status_code == 200 and of_kind(db, "mobile")[0].last_used_at is not None


def test_a_token_only_works_the_way_it_was_issued(api, db, owner):
    api.cookies.clear()
    phone = phone_login(api)
    api.cookies.set("vyterlix_refresh", phone["refresh_token"], path="/api/v1/auth")
    assert (
        api.post(f"{AUTH}/refresh").status_code == 401
    )  # a phone's token cannot be put in a cookie
    api.cookies.clear()
    web = api.post(f"{AUTH}/login", json={"email": "owner@acme.co.uk", "password": TEST_PASSWORD})
    cookie = web.cookies.get("vyterlix_refresh") or api.cookies.get("vyterlix_refresh")
    assert cookie
    assert (
        api.post(f"{AUTH}/refresh", json={"refresh_token": cookie}).status_code == 401
    )  # nor a browser's in a body
    logout = api.post(f"{AUTH}/logout", json={"refresh_token": cookie})
    assert logout.status_code == 204
    assert (
        api.post(f"{AUTH}/refresh").status_code == 200
    )  # and that did not end the browser's session


def test_a_bad_refresh_body_is_refused(api, owner):
    assert api.post(f"{AUTH}/refresh", json={"refresh_token": "short"}).status_code == 422
    assert api.post(f"{AUTH}/refresh", json={"refresh_token": "x" * 201}).status_code == 422
    assert (
        api.post(f"{AUTH}/refresh", json={"refresh_token": "x" * 40, "extra": 1}).status_code == 422
    )
    unknown = api.post(f"{AUTH}/refresh", json={"refresh_token": "x" * 40})
    assert unknown.status_code == 401 and "set-cookie" not in unknown.headers


def test_signing_out_on_a_phone_ends_its_session(api, db, owner):
    api.cookies.clear()
    phone = phone_login(api)
    assert (
        api.post(f"{AUTH}/logout", json={"refresh_token": phone["refresh_token"]}).status_code
        == 204
    )
    assert (
        api.post(f"{AUTH}/refresh", json={"refresh_token": phone["refresh_token"]}).status_code
        == 401
    )
    assert api.get(ME, headers=bearer(phone)).status_code == 401  # even the access token is dead
    assert "auth.logout" in [a for a in db.scalars(select(AuditLog.action))]
    assert (
        api.post(f"{AUTH}/logout", json={"refresh_token": phone["refresh_token"]}).status_code
        == 204
    )  # quietly nothing


def test_a_device_name_is_trimmed_limited_and_optional(api, db, owner):
    api.cookies.clear()
    phone_login(api, device_name="   ")
    assert [x.device_name for x in of_kind(db, "mobile")] == [None]
    phone_login(api, device_name="x" * 100)
    assert sorted(len(x.device_name or "") for x in of_kind(db, "mobile")) == [0, 100]
    res = api.post(
        f"{AUTH}/login",
        json={
            "email": "owner@acme.co.uk",
            "password": TEST_PASSWORD,
            "client": "mobile",
            "device_name": "x" * 101,
        },
    )
    assert res.status_code == 422
    res = api.post(
        f"{AUTH}/login",
        json={"email": "owner@acme.co.uk", "password": TEST_PASSWORD, "client": "tablet"},
    )
    assert res.status_code == 422


def test_the_login_audit_says_which_kind_of_device(api, db, owner):
    api.cookies.clear()
    phone_login(api)
    entries = db.scalars(
        select(AuditLog)
        .where(AuditLog.action == "auth.login_succeeded")
        .order_by(AuditLog.created_at)
    ).all()
    assert entries[-1].details == {"client": "mobile"} and entries[0].details == {"client": "web"}


# --- old apps are told to update ---------------------------------------------------------------------------------------


def minimum(
    monkeypatch,
    minimum="2.0.0",
    latest="2.3.0",
    ios="https://apps.apple.com/app/x",
    android="https://play.google.com/x",
):
    from app.core.config import get_settings

    base = get_settings()
    monkeypatch.setattr(
        mobile,
        "get_settings",
        lambda: base.model_copy(
            update={
                "mobile_min_version": minimum,
                "mobile_latest_version": latest,
                "ios_store_url": ios,
                "android_store_url": android,
            }
        ),
    )


def test_an_app_older_than_the_minimum_cannot_sign_in(api, owner, monkeypatch):
    minimum(monkeypatch)
    api.cookies.clear()
    old = api.post(
        f"{AUTH}/login",
        json={
            "email": "owner@acme.co.uk",
            "password": TEST_PASSWORD,
            "client": "mobile",
            "app_version": "1.9.9",
        },
    )
    assert old.status_code == 426 and old.json()["error"]["code"] == "app_update_required"
    assert "update" in old.json()["error"]["message"]
    assert phone_login(api, app_version="2.0.0")["access_token"]  # exactly the minimum is fine
    assert (
        phone_login(api, app_version="2.0")["access_token"]
        and phone_login(api, app_version="10.0.0")["access_token"]
    )
    assert phone_login(api)["access_token"]  # an app that does not say is let in
    web = api.post(
        f"{AUTH}/login",
        json={"email": "owner@acme.co.uk", "password": TEST_PASSWORD, "app_version": "0.1"},
    )
    assert web.status_code == 200  # the version only matters to phones
    bad = api.post(
        f"{AUTH}/login",
        json={
            "email": "owner@acme.co.uk",
            "password": TEST_PASSWORD,
            "client": "mobile",
            "app_version": "abc",
        },
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "bad_version"


def test_a_too_old_app_is_turned_away_before_the_password_is_checked(api, owner, monkeypatch, db):
    from app.models.identity import LoginAttempt

    minimum(monkeypatch)
    before = len(db.scalars(select(LoginAttempt)).all())
    res = api.post(
        f"{AUTH}/login",
        json={
            "email": "owner@acme.co.uk",
            "password": "wrong password!!",
            "client": "mobile",
            "app_version": "1.0.0",
        },
    )
    assert res.status_code == 426
    assert len(db.scalars(select(LoginAttempt)).all()) == before  # not counted as a failed attempt


def test_the_app_asks_whether_it_must_or_may_update(api, monkeypatch):
    minimum(monkeypatch)
    ask = lambda **q: api.get("/api/v1/app-config", params=q)  # noqa: E731
    must = ask(platform="ios", version="1.9.9").json()
    assert must == {
        "minimum_version": "2.0.0",
        "latest_version": "2.3.0",
        "update_required": True,
        "update_available": True,
        "store_url": "https://apps.apple.com/app/x",
        "message": "This version of Vyterlix is no longer supported. Please update to carry on.",
    }
    may = ask(platform="android", version="2.1.0").json()
    assert (may["update_required"], may["update_available"], may["store_url"], may["message"]) == (
        False,
        True,
        "https://play.google.com/x",
        "A newer version of Vyterlix is available.",
    )
    current = ask(platform="android", version="2.3.0").json()
    assert (current["update_required"], current["update_available"], current["message"]) == (
        False,
        False,
        None,
    )
    assert ask(platform="ios", version="2.3.1").json()["update_available"] is False
    unknown = ask(platform="ios").json()
    assert (
        unknown["update_required"],
        unknown["update_available"],
        unknown["minimum_version"],
    ) == (False, False, "2.0.0")


def test_versions_are_compared_as_numbers_not_as_text(api, monkeypatch):
    minimum(monkeypatch, minimum="1.9.0", latest="1.10.0")
    ask = lambda v: api.get("/api/v1/app-config", params={"platform": "ios", "version": v}).json()  # noqa: E731
    assert ask("1.10.0")["update_required"] is False and ask("1.10.0")["update_available"] is False
    assert ask("1.9")["update_available"] is True and ask("1.9")["update_required"] is False
    assert ask("1.8.9")["update_required"] is True
    assert ask("2")["update_available"] is False


def test_the_app_config_refuses_nonsense(api):
    assert api.get("/api/v1/app-config").status_code == 422
    assert api.get("/api/v1/app-config", params={"platform": "windows"}).status_code == 422
    for version in ("abc", "1.2.3.4", "1..2", "", "-1", "1.2.x", "12345.0"):
        res = api.get("/api/v1/app-config", params={"platform": "ios", "version": version})
        assert res.status_code == 422, version


def test_a_store_link_is_only_given_when_one_is_set(api, monkeypatch):
    minimum(monkeypatch, ios=None, android=None)
    assert (
        api.get("/api/v1/app-config", params={"platform": "ios", "version": "1.0"}).json()[
            "store_url"
        ]
        is None
    )


# --- the devices a person is signed in on -------------------------------------------------------------------------


def test_a_person_sees_every_device_signed_in_with_this_one_first(api, db, signup, owner):
    api.cookies.clear()
    web = api.post(
        f"{AUTH}/login", json={"email": "owner@acme.co.uk", "password": TEST_PASSWORD}
    ).json()
    phone = phone_login(api, device_name="Jo's iPhone")
    other = signup("someone@else.co.uk")
    res = api.get(f"{ME}/sessions", headers=bearer(phone))
    assert res.status_code == 200, res.text
    rows = res.json()
    assert [(r["client"], r["device_name"], r["current"]) for r in rows[:2]] == [
        ("mobile", "Jo's iPhone", True),
        ("web", None, False),
    ]
    assert len(rows) == 3  # the sign-in made by the signup fixture is there too
    assert set(rows[0]) == {
        "id",
        "client",
        "device_name",
        "user_agent",
        "created_at",
        "last_used_at",
        "expires_at",
        "current",
    }
    assert "refresh" not in str(rows) and sum(r["current"] for r in rows) == 1
    assert api.get(f"{ME}/sessions", headers=bearer(web)).json()[0]["client"] == "web"
    assert api.get(f"{ME}/sessions").status_code == 401
    theirs = api.get(f"{ME}/sessions", headers=other).json()
    assert len(theirs) == 1 and theirs[0]["current"] is True


def test_signed_out_and_expired_devices_are_not_listed(api, db, owner):
    api.cookies.clear()
    phone = phone_login(api, device_name="Phone A")
    old = phone_login(api, device_name="Phone B")
    expired = phone_login(api, device_name="Phone C")
    api.post(f"{AUTH}/logout", json={"refresh_token": old["refresh_token"]})
    db.execute(
        update(UserSession)
        .where(UserSession.device_name == "Phone C")
        .values(expires_at=datetime.now(UTC) - timedelta(days=1))
    )
    names = [r["device_name"] for r in api.get(f"{ME}/sessions", headers=bearer(phone)).json()]
    assert "Phone A" in names and "Phone B" not in names and "Phone C" not in names
    assert expired and api.get(f"{ME}/sessions", headers=bearer(expired)).status_code == 401


def test_one_device_can_be_signed_out_from_another(api, db, owner):
    api.cookies.clear()
    laptop = phone_login(api, device_name="Laptop")
    lost = phone_login(api, device_name="Lost phone")
    lost_id = next(
        r["id"]
        for r in api.get(f"{ME}/sessions", headers=bearer(laptop)).json()
        if r["device_name"] == "Lost phone"
    )
    assert api.delete(f"{ME}/sessions/{lost_id}", headers=bearer(laptop)).status_code == 204
    assert api.get(ME, headers=bearer(lost)).status_code == 401
    assert (
        api.post(f"{AUTH}/refresh", json={"refresh_token": lost["refresh_token"]}).status_code
        == 401
    )
    assert api.get(ME, headers=bearer(laptop)).status_code == 200
    again = api.delete(f"{ME}/sessions/{lost_id}", headers=bearer(laptop))
    assert again.status_code == 404 and again.json()["error"]["code"] == "session_not_found"
    [entry] = db.scalars(select(AuditLog).where(AuditLog.action == "auth.session_revoked")).all()
    assert entry.target_id == lost_id and entry.details == {"this_device": False}


def test_a_device_can_sign_itself_out_and_cannot_touch_someone_elses(api, db, signup, owner):
    api.cookies.clear()
    mine = phone_login(api)
    theirs = signup("someone@else.co.uk")
    their_id = api.get(f"{ME}/sessions", headers=theirs).json()[0]["id"]
    nope = api.delete(f"{ME}/sessions/{their_id}", headers=bearer(mine))
    assert nope.status_code == 404
    assert api.get(f"{ME}/sessions", headers=theirs).status_code == 200  # untouched
    my_id = next(
        r["id"] for r in api.get(f"{ME}/sessions", headers=bearer(mine)).json() if r["current"]
    )
    assert api.delete(f"{ME}/sessions/{my_id}", headers=bearer(mine)).status_code == 204
    assert api.get(ME, headers=bearer(mine)).status_code == 401
    assert db.scalars(
        select(AuditLog).where(AuditLog.action == "auth.session_revoked")
    ).one().details == {"this_device": True}
    assert api.delete(f"{ME}/sessions/{uuid.uuid4()}", headers=theirs).status_code == 404
    assert api.delete(f"{ME}/sessions/not-an-id", headers=theirs).status_code == 422


def test_every_other_device_can_be_signed_out_at_once(api, db, signup, owner):
    api.cookies.clear()
    keep = phone_login(api, device_name="Keep")
    one = phone_login(api, device_name="One")
    two = phone_login(api, device_name="Two")
    other = signup("someone@else.co.uk")
    res = api.post(f"{ME}/sessions/revoke-others", headers=bearer(keep))
    assert res.status_code == 200 and res.json()["sessions_ended"] >= 3
    assert api.get(ME, headers=bearer(keep)).status_code == 200
    assert (
        api.get(ME, headers=bearer(one)).status_code == 401
        and api.get(ME, headers=bearer(two)).status_code == 401
    )
    assert api.get(ME, headers=other).status_code == 200  # another person is unaffected
    assert [r["device_name"] for r in api.get(f"{ME}/sessions", headers=bearer(keep)).json()] == [
        "Keep"
    ]
    again = api.post(f"{ME}/sessions/revoke-others", headers=bearer(keep))
    assert again.json() == {"sessions_ended": 0}
    entries = db.scalars(select(AuditLog).where(AuditLog.action == "auth.sessions_revoked")).all()
    assert (
        len(entries) == 1 and entries[0].details["sessions_ended"] == res.json()["sessions_ended"]
    )


# --- where to send push notifications -----------------------------------------------------------------------------------


def register(api, tokens, token=LONG, platform="ios", **extra):
    return api.post(
        f"{ME}/push-devices",
        json={"token": token, "platform": platform, **extra},
        headers=bearer(tokens),
    )


def test_a_phone_registers_where_to_send_it_notifications_and_repeating_it_changes_nothing(
    api, db, owner
):
    api.cookies.clear()
    phone = phone_login(api)
    res = register(api, phone, app_version="1.2.0")
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["platform"], body["app_version"]) == ("ios", "1.2.0") and set(body) == {
        "id",
        "platform",
        "app_version",
        "created_at",
        "last_seen_at",
    }
    again = register(api, phone, app_version="1.3.0")
    assert again.json()["id"] == body["id"] and again.json()["app_version"] == "1.3.0"
    assert again.json()["last_seen_at"] >= body["last_seen_at"]
    assert len(db.scalars(select(PushDevice)).all()) == 1
    listing = api.get(f"{ME}/push-devices", headers=bearer(phone)).json()
    assert [d["id"] for d in listing] == [body["id"]]


def test_a_push_registration_must_make_sense(api, owner):
    api.cookies.clear()
    phone = phone_login(api)
    assert register(api, phone, token="short").status_code == 422
    assert register(api, phone, token="t" * 4097).status_code == 422
    assert register(api, phone, platform="windows").status_code == 422
    assert register(api, phone, app_version="abc").status_code == 422
    assert register(api, phone, colour="red").status_code == 422
    assert (
        api.post(f"{ME}/push-devices", json={"token": LONG, "platform": "ios"}).status_code == 401
    )
    assert register(api, phone, token="t" * 4096, platform="android").status_code == 200


def test_a_phone_that_changes_hands_stops_notifying_the_first_person(api, db, signup, owner):
    api.cookies.clear()
    first = phone_login(api)
    signup("viewer@acme.co.uk")
    second = phone_login(api, "viewer@acme.co.uk")
    register(api, first)
    assert len(api.get(f"{ME}/push-devices", headers=bearer(first)).json()) == 1
    moved = register(api, second)
    assert moved.status_code == 200
    assert api.get(f"{ME}/push-devices", headers=bearer(first)).json() == []
    assert len(api.get(f"{ME}/push-devices", headers=bearer(second)).json()) == 1
    assert len(db.scalars(select(PushDevice)).all()) == 1


def test_a_person_can_forget_their_own_phone_and_nobody_elses(api, db, signup, owner):
    api.cookies.clear()
    mine = phone_login(api)
    signup("viewer@acme.co.uk")
    theirs = phone_login(api, "viewer@acme.co.uk")
    mine_id = register(api, mine).json()["id"]
    their_id = register(api, theirs, token="u" * 40).json()["id"]
    assert api.delete(f"{ME}/push-devices/{their_id}", headers=bearer(mine)).status_code == 404
    assert api.delete(f"{ME}/push-devices/{mine_id}", headers=bearer(mine)).status_code == 204
    assert api.get(f"{ME}/push-devices", headers=bearer(mine)).json() == []
    again = api.delete(f"{ME}/push-devices/{mine_id}", headers=bearer(mine))
    assert again.status_code == 404 and again.json()["error"]["code"] == "device_not_found"
    assert len(api.get(f"{ME}/push-devices", headers=bearer(theirs)).json()) == 1


def test_only_the_ten_most_recent_phones_are_kept(api, db, owner):
    api.cookies.clear()
    phone = phone_login(api)
    for n in range(12):
        assert register(api, phone, token=f"{n:02d}" + "z" * 38).status_code == 200
    kept = {d.token[:2] for d in db.scalars(select(PushDevice))}
    assert kept == {f"{n:02d}" for n in range(2, 12)}
    assert len(api.get(f"{ME}/push-devices", headers=bearer(phone)).json()) == 10
    register(api, phone, token="02" + "z" * 38)  # using an old one again keeps it
    register(api, phone, token="99" + "z" * 38)
    assert {d.token[:2] for d in db.scalars(select(PushDevice))} == {"02", "99"} | {
        f"{n:02d}" for n in range(4, 12)
    }


def test_a_phone_is_no_longer_reachable_once_its_session_ends(api, db, owner):
    api.cookies.clear()
    phone = phone_login(api)
    register(api, phone)
    user_id = db.scalars(select(User.id).where(User.email == "owner@acme.co.uk")).one()
    assert mobile.reachable_tokens(db, user_id) == [("ios", LONG)]
    other = phone_login(api)
    register(api, other, token="v" * 40, platform="android")
    assert mobile.reachable_tokens(db, user_id) == [("android", "v" * 40), ("ios", LONG)]
    api.post(f"{AUTH}/logout", json={"refresh_token": phone["refresh_token"]})
    assert mobile.reachable_tokens(db, user_id) == [("android", "v" * 40)]
    assert api.get(f"{ME}/push-devices", headers=bearer(other)).json()[0]["platform"] == "android"
    db.execute(update(UserSession).values(expires_at=datetime.now(UTC) - timedelta(days=1)))
    assert mobile.reachable_tokens(db, user_id) == []
    assert mobile.reachable_tokens(db, uuid.uuid4()) == []


def test_changing_the_password_signs_out_every_other_phone_too(api, db, owner):
    api.cookies.clear()
    stays = phone_login(api)
    goes = phone_login(api)
    res = api.post(
        f"{ME}/change-password",
        json={"current_password": TEST_PASSWORD, "new_password": "a brand new passphrase"},
        headers=bearer(stays),
    )
    assert res.status_code == 200, res.text
    assert api.get(ME, headers=bearer(goes)).status_code == 401
    assert api.get(ME, headers=bearer(stays)).status_code == 200


def test_a_session_must_be_a_web_or_a_mobile_one_and_a_push_address_is_unique(db, owner):
    from sqlalchemy.exc import IntegrityError

    uid = db.scalars(select(User.id).where(User.email == "owner@acme.co.uk")).one()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(
            UserSession(
                user_id=uid,
                refresh_token_hash="h" * 64,
                client="tablet",
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        db.flush()
    db.add(PushDevice(user_id=uid, platform="ios", token="w" * 30))
    db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(PushDevice(user_id=uid, platform="android", token="w" * 30))
        db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(PushDevice(user_id=uid, platform="symbian", token="x" * 30))
        db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(PushDevice(user_id=uid, platform="ios", token="short"))
        db.flush()
