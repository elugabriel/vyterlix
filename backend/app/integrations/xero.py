"""Xero: sign in, and bring in the business's sales invoices and bills.

Read-only: Vyterlix only ever reads from Xero and never changes anything there. It reads
- sales invoices (Xero "ACCREC") and brings them in as sales, with their due and paid days, so money
  owed to the business can be worked out;
- bills (Xero "ACCPAY") and brings them in as costs;
- the name of the customer or supplier on each, so they can be told apart.

Only invoices that have been approved or paid are read: a draft or a voided invoice is not money. An
invoice in another currency than pounds is left out, and counted, rather than guessed at.

Written from Xero's documented OAuth 2.0 flow and Accounting API, and tested against a pretend Xero.
It has to be tried against a real (demo) Xero company before it is relied on.
"""

import base64
import re
from datetime import UTC, date, datetime, timedelta
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

AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
REVOKE_URL = "https://identity.xero.com/connect/revocation"
CONNECTIONS_URL = "https://api.xero.com/connections"
INVOICES_URL = "https://api.xero.com/api.xro/2.0/Invoices"

PAGE_SIZE = 100
FIRST_SYNC_DAYS = 730  # the first time, go back two years
MAX_PAGES = 500  # a stop against a runaway loop (50,000 invoices a type)
KEPT_STATUSES = "AUTHORISED,PAID"  # not drafts, not voided, not deleted
CURRENCY = "GBP"
_DOTNET_DATE = re.compile(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/")


def _day(text: str | None) -> date | None:
    """A date from Xero, which writes it as 2026-09-10T00:00:00 or as /Date(1789000000000+0000)/."""
    if not text:
        return None
    match = _DOTNET_DATE.match(text)
    if match:
        return datetime.fromtimestamp(int(match[1]) / 1000, tz=UTC).date()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _money(value: Any) -> Decimal:
    return Decimal(str(value if value is not None else 0)).quantize(Decimal("0.0001"))


class XeroProvider(Provider):
    key = "xero"
    label = "Xero"
    scopes = (
        "offline_access",
        "accounting.invoices.read",
        "accounting.contacts.read",
    )
    permissions = (
        "Read your sales invoices, so we can show your sales and what customers owe you.",
        "Read your bills, so we can show your costs.",
        "Read the names of your customers and suppliers.",
        "Vyterlix never changes anything in Xero.",
    )
    # Tests put a pretend Xero here; in real use it stays None and the real one is called.
    transport: httpx.BaseTransport | None = None

    def is_configured(self, settings: Settings) -> bool:
        return bool(settings.xero_client_id and settings.xero_client_secret)

    # --- signing in ---------------------------------------------------------------------------

    def _credentials(self) -> tuple[str, str]:
        settings = get_settings()
        if not (settings.xero_client_id and settings.xero_client_secret):
            raise ProviderRejected(
                "Xero has not been set up for this Vyterlix yet.", code="not_configured"
            )
        return settings.xero_client_id, settings.xero_client_secret.get_secret_value()

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=20.0, transport=self.transport)

    def authorize_url(self, *, state: str, redirect_uri: str, code_challenge: str) -> str:
        client_id, _ = self._credentials()
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": " ".join(self.scopes),
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{AUTHORIZE_URL}?{query}"

    def _token_request(self, form: dict[str, str], *, refreshing: bool) -> TokenSet:
        client_id, secret = self._credentials()
        basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
        try:
            with self._client() as http:
                response = http.post(
                    TOKEN_URL, data=form, headers={"Authorization": f"Basic {basic}"}
                )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable() from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise ProviderUnavailable()
        if response.status_code >= 400:
            if refreshing:
                raise ReauthRequired(
                    "Xero no longer accepts this connection. Please connect Xero again."
                )
            raise ProviderRejected(
                "Xero did not accept that sign-in. Please try connecting again.",
                code="invalid_code",
            )
        body = response.json()
        return TokenSet(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token"),
            expires_at=utcnow() + timedelta(seconds=int(body.get("expires_in", 1800))),
            scopes=tuple(str(body.get("scope", "")).split()),
        )

    def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str) -> TokenSet:
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
            refreshing=False,
        )

    def refresh(self, tokens: TokenSet) -> TokenSet:
        # Xero gives a new refresh token each time and the old one stops working, so the new one
        # must be kept (the framework saves what this returns).
        if not tokens.refresh_token:
            raise ReauthRequired(
                "Xero no longer accepts this connection. Please connect Xero again."
            )
        return self._token_request(
            {"grant_type": "refresh_token", "refresh_token": tokens.refresh_token},
            refreshing=True,
        )

    def revoke(self, tokens: TokenSet) -> None:
        if not tokens.refresh_token:
            return
        try:
            client_id, secret = self._credentials()
            basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
            with self._client() as http:
                http.post(
                    REVOKE_URL,
                    data={"token": tokens.refresh_token},
                    headers={"Authorization": f"Basic {basic}"},
                )
        except (httpx.HTTPError, ProviderRejected):
            return  # best effort: the connection is removed on our side either way

    # --- who is connected ---------------------------------------------------------------------

    def _get(self, http: httpx.Client, url: str, tokens: TokenSet, **kwargs: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {tokens.access_token}", "Accept": "application/json"}
        headers.update(kwargs.pop("headers", {}))
        try:
            response = http.get(url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable() from exc
        if response.status_code == 401:
            raise ReauthRequired(
                "Xero no longer accepts this connection. Please connect Xero again."
            )
        if response.status_code == 403:
            raise ProviderRejected(
                "Xero says Vyterlix was not allowed to read that. Please connect Xero again and "
                "approve access.",
                code="missing_scope",
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderUnavailable("Xero is busy. We'll try again shortly.")
        if response.status_code >= 400:
            raise ProviderRejected(
                "Xero would not give us that information. Please try again later.",
                code="provider_rejected",
            )
        return response

    def account(self, tokens: TokenSet) -> AccountInfo:
        with self._client() as http:
            connections = self._get(http, CONNECTIONS_URL, tokens).json()
        orgs = [c for c in connections if c.get("tenantType", "ORGANISATION") == "ORGANISATION"]
        if not orgs:
            raise ProviderRejected(
                "No Xero organisation was shared with Vyterlix. Please connect again and choose "
                "the organisation to share.",
                code="no_organisation",
            )
        # If several were shared, use the most recently connected one.
        orgs.sort(key=lambda c: str(c.get("updatedDateUtc") or c.get("createdDateUtc") or ""))
        chosen = orgs[-1]
        return AccountInfo(
            external_id=str(chosen["tenantId"]),
            name=str(chosen.get("tenantName") or "Xero organisation"),
            settings={"shared_organisations": len(orgs)},
        )

    # --- bringing the data in -----------------------------------------------------------------

    def _invoices(
        self, http: httpx.Client, ctx: SyncContext, kind: str, since: datetime
    ) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            response = self._get(
                http,
                INVOICES_URL,
                ctx.tokens,
                params={
                    "page": page,
                    "pageSize": PAGE_SIZE,
                    "Statuses": KEPT_STATUSES,
                    "where": f'Type=="{kind}"',
                },
                headers={
                    "xero-tenant-id": ctx.account.external_id,
                    "If-Modified-Since": since.strftime("%Y-%m-%dT%H:%M:%S"),
                },
            )
            batch = response.json().get("Invoices", [])
            found.extend(batch)
            ctx.progress(len(found), len(found) + (PAGE_SIZE if len(batch) == PAGE_SIZE else 0))
            if len(batch) < PAGE_SIZE:
                return found
        return found

    def sync(self, ctx: SyncContext) -> SyncOutcome:
        since = (
            datetime.fromisoformat(ctx.cursor["since"])
            if ctx.cursor.get("since")
            else ctx.now - timedelta(days=FIRST_SYNC_DAYS)
        )
        sales: list[dict[str, Any]] = []
        costs: list[dict[str, Any]] = []
        other_currency = 0
        fetched = 0
        with self._client() as http:
            for kind in ("ACCREC", "ACCPAY"):
                for invoice in self._invoices(http, ctx, kind, since):
                    fetched += 1
                    if invoice.get("CurrencyCode", CURRENCY) != CURRENCY:
                        other_currency += 1
                        continue
                    row = self._sale(invoice) if kind == "ACCREC" else self._cost(invoice)
                    if row is not None:
                        (sales if kind == "ACCREC" else costs).append(row)
        return SyncOutcome(
            records_fetched=fetched,
            cursor={"since": ctx.now.isoformat(), "left_out_other_currency": other_currency},
            datasets={"sales": sales, "expenses": costs},
        )

    # --- one Xero invoice -> one row ----------------------------------------------------------

    @staticmethod
    def _amounts(invoice: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal] | None:
        net, vat = _money(invoice.get("SubTotal")), _money(invoice.get("TotalTax"))
        if net == 0 and vat == 0:
            return None  # nothing to record
        return net, vat, net + vat

    def _sale(self, invoice: dict[str, Any]) -> dict[str, Any] | None:
        when, amounts = (
            _day(invoice.get("DateString") or invoice.get("Date")),
            self._amounts(invoice),
        )
        if when is None or amounts is None:
            return None
        net, vat, gross = amounts
        contact = invoice.get("Contact") or {}
        row: dict[str, Any] = {
            "kind": "sale",
            "sold_on": when,
            "net_amount": net,
            "vat_amount": vat,
            "gross_amount": gross,
            "reference": str(invoice["InvoiceID"]),
            "notes": f"Xero invoice {invoice.get('InvoiceNumber') or ''}".strip(),
            "customer_name": (contact.get("Name") or None),
            "due_on": _day(invoice.get("DueDateString") or invoice.get("DueDate")),
            "paid_on": (
                _day(invoice.get("FullyPaidOnDate")) if invoice.get("Status") == "PAID" else None
            ),
        }
        if row["due_on"] is not None and row["due_on"] < when:
            row["due_on"] = (
                when  # a due day before the sale cannot be stored; the sale day is the earliest
            )
        # What was sold: only when it is one clearly coded item, so a mixed one is not guessed at.
        lines = invoice.get("LineItems") or []
        coded = [line for line in lines if line.get("ItemCode")]
        if len(lines) == 1 and len(coded) == 1:
            line = coded[0]
            row["sku"] = str(line["ItemCode"])[:100]
            row["product"] = (line.get("Description") or str(line["ItemCode"]))[:200]
            quantity = line.get("Quantity")
            if quantity:
                row["quantity"] = _money(quantity)
        return row

    def _cost(self, invoice: dict[str, Any]) -> dict[str, Any] | None:
        when, amounts = (
            _day(invoice.get("DateString") or invoice.get("Date")),
            self._amounts(invoice),
        )
        if when is None or amounts is None:
            return None
        net, vat, gross = amounts
        contact = invoice.get("Contact") or {}
        number = invoice.get("InvoiceNumber") or ""
        return {
            "kind": "expense",
            "spent_on": when,
            "net_amount": net,
            "vat_amount": vat,
            "gross_amount": gross,
            "reference": str(invoice["InvoiceID"]),
            "supplier": (contact.get("Name") or None),
            "description": f"Xero bill {number}".strip(),
        }
