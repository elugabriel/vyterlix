# ruff: noqa: E501, F811
"""Meta (Facebook and Instagram adverts): signing in, which ad account is connected, bringing daily
advert spend in, and the advert figures made from it, against a pretend Meta."""

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.integrations.base import (
    AccountInfo,
    ProviderRejected,
    ProviderUnavailable,
    ReauthRequired,
    SyncContext,
    TokenSet,
)
from app.integrations.meta import SCOPE, MetaProvider
from app.integrations.registry import PROVIDERS
from app.models.data import AdSpend
from app.plain.figures import WORDS
from app.services.integration_ingest import ingest
from tests.shops import _sale
from tests.test_kpi_customers_stock import customer, value
from tests.test_kpi_engine import run, scoped

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
ACCOUNT = "1125845100373106"
CODES = {
    "ad_spend", "ad_clicks", "ad_cost_per_click", "ad_results", "ad_cost_per_result",
    "cost_per_new_customer", "sales_per_ad_pound",
}  # fmt: skip
D = Decimal


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("VYTERLIX_META_APP_ID", "m-app")
    monkeypatch.setenv("VYTERLIX_META_APP_SECRET", "m-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def pretend(handler):
    provider = MetaProvider()
    provider.transport = httpx.MockTransport(handler)
    return provider


def tokens():
    return TokenSet("long-token", None, scopes=(SCOPE,))


def ctx(cursor=None):
    return SyncContext(
        tokens=tokens(), account=AccountInfo(ACCOUNT, "Glow adverts", {"currency": "GBP"}),
        cursor=cursor or {}, settings={}, progress=lambda done, total: None, now=NOW,
    )  # fmt: skip


def insight(day, platform, spend, impressions, clicks, actions=()):
    return {
        "date_start": day, "date_stop": day, "publisher_platform": platform, "spend": spend,
        "impressions": str(impressions), "clicks": str(clicks),
        "actions": [{"action_type": t, "value": str(v)} for t, v in actions],
    }  # fmt: skip


def meta_with(rows=(), accounts=None, granted=("ads_read",), page=2):
    """A pretend Meta. `seen` collects every request. Insights come `page` rows at a time."""
    seen = []
    if accounts is None:
        accounts = [
            {"account_id": ACCOUNT, "name": "Glow adverts", "currency": "GBP", "account_status": 1}
        ]

    def handler(request):
        seen.append(request)
        path = request.url.path
        query = parse_qs(request.url.query.decode())
        if path.endswith("/oauth/access_token"):
            if query.get("grant_type") == ["fb_exchange_token"]:
                return httpx.Response(
                    200, json={"access_token": "long-token", "expires_in": 5184000}
                )
            return httpx.Response(200, json={"access_token": "short-token", "expires_in": 3600})
        if path.endswith("/me/permissions"):
            return httpx.Response(
                200, json={"data": [{"permission": p, "status": "granted"} for p in granted]}
            )
        if path.endswith("/me/adaccounts"):
            return httpx.Response(200, json={"data": accounts})
        if path.endswith("/insights"):
            after = int(query.get("after", ["0"])[0])
            chunk = list(rows)[after : after + page]
            more = after + page < len(rows)
            body = {"data": chunk}
            if more:
                body["paging"] = {
                    "cursors": {"after": str(after + page)},
                    "next": "https://graph.facebook.com/next",
                }
            return httpx.Response(200, json=body)
        return httpx.Response(404)

    provider = pretend(handler)
    provider.seen = seen
    return provider


# --- is it set up ------------------------------------------------------------------------------------


def test_it_is_only_available_once_the_app_is_registered_with_meta():
    provider = MetaProvider()
    assert provider.is_configured(Settings(meta_app_id="a", meta_app_secret="b"))
    assert not provider.is_configured(Settings(meta_app_id=None, meta_app_secret=None))
    assert not provider.is_configured(Settings(meta_app_id="a", meta_app_secret=None))
    assert "meta" in PROVIDERS


def test_it_asks_to_read_only_and_says_so():
    provider = MetaProvider()
    assert provider.scopes == ("ads_read",)
    text = " ".join(provider.permissions)
    assert "never changes your adverts" in text and "never spends your money" in text


# --- signing in --------------------------------------------------------------------------------------


def test_the_sign_in_address_goes_to_facebook_and_asks_only_to_read_adverts():
    url = MetaProvider().authorize_url(
        state="S",
        redirect_uri="https://vyterlix.com/integrations-callback.html",
        code_challenge="C",
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "www.facebook.com" and parsed.path.endswith("/dialog/oauth")
    assert query["client_id"] == ["m-app"] and query["scope"] == ["ads_read"]
    assert query["state"] == ["S"] and query["response_type"] == ["code"]
    assert query["redirect_uri"] == ["https://vyterlix.com/integrations-callback.html"]


def test_a_sign_in_is_turned_into_a_token_that_lasts_about_sixty_days():
    provider = meta_with()
    got = provider.exchange_code(code="the-code", redirect_uri="https://x/cb", code_verifier="v")
    assert got.access_token == "long-token" and got.refresh_token is None
    days = (got.expires_at - datetime.now(UTC)).days
    assert 58 <= days <= 60
    first, second = provider.seen[0], provider.seen[1]
    assert parse_qs(first.url.query.decode())["code"] == ["the-code"]
    assert parse_qs(first.url.query.decode())["client_secret"] == ["m-secret"]
    assert parse_qs(second.url.query.decode())["fb_exchange_token"] == ["short-token"]


def test_it_refuses_a_sign_in_that_did_not_share_the_adverts():
    provider = meta_with(granted=())
    with pytest.raises(ProviderRejected) as err:
        provider.exchange_code(code="c", redirect_uri="https://x/cb", code_verifier="v")
    assert err.value.code == "missing_scope"


def test_it_refuses_a_code_meta_does_not_accept():
    provider = pretend(lambda request: httpx.Response(400, json={"error": {"code": 100}}))
    with pytest.raises(ProviderRejected) as err:
        provider.exchange_code(code="bad", redirect_uri="https://x/cb", code_verifier="v")
    assert err.value.code == "invalid_code"


def test_a_token_still_valid_is_extended_and_one_that_has_run_out_asks_to_sign_in_again():
    fresh = meta_with().refresh(tokens())
    assert fresh.access_token == "long-token" and fresh.refresh_token is None
    dead = pretend(lambda request: httpx.Response(400, json={"error": {"code": 190}}))
    with pytest.raises(ReauthRequired):
        dead.refresh(tokens())


# --- which ad account ----------------------------------------------------------------------------------


def test_it_takes_the_first_active_ad_account_and_drops_the_act_prefix():
    accounts = [
        {"account_id": "111", "name": "Old", "currency": "GBP", "account_status": 2},
        {
            "account_id": f"act_{ACCOUNT}",
            "name": "Glow adverts",
            "currency": "GBP",
            "account_status": 1,
        },
    ]
    info = meta_with(accounts=accounts).account(tokens())
    assert info.external_id == ACCOUNT and info.name == "Glow adverts"
    assert info.settings["currency"] == "GBP"


def test_an_ad_account_in_another_currency_is_refused_in_plain_words():
    accounts = [{"account_id": ACCOUNT, "name": "US", "currency": "USD", "account_status": 1}]
    with pytest.raises(ProviderRejected) as err:
        meta_with(accounts=accounts).account(tokens())
    assert err.value.code == "currency_not_supported" and "pounds" in err.value.message


def test_a_facebook_account_with_no_ad_account_is_told_so():
    with pytest.raises(ProviderRejected) as err:
        meta_with(accounts=[]).account(tokens())
    assert err.value.code == "no_ad_account"


# --- bringing the adverts in ---------------------------------------------------------------------------


ROWS = [
    insight(
        "2026-10-18",
        "facebook",
        "12.50",
        3000,
        40,
        [("lead", 2), ("link_click", 40), ("post_engagement", 90)],
    ),
    insight(
        "2026-10-18",
        "instagram",
        "8.25",
        2000,
        25,
        [("purchase", 1), ("schedule", 1), ("contact", 1)],
    ),
    insight(
        "2026-10-19",
        "facebook",
        "10.00",
        2500,
        30,
        [("onsite_conversion.messaging_conversation_started_7d", 4)],
    ),
    insight("2026-10-19", "audience_network", "1.10", 400, 2),
]


def test_each_day_and_platform_becomes_one_row_with_only_real_results_counted():
    out = meta_with(ROWS).sync(ctx())
    rows = {r["reference"]: r for r in out.datasets["ad_spend"]}
    assert len(rows) == 4 and out.records_fetched == 4
    fb = rows["meta-20261018-facebook"]
    assert (fb["platform"], fb["spend"], fb["impressions"], fb["clicks"]) == (
        "Facebook",
        D("12.50"),
        3000,
        40,
    )
    assert fb["results"] == 2  # the leads, not the link clicks or the post reactions
    assert rows["meta-20261018-instagram"]["results"] == 3  # an order, an appointment and a contact
    assert rows["meta-20261019-facebook"]["results"] == 4  # new conversations count
    assert rows["meta-20261019-audience-network"]["platform"] == "Audience Network"
    assert out.cursor == {"since": "2026-10-20"}


def test_the_first_sync_goes_back_two_years_and_later_ones_read_the_last_week_again():
    first = meta_with(ROWS)
    first.sync(ctx())
    window = json.loads(parse_qs(first.seen[-1].url.query.decode())["time_range"][0])
    assert window == {
        "since": (NOW.date() - timedelta(days=730)).isoformat(),
        "until": "2026-10-20",
    }
    later = meta_with(ROWS)
    later.sync(ctx({"since": "2026-10-15"}))
    window = json.loads(parse_qs(later.seen[-1].url.query.decode())["time_range"][0])
    assert window["since"] == "2026-10-08"


def test_more_than_one_page_of_adverts_is_all_read():
    provider = meta_with(ROWS, page=1)
    out = provider.sync(ctx())
    assert out.records_fetched == 4
    assert len([r for r in provider.seen if r.url.path.endswith("/insights")]) == 4


def test_the_token_goes_in_a_header_not_in_the_address():
    provider = meta_with(ROWS)
    provider.sync(ctx())
    request = provider.seen[-1]
    assert request.headers["Authorization"] == "Bearer long-token"
    assert "long-token" not in str(request.url)


@pytest.mark.parametrize(
    ("status", "code", "raises"),
    [
        (400, 190, ReauthRequired),
        (401, 0, ReauthRequired),
        (400, 17, ProviderUnavailable),
        (429, 0, ProviderUnavailable),
        (500, 0, ProviderUnavailable),
        (403, 0, ProviderRejected),
        (400, 200, ProviderRejected),
        (400, 100, ProviderRejected),
    ],
)
def test_what_meta_says_when_it_will_not_help_is_turned_into_plain_next_steps(status, code, raises):
    provider = pretend(lambda request: httpx.Response(status, json={"error": {"code": code}}))
    with pytest.raises(raises):
        provider.sync(ctx())


# --- stored, and the figures made from it --------------------------------------------------------------


def stored(db, business, rows):
    with scoped(db, business):
        made = ingest(db, uuid.UUID(business[0]), "meta", {"ad_spend": rows})
        db.flush()
    return made


def test_fetching_the_same_days_again_updates_them_and_never_doubles_them(api, db, business):
    rows = meta_with(ROWS).sync(ctx()).datasets["ad_spend"]
    assert stored(db, business, rows)["ad_spend"] == 4
    revised = [
        dict(r, spend=D("99.00")) if r["reference"] == "meta-20261018-facebook" else r for r in rows
    ]
    assert stored(db, business, revised)["ad_spend"] == 0  # nothing new
    with scoped(db, business):
        assert db.scalar(select(func.count()).select_from(AdSpend)) == 4
        spend = db.scalar(
            select(AdSpend.spend).where(AdSpend.source_ref == "meta-20261018-facebook")
        )
    assert spend == D("99.00")


@pytest.fixture
def advertising(db, business):
    """September 2026, by hand. Adverts: 300 pounds, 150 clicks, 20 results. Sales 1,500 before VAT.
    Three customers bought for the first time."""
    a, b, c = (customer(db, business, n) for n in ("Ana", "Ben", "Cat"))
    _sale(db, business, date(2026, 9, 3), 500, customer=a)
    _sale(db, business, date(2026, 9, 10), 500, customer=b)
    _sale(db, business, date(2026, 9, 20), 500, customer=c)
    rows = [
        {
            "day": date(2026, 9, 1),
            "platform": "Facebook",
            "spend": D("200.00"),
            "impressions": 9000,
            "clicks": 100,
            "results": 12,
            "reference": "meta-20260901-facebook",
        },
        {
            "day": date(2026, 9, 1),
            "platform": "Instagram",
            "spend": D("100.00"),
            "impressions": 5000,
            "clicks": 50,
            "results": 8,
            "reference": "meta-20260901-instagram",
        },
    ]
    stored(db, business, rows)
    run(db, business, first=date(2026, 9, 1), today=date(2026, 11, 15))
    return business


def sept(api, advertising, code):
    return value(api, advertising, code, "2026-09-01")


def test_the_advert_figures_match_the_hand_worked_numbers(api, advertising):
    assert sept(api, advertising, "ad_spend") == "300.00"
    assert sept(api, advertising, "ad_clicks") == "150"
    assert sept(api, advertising, "ad_results") == "20"
    assert sept(api, advertising, "ad_cost_per_click") == "2.00"
    assert sept(api, advertising, "ad_cost_per_result") == "15.00"
    assert sept(api, advertising, "cost_per_new_customer") == "100.00"
    assert sept(api, advertising, "sales_per_ad_pound") == "5.00"


def test_the_advert_figures_are_shown_under_marketing_once_there_are_adverts(api, advertising):
    body = api.get(
        f"/api/v1/organizations/{advertising[0]}/kpis", headers=advertising[2]["owner"]
    ).json()
    assert CODES <= {k["code"] for k in body["kpis"]}
    assert {k["module"] for k in body["kpis"] if k["code"] in CODES} == {"marketing"}


def test_without_any_adverts_the_advert_figures_are_not_shown_at_all(api, db, business):
    _sale(db, business, date(2026, 9, 3), 500)
    run(db, business, first=date(2026, 9, 1), today=date(2026, 11, 15))
    body = api.get(f"/api/v1/organizations/{business[0]}/kpis", headers=business[2]["owner"]).json()
    assert not CODES & {k["code"] for k in body["kpis"]}


def test_every_advert_figure_has_plain_words_and_no_cause_is_claimed():
    for code in CODES:
        for say in (WORDS[code].up, WORDS[code].down):
            assert say.says.strip() and say.why.strip()
            assert "because" not in (say.says + say.why).lower()


# --- the whole connect and sync through the framework --------------------------------------------------


def test_signing_in_connects_the_ad_account_and_the_sync_stores_the_days_and_updates_them_next_time(
    api, db, business, storage, monkeypatch
):
    from app.models.integrations import Integration
    from tests.test_integrations import auth, request_sync, run_worker, start, url

    provider = meta_with(ROWS)
    monkeypatch.setitem(PROVIDERS, "meta", provider)
    res = start(api, business, "meta")
    assert res.status_code == 200, res.text
    assert urlparse(res.json()["authorize_url"]).netloc == "www.facebook.com"
    state = parse_qs(urlparse(res.json()["authorize_url"]).query)["state"][0]
    done = api.post(
        url(business, "/callback"), json={"state": state, "code": "c"}, headers=auth(business)
    )
    assert done.status_code == 200, done.text
    conn = done.json()
    assert conn["provider"] == "meta" and conn["account_name"] == "Glow adverts"
    assert "long-token" not in json.dumps(conn)

    request_sync(api, business, conn["id"])
    ran = run_worker(db, storage)
    assert (
        ran.status == "succeeded" and ran.result["records_created"] == 4 and ran.result["ingested"]
    )
    with scoped(db, business):
        days = {(a.day, a.platform): a for a in db.scalars(select(AdSpend))}
        assert days[(date(2026, 10, 18), "Facebook")].spend == D("12.50")
        assert {a.source for a in days.values()} == {"meta"}
        assert db.scalars(select(Integration)).one().external_account_id == ACCOUNT

    # Meta revises a recent day: it comes again with new numbers and is updated, not doubled.
    revised = [dict(r) for r in ROWS]
    revised[0] = insight("2026-10-18", "facebook", "20.00", 3000, 40, [("lead", 2)])
    provider.transport = meta_with(revised).transport
    request_sync(api, business, conn["id"])
    ran = run_worker(db, storage)
    if "records_created" not in ran.result:  # the figures were recalculated first
        ran = run_worker(db, storage)
    assert ran.status == "succeeded" and ran.result["records_created"] == 0
    with scoped(db, business):
        assert len(list(db.scalars(select(AdSpend)))) == 4
        assert db.scalar(
            select(AdSpend.spend).where(AdSpend.source_ref == "meta-20261018-facebook")
        ) == D("20.00")
