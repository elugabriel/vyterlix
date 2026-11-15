"""Meta: sign in with Facebook, and bring in what you spend on Facebook and Instagram adverts.

Read-only: Vyterlix asks for the "ads_read" permission only. It can see what your adverts cost and
how they did; it can never create, change, pause or pay for an advert, and never posts anything.
For each day and each place the adverts were shown (Facebook, Instagram, ...) it reads
- what was spent,
- how many times the adverts were shown and clicked,
- how many results they brought: enquiries (leads), orders (purchases), appointments, contacts and
  new conversations, as Meta counts them.

A person can have several ad accounts. Vyterlix reads the first active one; choosing between
several is not built yet. Money is read in pounds only: an ad account in another currency is
refused with a plain message rather than mixed in. Meta revises the most recent days, so each sync
reads again from a week before the last one.

Meta gives no refresh token. A sign-in is turned into a "long-lived" token that lasts about 60 days
and is extended while it is still valid; once it has run out the person is asked to connect again.

Written from Meta's documented Facebook Login and Marketing API (insights) and tested against a
pretend Meta. It has to be tried against a real ad account before it is relied on.
"""

import json
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import Settings, get_settings
from app.integrations.base import (
    AccountInfo,
    Provider,
    ProviderRejected,
    ProviderUnavailable,
    ReauthRequired,
    SyncContext,
    SyncOutcome,
    TokenSet,
    utcnow,
)

SCOPE = "ads_read"
FIRST_SYNC_DAYS = 730  # the first time, go back two years
REREAD_DAYS = 7  # Meta revises the most recent days
PAGE_SIZE = 500
MAX_PAGES = 100
AGAIN = (
    "Meta no longer accepts this connection. Please connect Facebook and Instagram adverts again."
)
# What counts as a result. Meta's "lead" and "purchase" already add up the ways of getting one (on
# Facebook, on a website), so the others are not added again.
RESULT_ACTIONS = frozenset(
    {
        "lead",
        "purchase",
        "schedule",
        "contact",
        "onsite_conversion.messaging_conversation_started_7d",
    }
)
PLATFORMS = {
    "facebook": "Facebook",
    "instagram": "Instagram",
    "audience_network": "Audience Network",
    "messenger": "Messenger",
}
RATE_LIMIT_CODES = {4, 17, 32, 341, 613}  # Meta's "slow down" codes


def _count(text: Any) -> int:
    try:
        return max(0, int(float(text or 0)))
    except (TypeError, ValueError):
        return 0


def _pounds(text: Any) -> Decimal | None:
    try:
        value = Decimal(str(text))
    except InvalidOperation:
        return None
    return value.quantize(Decimal("0.01")) if value >= 0 else None


def _slug(platform: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", platform.lower()).strip("-")[:30] or "other"


class MetaProvider(Provider):
    key = "meta"
    label = "Facebook and Instagram adverts (Meta)"
    scopes = (SCOPE,)
    permissions = (
        "Read how much you spend on Facebook and Instagram adverts, day by day.",
        "Read how many people saw and clicked on them, and how many enquiries or orders came.",
        "Read which ad account you use.",
        "Vyterlix never changes your adverts, never spends your money and never posts anything.",
    )
    # Tests put a pretend Meta here; in real use it stays None and the real one is called.
    transport: httpx.BaseTransport | None = None

    def is_configured(self, settings: Settings) -> bool:
        return bool(settings.meta_app_id and settings.meta_app_secret)

    @staticmethod
    def _credentials() -> tuple[str, str, str]:
        settings = get_settings()
        if not (settings.meta_app_id and settings.meta_app_secret):
            raise ProviderRejected(
                "Meta has not been set up for this Vyterlix yet.", code="not_configured"
            )
        return (
            settings.meta_app_id,
            settings.meta_app_secret.get_secret_value(),
            settings.meta_graph_version,
        )

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=60.0, transport=self.transport)

    # --- signing in ---------------------------------------------------------------------------

    def authorize_url(self, *, state: str, redirect_uri: str, code_challenge: str) -> str:
        app_id, _, version = self._credentials()
        query = urlencode(
            {
                "client_id": app_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "scope": SCOPE,
                "response_type": "code",
            }
        )
        return f"https://www.facebook.com/{version}/dialog/oauth?{query}"

    def _token_call(self, params: dict[str, str], *, refreshing: bool) -> dict[str, Any]:
        app_id, secret, version = self._credentials()
        try:
            with self._client() as http:
                response = http.get(
                    f"https://graph.facebook.com/{version}/oauth/access_token",
                    params={**params, "client_id": app_id, "client_secret": secret},
                )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable() from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise ProviderUnavailable()
        if response.status_code >= 400:
            if refreshing:
                raise ReauthRequired(AGAIN)
            raise ProviderRejected(
                "Meta did not accept that sign-in. Please try connecting again.",
                code="invalid_code",
            )
        return response.json()

    def _long_lived(self, token: str, *, refreshing: bool) -> TokenSet:
        body = self._token_call(
            {"grant_type": "fb_exchange_token", "fb_exchange_token": token}, refreshing=refreshing
        )
        # With no stated length Meta's long-lived token still lasts about 60 days
        seconds = int(body.get("expires_in") or 60 * 24 * 3600)
        return TokenSet(
            access_token=body["access_token"],
            refresh_token=None,
            expires_at=utcnow() + timedelta(seconds=seconds),
            scopes=(SCOPE,),
        )

    def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str) -> TokenSet:
        short = self._token_call({"redirect_uri": redirect_uri, "code": code}, refreshing=False)
        tokens = self._long_lived(short["access_token"], refreshing=False)
        if SCOPE not in self._granted(tokens):
            raise ProviderRejected(
                "Access to your adverts was not shared. Please connect again and allow Vyterlix to "
                "see your ad account.",
                code="missing_scope",
            )
        return tokens

    def refresh(self, tokens: TokenSet) -> TokenSet:
        """Meta has no refresh token: a token that is still valid is exchanged for a fresh one."""
        return self._long_lived(tokens.access_token, refreshing=True)

    def revoke(self, tokens: TokenSet) -> None:
        _, _, version = self._credentials()
        try:
            with self._client() as http:
                http.delete(
                    f"https://graph.facebook.com/{version}/me/permissions",
                    headers={"Authorization": f"Bearer {tokens.access_token}"},
                )
        except httpx.HTTPError:
            return  # best effort: the connection is removed on our side either way

    # --- talking to Meta ----------------------------------------------------------------------

    def _call(
        self, http: httpx.Client, path: str, tokens: TokenSet, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        _, _, version = self._credentials()
        try:
            response = http.get(
                f"https://graph.facebook.com/{version}{path}",
                params=params,
                headers={"Authorization": f"Bearer {tokens.access_token}"},
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable() from exc
        if response.status_code >= 400:
            error = {}
            try:
                error = response.json().get("error", {})
            except ValueError:
                pass
            code = int(error.get("code") or 0)
            if code == 190 or response.status_code == 401:  # the token is no good any more
                raise ReauthRequired(AGAIN)
            if (
                code in RATE_LIMIT_CODES
                or response.status_code == 429
                or response.status_code >= 500
            ):
                raise ProviderUnavailable("Meta is busy. We'll try again shortly.")
            if code in (10, 200, 294) or response.status_code == 403:
                raise ProviderRejected(
                    "Meta says Vyterlix was not allowed to read your adverts. Please connect "
                    "again with an account that can see your ad account.",
                    code="missing_scope",
                )
            raise ProviderRejected(
                "Meta would not give us that information. Please try again later.",
                code="provider_rejected",
            )
        return response.json()

    def _granted(self, tokens: TokenSet) -> set[str]:
        with self._client() as http:
            body = self._call(http, "/me/permissions", tokens)
        return {p["permission"] for p in body.get("data", []) if p.get("status") == "granted"}

    def account(self, tokens: TokenSet) -> AccountInfo:
        with self._client() as http:
            body = self._call(
                http,
                "/me/adaccounts",
                tokens,
                {"fields": "account_id,name,currency,account_status", "limit": 50},
            )
        accounts = body.get("data", [])
        if not accounts:
            raise ProviderRejected(
                "That Facebook account has no ad account on it. Connect the account that you use "
                "for your adverts.",
                code="no_ad_account",
            )
        chosen = next((a for a in accounts if a.get("account_status") == 1), accounts[0])
        currency = str(chosen.get("currency") or "").upper()
        if currency != "GBP":
            raise ProviderRejected(
                f"That ad account is in {currency or 'another currency'}. Vyterlix works in pounds "
                "for now, so it can only read an ad account that is in pounds (GBP).",
                code="currency_not_supported",
            )
        number = str(chosen.get("account_id") or "").removeprefix("act_")
        if not number.isdigit():
            raise ProviderRejected(
                "Meta did not say which ad account that is.", code="no_ad_account"
            )
        return AccountInfo(
            external_id=number,
            name=str(chosen.get("name") or "Ad account")[:100],
            settings={"currency": currency},
        )

    # --- bringing the adverts in --------------------------------------------------------------

    def sync(self, ctx: SyncContext) -> SyncOutcome:
        today = ctx.now.date()
        if ctx.cursor.get("since"):
            start = date.fromisoformat(ctx.cursor["since"]) - timedelta(days=REREAD_DAYS)
        else:
            start = today - timedelta(days=FIRST_SYNC_DAYS)
        rows: list[dict[str, Any]] = []
        after: str | None = None
        with self._client() as http:
            for _ in range(MAX_PAGES):
                params: dict[str, Any] = {
                    "level": "account",
                    "time_increment": 1,
                    "breakdowns": "publisher_platform",
                    "fields": "spend,impressions,clicks,actions",
                    "time_range": json.dumps(
                        {"since": start.isoformat(), "until": today.isoformat()}
                    ),
                    "limit": PAGE_SIZE,
                }
                if after:
                    params["after"] = after
                body = self._call(
                    http, f"/act_{ctx.account.external_id}/insights", ctx.tokens, params
                )
                rows.extend(self._row(item) for item in body.get("data", []))
                ctx.progress(len(rows), len(rows))
                after = (body.get("paging", {}).get("cursors", {}) or {}).get("after")
                if not after or not body.get("paging", {}).get("next"):
                    break
        rows = [r for r in rows if r is not None]
        return SyncOutcome(
            records_fetched=len(rows),
            cursor={"since": today.isoformat()},
            datasets={"ad_spend": rows},
        )

    @staticmethod
    def _row(item: dict[str, Any]) -> dict[str, Any] | None:
        try:
            day = date.fromisoformat(str(item.get("date_start")))
        except ValueError:
            return None
        spend = _pounds(item.get("spend", "0"))
        if spend is None:
            return None
        raw = str(item.get("publisher_platform") or "other")
        platform = PLATFORMS.get(raw, raw.replace("_", " ").title())[:40]
        results = sum(
            _count(a.get("value"))
            for a in item.get("actions", []) or []
            if a.get("action_type") in RESULT_ACTIONS
        )
        return {
            "day": day, "platform": platform, "spend": spend,
            "impressions": _count(item.get("impressions")), "clicks": _count(item.get("clicks")),
            "results": results, "reference": f"meta-{day:%Y%m%d}-{_slug(platform)}",
        }  # fmt: skip
