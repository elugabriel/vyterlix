# ruff: noqa: E501
"""TrueLayer: connect the business's bank account, and bring in the money going in and out.

Read-only: TrueLayer only lets Vyterlix look. It cannot move money, and Vyterlix asks for nothing
that could. It reads, for each current account the person shares,
- the transactions (money in and money out): the day, the amount, what it says on the statement
  and who it was with.

These are kept as bank transactions and shown as two figures: money into the bank and money out of
it. They are not turned into sales or costs, because those come from invoices and till takings and
counting both would count the same money twice. An amount in another currency than pounds is left
out. Pending transactions are not read until the bank has settled them.

Banks in the UK only let an app read for 90 days at a time, then the person has to agree again.
When that happens the connection says so and asks them to connect again.

Written from TrueLayer's documented Auth and Data API (v1) and tested against a pretend TrueLayer.
It has to be tried against the real sandbox (a practice bank) before it is relied on.
"""

from datetime import date, timedelta
from decimal import Decimal
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

HOSTS = {
    "sandbox": ("https://auth.truelayer-sandbox.com", "https://api.truelayer-sandbox.com"),
    "live": ("https://auth.truelayer.com", "https://api.truelayer.com"),
}
# Which banks to offer: the practice bank in the sandbox, every UK bank for real.
BANKS = {"sandbox": "uk-cs-mock uk-ob-all uk-oauth-all", "live": "uk-ob-all uk-oauth-all"}
SCOPES = ("info", "accounts", "balance", "transactions", "offline_access")
FIRST_SYNC_DAYS = 730  # the first time, ask for as far back as the bank will give
REREAD_DAYS = 3  # a bank can post a transaction a few days late
CURRENCY = "GBP"
AGAIN = "Your bank needs you to agree again. Please connect your bank again."


def _day(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


class TrueLayerProvider(Provider):
    key = "truelayer"
    label = "Bank account (TrueLayer)"
    scopes = SCOPES
    permissions = (
        "Read the money going into and out of your bank account: the day, the amount, and who it was with.",
        "Read the name of your account. Never your login, and never your card details.",
        "Your bank asks you to agree again every 90 days.",
        "Vyterlix can only look. It can never move money or change anything at your bank.",
    )
    # Tests put a pretend TrueLayer here; in real use it stays None and the real one is called.
    transport: httpx.BaseTransport | None = None

    def is_configured(self, settings: Settings) -> bool:
        return bool(settings.truelayer_client_id and settings.truelayer_client_secret)

    @staticmethod
    def _env() -> str:
        return "live" if get_settings().truelayer_environment == "live" else "sandbox"

    def _auth(self) -> str:
        return HOSTS[self._env()][0]

    def _api(self) -> str:
        return HOSTS[self._env()][1]

    def _credentials(self) -> tuple[str, str]:
        settings = get_settings()
        if not (settings.truelayer_client_id and settings.truelayer_client_secret):
            raise ProviderRejected(
                "TrueLayer has not been set up for this Vyterlix yet.", code="not_configured"
            )
        return settings.truelayer_client_id, settings.truelayer_client_secret.get_secret_value()

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=60.0, transport=self.transport)

    # --- signing in ---------------------------------------------------------------------------

    def authorize_url(self, *, state: str, redirect_uri: str, code_challenge: str) -> str:
        client_id, _ = self._credentials()
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "scope": " ".join(SCOPES),
                "redirect_uri": redirect_uri,
                "providers": BANKS[self._env()],
                "state": state,
            }
        )
        return f"{self._auth()}/?{query}"

    def _token_request(self, form: dict[str, str], *, refreshing: bool) -> TokenSet:
        client_id, secret = self._credentials()
        try:
            with self._client() as http:
                response = http.post(
                    f"{self._auth()}/connect/token",
                    data={**form, "client_id": client_id, "client_secret": secret},
                )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable() from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise ProviderUnavailable()
        if response.status_code >= 400:
            if refreshing:
                raise ReauthRequired(AGAIN)
            raise ProviderRejected(
                "TrueLayer did not accept that sign-in. Please try connecting again.",
                code="invalid_code",
            )
        body = response.json()
        return TokenSet(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token"),
            expires_at=utcnow() + timedelta(seconds=int(body.get("expires_in", 3600))),
            scopes=tuple(str(body.get("scope") or " ".join(SCOPES)).split()),
        )

    def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str) -> TokenSet:
        tokens = self._token_request(
            {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
            refreshing=False,
        )
        if "transactions" not in tokens.scopes:
            raise ProviderRejected(
                "Access to your bank's transactions was not shared. Please connect again and "
                "agree to share them.",
                code="missing_scope",
            )
        return tokens

    def refresh(self, tokens: TokenSet) -> TokenSet:
        if not tokens.refresh_token:
            raise ReauthRequired(AGAIN)
        return self._token_request(
            {"grant_type": "refresh_token", "refresh_token": tokens.refresh_token}, refreshing=True
        )

    def revoke(self, tokens: TokenSet) -> None:
        try:
            with self._client() as http:
                http.delete(
                    f"{self._auth()}/api/delete",
                    headers={"Authorization": f"Bearer {tokens.access_token}"},
                )
        except httpx.HTTPError:
            return  # best effort: the connection is removed on our side either way

    # --- talking to TrueLayer -----------------------------------------------------------------

    def _get(
        self, http: httpx.Client, path: str, tokens: TokenSet, params: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:
        try:
            response = http.get(
                f"{self._api()}/data/v1{path}",
                params=params,
                headers={"Authorization": f"Bearer {tokens.access_token}"},
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable() from exc
        if response.status_code == 401:
            raise ReauthRequired(AGAIN)
        if response.status_code == 403:
            try:
                error = str(response.json().get("error", ""))
            except ValueError:
                error = ""
            if error == "sca_exceeded":  # the 90 days are up
                raise ReauthRequired(AGAIN)
            raise ProviderRejected(
                "Your bank says Vyterlix was not allowed to read that. Please connect your bank "
                "again and agree to share your transactions.",
                code="missing_scope",
            )
        if response.status_code in (404, 501):  # this account or bank cannot give that
            raise ProviderRejected("Your bank does not share that.", code="unsupported")
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderUnavailable("Your bank is busy. We'll try again shortly.")
        if response.status_code >= 400:
            raise ProviderRejected(
                "Your bank would not give us that information. Please try again later.",
                code="provider_rejected",
            )
        return response.json().get("results", [])

    def account(self, tokens: TokenSet) -> AccountInfo:
        with self._client() as http:
            found = self._get(http, "/me", tokens)
        if not found or not found[0].get("credentials_id"):
            raise ProviderRejected(
                "TrueLayer did not say which bank was shared. Please connect again.",
                code="no_account",
            )
        me = found[0]
        bank = (me.get("provider") or {}).get("display_name") or "Bank"
        return AccountInfo(external_id=str(me["credentials_id"]), name=str(bank)[:100], settings={})

    # --- bringing the transactions in ---------------------------------------------------------

    def sync(self, ctx: SyncContext) -> SyncOutcome:
        today = ctx.now.date()
        if ctx.cursor.get("since"):
            start = date.fromisoformat(ctx.cursor["since"]) - timedelta(days=REREAD_DAYS)
        else:
            start = today - timedelta(days=FIRST_SYNC_DAYS)
        rows: list[dict[str, Any]] = []
        fetched = other_currency = 0
        with self._client() as http:
            accounts = self._get(http, "/accounts", ctx.tokens)
            for number, account in enumerate(accounts, start=1):
                if account.get("currency", CURRENCY) != CURRENCY:
                    other_currency += 1
                    continue
                try:
                    found = self._get(
                        http,
                        f"/accounts/{account['account_id']}/transactions",
                        ctx.tokens,
                        {"from": start.isoformat(), "to": today.isoformat()},
                    )
                except ProviderRejected as exc:
                    if exc.code == "unsupported":
                        continue  # this account cannot list transactions: leave it out
                    raise
                name = str(account.get("display_name") or "Account")[:100]
                for item in found:
                    fetched += 1
                    row = self._row(account["account_id"], name, item)
                    if row is not None:
                        rows.append(row)
                ctx.progress(number, len(accounts))
        return SyncOutcome(
            records_fetched=fetched,
            cursor={"since": today.isoformat(), "left_out_other_currency": other_currency},
            datasets={"bank_transactions": rows},
        )

    @staticmethod
    def _row(account_id: str, account_name: str, item: dict[str, Any]) -> dict[str, Any] | None:
        day = _day(item.get("timestamp"))
        try:
            amount = abs(Decimal(str(item.get("amount"))))
        except ArithmeticError:
            return None
        if day is None or amount == 0 or item.get("currency", CURRENCY) != CURRENCY:
            return None
        if item.get("transaction_type") == "DEBIT":
            amount = -amount
        elif item.get("transaction_type") != "CREDIT":
            return None  # not clear which way the money went: leave it out rather than guess
        ref = item.get("normalised_provider_transaction_id") or item.get("transaction_id")
        if not ref:
            return None
        return {
            "day": day,
            "amount": amount.quantize(Decimal("0.0001")),
            "description": (str(item["description"])[:300] if item.get("description") else None),
            "counterparty": (str(item["merchant_name"])[:200] if item.get("merchant_name") else None),
            "account_name": account_name,
            "reference": f"tl-{str(account_id)[:12]}-{str(ref)[:150]}",
        }  # fmt: skip
