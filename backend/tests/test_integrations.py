"""Connections to other systems: the connect flow, encrypted credentials, token refresh, sync
outcomes, re-authentication, disconnecting, and keeping businesses apart."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import DEV_ENCRYPTION_KEY, Settings, get_settings
from app.db.tenant import ACROSS_TENANTS
from app.integrations.base import (
    AccountInfo,
    Provider,
    ProviderRejected,
    ProviderUnavailable,
    ReauthRequired,
    SyncOutcome,
    TokenSet,
    utcnow,
)
from app.integrations.registry import PROVIDERS
from app.models.identity import AuditLog, OrganizationUser, Role, User
from app.models.integrations import Integration, IntegrationOAuthState, IntegrationSync
from app.models.jobs import Job
from app.services import crypto, jobs

ORGS = "/api/v1/organizations"


# --- a provider we control ---------------------------------------------------------------------


class FakeProvider(Provider):
    key = "fake"
    label = "Fake Books"
    scopes = ("read",)
    permissions = ("Read your invoices",)

    def __init__(self):
        self.calls: list = []
        self.account_id, self.account_name = "acct-1", "Acme Ltd"
        self.expires_in = timedelta(hours=1)
        self.exchange_error = self.refresh_error = self.sync_error = self.revoke_error = None
        self.cursors: list = []
        self.n = 0

    def authorize_url(self, *, state, redirect_uri, code_challenge):
        self.calls.append(("authorize", code_challenge))
        return f"{redirect_uri}?code=fake-code&state={state}"

    def exchange_code(self, *, code, redirect_uri, code_verifier):
        self.calls.append(("exchange", code, code_verifier))
        if self.exchange_error:
            raise self.exchange_error
        self.n += 1
        return TokenSet(f"access-{self.n}", "refresh-1", utcnow() + self.expires_in, ("read",))

    def refresh(self, tokens):
        self.calls.append(("refresh", tokens.refresh_token))
        if self.refresh_error:
            raise self.refresh_error
        self.n += 1
        return TokenSet(f"access-{self.n}", None, utcnow() + timedelta(hours=1), ("read",))

    def account(self, tokens):
        return AccountInfo(self.account_id, self.account_name, {"region": "UK"})

    def revoke(self, tokens):
        self.calls.append(("revoke", tokens.access_token))
        if self.revoke_error:
            raise self.revoke_error

    def sync(self, ctx):
        self.calls.append(("sync", ctx.tokens.access_token))
        self.cursors.append(ctx.cursor)
        if self.sync_error:
            raise self.sync_error
        ctx.progress(1, 2)
        ctx.progress(2, 2)
        return SyncOutcome(records_fetched=5, records_created=2, cursor={"page": 2})

    def names(self) -> list[str]:
        return [c if isinstance(c, str) else c[0] for c in self.calls]


@pytest.fixture
def fake(monkeypatch) -> FakeProvider:
    provider = FakeProvider()
    monkeypatch.setitem(PROVIDERS, "fake", provider)
    return provider


# --- helpers -----------------------------------------------------------------------------------


def url(business, path="", org=0):
    return f"{ORGS}/{business[org]}/integrations{path}"


def auth(business, who="owner"):
    return business[2][who]


def start(api, business, provider="fake", who="owner", integration_id=None, org=0):
    body = {"provider": provider}
    if integration_id:
        body["integration_id"] = integration_id
    return api.post(url(business, "/connect", org), json=body, headers=auth(business, who))


def approve(api, business, provider="fake", who="owner", integration_id=None, org=0):
    """Run the whole connect flow; returns the callback response."""
    res = start(api, business, provider, who, integration_id, org)
    assert res.status_code == 200, res.text
    query = parse_qs(urlparse(res.json()["authorize_url"]).query)
    return api.post(
        url(business, "/callback", org),
        json={"state": query["state"][0], "code": query["code"][0]},
        headers=auth(business, who),
    )


def connected(api, business, **kw) -> dict:
    res = approve(api, business, **kw)
    assert res.status_code == 200, res.text
    return res.json()


def run_worker(db, storage):
    return jobs.work_once(db, "test-worker", storage=storage)


def request_sync(api, business, integration_id, who="owner", org=0):
    return api.post(url(business, f"/{integration_id}/sync", org), headers=auth(business, who))


def row(db, integration_id) -> Integration:
    return db.scalars(
        select(Integration)
        .where(Integration.id == uuid.UUID(str(integration_id)))
        .execution_options(populate_existing=True, **ACROSS_TENANTS)
    ).one()


def make_due(db):
    db.execute(
        update(Job)
        .where(Job.status == "queued")
        .values(run_after=datetime.now(UTC) - timedelta(seconds=1))
        .execution_options(**ACROSS_TENANTS)
    )


def audit_actions(db):
    return [a for (a,) in db.execute(select(AuditLog.action).order_by(AuditLog.created_at))]


@pytest.fixture
def manager(api, db, signup, business):
    """A Manager of Acme (can sync, cannot connect or disconnect)."""
    headers = signup("manager@acme.co.uk")
    db.add(
        OrganizationUser(
            organization_id=uuid.UUID(business[0]),
            user_id=db.scalars(select(User.id).where(User.email == "manager@acme.co.uk")).one(),
            role_id=db.scalars(
                select(Role.id).where(Role.code == "manager", Role.organization_id.is_(None))
            ).one(),
        )
    )
    db.flush()
    business[2]["manager"] = headers
    return headers


# --- encryption --------------------------------------------------------------------------------


def test_encrypted_values_round_trip_and_are_not_readable():
    token = crypto.encrypt_json({"access_token": "super-secret"})
    assert "super-secret" not in token
    assert crypto.decrypt_json(token) == {"access_token": "super-secret"}


def test_a_tampered_or_foreign_value_cannot_be_decrypted():
    token = crypto.encrypt_text("hello")
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_text(token[:-4] + "AAAA")
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_text("not-a-token")


def test_old_keys_still_decrypt_and_reencrypt_moves_to_the_new_key(monkeypatch):
    from cryptography.fernet import Fernet

    old = get_settings().encryption_key
    token = crypto.encrypt_text("keep me")
    new_key = Fernet.generate_key().decode()
    monkeypatch.setattr(get_settings(), "encryption_key", new_key)
    monkeypatch.setattr(get_settings(), "previous_encryption_keys", [old])
    assert crypto.decrypt_text(token) == "keep me"  # old key still works
    moved = crypto.reencrypt(token)
    monkeypatch.setattr(get_settings(), "previous_encryption_keys", [])
    assert crypto.decrypt_text(moved) == "keep me"  # now readable with the new key alone
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_text(token)  # the old value needs the old key


def test_the_public_dev_key_is_refused_in_staging_and_production():
    base = {"jwt_secret": "x" * 40, "cors_origins": ["https://app.vyterlix.com"]}
    for env in ("staging", "prod"):
        with pytest.raises(ValueError, match="ENCRYPTION_KEY"):
            Settings(env=env, encryption_key=DEV_ENCRYPTION_KEY, **base, _env_file=None)
    from cryptography.fernet import Fernet

    Settings(env="staging", encryption_key=Fernet.generate_key().decode(), **base, _env_file=None)


# --- who may do what ---------------------------------------------------------------------------


def test_providers_list_shows_what_would_be_read(api, business, fake):
    res = api.get(url(business, "/providers"), headers=auth(business))
    assert res.status_code == 200
    by_key = {p["key"]: p for p in res.json()}
    assert by_key["fake"]["permissions"] == ["Read your invoices"]
    assert by_key["fake"]["read_only"] is True
    assert "sandbox" in by_key  # available in the test environment


def test_viewers_see_nothing_and_do_nothing(api, business, fake):
    for path in ("", "/providers"):
        assert api.get(url(business, path), headers=auth(business, "viewer")).status_code == 403
    assert start(api, business, who="viewer").status_code == 403


def test_managers_cannot_see_connect_sync_or_disconnect(api, db, business, manager, fake):
    owned = connected(api, business)
    manager_auth = auth(business, "manager")
    assert api.get(url(business), headers=manager_auth).status_code == 403
    assert start(api, business, who="manager").status_code == 403
    cb = {"state": "x" * 20, "code": "c"}
    assert api.post(url(business, "/callback"), json=cb, headers=manager_auth).status_code == 403
    disconnect_url = url(business, f"/{owned['id']}/disconnect")
    assert api.post(disconnect_url, headers=manager_auth).status_code == 403
    assert request_sync(api, business, owned["id"], who="manager").status_code == 403


def test_an_unknown_provider_is_refused(api, business):
    res = start(api, business, provider="nonsense")
    assert res.status_code == 422 and res.json()["error"]["code"] == "unknown_provider"


# --- connecting ----------------------------------------------------------------------------------


def test_connecting_saves_an_encrypted_connection_and_shows_no_secrets(api, db, business, fake):
    res = start(api, business)
    assert res.status_code == 200 and res.json()["expires_in_minutes"] == 10
    state = parse_qs(urlparse(res.json()["authorize_url"]).query)["state"][0]
    stored = db.scalars(select(IntegrationOAuthState).execution_options(**ACROSS_TENANTS)).one()
    assert state not in stored.state_hash and len(stored.state_hash) == 64  # only a hash is kept
    # PKCE: the provider got a challenge, and later the matching verifier
    challenge = fake.calls[0][1]
    assert challenge and "=" not in challenge

    done = approve(api, business)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == "connected" and body["display_name"] == "Fake Books · Acme Ltd"
    assert body["permissions"] == ["Read your invoices"]
    assert fake.names()[-1] == "exchange" and len(fake.calls[-1][2]) > 40  # verifier was sent

    blob = json.dumps(body)
    for secret in ("access-1", "refresh-1", "credentials", "token"):
        assert secret not in blob  # nothing secret in what the browser sees
    saved = row(db, body["id"])
    assert "access-1" not in saved.credentials and "refresh-1" not in saved.credentials
    assert crypto.decrypt_json(saved.credentials)["access_token"] == "access-1"
    assert saved.granted_scopes == ["read"] and saved.settings == {"region": "UK"}
    assert saved.connected_at is not None


def test_connecting_is_recorded_in_the_audit_log_without_secrets(api, db, business, fake):
    connected(api, business)
    entries = db.scalars(select(AuditLog).where(AuditLog.action == "integration.connected")).all()
    assert len(entries) == 1
    assert "access-1" not in json.dumps(entries[0].details)
    assert entries[0].details == {"provider": "fake", "account": "Acme Ltd"}


def test_the_sign_in_link_is_single_use(api, business, fake):
    res = start(api, business)
    query = parse_qs(urlparse(res.json()["authorize_url"]).query)
    body = {"state": query["state"][0], "code": "fake-code"}
    assert (
        api.post(url(business, "/callback"), json=body, headers=auth(business)).status_code == 200
    )
    again = api.post(url(business, "/callback"), json=body, headers=auth(business))
    assert again.status_code == 400 and again.json()["error"]["code"] == "invalid_state"


def test_an_unknown_or_expired_sign_in_is_refused(api, db, business, fake):
    bad = api.post(
        url(business, "/callback"), json={"state": "x" * 30, "code": "c"}, headers=auth(business)
    )
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_state"

    res = start(api, business)
    state = parse_qs(urlparse(res.json()["authorize_url"]).query)["state"][0]
    db.execute(
        update(IntegrationOAuthState)
        .values(expires_at=utcnow() - timedelta(minutes=1))
        .execution_options(**ACROSS_TENANTS)
    )
    late = api.post(
        url(business, "/callback"),
        json={"state": state, "code": "fake-code"},
        headers=auth(business),
    )
    assert late.status_code == 400 and late.json()["error"]["code"] == "invalid_state"


def test_a_sign_in_started_for_one_business_cannot_be_finished_in_another(api, business, fake):
    state = parse_qs(urlparse(start(api, business).json()["authorize_url"]).query)["state"][0]
    theirs = api.post(
        url(business, "/callback", org=1),
        json={"state": state, "code": "fake-code"},
        headers=auth(business, "other"),
    )
    assert theirs.status_code == 400 and theirs.json()["error"]["code"] == "invalid_state"
    assert api.get(url(business, org=1), headers=auth(business, "other")).json() == []


def test_a_sign_in_cannot_be_finished_by_a_different_person(api, db, business, manager, fake):
    # An Owner of the same business starts; someone else with the permission cannot finish it.
    db.execute(
        update(OrganizationUser)
        .where(
            OrganizationUser.user_id
            == db.scalars(select(User.id).where(User.email == "manager@acme.co.uk")).one()
        )
        .values(
            role_id=db.scalars(
                select(Role.id).where(Role.code == "owner", Role.organization_id.is_(None))
            ).one()
        )
        .execution_options(**ACROSS_TENANTS)
    )
    state = parse_qs(urlparse(start(api, business).json()["authorize_url"]).query)["state"][0]
    res = api.post(
        url(business, "/callback"),
        json={"state": state, "code": "fake-code"},
        headers=auth(business, "manager"),
    )
    assert res.status_code == 400 and res.json()["error"]["code"] == "invalid_state"


def test_a_failed_code_exchange_gives_a_plain_message_and_burns_the_state(api, db, business, fake):
    fake.exchange_error = ProviderRejected("Fake Books said no.", code="denied")
    res = approve(api, business)
    assert res.status_code == 422 and res.json()["error"]["message"] == "Fake Books said no."
    assert db.scalars(select(Integration).execution_options(**ACROSS_TENANTS)).all() == []
    fake.exchange_error = ProviderUnavailable()
    assert approve(api, business).status_code == 503


def test_connecting_the_same_account_again_updates_the_same_connection(api, db, business, fake):
    first = connected(api, business)
    fake.account_name = "Acme Limited (renamed)"
    second = connected(api, business)
    assert second["id"] == first["id"]
    assert second["display_name"] == "Fake Books · Acme Limited (renamed)"
    assert len(db.scalars(select(Integration).execution_options(**ACROSS_TENANTS)).all()) == 1
    assert crypto.decrypt_json(row(db, first["id"]).credentials)["access_token"] == "access-2"


def test_two_different_accounts_are_two_connections(api, db, business, fake):
    one = connected(api, business)
    fake.account_id = "acct-2"
    two = connected(api, business)
    assert one["id"] != two["id"]
    assert len(api.get(url(business), headers=auth(business)).json()) == 2


def test_reconnecting_must_be_the_same_account(api, business, fake):
    original = connected(api, business)
    fake.account_id = "acct-other"
    res = approve(api, business, integration_id=original["id"])
    assert res.status_code == 409 and res.json()["error"]["code"] == "different_account"
    fake.account_id = "acct-1"
    assert approve(api, business, integration_id=original["id"]).status_code == 200


def test_reconnect_for_the_wrong_provider_is_refused(api, business, fake):
    original = connected(api, business)
    res = start(api, business, provider="sandbox", integration_id=original["id"])
    assert res.status_code == 422 and res.json()["error"]["code"] == "wrong_provider"


def test_old_unfinished_attempts_are_tidied_away(api, db, business, fake):
    start(api, business)
    db.execute(
        update(IntegrationOAuthState)
        .values(expires_at=utcnow() - timedelta(days=3))
        .execution_options(**ACROSS_TENANTS)
    )
    start(api, business)
    assert (
        len(db.scalars(select(IntegrationOAuthState).execution_options(**ACROSS_TENANTS)).all())
        == 1
    )


def test_the_sandbox_provider_works_end_to_end_in_dev(api, db, business, storage):
    done = connected(api, business, provider="sandbox")
    assert done["display_name"] == "Sandbox (practice connection) · Practice Bakery Ltd"
    sync = request_sync(api, business, done["id"])
    assert sync.status_code == 202
    ran = run_worker(db, storage)
    assert ran.status == "succeeded" and ran.result["records_fetched"] == 3


# --- keeping businesses apart ------------------------------------------------------------------


def test_another_business_cannot_see_or_touch_a_connection(api, business, fake):
    mine = connected(api, business)
    theirs = auth(business, "other")
    assert api.get(url(business, org=1), headers=theirs).json() == []
    for method, path in (
        ("get", f"/{mine['id']}"),
        ("get", f"/{mine['id']}/syncs"),
        ("post", f"/{mine['id']}/sync"),
        ("post", f"/{mine['id']}/disconnect"),
    ):
        res = getattr(api, method)(url(business, path, org=1), headers=theirs)
        assert res.status_code == 404, (method, path)


# --- syncing -------------------------------------------------------------------------------------


def test_a_sync_runs_in_the_background_and_leaves_a_record(api, db, business, storage, fake):
    conn = connected(api, business)
    res = request_sync(api, business, conn["id"])
    assert res.status_code == 202 and res.json()["kind"] == "integration.sync"
    assert fake.names().count("sync") == 0  # nothing happens until a worker runs it

    ran = run_worker(db, storage)
    assert ran.status == "succeeded"
    assert ran.result["records_fetched"] == 5 and ran.result["records_created"] == 2
    assert (ran.progress_done, ran.progress_total) == (2, 2)

    after = api.get(url(business, f"/{conn['id']}"), headers=auth(business)).json()
    assert after["last_sync_status"] == "succeeded" and after["needs_attention"] is False
    assert after["last_successful_sync_at"] is not None and after["last_error_message"] is None
    syncs = api.get(url(business, f"/{conn['id']}/syncs"), headers=auth(business)).json()
    assert (
        len(syncs) == 1 and syncs[0]["status"] == "succeeded" and syncs[0]["records_fetched"] == 5
    )
    assert syncs[0]["finished_at"] is not None


def test_each_sync_starts_where_the_last_one_stopped(api, db, business, storage, fake):
    conn = connected(api, business)
    for _ in range(2):
        request_sync(api, business, conn["id"])
        run_worker(db, storage)
    assert fake.cursors == [{}, {"page": 2}]


def test_only_one_sync_at_a_time_per_connection(api, business, fake):
    conn = connected(api, business)
    assert request_sync(api, business, conn["id"]).status_code == 202
    again = request_sync(api, business, conn["id"])
    assert again.status_code == 409 and again.json()["error"]["code"] == "job_already_running"


def test_tokens_that_are_about_to_expire_are_refreshed_before_syncing(
    api, db, business, storage, fake
):
    fake.expires_in = timedelta(seconds=30)  # inside the refresh margin already
    conn = connected(api, business)
    request_sync(api, business, conn["id"])
    assert run_worker(db, storage).status == "succeeded"
    assert fake.names() == ["authorize", "exchange", "refresh", "sync"]
    assert ("sync", "access-2") in fake.calls  # the sync used the new token
    saved = crypto.decrypt_json(row(db, conn["id"]).credentials)
    assert saved["access_token"] == "access-2"
    assert saved["refresh_token"] == "refresh-1"  # kept: the provider sent no new one


def test_tokens_with_plenty_of_life_left_are_not_refreshed(api, db, business, storage, fake):
    conn = connected(api, business)
    request_sync(api, business, conn["id"])
    run_worker(db, storage)
    assert "refresh" not in fake.names()


def test_when_the_provider_rejects_our_tokens_the_person_is_asked_to_sign_in_again(
    api, db, business, storage, fake
):
    fake.expires_in = timedelta(seconds=30)
    conn = connected(api, business)
    fake.refresh_error = ReauthRequired("Fake Books needs you to sign in again.")
    request_sync(api, business, conn["id"])

    ran = run_worker(db, storage)
    assert ran.status == "failed" and ran.attempts == 1  # no pointless retries
    assert ran.error_code == "reauth_required"
    assert ran.error_message == "Fake Books needs you to sign in again."

    after = api.get(url(business, f"/{conn['id']}"), headers=auth(business)).json()
    assert after["status"] == "needs_reauth" and after["needs_attention"] is True
    assert after["last_error_message"] == "Fake Books needs you to sign in again."
    assert "integration.reauth_needed" in audit_actions(db)

    blocked = request_sync(api, business, conn["id"])
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "reauth_required"

    # Signing in again (same account) brings it back.
    fake.refresh_error = None
    fake.expires_in = timedelta(hours=1)
    assert approve(api, business, integration_id=conn["id"]).json()["status"] == "connected"
    request_sync(api, business, conn["id"])
    assert run_worker(db, storage).status == "succeeded"
    cleared = api.get(url(business, f"/{conn['id']}"), headers=auth(business)).json()
    assert cleared["needs_attention"] is False and cleared["consecutive_failures"] == 0


def test_a_temporary_outage_is_retried_and_does_not_disconnect(api, db, business, storage, fake):
    conn = connected(api, business)
    fake.sync_error = ProviderUnavailable("Fake Books is down.")
    request_sync(api, business, conn["id"])

    first = run_worker(db, storage)
    assert first.status == "queued" and first.attempts == 1  # waiting for another go
    mid = api.get(url(business, f"/{conn['id']}"), headers=auth(business)).json()
    assert mid["status"] == "connected" and mid["consecutive_failures"] == 1
    assert mid["needs_attention"] is True and mid["last_error_message"] == "Fake Books is down."

    fake.sync_error = None  # it comes back
    make_due(db)
    assert run_worker(db, storage).status == "succeeded"
    healed = api.get(url(business, f"/{conn['id']}"), headers=auth(business)).json()
    assert healed["consecutive_failures"] == 0 and healed["needs_attention"] is False
    assert healed["last_error_message"] is None
    states = [
        s["status"]
        for s in api.get(url(business, f"/{conn['id']}/syncs"), headers=auth(business)).json()
    ]
    assert sorted(states) == ["failed", "succeeded"]


def test_an_outage_that_never_ends_fails_the_job_after_three_tries(
    api, db, business, storage, fake
):
    conn = connected(api, business)
    fake.sync_error = ProviderUnavailable()
    request_sync(api, business, conn["id"])
    for _ in range(3):
        make_due(db)
        ran = run_worker(db, storage)
    assert ran.status == "failed" and ran.attempts == 3
    assert row(db, conn["id"]).consecutive_failures == 3


def test_a_permanent_rejection_fails_at_once_with_the_providers_message(
    api, db, business, storage, fake
):
    conn = connected(api, business)
    fake.sync_error = ProviderRejected("Your plan doesn't include reports.", code="plan_limit")
    request_sync(api, business, conn["id"])
    ran = run_worker(db, storage)
    assert ran.status == "failed" and ran.attempts == 1
    assert (
        ran.error_code == "plan_limit" and ran.error_message == "Your plan doesn't include reports."
    )
    assert row(db, conn["id"]).status == "connected"  # not a sign-in problem


def test_an_unexpected_bug_in_a_connector_is_retried_with_a_generic_message(
    api, db, business, storage, fake
):
    conn = connected(api, business)
    fake.sync_error = RuntimeError("secret internal detail")
    request_sync(api, business, conn["id"])
    ran = run_worker(db, storage)
    assert ran.status == "queued" and "secret internal detail" not in ran.error_message
    saved = row(db, conn["id"])
    assert "secret internal detail" not in (saved.last_error_message or "")
    assert saved.last_sync_status == "failed"


def test_credentials_that_cannot_be_decrypted_mean_sign_in_again(api, db, business, storage, fake):
    conn = connected(api, business)
    db.execute(
        update(Integration).values(credentials="garbage").execution_options(**ACROSS_TENANTS)
    )
    request_sync(api, business, conn["id"])
    ran = run_worker(db, storage)
    assert ran.status == "failed" and ran.error_code == "reauth_required"
    assert row(db, conn["id"]).status == "needs_reauth"


# --- disconnecting -------------------------------------------------------------------------------


def test_disconnecting_wipes_the_tokens_and_tells_the_provider(api, db, business, fake):
    conn = connected(api, business)
    res = api.post(url(business, f"/{conn['id']}/disconnect"), headers=auth(business))
    assert res.status_code == 200 and res.json()["status"] == "disconnected"
    assert ("revoke", "access-1") in fake.calls
    saved = row(db, conn["id"])
    assert saved.credentials is None and saved.token_expires_at is None
    assert saved.disconnected_at is not None
    assert "integration.disconnected" in audit_actions(db)


def test_disconnecting_works_even_if_the_provider_cannot_be_reached(api, db, business, fake):
    conn = connected(api, business)
    fake.revoke_error = RuntimeError("network down")
    res = api.post(url(business, f"/{conn['id']}/disconnect"), headers=auth(business))
    assert res.status_code == 200 and row(db, conn["id"]).credentials is None


def test_disconnecting_twice_is_harmless(api, business, fake):
    conn = connected(api, business)
    path = url(business, f"/{conn['id']}/disconnect")
    assert api.post(path, headers=auth(business)).status_code == 200
    assert api.post(path, headers=auth(business)).status_code == 200


def test_a_disconnected_connection_cannot_sync_but_can_be_connected_again(
    api, db, business, storage, fake
):
    conn = connected(api, business)
    api.post(url(business, f"/{conn['id']}/disconnect"), headers=auth(business))
    res = request_sync(api, business, conn["id"])
    assert res.status_code == 409 and res.json()["error"]["code"] == "disconnected"

    back = connected(api, business)  # same account: the old connection comes back
    assert back["id"] == conn["id"] and back["status"] == "connected"
    assert back["disconnected_at"] is None
    request_sync(api, business, conn["id"])
    assert run_worker(db, storage).status == "succeeded"


def test_a_sync_queued_before_disconnecting_does_not_run(api, db, business, storage, fake):
    conn = connected(api, business)
    request_sync(api, business, conn["id"])
    api.post(url(business, f"/{conn['id']}/disconnect"), headers=auth(business))
    ran = run_worker(db, storage)
    assert ran.status == "failed" and ran.error_code == "disconnected"
    assert "sync" not in fake.names()


# --- the database itself refuses nonsense --------------------------------------------------------


def test_the_database_will_not_hold_credentials_for_a_disconnected_connection(
    api, db, business, fake
):
    conn = connected(api, business)
    with pytest.raises(IntegrityError):
        db.execute(
            update(Integration)
            .where(Integration.id == uuid.UUID(conn["id"]))
            .values(status="disconnected", disconnected_at=utcnow())
            .execution_options(**ACROSS_TENANTS)
        )
        db.flush()


def test_a_connected_connection_must_have_credentials(api, db, business, fake):
    conn = connected(api, business)
    with pytest.raises(IntegrityError):
        db.execute(
            update(Integration)
            .where(Integration.id == uuid.UUID(conn["id"]))
            .values(credentials=None)
            .execution_options(**ACROSS_TENANTS)
        )
        db.flush()


def test_sync_history_belongs_to_the_connection(api, db, business, storage, fake):
    conn = connected(api, business)
    request_sync(api, business, conn["id"])
    run_worker(db, storage)
    assert len(db.scalars(select(IntegrationSync).execution_options(**ACROSS_TENANTS)).all()) == 1


def test_connecting_and_disconnecting_need_their_own_permission(api, db, business, fake):
    """Seeing and syncing (data.manage) is separate from connecting (integrations.manage)."""
    from app.models.identity import Permission, role_permissions

    conn = connected(api, business)
    owner_role = db.scalars(
        select(Role.id).where(Role.code == "owner", Role.organization_id.is_(None))
    ).one()
    permission_id = db.scalars(
        select(Permission.id).where(Permission.code == "integrations.manage")
    ).one()
    db.execute(
        role_permissions.delete().where(
            role_permissions.c.role_id == owner_role,
            role_permissions.c.permission_id == permission_id,
        )
    )
    assert api.get(url(business), headers=auth(business)).status_code == 200  # can still look
    assert request_sync(api, business, conn["id"]).status_code == 202  # and sync
    assert start(api, business).status_code == 403
    cb = {"state": "x" * 20, "code": "c"}
    assert api.post(url(business, "/callback"), json=cb, headers=auth(business)).status_code == 403
    path = url(business, f"/{conn['id']}/disconnect")
    assert api.post(path, headers=auth(business)).status_code == 403
