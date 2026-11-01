"""Google Analytics: sign in with Google, and bring in how many people visit the website.

Read-only: Vyterlix only ever reads from Google Analytics (the "analytics.readonly" permission) and
never changes anything there. For each day and each kind of source (search, social, email, direct,
...) it reads
- visits (sessions),
- new visitors,
- people who visited,
- key events: what the owner set up in Analytics as important, such as an enquiry or a purchase.

Nothing here is money. It is kept as daily website visits and shown as figures of its own.

A person can have several Analytics properties (one per website). Vyterlix reads the first one it is
shown; choosing between several is not built yet. Analytics goes back and corrects the last few
days, so each sync reads again from three days before the last one.

Written from Google's documented OAuth 2.0 flow, Analytics Admin API and Analytics Data API (GA4)
and tested against a pretend Google. It has to be tried against the real account before it is
relied on.
"""

import re
from datetime import date, timedelta
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

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
ADMIN_API = "https://analyticsadmin.googleapis.com/v1beta"
DATA_API = "https://analyticsdata.googleapis.com/v1beta"
SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
PAGE_SIZE = 10000
MAX_PAGES = 100
FIRST_SYNC_DAYS = 730  # the first time, go back two years
REREAD_DAYS = 3  # Analytics corrects the most recent days
AGAIN = "Google no longer accepts this connection. Please connect Google Analytics again."
METRICS = ("sessions", "activeUsers", "newUsers", "keyEvents")


def _count(text: str | None) -> int:
    try:
        return max(0, int(float(text or 0)))
    except ValueError:
        return 0


def _slug(channel: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", channel.lower()).strip("-")[:40] or "other"


class GoogleAnalyticsProvider(Provider):
    key = "google_analytics"
    label = "Google Analytics"
    scopes = (SCOPE,)
    permissions = (
        "Read how many people visit your website, day by day.",
        "Read where they came from in broad groups (search, social, email), not who they are.",
        "Read how many of them did what you set up as important, such as sending an enquiry.",
        "Vyterlix never changes anything in Google Analytics.",
    )
    # Tests put a pretend Google here; in real use it stays None and the real one is called.
    transport: httpx.BaseTransport | None = None

    def is_configured(self, settings: Settings) -> bool:
        return bool(settings.google_client_id and settings.google_client_secret)

    @staticmethod
    def _credentials() -> tuple[str, str]:
        settings = get_settings()
        if not (settings.google_client_id and settings.google_client_secret):
            raise ProviderRejected(
                "Google has not been set up for this Vyterlix yet.", code="not_configured"
            )
        return settings.google_client_id, settings.google_client_secret.get_secret_value()

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=60.0, transport=self.transport)

    # --- signing in ---------------------------------------------------------------------------

    def authorize_url(self, *, state: str, redirect_uri: str, code_challenge: str) -> str:
        client_id, _ = self._credentials()
        query = urlencode(
            {
                "client_id": client_id,
                "response_type": "code",
                "scope": SCOPE,
                "redirect_uri": redirect_uri,
                "state": state,
                "access_type": "offline",  # so we get a refresh token
                "prompt": "consent",  # Google only sends the refresh token the first time otherwise
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{AUTHORIZE_URL}?{query}"

    def _token_request(self, form: dict[str, str], *, refreshing: bool) -> TokenSet:
        client_id, secret = self._credentials()
        try:
            with self._client() as http:
                response = http.post(
                    TOKEN_URL, data={**form, "client_id": client_id, "client_secret": secret}
                )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable() from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise ProviderUnavailable()
        if response.status_code >= 400:
            if refreshing:
                raise ReauthRequired(AGAIN)
            raise ProviderRejected(
                "Google did not accept that sign-in. Please try connecting again.",
                code="invalid_code",
            )
        body = response.json()
        granted = tuple(str(body.get("scope", SCOPE)).split())
        return TokenSet(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token"),
            expires_at=utcnow() + timedelta(seconds=int(body.get("expires_in", 3600))),
            scopes=granted,
        )

    def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str) -> TokenSet:
        tokens = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
            refreshing=False,
        )
        if SCOPE not in tokens.scopes:
            raise ProviderRejected(
                "Google Analytics access was not shared. Please connect again and tick the box "
                "that lets Vyterlix view your Analytics.",
                code="missing_scope",
            )
        return tokens

    def refresh(self, tokens: TokenSet) -> TokenSet:
        if not tokens.refresh_token:
            raise ReauthRequired(AGAIN)
        fresh = self._token_request(
            {"grant_type": "refresh_token", "refresh_token": tokens.refresh_token}, refreshing=True
        )
        return TokenSet(
            fresh.access_token, tokens.refresh_token, fresh.expires_at, fresh.scopes, tokens.extra
        )

    def revoke(self, tokens: TokenSet) -> None:
        token = tokens.refresh_token or tokens.access_token
        try:
            with self._client() as http:
                http.post(REVOKE_URL, data={"token": token})
        except httpx.HTTPError:
            return  # best effort: the connection is removed on our side either way

    # --- talking to Google --------------------------------------------------------------------

    def _call(
        self,
        http: httpx.Client,
        method: str,
        url: str,
        tokens: TokenSet,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = http.request(
                method,
                url,
                json=body,
                headers={"Authorization": f"Bearer {tokens.access_token}"},
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable() from exc
        if response.status_code == 401:
            raise ReauthRequired(AGAIN)
        if response.status_code == 403:
            raise ProviderRejected(
                "Google says Vyterlix was not allowed to read that. Please connect again with a "
                "Google account that can view your Analytics.",
                code="missing_scope",
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderUnavailable("Google is busy. We'll try again shortly.")
        if response.status_code >= 400:
            raise ProviderRejected(
                "Google would not give us that information. Please try again later.",
                code="provider_rejected",
            )
        return response.json()

    def account(self, tokens: TokenSet) -> AccountInfo:
        with self._client() as http:
            summaries = self._call(http, "GET", f"{ADMIN_API}/accountSummaries", tokens)
        for account in summaries.get("accountSummaries", []):
            for prop in account.get("propertySummaries", []):
                number = str(prop.get("property", "")).removeprefix("properties/")
                if number.isdigit():
                    return AccountInfo(
                        external_id=number,
                        name=str(prop.get("displayName") or "Website")[:100],
                        settings={"account": account.get("displayName")},
                    )
        raise ProviderRejected(
            "That Google account has no Google Analytics website on it. Connect the account "
            "that you use for Analytics.",
            code="no_property",
        )

    # --- bringing the visits in ---------------------------------------------------------------

    def sync(self, ctx: SyncContext) -> SyncOutcome:
        today = ctx.now.date()
        if ctx.cursor.get("since"):
            start = date.fromisoformat(ctx.cursor["since"]) - timedelta(days=REREAD_DAYS)
        else:
            start = today - timedelta(days=FIRST_SYNC_DAYS)
        rows: list[dict[str, Any]] = []
        total = 0
        with self._client() as http:
            for page in range(MAX_PAGES):
                body = self._call(
                    http,
                    "POST",
                    f"{DATA_API}/properties/{ctx.account.external_id}:runReport",
                    ctx.tokens,
                    {
                        "dateRanges": [
                            {"startDate": start.isoformat(), "endDate": today.isoformat()}
                        ],
                        "dimensions": [{"name": "date"}, {"name": "sessionDefaultChannelGroup"}],
                        "metrics": [{"name": m} for m in METRICS],
                        "limit": PAGE_SIZE,
                        "offset": page * PAGE_SIZE,
                    },
                )
                found = body.get("rows", [])
                rows.extend(self._row(r) for r in found)
                total = int(body.get("rowCount") or len(rows))
                ctx.progress(len(rows), total)
                if not found or len(rows) >= total:
                    break
        rows = [r for r in rows if r is not None]
        return SyncOutcome(
            records_fetched=len(rows),
            cursor={"since": today.isoformat()},
            datasets={"web_visits": rows},
        )

    @staticmethod
    def _row(item: dict[str, Any]) -> dict[str, Any] | None:
        dims = [d.get("value", "") for d in item.get("dimensionValues", [])]
        values = [m.get("value") for m in item.get("metricValues", [])]
        if len(dims) < 2 or len(values) < len(METRICS):
            return None
        try:
            day = date(int(dims[0][:4]), int(dims[0][4:6]), int(dims[0][6:8]))
        except ValueError:
            return None
        channel = (dims[1] or "Other")[:60]
        sessions, users, new_users, key_events = (_count(v) for v in values[: len(METRICS)])
        return {
            "day": day, "channel": channel, "sessions": sessions, "users": users,
            "new_users": new_users, "key_events": key_events,
            "reference": f"ga-{day:%Y%m%d}-{_slug(channel)}",
        }  # fmt: skip
