# ruff: noqa: E501, F811
"""TrueLayer: signing in to a bank, and bringing money in and out of the bank in, against a pretend
TrueLayer, and the two bank figures made from it."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.integrations.base import (
    AccountInfo,
    ProviderRejected,
    ProviderUnavailable,
    ReauthRequired,
    SyncContext,
    TokenSet,
)
from app.integrations.registry import PROVIDERS
from app.integrations.truelayer import TrueLayerProvider
from app.models.data import BankTransaction
from app.plain.figures import WORDS
from tests.test_kpi_customers_stock import value
from tests.test_kpi_engine import run, scoped

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
CREDS = "cred-123"
SCOPES = "info accounts balance transactions offline_access"
CODES = {"bank_money_in", "bank_money_out"}


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("VYTERLIX_TRUELAYER_CLIENT_ID", "sandbox-vyterlix-test")
    monkeypatch.setenv("VYTERLIX_TRUELAYER_CLIENT_SECRET", "tl-secret")
    monkeypatch.delenv("VYTERLIX_TRUELAYER_ENVIRONMENT", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def pretend(handler):
    provider = TrueLayerProvider()
    provider.transport = httpx.MockTransport(handler)
    return provider


def tokens():
    return TokenSet("access-1", "refresh-1", scopes=tuple(SCOPES.split()))


def ctx(cursor=None):
    return SyncContext(
        tokens=tokens(), account=AccountInfo(CREDS, "Mock Bank"), cursor=cursor or {},
        settings={}, progress=lambda done, total: None, now=NOW,
    )  # fmt: skip


def tx(number, amount, kind=None, **over):
    base = {
        "transaction_id": f"t{number}", "normalised_provider_transaction_id": f"n{number}",
        "timestamp": "2026-09-10T00:00:00", "description": "CARD PAYMENT", "currency": "GBP",
        "amount": amount, "transaction_type": kind or ("CREDIT" if float(amount) > 0 else "DEBIT"),
        "merchant_name": "Flour Co",
    }  # fmt: skip
    return {**base, **over}


def bank(transactions=None, accounts=None, status=200, error=None):
    """A pretend TrueLayer: `transactions` maps account id -> list. `seen` collects requests."""
    seen = []
    accounts = (
        accounts
        if accounts is not None
        else [{"account_id": "acc1", "display_name": "Current", "currency": "GBP"}]
    )
    transactions = transactions if transactions is not None else {}

    def handler(request):
        seen.append(request)
        path = request.url.path
        if status != 200:
            return httpx.Response(status, json={"error": error or "x"})
        if path.endswith("/me"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"credentials_id": CREDS, "provider": {"display_name": "Mock Bank"}}
                    ]
                },
            )
        if path.endswith("/accounts"):
            return httpx.Response(200, json={"results": accounts})
        if path.endswith("/transactions"):
            found = transactions.get(path.split("/")[-2])
            if found is None:
                return httpx.Response(501, json={"error": "endpoint_not_supported"})
            return httpx.Response(200, json={"results": found})
        return httpx.Response(404)

    provider = pretend(handler)
    provider.seen = seen
    return provider


# --- is it set up -------------------------------------------------------------------------------------


def test_it_is_only_available_once_the_app_is_registered_with_truelayer():
    provider = TrueLayerProvider()
    assert provider.is_configured(Settings(truelayer_client_id="a", truelayer_client_secret="b"))
    assert not provider.is_configured(
        Settings(truelayer_client_id=None, truelayer_client_secret=None)
    )
    assert not provider.is_configured(
        Settings(truelayer_client_id="a", truelayer_client_secret=None)
    )
    assert "truelayer" in PROVIDERS


def test_it_says_it_can_only_look():
    provider = TrueLayerProvider()
    assert any("can never move money" in p for p in provider.permissions)
    assert not {"payments", "direct_debits", "standing_orders"} & set(provider.scopes)


# --- signing in ----------------------------------------------------------------------------------------


def test_the_sign_in_address_goes_to_the_practice_bank_in_the_sandbox_and_asks_to_read_only():
    url = TrueLayerProvider().authorize_url(
        state="S",
        redirect_uri="https://vyterlix.com/integrations-callback.html",
        code_challenge="C",
    )
    parsed = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert f"{parsed.scheme}://{parsed.netloc}" == "https://auth.truelayer-sandbox.com"
    assert q["client_id"] == "sandbox-vyterlix-test" and q["state"] == "S" and q["scope"] == SCOPES
    assert "uk-cs-mock" in q["providers"] and "code_challenge" not in q


def test_real_banks_are_used_only_when_live_is_chosen(monkeypatch):
    monkeypatch.setenv("VYTERLIX_TRUELAYER_ENVIRONMENT", "live")
    get_settings.cache_clear()
    url = TrueLayerProvider().authorize_url(
        state="S", redirect_uri="https://x/cb", code_challenge="C"
    )
    assert url.startswith("https://auth.truelayer.com/") and "uk-cs-mock" not in url


def test_the_code_is_swapped_for_tokens_with_the_apps_credentials():
    seen = {}

    def handler(request):
        seen["url"], seen["form"] = str(request.url), parse_qs(request.content.decode())
        return httpx.Response(
            200,
            json={"access_token": "A", "refresh_token": "R", "expires_in": 3600, "scope": SCOPES},
        )

    out = pretend(handler).exchange_code(
        code="the-code", redirect_uri="https://x/cb", code_verifier="V"
    )
    assert seen["url"] == "https://auth.truelayer-sandbox.com/connect/token"
    assert seen["form"]["grant_type"] == ["authorization_code"] and seen["form"][
        "client_secret"
    ] == ["tl-secret"]
    assert (out.access_token, out.refresh_token) == ("A", "R") and "transactions" in out.scopes


def test_if_transactions_were_not_shared_it_is_said_plainly():
    def handler(request):
        body = {"access_token": "A", "refresh_token": "R", "expires_in": 3600, "scope": "info"}
        return httpx.Response(200, json=body)

    with pytest.raises(ProviderRejected) as e:
        pretend(handler).exchange_code(code="x", redirect_uri="https://x/cb", code_verifier="V")
    assert e.value.code == "missing_scope" and "agree to share" in e.value.message


def test_a_refused_code_is_said_plainly_and_a_busy_truelayer_is_tried_again_later():
    with pytest.raises(ProviderRejected) as bad:
        pretend(lambda r: httpx.Response(400, json={"error": "invalid_grant"})).exchange_code(
            code="x", redirect_uri="https://x/cb", code_verifier="V"
        )
    assert bad.value.code == "invalid_code"
    for status in (429, 503):
        with pytest.raises(ProviderUnavailable):
            pretend(lambda r, s=status: httpx.Response(s)).exchange_code(
                code="x", redirect_uri="https://x/cb", code_verifier="V"
            )


def test_refreshing_takes_the_new_tokens_and_a_refusal_means_connect_again():
    fresh = pretend(
        lambda r: httpx.Response(
            200, json={"access_token": "A2", "refresh_token": "R2", "expires_in": 3600}
        )
    ).refresh(tokens())
    assert (fresh.access_token, fresh.refresh_token) == ("A2", "R2")
    with pytest.raises(ReauthRequired):
        pretend(lambda r: httpx.Response(400, json={"error": "invalid_grant"})).refresh(tokens())
    with pytest.raises(ReauthRequired):
        TrueLayerProvider().refresh(TokenSet("a"))


def test_disconnecting_tells_truelayer_and_ignores_it_being_unreachable():
    seen = []
    pretend(lambda r: seen.append(r) or httpx.Response(200)).revoke(tokens())
    assert seen[0].method == "DELETE" and seen[0].url.path == "/api/delete"

    def down(request):
        raise httpx.ConnectError("no route")

    pretend(down).revoke(tokens())


# --- which bank ----------------------------------------------------------------------------------------


def test_the_bank_is_named_and_a_missing_one_is_refused_plainly():
    info = bank().account(tokens())
    assert (info.external_id, info.name) == (CREDS, "Mock Bank")
    with pytest.raises(ProviderRejected) as e:
        pretend(lambda r: httpx.Response(200, json={"results": []})).account(tokens())
    assert e.value.code == "no_account"


# --- bringing the transactions in ----------------------------------------------------------------------


def test_money_in_is_positive_and_money_out_is_negative_whatever_sign_the_bank_used():
    out = bank(
        {"acc1": [tx(1, 120.5), tx(2, -60.25), tx(3, 40, "DEBIT"), tx(4, -10, "CREDIT")]}
    ).sync(ctx())
    rows = {r["reference"]: r for r in out.datasets["bank_transactions"]}
    assert [r["amount"] for r in rows.values()] == [D("120.5"), D("-60.25"), D("-40"), D("10")]
    first = rows["tl-acc1-n1"]
    assert (first["day"], first["counterparty"], first["description"], first["account_name"]) == (
        date(2026, 9, 10),
        "Flour Co",
        "CARD PAYMENT",
        "Current",
    )


def test_transactions_that_do_not_make_sense_are_left_out():
    out = bank(
        {
            "acc1": [
                tx(1, 5),
                tx(2, 0),
                tx(3, 5, currency="USD"),
                tx(4, 5, timestamp=None),
                tx(5, 5, "OTHER"),
                tx(6, 5, normalised_provider_transaction_id=None, transaction_id=None),
                tx(7, 5),
            ]
        }
    ).sync(ctx())
    assert [r["reference"] for r in out.datasets["bank_transactions"]] == [
        "tl-acc1-n1",
        "tl-acc1-n7",
    ]


def test_every_account_is_read_and_one_that_cannot_list_transactions_is_skipped():
    provider = bank(
        {"acc1": [tx(1, 5)], "acc2": [tx(1, -7)]},
        accounts=[
            {"account_id": "acc1", "display_name": "Current", "currency": "GBP"},
            {"account_id": "acc2", "display_name": "Bills", "currency": "GBP"},
            {"account_id": "acc3", "display_name": "Locked", "currency": "GBP"},
            {"account_id": "acc4", "display_name": "Dollars", "currency": "USD"},
        ],
    )
    out = provider.sync(ctx())
    assert sorted(r["reference"] for r in out.datasets["bank_transactions"]) == [
        "tl-acc1-n1",
        "tl-acc2-n1",
    ]  # the same bank id on two accounts does not clash
    assert out.cursor["left_out_other_currency"] == 1 and all(
        r.method == "GET" for r in provider.seen
    )


def test_the_first_sync_bounds_the_history_and_later_ones_read_again_from_a_few_days_before():
    first = bank({"acc1": []})
    out = first.sync(ctx())
    q = parse_qs(
        next(r for r in first.seen if r.url.path.endswith("/transactions")).url.query.decode()
    )
    assert q["from"] == [(NOW - timedelta(days=730)).date().isoformat()] and q["to"] == [
        "2026-10-20"
    ]
    assert out.cursor["since"] == "2026-10-20"
    later = bank({"acc1": []})
    later.sync(ctx({"since": "2026-10-20"}))
    q = parse_qs(
        next(r for r in later.seen if r.url.path.endswith("/transactions")).url.query.decode()
    )
    assert q["from"] == ["2026-10-17"]


def test_the_90_days_being_up_and_other_problems_read_in_plain_english():
    with pytest.raises(ReauthRequired) as e:
        bank(status=403, error="sca_exceeded").sync(ctx())
    assert "agree again" in e.value.message
    with pytest.raises(ReauthRequired):
        bank(status=401).sync(ctx())
    with pytest.raises(ProviderRejected) as e:
        bank(status=403, error="access_denied").sync(ctx())
    assert e.value.code == "missing_scope"
    with pytest.raises(ProviderUnavailable):
        bank(status=429).sync(ctx())


# --- the whole connect and sync through the framework --------------------------------------------------


def test_signing_in_connects_the_bank_and_the_sync_stores_each_transaction_once(
    api, db, business, storage, monkeypatch
):
    from app.models.integrations import Integration
    from tests.test_integrations import auth, request_sync, run_worker, start, url

    inner = bank({"acc1": [tx(1, 100), tx(2, -40)]})
    base = inner.transport

    def handler(request):
        if request.url.path == "/connect/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "tl-access-xyz",
                    "refresh_token": "tl-refresh-xyz",
                    "expires_in": 3600,
                    "scope": SCOPES,
                },
            )
        return base.handle_request(request)

    monkeypatch.setitem(PROVIDERS, "truelayer", pretend(handler))
    res = start(api, business, "truelayer")
    assert res.status_code == 200, res.text
    state = parse_qs(urlparse(res.json()["authorize_url"]).query)["state"][0]
    done = api.post(
        url(business, "/callback"), json={"state": state, "code": "c"}, headers=auth(business)
    )
    assert done.status_code == 200, done.text
    conn = done.json()
    assert conn["provider"] == "truelayer" and conn["account_name"] == "Mock Bank"
    assert "tl-access-xyz" not in json.dumps(conn) and "tl-refresh-xyz" not in json.dumps(conn)

    request_sync(api, business, conn["id"])
    ran = run_worker(db, storage)
    assert (
        ran.status == "succeeded" and ran.result["records_created"] == 2 and ran.result["ingested"]
    )
    request_sync(api, business, conn["id"])
    ran = run_worker(db, storage)
    if (
        "records_created" not in ran.result
    ):  # the figures were recalculated first (queued by the first sync)
        ran = run_worker(db, storage)
    assert (
        ran.status == "succeeded" and ran.result["records_created"] == 0
    )  # the same transactions are not added again
    with scoped(db, business):
        assert sorted(t.amount for t in db.scalars(select(BankTransaction))) == [D("-40"), D("100")]
        assert {t.source for t in db.scalars(select(BankTransaction))} == {"truelayer"}
        assert db.scalars(select(Integration)).one().external_account_id == CREDS


# --- the bank figures ----------------------------------------------------------------------------------


def money(db, business, day, amount):
    with scoped(db, business):
        db.add(
            BankTransaction(
                day=day, amount=D(amount), source="truelayer", source_ref=f"tl-{day}-{amount}"
            )
        )
        db.flush()


def codes(api, business):
    res = api.get(f"/api/v1/organizations/{business[0]}/kpis", headers=business[2]["owner"])
    return {k["code"] for k in res.json()["kpis"]}


def test_a_business_with_no_bank_connected_is_not_shown_the_bank_figures(api, business):
    assert not CODES & codes(api, business)


def test_once_the_bank_is_connected_the_figures_appear(api, db, business):
    money(db, business, date(2026, 9, 1), "10")
    assert CODES <= codes(api, business)


def test_money_in_and_money_out_are_added_up_for_the_month(api, db, business):
    for day, amount in ((1, "100.50"), (2, "-40.25"), (3, "20"), (4, "-9.75")):
        money(db, business, date(2026, 9, day), amount)
    money(db, business, date(2026, 8, 31), "999")  # a different month
    run(db, business, first=date(2026, 9, 1), today=date(2026, 11, 15))
    assert value(api, business, "bank_money_in", "2026-09-01") == "120.50"
    assert value(api, business, "bank_money_out", "2026-09-01") == "50.00"


def test_every_bank_figure_has_plain_words_and_claims_no_cause():
    for code in CODES:
        for say in (WORDS[code].up, WORDS[code].down):
            assert (
                say.says.strip()
                and say.why.strip()
                and "because" not in (say.says + say.why).lower()
            )
