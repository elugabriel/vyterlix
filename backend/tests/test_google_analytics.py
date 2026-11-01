# ruff: noqa: E501, F811
"""Google Analytics: signing in, which website is connected, bringing daily visits in, and the
website figures made from them, against a pretend Google."""

import json
from datetime import UTC, date, datetime, timedelta
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
from app.integrations.google_analytics import SCOPE, GoogleAnalyticsProvider
from app.integrations.registry import PROVIDERS
from app.models.data import WebVisit
from app.plain.figures import WORDS
from tests.test_kpi_customers_stock import value
from tests.test_kpi_engine import run, scoped

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
PROPERTY = "561127059"
CODES = {"website_visits", "website_new_visitors", "website_actions"}


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("VYTERLIX_GOOGLE_CLIENT_ID", "g-client")
    monkeypatch.setenv("VYTERLIX_GOOGLE_CLIENT_SECRET", "g-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def pretend(handler):
    provider = GoogleAnalyticsProvider()
    provider.transport = httpx.MockTransport(handler)
    return provider


def tokens():
    return TokenSet("access-1", "refresh-1", scopes=(SCOPE,))


def ctx(cursor=None):
    return SyncContext(
        tokens=tokens(), account=AccountInfo(PROPERTY, "Baker Street Bakes"), cursor=cursor or {},
        settings={}, progress=lambda done, total: None, now=NOW,
    )  # fmt: skip


def report_row(day, channel, sessions, users, new_users, key_events):
    return {
        "dimensionValues": [{"value": day}, {"value": channel}],
        "metricValues": [{"value": str(v)} for v in (sessions, users, new_users, key_events)],
    }


def google_with(rows=(), summaries=None):
    """A pretend Google Analytics. `seen` collects requests, `bodies` the report requests."""
    seen, bodies = [], []
    if summaries is None:
        summaries = [
            {
                "displayName": "Acct",
                "propertySummaries": [
                    {"property": f"properties/{PROPERTY}", "displayName": "Baker Street Bakes"}
                ],
            }
        ]

    def handler(request):
        seen.append(request)
        path = request.url.path
        if path.endswith("/accountSummaries"):
            return httpx.Response(200, json={"accountSummaries": summaries})
        if path.endswith(":runReport"):
            body = json.loads(request.content)
            bodies.append(body)
            chunk = list(rows)[body["offset"] : body["offset"] + body["limit"]]
            return httpx.Response(200, json={"rows": chunk, "rowCount": len(rows)})
        return httpx.Response(404)

    provider = pretend(handler)
    provider.seen, provider.bodies = seen, bodies
    return provider


# --- is it set up -------------------------------------------------------------------------------------


def test_it_is_only_available_once_the_app_is_registered_with_google():
    provider = GoogleAnalyticsProvider()
    assert provider.is_configured(Settings(google_client_id="a", google_client_secret="b"))
    assert not provider.is_configured(Settings(google_client_id=None, google_client_secret=None))
    assert not provider.is_configured(Settings(google_client_id="a", google_client_secret=None))
    assert "google_analytics" in PROVIDERS


def test_it_asks_to_read_only_and_says_so():
    provider = GoogleAnalyticsProvider()
    assert provider.scopes == ("https://www.googleapis.com/auth/analytics.readonly",)
    assert any("never changes anything in Google Analytics" in p for p in provider.permissions)


# --- signing in ----------------------------------------------------------------------------------------


def test_the_sign_in_address_asks_for_read_access_a_refresh_token_and_the_extra_check():
    url = GoogleAnalyticsProvider().authorize_url(
        state="S",
        redirect_uri="https://vyterlix.com/integrations-callback.html",
        code_challenge="C",
    )
    parsed = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert (
        f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        == "https://accounts.google.com/o/oauth2/v2/auth"
    )
    assert q["client_id"] == "g-client" and q["state"] == "S" and q["scope"] == SCOPE
    assert q["access_type"] == "offline" and q["prompt"] == "consent"
    assert q["code_challenge"] == "C" and q["code_challenge_method"] == "S256"


def test_the_code_is_swapped_for_tokens_with_the_verifier():
    seen = {}

    def handler(request):
        seen["url"], seen["form"] = str(request.url), parse_qs(request.content.decode())
        return httpx.Response(
            200,
            json={"access_token": "A", "refresh_token": "R", "expires_in": 3599, "scope": SCOPE},
        )

    out = pretend(handler).exchange_code(
        code="the-code", redirect_uri="https://x/cb", code_verifier="V"
    )
    assert seen["url"] == "https://oauth2.googleapis.com/token"
    assert seen["form"]["code_verifier"] == ["V"] and seen["form"]["client_secret"] == ["g-secret"]
    assert (out.access_token, out.refresh_token, out.scopes) == ("A", "R", (SCOPE,))
    assert timedelta(minutes=58) < out.expires_at - datetime.now(UTC) <= timedelta(minutes=60)


def test_if_the_box_for_analytics_was_not_ticked_it_is_said_plainly():
    def handler(request):
        body = {"access_token": "A", "refresh_token": "R", "expires_in": 3599, "scope": "openid"}
        return httpx.Response(200, json=body)

    with pytest.raises(ProviderRejected) as e:
        pretend(handler).exchange_code(code="x", redirect_uri="https://x/cb", code_verifier="V")
    assert e.value.code == "missing_scope" and "tick the box" in e.value.message


def test_a_refused_code_is_said_plainly_and_a_busy_google_is_tried_again_later():
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


def test_refreshing_keeps_the_refresh_token_google_does_not_resend_and_a_refusal_means_sign_in_again():
    fresh = pretend(
        lambda r: httpx.Response(200, json={"access_token": "A2", "expires_in": 3599})
    ).refresh(tokens())
    assert (fresh.access_token, fresh.refresh_token) == ("A2", "refresh-1")
    with pytest.raises(ReauthRequired):
        pretend(lambda r: httpx.Response(400, json={"error": "invalid_grant"})).refresh(tokens())
    with pytest.raises(ReauthRequired):
        GoogleAnalyticsProvider().refresh(TokenSet("a"))


def test_disconnecting_tells_google_and_ignores_it_being_unreachable():
    seen = []
    pretend(lambda r: seen.append(r) or httpx.Response(200)).revoke(tokens())
    assert "revoke" in seen[0].url.path and parse_qs(seen[0].content.decode())["token"] == [
        "refresh-1"
    ]

    def down(request):
        raise httpx.ConnectError("no route")

    pretend(down).revoke(tokens())


# --- which website -------------------------------------------------------------------------------------


def test_the_first_website_is_the_account_and_none_at_all_is_refused_plainly():
    info = google_with().account(tokens())
    assert (info.external_id, info.name) == (PROPERTY, "Baker Street Bakes")
    with pytest.raises(ProviderRejected) as e:
        google_with(summaries=[{"displayName": "Empty", "propertySummaries": []}]).account(tokens())
    assert e.value.code == "no_property"


# --- bringing the visits in ----------------------------------------------------------------------------


def test_a_day_and_kind_of_source_becomes_one_row():
    out = google_with([report_row("20260910", "Organic Search", 40, 35, 20, 3)]).sync(ctx())
    [row] = out.datasets["web_visits"]
    assert (row["day"], row["channel"], row["reference"]) == (
        date(2026, 9, 10),
        "Organic Search",
        "ga-20260910-organic-search",
    )
    assert (row["sessions"], row["users"], row["new_users"], row["key_events"]) == (40, 35, 20, 3)


def test_odd_rows_are_skipped_and_numbers_are_made_safe():
    out = google_with(
        [
            {"dimensionValues": [{"value": "20260910"}], "metricValues": []},
            report_row("notadate", "Direct", 1, 1, 1, 1),
            report_row("20260911", "", "x", -4, 2.0, None),
            report_row("20260912", "(not set)", 1, 1, 1, 0),
        ]
    ).sync(ctx())
    first, second = out.datasets["web_visits"]
    assert (
        first["channel"],
        first["sessions"],
        first["users"],
        first["new_users"],
        first["key_events"],
    ) == ("Other", 0, 0, 2, 0)
    assert second["reference"] == "ga-20260912-not-set"


def test_every_page_is_read_and_only_the_report_and_the_website_list_are_asked_for():
    rows = [
        report_row(f"2026{m:02d}{d:02d}", ch, 1, 1, 1, 0)
        for m in (8, 9)
        for d in range(1, 28)
        for ch in ("Direct", "Email", "Organic Search")
    ]
    provider = google_with(rows)
    out = provider.sync(ctx())
    assert out.records_fetched == len(rows) == len(out.datasets["web_visits"])
    assert all(r.headers["authorization"] == "Bearer access-1" for r in provider.seen)
    assert {r.url.path.rsplit("/", 1)[-1] for r in provider.seen} == {f"{PROPERTY}:runReport"}


def test_the_first_sync_bounds_the_history_and_later_ones_read_again_from_a_few_days_before():
    first = google_with()
    out = first.sync(ctx())
    body = first.bodies[0]
    assert body["dateRanges"] == [
        {"startDate": (NOW - timedelta(days=730)).date().isoformat(), "endDate": "2026-10-20"}
    ]
    assert [d["name"] for d in body["dimensions"]] == ["date", "sessionDefaultChannelGroup"]
    assert [m["name"] for m in body["metrics"]] == [
        "sessions",
        "activeUsers",
        "newUsers",
        "keyEvents",
    ]
    assert out.cursor == {"since": "2026-10-20"}
    later = google_with()
    later.sync(ctx({"since": "2026-10-20"}))
    assert later.bodies[0]["dateRanges"][0]["startDate"] == "2026-10-17"


def test_permission_problems_read_in_plain_english():
    with pytest.raises(ProviderRejected) as e:
        pretend(lambda r: httpx.Response(403)).sync(ctx())
    assert e.value.code == "missing_scope" and "view your Analytics" in e.value.message
    with pytest.raises(ReauthRequired):
        pretend(lambda r: httpx.Response(401)).sync(ctx())
    with pytest.raises(ProviderUnavailable):
        pretend(lambda r: httpx.Response(429)).sync(ctx())


# --- the whole connect and sync through the framework --------------------------------------------------


def test_signing_in_connects_the_website_and_the_sync_stores_the_days_and_updates_them_next_time(
    api, db, business, storage, monkeypatch
):
    from app.models.integrations import Integration
    from tests.test_integrations import auth, request_sync, run_worker, start, url

    rows = [
        report_row("20260910", "Direct", 10, 8, 5, 1),
        report_row("20260910", "Email", 4, 4, 2, 0),
    ]
    inner = google_with(rows)
    base = inner.transport

    def handler(request):
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(
                200,
                json={
                    "access_token": "g-access-xyz",
                    "refresh_token": "g-refresh-xyz",
                    "expires_in": 3599,
                    "scope": SCOPE,
                },
            )
        return base.handle_request(request)

    provider = pretend(handler)
    monkeypatch.setitem(PROVIDERS, "google_analytics", provider)
    res = start(api, business, "google_analytics")
    assert res.status_code == 200, res.text
    state = parse_qs(urlparse(res.json()["authorize_url"]).query)["state"][0]
    done = api.post(
        url(business, "/callback"), json={"state": state, "code": "c"}, headers=auth(business)
    )
    assert done.status_code == 200, done.text
    conn = done.json()
    assert conn["provider"] == "google_analytics" and conn["account_name"] == "Baker Street Bakes"
    assert "g-access-xyz" not in json.dumps(conn) and "g-refresh-xyz" not in json.dumps(conn)

    request_sync(api, business, conn["id"])
    ran = run_worker(db, storage)
    assert (
        ran.status == "succeeded" and ran.result["records_created"] == 2 and ran.result["ingested"]
    )
    with scoped(db, business):
        visits = {v.channel: v for v in db.scalars(select(WebVisit))}
        assert visits["Direct"].sessions == 10 and visits["Email"].key_events == 0
        assert {v.source for v in visits.values()} == {"google_analytics"}
        assert db.scalars(select(Integration)).one().external_account_id == PROPERTY

    # Analytics corrects a recent day: the same day comes again with new numbers and is updated, not doubled.
    corrected = google_with(
        [report_row("20260910", "Direct", 12, 9, 5, 2), report_row("20260910", "Email", 4, 4, 2, 0)]
    )
    provider.transport = httpx.MockTransport(
        lambda r: (
            httpx.Response(200, json={"access_token": "A"})
            if r.url.host == "oauth2.googleapis.com"
            else corrected.transport.handle_request(r)
        )
    )
    request_sync(api, business, conn["id"])
    ran = run_worker(db, storage)
    if (
        "records_created" not in ran.result
    ):  # the figures were recalculated first (queued by the first sync)
        ran = run_worker(db, storage)
    assert ran.status == "succeeded"
    assert ran.result["records_created"] == 0
    with scoped(db, business):
        assert db.scalars(select(WebVisit).where(WebVisit.channel == "Direct")).one().sessions == 12
        assert len(list(db.scalars(select(WebVisit)))) == 2


# --- the website figures -------------------------------------------------------------------------------


def visit(db, business, day, channel, sessions, users, new_users, key_events):
    with scoped(db, business):
        db.add(
            WebVisit(
                day=day,
                channel=channel,
                sessions=sessions,
                users=users,
                new_users=new_users,
                key_events=key_events,
                source="google_analytics",
                source_ref=f"ga-{day:%Y%m%d}-{channel}",
            )
        )
        db.flush()


def codes(api, business):
    res = api.get(f"/api/v1/organizations/{business[0]}/kpis", headers=business[2]["owner"])
    return {k["code"] for k in res.json()["kpis"]}


def test_a_business_with_no_website_visits_is_not_shown_the_website_figures(api, business):
    assert not CODES & codes(api, business)


def test_once_visits_are_connected_the_website_figures_appear(api, db, business):
    visit(db, business, date(2026, 9, 1), "Direct", 1, 1, 1, 0)
    assert CODES <= codes(api, business)


def test_visits_new_visitors_and_actions_are_added_up_for_the_month(api, db, business):
    for day, channel, s, u, n, k in (
        (1, "Direct", 10, 8, 5, 1),
        (1, "Email", 4, 4, 2, 0),
        (2, "Direct", 6, 5, 3, 2),
        (3, "Direct", 0, 0, 0, 0),
    ):
        visit(db, business, date(2026, 9, day), channel, s, u, n, k)
    visit(db, business, date(2026, 8, 31), "Direct", 99, 99, 99, 99)  # a different month
    run(db, business, first=date(2026, 9, 1), today=date(2026, 11, 15))
    assert value(api, business, "website_visits", "2026-09-01") == "20"
    assert value(api, business, "website_new_visitors", "2026-09-01") == "10"
    assert value(api, business, "website_actions", "2026-09-01") == "3"


def test_every_website_figure_has_plain_words_and_claims_no_cause():
    for code in CODES:
        for say in (WORDS[code].up, WORDS[code].down):
            assert (
                say.says.strip()
                and say.why.strip()
                and "because" not in (say.says + say.why).lower()
            )
