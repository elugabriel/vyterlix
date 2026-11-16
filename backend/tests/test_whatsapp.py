# ruff: noqa: E501, F811
"""WhatsApp: connecting, the notices WhatsApp sends us, and the reply-speed figures."""

import hashlib
import hmac
import json
from datetime import UTC, date, datetime
from decimal import Decimal as D

import httpx
import pytest
from sqlalchemy import select

from app.integrations.base import (
    AccountInfo,
    ProviderRejected,
    ProviderUnavailable,
    ReauthRequired,
    SyncContext,
    TokenSet,
)
from app.integrations.registry import PROVIDERS
from app.integrations.whatsapp import WhatsAppProvider, verify_token
from app.kpi.measures import MEASURES
from app.models.data import CustomerMessage
from app.models.integrations import Integration
from tests.test_booking_figures import kpi_codes
from tests.test_kpi_customers_stock import value
from tests.test_kpi_engine import run, scoped

ORGS = "/api/v1/organizations"
PHONE = "109876543210987"
SECRET = "app-secret-123"
VALUES = {"phone_number_id": PHONE, "access_token": "EAAtoken", "app_secret": SECRET}
CODES = {
    "messages_received", "conversations_started", "replied_within_hour_pct",
    "average_reply_minutes", "unanswered_conversations",
}  # fmt: skip
NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def graph(status=200, body=None):
    """A pretend Meta. `seen` collects the requests."""
    seen = []

    def handler(request):
        seen.append(request)
        if status != 200:
            return httpx.Response(status, json=body if body is not None else {})
        return httpx.Response(
            200, json={"display_phone_number": "+44 7700 900123", "verified_name": "Glow Salon"}
        )

    provider = WhatsAppProvider()
    provider.transport = httpx.MockTransport(handler)
    provider.seen = seen
    return provider


def tokens():
    return TokenSet("EAAtoken", SECRET, extra={"phone_number_id": PHONE})


def ctx():
    return SyncContext(
        tokens=tokens(), account=AccountInfo(PHONE, "Glow Salon"), cursor={"figures_queued_at": "x"},
        settings={}, progress=lambda done, total: None, now=NOW,
    )  # fmt: skip


# --- what it is --------------------------------------------------------------------------------------------


def test_it_is_always_available_never_sends_and_asks_for_three_things():
    provider = WhatsAppProvider()
    assert provider.is_configured(None) and "whatsapp" in PROVIDERS
    assert [f.name for f in provider.connect_fields] == [
        "phone_number_id",
        "access_token",
        "app_secret",
    ]
    assert [f.secret for f in provider.connect_fields] == [False, True, True]
    text = " ".join(provider.permissions)
    assert "never reads what your messages say" in text and "never sends a message" in text


def test_it_has_no_sign_in_page():
    with pytest.raises(ProviderRejected):
        WhatsAppProvider().authorize_url(state="s", redirect_uri="x", code_challenge="c")
    with pytest.raises(ReauthRequired):
        WhatsAppProvider().refresh(tokens())


# --- connecting ----------------------------------------------------------------------------------------------


def test_good_details_connect_and_the_business_name_is_read_from_whatsapp():
    provider = graph()
    got = provider.connect_with_keys(VALUES)
    assert (got.access_token, got.refresh_token, got.extra) == (
        "EAAtoken",
        SECRET,
        {"phone_number_id": PHONE},
    )
    info = provider.account(got)
    assert (info.external_id, info.name) == (PHONE, "Glow Salon")
    assert info.settings["display_phone_number"] == "+44 7700 900123"
    call = provider.seen[0]
    assert (
        call.method == "GET"
        and call.headers["authorization"] == "Bearer EAAtoken"
        and call.url.host == "graph.facebook.com"
    )
    assert PHONE in call.url.path


@pytest.mark.parametrize("typed", ["", "07700 900123", "+447700900123", "abc", "123"])
def test_a_phone_number_instead_of_the_phone_number_id_is_refused_plainly(typed):
    with pytest.raises(ProviderRejected) as e:
        graph().connect_with_keys({**VALUES, "phone_number_id": typed})
    assert e.value.code == "bad_phone_number_id" and "not your phone number" in e.value.message


def test_a_token_whatsapp_does_not_accept_says_so_plainly():
    for provider in (graph(401), graph(403), graph(400, {"error": {"code": 190}})):
        with pytest.raises(ReauthRequired) as e:
            provider.connect_with_keys(VALUES)
        assert "did not accept the access token" in e.value.message


def test_an_unknown_number_is_said_plainly():
    with pytest.raises(ProviderRejected) as e:
        graph(400, {"error": {"code": 100}}).connect_with_keys(VALUES)
    assert e.value.code == "not_found"
    with pytest.raises(ProviderRejected):
        graph(404).connect_with_keys(VALUES)


def test_a_busy_or_unreachable_whatsapp_is_tried_again_later():
    for status in (429, 503):
        with pytest.raises(ProviderUnavailable):
            graph(status).connect_with_keys(VALUES)

    def down(request):
        raise httpx.ConnectError("no route")

    provider = WhatsAppProvider()
    provider.transport = httpx.MockTransport(down)
    with pytest.raises(ProviderUnavailable):
        provider.connect_with_keys(VALUES)


def test_the_check_word_is_stable_depends_on_the_secret_and_does_not_reveal_it():
    word = verify_token(SECRET, PHONE)
    assert word == verify_token(SECRET, PHONE) and len(word) == 24
    assert word != verify_token("another-secret", PHONE) and word != verify_token(SECRET, "1" * 15)
    assert SECRET not in word
    info = graph().account(tokens())
    assert info.settings["verify_token"] == word and SECRET not in json.dumps(info.settings)


def test_the_setup_steps_give_the_address_and_the_check_word():
    from uuid import uuid4

    some = uuid4()
    notes = WhatsAppProvider().setup_notes(some, {"verify_token": "abc123"})
    joined = "\n".join(notes)
    assert f"/api/v1/webhooks/whatsapp/{some}" in joined and "Verify token: abc123" in joined
    assert "Past messages cannot be fetched" in joined


def test_update_now_only_checks_the_token_still_works():
    provider = graph()
    out = provider.sync(ctx())
    assert out.datasets == {} and out.records_fetched == 0
    assert (
        out.cursor["checked"] == "2026-10-20T09:00:00Z" and out.cursor["figures_queued_at"] == "x"
    )
    with pytest.raises(ReauthRequired):
        graph(401).sync(ctx())


# --- the figures ---------------------------------------------------------------------------------------------


def message(db, business, who, when, direction):
    with scoped(db, business):
        db.add(
            CustomerMessage(
                sent_at=when, direction=direction, contact_ref=who, source="whatsapp",
                source_ref=f"{who}-{when.isoformat()}-{direction}",
            )
        )  # fmt: skip
        db.flush()


def at(day, hour, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


@pytest.fixture
def chatting(db, business):
    """September 2026 by hand (UTC times; the UK is an hour ahead, which changes no day here).

    A writes at 09:00 and is answered at 09:20, then writes again at 09:25 (the same conversation).
    B writes on the 2nd and is answered 90 minutes later. C writes on the 3rd and is never answered.
    D writes on the 5th and is answered in 10 minutes, then writes again two days later and is never
    answered (a new conversation). E writes at 18:00 on the 20th, the latest message on record, so it
    has not had time to be called unanswered.

    7 messages from customers; 6 new conversations; 5 old enough to have been answered; 2 answered
    within the hour (A, D); 3 answered at all (A, B, D) after 20 + 90 + 10 = 120 minutes, so 40 on
    average; 2 unanswered (C, and D's second): 2 of 5 answered quickly is 40%.
    """
    for who, when, way in [
        ("A", at(1, 9), "in"), ("A", at(1, 9, 20), "out"), ("A", at(1, 9, 25), "in"),
        ("B", at(2, 10), "in"), ("B", at(2, 11, 30), "out"),
        ("C", at(3, 12), "in"),
        ("D", at(5, 8), "in"), ("D", at(5, 8, 10), "out"), ("D", at(7, 8), "in"),
        ("E", at(20, 18), "in"),
    ]:  # fmt: skip
        message(db, business, who, when, way)
    run(db, business, first=date(2026, 9, 1), today=date(2026, 11, 15))
    return business


def sept(api, business, code):
    return D(value(api, business, code, "2026-09-01"))


def test_the_message_figures_match_the_hand_worked_numbers(api, chatting):
    assert sept(api, chatting, "messages_received") == 7
    assert sept(api, chatting, "conversations_started") == 6
    assert sept(api, chatting, "replied_within_hour_pct") == D("40")
    assert sept(api, chatting, "average_reply_minutes") == 40
    assert sept(api, chatting, "unanswered_conversations") == 2


def test_the_message_figures_are_shown_when_there_are_messages_and_belong_to_operations(
    api, chatting
):
    from app.modules.rules import module_of

    assert CODES <= kpi_codes(api, chatting)
    assert {module_of(code) for code in CODES} == {"operations"}
    body = api.get(f"{ORGS}/{chatting[0]}/kpis", headers=chatting[2]["owner"]).json()
    assert "operations" in {m["code"] for m in body["modules"]}


def test_without_messages_the_message_figures_are_not_shown(api, business):
    assert not CODES & kpi_codes(api, business)


def test_a_message_just_after_midnight_in_the_uk_counts_on_the_uk_day(db, business):
    # 23:30 UTC on 30 September is 00:30 on 1 October in the UK (summer time).
    message(db, business, "Z", datetime(2026, 9, 30, 23, 30, tzinfo=UTC), "in")
    with scoped(db, business):
        sept_ = MEASURES["messages_received"].compute(
            db, "month", date(2026, 9, 1), date(2026, 9, 30)
        )
        octo = MEASURES["messages_received"].compute(
            db, "month", date(2026, 10, 1), date(2026, 10, 31)
        )
    assert sept_ == {} and octo == {date(2026, 10, 1): D(1)}


def test_a_conversation_that_began_before_the_period_is_not_started_again_in_it(db, business):
    message(db, business, "Y", at(30, 20), "in")  # 30 September evening
    message(
        db, business, "Y", datetime(2026, 10, 1, 7, 0, tzinfo=UTC), "in"
    )  # next morning: same conversation
    with scoped(db, business):
        octo = MEASURES["conversations_started"].compute(
            db, "month", date(2026, 10, 1), date(2026, 10, 31)
        )
    assert octo == {}


def test_a_reply_after_more_than_a_day_does_not_count_as_a_reply(db, business):
    message(db, business, "X", at(1, 9), "in")
    message(db, business, "X", at(2, 10), "out")  # 25 hours later
    message(db, business, "W", at(15, 9), "in")  # makes the 1st old enough to judge
    with scoped(db, business):
        replied = MEASURES["replied_starts"].compute(
            db, "month", date(2026, 9, 1), date(2026, 9, 30)
        )
        silent = MEASURES["unanswered_starts"].compute(
            db, "month", date(2026, 9, 1), date(2026, 9, 30)
        )
    assert replied == {} and silent == {date(2026, 9, 1): D(1)}


# --- notices from WhatsApp -------------------------------------------------------------------------------------


def stamp(day, hour, minute=0):
    return str(int(at(day, hour, minute).timestamp()))


def notice(*, inbound=(), sent=(), echoes=(), other=(), phone=PHONE):
    value = {
        "metadata": {"display_phone_number": "447700900123", "phone_number_id": phone},
        "messages": [
            {
                "from": number,
                "id": wamid,
                "timestamp": ts,
                "type": "text",
                "text": {"body": "my private words"},
            }
            for number, wamid, ts in inbound
        ],
        "statuses": [
            {"id": wamid, "status": "sent", "timestamp": ts, "recipient_id": number}
            for number, wamid, ts in sent
        ]
        + [
            {"id": wamid, "status": status, "timestamp": ts, "recipient_id": number}
            for number, wamid, ts, status in other
        ],
        "message_echoes": [
            {"from": "447700900123", "to": number, "id": wamid, "timestamp": ts}
            for number, wamid, ts in echoes
        ],
    }
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [{"id": "1", "changes": [{"field": "messages", "value": value}]}],
        }
    ).encode()


def sign(body, secret=SECRET):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def number(api, db, business, monkeypatch):
    """A connected WhatsApp number: returns (integration id, its webhook path)."""
    from tests.test_integrations import auth, url

    monkeypatch.setitem(PROVIDERS, "whatsapp", graph())
    res = api.post(
        url(business, "/connect-keys"),
        json={"provider": "whatsapp", "values": VALUES},
        headers=auth(business),
    )
    assert res.status_code == 200, res.text
    conn = res.json()
    return conn["id"], f"/api/v1/webhooks/whatsapp/{conn['id']}", conn


def post(api, path, body, signature="good"):
    headers = {"content-type": "application/json"}
    if signature == "good":
        headers["x-hub-signature-256"] = sign(body)
    elif signature:
        headers["x-hub-signature-256"] = signature
    return api.post(path, content=body, headers=headers)


def stored(db, business):
    with scoped(db, business):
        return list(
            db.scalars(
                select(CustomerMessage).order_by(
                    CustomerMessage.sent_at, CustomerMessage.source_ref
                )
            )
        )


def test_connecting_shows_the_owner_the_address_and_check_word_to_put_into_their_meta_app(number):
    integration_id, path, conn = number
    notes = "\n".join(conn["setup_notes"])
    assert f"Callback URL: http://localhost:8000{path}" in notes
    assert f"Verify token: {verify_token(SECRET, PHONE)}" in notes
    assert SECRET not in json.dumps(conn) and "EAAtoken" not in json.dumps(conn)


def test_whatsapp_checks_the_address_and_is_answered_only_with_the_right_check_word(api, number):
    _, path, _ = number
    ok = api.get(
        path,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": verify_token(SECRET, PHONE),
            "hub.challenge": "1158201444",
        },
    )
    assert ok.status_code == 200 and ok.text == "1158201444"
    for params in (
        {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"},
        {
            "hub.mode": "unsubscribe",
            "hub.verify_token": verify_token(SECRET, PHONE),
            "hub.challenge": "1",
        },
        {},
    ):
        assert api.get(path, params=params).status_code == 403


def test_an_address_that_belongs_to_no_connection_is_not_found(api, number):
    from uuid import uuid4

    res = api.get(
        f"/api/v1/webhooks/whatsapp/{uuid4()}",
        params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "1"},
    )
    assert res.status_code == 404
    assert post(api, f"/api/v1/webhooks/whatsapp/{uuid4()}", b"{}").status_code == 404


def test_signed_messages_are_stored_as_when_which_way_and_a_one_way_code_and_nothing_else(
    api, db, business, number
):
    _, path, _ = number
    body = notice(
        inbound=[("447700900111", "wamid.IN1", stamp(1, 9))],
        sent=[("447700900111", "wamid.OUT1", stamp(1, 9, 20))],
        echoes=[("447700900222", "wamid.ECHO1", stamp(2, 10))],
    )
    res = post(api, path, body)
    assert res.status_code == 200 and res.json() == {"received": 3, "added": 3}
    rows = stored(db, business)
    assert [(r.direction, r.source, r.source_ref) for r in rows] == [
        ("in", "whatsapp", "wamid.IN1"), ("out", "whatsapp", "wamid.OUT1"), ("out", "whatsapp", "wamid.ECHO1"),
    ]  # fmt: skip
    assert str(rows[0].organization_id) == business[0]
    dumped = json.dumps(
        [[str(getattr(r, c.name)) for c in CustomerMessage.__table__.columns] for r in rows]
    )
    assert "447700900111" not in dumped and "my private words" not in dumped
    assert (
        rows[0].contact_ref == rows[1].contact_ref != rows[2].contact_ref
    )  # the same person is the same code
    assert len(rows[0].contact_ref) == 64


def test_the_same_notice_twice_stores_each_message_once(api, db, business, number):
    _, path, _ = number
    body = notice(
        inbound=[("447700900111", "wamid.IN1", stamp(1, 9))],
        sent=[("447700900111", "wamid.OUT1", stamp(1, 9, 20))],
    )
    assert post(api, path, body).json() == {"received": 2, "added": 2}
    assert post(api, path, body).json() == {"received": 2, "added": 0}
    assert len(stored(db, business)) == 2


def test_delivered_and_read_updates_are_not_more_messages(api, db, business, number):
    _, path, _ = number
    body = notice(
        sent=[("447700900111", "wamid.OUT1", stamp(1, 9, 20))],
        other=[
            ("447700900111", "wamid.OUT1", stamp(1, 9, 21), "delivered"),
            ("447700900111", "wamid.OUT1", stamp(1, 9, 25), "read"),
        ],
    )
    assert post(api, path, body).json() == {"received": 1, "added": 1}


def test_a_notice_for_another_number_is_ignored(api, db, business, number):
    _, path, _ = number
    body = notice(inbound=[("447700900111", "wamid.IN1", stamp(1, 9))], phone="999999999999999")
    assert post(api, path, body).json() == {"received": 0, "added": 0}
    assert stored(db, business) == []


def test_a_message_without_a_sender_or_time_is_skipped(api, db, business, number):
    _, path, _ = number
    body = notice(
        inbound=[
            ("", "wamid.A", stamp(1, 9)),
            ("447700900111", "wamid.B", "soon"),
            ("447700900111", "wamid.C", stamp(1, 9)),
        ]
    )
    assert post(api, path, body).json() == {"received": 1, "added": 1}


def test_a_notice_that_is_not_signed_correctly_is_refused_before_it_is_read(
    api, db, business, number
):
    _, path, _ = number
    body = notice(inbound=[("447700900111", "wamid.IN1", stamp(1, 9))])
    for signature in (
        None,
        "",
        "sha256=deadbeef",
        sign(body, "another-secret"),
        sign(body).replace("sha256=", ""),
    ):
        res = post(api, path, body, signature=signature)
        assert res.status_code == 403 and res.json()["error"]["code"] == "bad_signature", signature
    assert post(api, path, b"not json at all", signature=sign(b"something else")).status_code == 403
    assert stored(db, business) == []


def test_a_signed_notice_that_is_not_json_is_a_bad_message(api, number):
    _, path, _ = number
    body = b"definitely not json"
    res = post(api, path, body, signature=sign(body))
    assert res.status_code == 400 and res.json()["error"]["code"] == "bad_message"
    body = b"[1, 2]"
    assert post(api, path, body, signature=sign(body)).status_code == 400


def test_a_huge_notice_is_refused(api, number):
    _, path, _ = number
    res = post(api, path, b"x" * 1_000_001, signature="sha256=00")
    assert res.status_code == 413


def test_a_disconnected_number_hears_nothing(api, db, business, number):
    from tests.test_integrations import auth, url

    integration_id, path, _ = number
    assert (
        api.post(url(business, f"/{integration_id}/disconnect"), headers=auth(business)).status_code
        == 200
    )
    body = notice(inbound=[("447700900111", "wamid.IN1", stamp(1, 9))])
    assert post(api, path, body).status_code == 404
    assert stored(db, business) == []


def test_new_messages_queue_the_figures_to_be_worked_out_again_and_the_connection_shows_it_heard(
    api, db, business, storage, number
):
    from tests.test_integrations import auth, run_worker, url

    integration_id, path, _ = number
    post(api, path, notice(inbound=[("447700900111", "wamid.IN1", stamp(1, 9))]))
    job = run_worker(db, storage)
    assert job.kind == "kpi.calculate" and job.status == "succeeded"
    listed = api.get(url(business, ""), headers=auth(business)).json()
    mine = next(c for c in listed if c["id"] == integration_id)
    assert mine["last_successful_sync_at"] is not None and mine["last_sync_status"] == "succeeded"
    with scoped(db, business):
        saved = db.get(Integration, __import__("uuid").UUID(integration_id))
        assert "figures_queued_at" in saved.sync_cursor


def test_a_second_notice_straight_after_does_not_queue_the_figures_again(
    api, db, business, storage, number
):
    from tests.test_integrations import run_worker

    _, path, _ = number
    post(api, path, notice(inbound=[("447700900111", "wamid.IN1", stamp(1, 9))]))
    assert run_worker(db, storage).kind == "kpi.calculate"
    post(api, path, notice(inbound=[("447700900111", "wamid.IN2", stamp(1, 10))]))
    assert run_worker(db, storage) is None


def test_connecting_with_the_wrong_details_is_refused_plainly_and_nothing_is_saved(
    api, db, business, monkeypatch
):
    from tests.test_integrations import auth, url

    monkeypatch.setitem(PROVIDERS, "whatsapp", graph(401))
    res = api.post(
        url(business, "/connect-keys"),
        json={"provider": "whatsapp", "values": VALUES},
        headers=auth(business),
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "reauth_required"
    res = api.post(
        url(business, "/connect-keys"),
        json={"provider": "whatsapp", "values": {"phone_number_id": PHONE}},
        headers=auth(business),
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "missing_fields"
    assert "App secret" in res.json()["error"]["message"]
    with scoped(db, business):
        assert db.scalars(select(Integration)).first() is None


def test_the_secret_and_token_are_stored_encrypted(api, db, business, number):
    integration_id, _, _ = number
    with scoped(db, business):
        saved = db.scalars(select(Integration)).one()
        stored_creds = (
            saved.credentials
            if isinstance(saved.credentials, bytes)
            else bytes(saved.credentials, "utf8")
        )
        assert SECRET.encode() not in stored_creds and b"EAAtoken" not in stored_creds
        assert SECRET not in json.dumps(saved.settings)


def test_only_the_owner_can_connect_a_number(api, business, monkeypatch):
    from tests.test_integrations import url

    monkeypatch.setitem(PROVIDERS, "whatsapp", graph())
    res = api.post(url(business, "/connect-keys"), json={"provider": "whatsapp", "values": VALUES})
    assert res.status_code in (401, 403)


def test_messages_from_one_business_never_appear_in_another(api, db, business, number):
    _, path, _ = number
    post(api, path, notice(inbound=[("447700900111", "wamid.IN1", stamp(1, 9))]))
    assert len(stored(db, business)) == 1
    with scoped(db, business, 1):
        assert db.scalars(select(CustomerMessage)).first() is None


def test_a_quick_reply_in_the_latest_hour_on_record_never_makes_the_rate_go_over_a_hundred(
    db, business
):
    message(db, business, "V", at(10, 9), "in")
    message(db, business, "V", at(10, 9, 20), "out")  # the latest message on record
    with scoped(db, business):
        args = ("month", date(2026, 9, 1), date(2026, 9, 30))
        quick = MEASURES["replied_within_hour"].compute(db, *args)
        total = MEASURES["answerable_starts"].compute(db, *args)
    assert quick == {date(2026, 9, 1): D(1)} and total == {date(2026, 9, 1): D(1)}
