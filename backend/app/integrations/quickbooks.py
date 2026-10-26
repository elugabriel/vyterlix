"""QuickBooks Online: sign in, and bring in the business's sales and costs.

Read-only: Vyterlix only ever reads from QuickBooks and never changes anything there. It reads
- invoices and sales receipts, and brings them in as sales (an invoice carries its due and paid
  days, so money owed can be worked out);
- credit memos, as refunds;
- bills and expenses, and brings them in as costs;
- the name of the customer or supplier on each.

An amount in another currency than pounds is left out, and counted, rather than guessed at.

QuickBooks does not say which company was shared in the sign-in answer: it sends a "realmId" back on
the redirect address, which the framework hands over (see `callback_params`). It does not support
the extra sign-in check (PKCE) that Xero does, so none is sent.

Written from Intuit's documented OAuth 2.0 flow and Accounting API, and tested against a pretend
QuickBooks. It has to be tried against the real sandbox company before it is relied on.
"""

import base64
from datetime import date, datetime, timedelta
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

AUTHORIZE_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
REVOKE_URL = "https://developer.api.intuit.com/v2/oauth2/tokens/revoke"
API = {
    "sandbox": "https://sandbox-quickbooks.api.intuit.com",
    "production": "https://quickbooks.api.intuit.com",
}
MINOR_VERSION = "75"
PAGE_SIZE = 500
FIRST_SYNC_DAYS = 730  # the first time, go back two years
MAX_PAGES = 200
CURRENCY = "GBP"
ENTITIES = ("Invoice", "SalesReceipt", "CreditMemo", "Bill", "Purchase")
PREFIX = {
    "Invoice": "inv",
    "SalesReceipt": "sr",
    "CreditMemo": "cm",
    "Bill": "bill",
    "Purchase": "pur",
}


def _day(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def _money(value: Any) -> Decimal:
    return Decimal(str(value if value is not None else 0)).quantize(Decimal("0.0001"))


def _name(ref: Any) -> str | None:
    if isinstance(ref, dict):
        return (ref.get("name") or None) and str(ref["name"])
    return None


class QuickBooksProvider(Provider):
    key = "quickbooks"
    label = "QuickBooks"
    scopes = ("com.intuit.quickbooks.accounting",)
    permissions = (
        "Read your invoices and sales receipts, so we can show your sales and what you are owed.",
        "Read your bills and expenses, so we can show your costs.",
        "Read the names of your customers and suppliers.",
        "Vyterlix never changes anything in QuickBooks.",
    )
    # QuickBooks sends the company's id back on the redirect address, not in the tokens.
    callback_params = ("realmId",)
    # Tests put a pretend QuickBooks here; in real use it stays None and the real one is called.
    transport: httpx.BaseTransport | None = None

    def is_configured(self, settings: Settings) -> bool:
        return bool(settings.quickbooks_client_id and settings.quickbooks_client_secret)

    def _credentials(self) -> tuple[str, str]:
        settings = get_settings()
        if not (settings.quickbooks_client_id and settings.quickbooks_client_secret):
            raise ProviderRejected(
                "QuickBooks has not been set up for this Vyterlix yet.", code="not_configured"
            )
        return settings.quickbooks_client_id, settings.quickbooks_client_secret.get_secret_value()

    def _basic(self) -> str:
        client_id, secret = self._credentials()
        return "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode()

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=30.0, transport=self.transport)

    @staticmethod
    def _api() -> str:
        return API.get(get_settings().quickbooks_environment, API["sandbox"])

    # --- signing in ---------------------------------------------------------------------------

    def authorize_url(self, *, state: str, redirect_uri: str, code_challenge: str) -> str:
        client_id, _ = self._credentials()
        query = urlencode(
            {
                "client_id": client_id,
                "response_type": "code",
                "scope": " ".join(self.scopes),
                "redirect_uri": redirect_uri,
                "state": state,
            }
        )
        return f"{AUTHORIZE_URL}?{query}"

    def _token_request(self, form: dict[str, str], *, refreshing: bool) -> TokenSet:
        try:
            with self._client() as http:
                response = http.post(
                    TOKEN_URL,
                    data=form,
                    headers={"Authorization": self._basic(), "Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable() from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise ProviderUnavailable()
        if response.status_code >= 400:
            if refreshing:
                raise ReauthRequired(
                    "QuickBooks no longer accepts this connection. Please connect QuickBooks again."
                )
            raise ProviderRejected(
                "QuickBooks did not accept that sign-in. Please try connecting again.",
                code="invalid_code",
            )
        body = response.json()
        return TokenSet(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token"),
            expires_at=utcnow() + timedelta(seconds=int(body.get("expires_in", 3600))),
            scopes=self.scopes,
        )

    def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str) -> TokenSet:
        return self._token_request(
            {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
            refreshing=False,
        )

    def refresh(self, tokens: TokenSet) -> TokenSet:
        # QuickBooks sends a new refresh token now and then; the framework keeps whichever is new,
        # and the company id (kept with the tokens) must not be lost.
        if not tokens.refresh_token:
            raise ReauthRequired(
                "QuickBooks no longer accepts this connection. Please connect QuickBooks again."
            )
        fresh = self._token_request(
            {"grant_type": "refresh_token", "refresh_token": tokens.refresh_token}, refreshing=True
        )
        return TokenSet(
            fresh.access_token, fresh.refresh_token, fresh.expires_at, fresh.scopes, tokens.extra
        )

    def revoke(self, tokens: TokenSet) -> None:
        if not tokens.refresh_token:
            return
        try:
            with self._client() as http:
                http.post(
                    REVOKE_URL,
                    json={"token": tokens.refresh_token},
                    headers={"Authorization": self._basic(), "Accept": "application/json"},
                )
        except (httpx.HTTPError, ProviderRejected):
            return  # best effort: the connection is removed on our side either way

    # --- talking to QuickBooks -----------------------------------------------------------------

    def _get(
        self, http: httpx.Client, path: str, tokens: TokenSet, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        query = {"minorversion": MINOR_VERSION, **(params or {})}
        try:
            response = http.get(
                f"{self._api()}{path}",
                params=query,
                headers={
                    "Authorization": f"Bearer {tokens.access_token}",
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable() from exc
        if response.status_code == 401:
            raise ReauthRequired(
                "QuickBooks no longer accepts this connection. Please connect QuickBooks again."
            )
        if response.status_code == 403:
            raise ProviderRejected(
                "QuickBooks says Vyterlix was not allowed to read that. Please connect QuickBooks "
                "again and approve access.",
                code="missing_scope",
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderUnavailable("QuickBooks is busy. We'll try again shortly.")
        if response.status_code >= 400:
            raise ProviderRejected(
                "QuickBooks would not give us that information. Please try again later.",
                code="provider_rejected",
            )
        return response.json()

    def account(self, tokens: TokenSet) -> AccountInfo:
        realm = str(tokens.extra.get("realmId") or "")
        if not realm.isdigit():
            raise ProviderRejected(
                "QuickBooks did not say which company was shared. Please connect again and "
                "choose the company to share.",
                code="no_company",
            )
        with self._client() as http:
            info = self._get(http, f"/v3/company/{realm}/companyinfo/{realm}", tokens).get(
                "CompanyInfo", {}
            )
        return AccountInfo(
            external_id=realm,
            name=str(info.get("CompanyName") or info.get("LegalName") or "QuickBooks company"),
            settings={
                "country": info.get("Country"),
                "environment": get_settings().quickbooks_environment,
            },
        )

    # --- bringing the data in -----------------------------------------------------------------

    def _query(
        self, http: httpx.Client, ctx: SyncContext, entity: str, where: str
    ) -> list[dict[str, Any]]:
        realm = ctx.account.external_id
        found: list[dict[str, Any]] = []
        for page in range(MAX_PAGES):
            start = page * PAGE_SIZE + 1
            body = self._get(
                http,
                f"/v3/company/{realm}/query",
                ctx.tokens,
                {
                    "query": f"select * from {entity} where {where} order by Id "
                    f"startposition {start} maxresults {PAGE_SIZE}"
                },
            )
            batch = body.get("QueryResponse", {}).get(entity, [])
            found.extend(batch)
            ctx.progress(len(found), len(found) + (PAGE_SIZE if len(batch) == PAGE_SIZE else 0))
            if len(batch) < PAGE_SIZE:
                break
        return found

    def sync(self, ctx: SyncContext) -> SyncOutcome:
        if ctx.cursor.get("since"):
            since = datetime.fromisoformat(ctx.cursor["since"])
            where = f"MetaData.LastUpdatedTime >= '{since.strftime('%Y-%m-%dT%H:%M:%S')}+00:00'"
        else:
            first = (ctx.now - timedelta(days=FIRST_SYNC_DAYS)).date()
            where = f"TxnDate >= '{first.isoformat()}'"
        sales: list[dict[str, Any]] = []
        costs: list[dict[str, Any]] = []
        other_currency = 0
        fetched = 0
        with self._client() as http:
            paid_on = self._payment_days(http, ctx, where)
            for entity in ENTITIES:
                for record in self._query(http, ctx, entity, where):
                    fetched += 1
                    currency = (record.get("CurrencyRef") or {}).get("value", CURRENCY)
                    if currency != CURRENCY:
                        other_currency += 1
                        continue
                    row = self._row(entity, record, paid_on)
                    if row is not None:
                        (costs if entity in ("Bill", "Purchase") else sales).append(row)
        return SyncOutcome(
            records_fetched=fetched,
            cursor={"since": ctx.now.isoformat(), "left_out_other_currency": other_currency},
            datasets={"sales": sales, "expenses": costs},
        )

    def _payment_days(self, http: httpx.Client, ctx: SyncContext, where: str) -> dict[str, date]:
        """The day each invoice was last paid, from the payments in the same window."""
        days: dict[str, date] = {}
        for payment in self._query(http, ctx, "Payment", where):
            day = _day(payment.get("TxnDate"))
            for line in payment.get("Line", []):
                for linked in line.get("LinkedTxn", []):
                    if linked.get("TxnType") == "Invoice" and day is not None:
                        key = str(linked.get("TxnId"))
                        days[key] = max(day, days.get(key, day))
        return days

    # --- one QuickBooks record -> one row ------------------------------------------------------

    @staticmethod
    def _amounts(record: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal] | None:
        total = _money(record.get("TotalAmt"))
        vat = _money((record.get("TxnTaxDetail") or {}).get("TotalTax"))
        net = total - vat
        if total == 0 or net < 0 or vat < 0:
            return None  # nothing to record, or not something that adds up
        return net, vat, total

    def _row(self, entity: str, record: dict[str, Any], paid_on: dict[str, date]) -> dict | None:
        when, amounts = _day(record.get("TxnDate")), self._amounts(record)
        if when is None or amounts is None:
            return None
        net, vat, gross = amounts
        reference = f"{PREFIX[entity]}-{record['Id']}"
        number = record.get("DocNumber") or record["Id"]
        if entity in ("Bill", "Purchase"):
            party = record.get("VendorRef") if entity == "Bill" else record.get("EntityRef")
            return {
                "kind": "expense", "spent_on": when, "net_amount": net, "vat_amount": vat,
                "gross_amount": gross, "reference": reference, "supplier": _name(party),
                "description": f"QuickBooks {'bill' if entity == 'Bill' else 'expense'} {number}",
            }  # fmt: skip
        sign = Decimal(-1) if entity == "CreditMemo" else Decimal(1)
        row: dict[str, Any] = {
            "kind": "refund" if entity == "CreditMemo" else "sale",
            "sold_on": when, "net_amount": sign * net, "vat_amount": sign * vat,
            "gross_amount": sign * gross, "reference": reference,
            "notes": f"QuickBooks {entity} {number}",
            "customer_name": _name(record.get("CustomerRef")),
        }  # fmt: skip
        if entity == "Invoice":
            due = _day(record.get("DueDate"))
            row["due_on"] = None if due is None else max(due, when)  # never before the sale
            if _money(record.get("Balance")) == 0:
                last_change = _day((record.get("MetaData") or {}).get("LastUpdatedTime"))
                row["paid_on"] = paid_on.get(str(record["Id"])) or last_change or when
            else:
                row["paid_on"] = None
        # What was sold: only when it is one clearly named item, so a mixed one is not guessed at.
        items = [
            line
            for line in record.get("Line", [])
            if line.get("DetailType") == "SalesItemLineDetail"
        ]
        if len(items) == 1:
            detail = items[0].get("SalesItemLineDetail") or {}
            name = _name(detail.get("ItemRef"))
            if name:
                row["product"] = name[:200]
                if detail.get("Qty"):
                    row["quantity"] = _money(abs(Decimal(str(detail["Qty"]))))
        return row
