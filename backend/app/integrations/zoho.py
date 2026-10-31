"""Zoho Inventory: sign in, and bring in the business's sales and costs.

Read-only: Vyterlix only ever reads from Zoho and never changes anything there. It reads
- invoices, and brings them in as sales (an invoice carries its due and paid days, so money owed
  can be worked out);
- credit notes, as refunds;
- bills, and brings them in as costs;
- the name of the customer or supplier on each, and what was sold when it is one named item.

Draft and cancelled (void) documents are left out. An amount in another currency than pounds is
left out, and counted, rather than guessed at.

Zoho keeps each business's data in one region. Vyterlix is set up for the UK one (zoho.uk); the
region is a setting. Zoho does not use the extra sign-in check (PKCE), so none is sent. Zoho's
token address answers "200 OK" even when it refuses, with the reason in the body; that is handled.

The list of invoices does not carry the VAT, so each document is also opened once to read it. That
is one extra request per document, so the first sync of a large business takes a while.

Written from Zoho's documented OAuth 2.0 flow and Inventory API (v1) and tested against a pretend
Zoho. It has to be tried against the real account before it is relied on.
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

REGIONS = ("com", "eu", "in", "com.au", "jp", "uk", "ca", "sa")
PAGE_SIZE = 200
MAX_PAGES = 100
FIRST_SYNC_DAYS = 730  # the first time, go back two years
CURRENCY = "GBP"
# What each kind of document is called in Zoho's answers, and which of them count.
KINDS = {
    "invoices": (
        "invoices",
        "invoice_id",
        "invoice",
        ("sent", "overdue", "paid", "partially_paid"),
    ),
    "creditnotes": ("creditnotes", "creditnote_id", "creditnote", ("open", "closed")),
    "bills": ("bills", "bill_id", "bill", ("open", "overdue", "paid", "partially_paid")),
}
PREFIX = {"invoices": "inv", "creditnotes": "cn", "bills": "bill"}


def _day(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def _money(value: Any) -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else 0)).quantize(Decimal("0.0001"))
    except ArithmeticError:
        return Decimal(0)


class ZohoProvider(Provider):
    key = "zoho"
    label = "Zoho Inventory"
    scopes = (
        "ZohoInventory.invoices.READ",
        "ZohoInventory.creditnotes.READ",
        "ZohoInventory.bills.READ",
        "ZohoInventory.settings.READ",
    )
    permissions = (
        "Read your invoices and credit notes, so we can show your sales and what you are owed.",
        "Read your bills, so we can show your costs.",
        "Read the names of your customers and suppliers, and what was sold.",
        "Vyterlix never changes anything in Zoho.",
    )
    # Tests put a pretend Zoho here; in real use it stays None and the real one is called.
    transport: httpx.BaseTransport | None = None

    def is_configured(self, settings: Settings) -> bool:
        return bool(settings.zoho_client_id and settings.zoho_client_secret)

    @staticmethod
    def _region() -> str:
        region = get_settings().zoho_region
        return region if region in REGIONS else "uk"

    def _accounts(self) -> str:
        return f"https://accounts.zoho.{self._region()}"

    def _api(self) -> str:
        return f"https://www.zohoapis.{self._region()}/inventory/v1"

    def _credentials(self) -> tuple[str, str]:
        settings = get_settings()
        if not (settings.zoho_client_id and settings.zoho_client_secret):
            raise ProviderRejected(
                "Zoho has not been set up for this Vyterlix yet.", code="not_configured"
            )
        return settings.zoho_client_id, settings.zoho_client_secret.get_secret_value()

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=30.0, transport=self.transport)

    # --- signing in ---------------------------------------------------------------------------

    def authorize_url(self, *, state: str, redirect_uri: str, code_challenge: str) -> str:
        client_id, _ = self._credentials()
        query = urlencode(
            {
                "client_id": client_id,
                "response_type": "code",
                "scope": ",".join(self.scopes),
                "redirect_uri": redirect_uri,
                "access_type": "offline",  # so we get a refresh token
                "prompt": "consent",
                "state": state,
            }
        )
        return f"{self._accounts()}/oauth/v2/auth?{query}"

    def _token_request(self, form: dict[str, str], *, refreshing: bool) -> TokenSet:
        client_id, secret = self._credentials()
        try:
            with self._client() as http:
                response = http.post(
                    f"{self._accounts()}/oauth/v2/token",
                    data={**form, "client_id": client_id, "client_secret": secret},
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable() from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise ProviderUnavailable()
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400 or "access_token" not in body:
            if body.get("error") == "Access Denied":  # Zoho says this when it is rate limiting
                raise ProviderUnavailable("Zoho is busy. We'll try again shortly.")
            if refreshing:
                raise ReauthRequired(
                    "Zoho no longer accepts this connection. Please connect Zoho again."
                )
            raise ProviderRejected(
                "Zoho did not accept that sign-in. Please try connecting again.",
                code="invalid_code",
            )
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
        if not tokens.refresh_token:
            raise ReauthRequired(
                "Zoho no longer accepts this connection. Please connect Zoho again."
            )
        return self._token_request(
            {"grant_type": "refresh_token", "refresh_token": tokens.refresh_token}, refreshing=True
        )

    def revoke(self, tokens: TokenSet) -> None:
        if not tokens.refresh_token:
            return
        try:
            with self._client() as http:
                http.post(
                    f"{self._accounts()}/oauth/v2/token/revoke",
                    params={"token": tokens.refresh_token},
                )
        except httpx.HTTPError:
            return  # best effort: the connection is removed on our side either way

    # --- talking to Zoho ----------------------------------------------------------------------

    def _get(
        self, http: httpx.Client, path: str, tokens: TokenSet, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            response = http.get(
                f"{self._api()}{path}",
                params=params,
                headers={
                    "Authorization": f"Zoho-oauthtoken {tokens.access_token}",
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable() from exc
        if response.status_code == 401:
            raise ReauthRequired(
                "Zoho no longer accepts this connection. Please connect Zoho again."
            )
        if response.status_code == 403:
            raise ProviderRejected(
                "Zoho says Vyterlix was not allowed to read that. Please connect Zoho again and "
                "approve access.",
                code="missing_scope",
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderUnavailable("Zoho is busy. We'll try again shortly.")
        if response.status_code >= 400:
            raise ProviderRejected(
                "Zoho would not give us that information. Please try again later.",
                code="provider_rejected",
            )
        return response.json()

    def account(self, tokens: TokenSet) -> AccountInfo:
        with self._client() as http:
            found = self._get(http, "/organizations", tokens).get("organizations", [])
        if not found:
            raise ProviderRejected(
                "Zoho did not share any business with Vyterlix. Please connect again.",
                code="no_organization",
            )
        chosen = next((o for o in found if o.get("is_default_org")), found[0])
        return AccountInfo(
            external_id=str(chosen["organization_id"]),
            name=str(chosen.get("name") or "Zoho business")[:100],
            settings={"country": chosen.get("country_code"), "region": self._region()},
        )

    # --- bringing the data in -----------------------------------------------------------------

    def _list(
        self, http: httpx.Client, ctx: SyncContext, kind: str, window: dict[str, str]
    ) -> list[dict[str, Any]]:
        listing, id_key, _, counted = KINDS[kind]
        found: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            body = self._get(
                http,
                f"/{kind}",
                ctx.tokens,
                {
                    "organization_id": ctx.account.external_id,
                    "page": page,
                    "per_page": PAGE_SIZE,
                    **window,
                },
            )
            found.extend(d for d in body.get(listing, []) if d.get("status") in counted)
            ctx.progress(len(found), len(found))
            if not (body.get("page_context") or {}).get("has_more_page"):
                break
        return found

    def _detail(
        self, http: httpx.Client, ctx: SyncContext, kind: str, brief: dict[str, Any]
    ) -> dict[str, Any]:
        _, id_key, single, _ = KINDS[kind]
        body = self._get(
            http,
            f"/{kind}/{brief[id_key]}",
            ctx.tokens,
            {"organization_id": ctx.account.external_id},
        )
        return {**brief, **(body.get(single) or {})}

    def sync(self, ctx: SyncContext) -> SyncOutcome:
        if ctx.cursor.get("since"):
            window = {"last_modified_time": ctx.cursor["since"]}
        else:
            first = (ctx.now - timedelta(days=FIRST_SYNC_DAYS)).date()
            window = {"date_start": first.isoformat()}
        sales: list[dict[str, Any]] = []
        costs: list[dict[str, Any]] = []
        other_currency = fetched = 0
        with self._client() as http:
            for kind in KINDS:
                for brief in self._list(http, ctx, kind, window):
                    fetched += 1
                    if brief.get("currency_code", CURRENCY) != CURRENCY:
                        other_currency += 1
                        continue
                    row = self._row(kind, self._detail(http, ctx, kind, brief))
                    if row is not None:
                        (costs if kind == "bills" else sales).append(row)
        return SyncOutcome(
            records_fetched=fetched,
            cursor={
                "since": (ctx.now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S+0000"),
                "left_out_other_currency": other_currency,
            },
            datasets={"sales": sales, "expenses": costs},
        )

    # --- one Zoho document -> one row ---------------------------------------------------------

    @staticmethod
    def _paid_on(doc: dict[str, Any], when: date) -> date | None:
        if _money(doc.get("balance")) != 0:
            return None
        paid = [d for d in (_day(p.get("date")) for p in doc.get("payments") or []) if d]
        return max(paid) if paid else _day(doc.get("last_modified_time")) or when

    def _row(self, kind: str, doc: dict[str, Any]) -> dict | None:
        _, id_key, _, _ = KINDS[kind]
        when = _day(doc.get("date"))
        total, vat = _money(doc.get("total")), _money(doc.get("tax_total"))
        net = total - vat
        if when is None or total == 0 or net < 0 or vat < 0:
            return None  # nothing to record, or not something that adds up
        reference = f"{PREFIX[kind]}-{doc[id_key]}"
        number = doc.get("invoice_number") or doc.get("creditnote_number") or doc.get("bill_number")
        if kind == "bills":
            return {
                "kind": "expense", "spent_on": when, "net_amount": net, "vat_amount": vat,
                "gross_amount": total, "reference": reference,
                "supplier": (doc.get("vendor_name") or None) and str(doc["vendor_name"])[:200],
                "description": f"Zoho bill {number}",
            }  # fmt: skip
        sign = Decimal(-1) if kind == "creditnotes" else Decimal(1)
        row: dict[str, Any] = {
            "kind": "refund" if kind == "creditnotes" else "sale",
            "sold_on": when, "net_amount": sign * net, "vat_amount": sign * vat,
            "gross_amount": sign * total, "reference": reference,
            "notes": f"Zoho {'credit note' if kind == 'creditnotes' else 'invoice'} {number}",
            "customer_name": (doc.get("customer_name") or None) and str(doc["customer_name"])[:200],
        }  # fmt: skip
        if kind == "invoices":
            due = _day(doc.get("due_date"))
            row["due_on"] = None if due is None else max(due, when)  # never before the sale
            row["paid_on"] = self._paid_on(doc, when)
        items = doc.get("line_items") or []
        if len(items) == 1 and items[0].get("name"):  # one clearly named item, so no guessing
            row["product"] = str(items[0]["name"])[:200]
            quantity = _money(items[0].get("quantity"))
            if quantity > 0:
                row["quantity"] = quantity
        return row
