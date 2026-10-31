"""Square: connect a till or online shop with an access token, and bring in its sales.

Vyterlix only ever reads from Square and never changes anything there. It reads
- completed orders (card, cash and online), and brings them in as sales, without tips (a tip is
  the staff's money, not the business's);
- refunds, which come off the sales;
- the name of the customer on an order, when Square has one.

An order in another currency than pounds is left out, and counted, rather than guessed at.

There is no sign-in page: the owner makes an access token in their Square developer dashboard and
types it in. A Square token cannot be limited to reading, so it is kept encrypted and never shown,
and Vyterlix only ever sends read requests with it. Sign-in through Square itself (OAuth) needs
the Vyterlix app's secret from Square and can replace this later without changing anything else.

Written from Square's documented Orders, Locations and Merchants APIs and tested against a pretend
Square. It has to be tried against the real sandbox before it is relied on.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx

from app.core.config import get_settings
from app.integrations.base import (
    AccountInfo,
    ConnectField,
    Provider,
    ProviderRejected,
    ProviderUnavailable,
    ReauthRequired,
    SyncContext,
    SyncOutcome,
    TokenSet,
)

API = {
    "sandbox": "https://connect.squareupsandbox.com",
    "production": "https://connect.squareup.com",
}
VERSION = "2025-01-23"
PAGE_SIZE = 500
MAX_PAGES = 200
FIRST_SYNC_DAYS = 730  # the first time, go back two years
CURRENCY = "GBP"


def _day(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def _pounds(money: Any) -> Decimal:
    """Square sends pence as a whole number."""
    amount = (money or {}).get("amount") or 0
    return (Decimal(int(amount)) / 100).quantize(Decimal("0.0001"))


class SquareProvider(Provider):
    key = "square"
    label = "Square"
    permissions = (
        "Read your completed orders, so we can show your sales (tips are left out).",
        "Read refunds, so they are taken off your sales.",
        "Read the names of your customers and what they bought.",
        "Vyterlix never changes anything in Square.",
        "You can delete the token in Square at any time to cut Vyterlix off.",
    )
    connect_fields = (
        ConnectField(
            "access_token",
            "Square access token",
            "In your Square developer dashboard: your application, Credentials, Access token.",
            secret=True,
        ),
    )
    # Tests put a pretend Square here; in real use it stays None and the real one is called.
    transport: httpx.BaseTransport | None = None

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=30.0, transport=self.transport, follow_redirects=False)

    @staticmethod
    def _api() -> str:
        return API.get(get_settings().square_environment, API["sandbox"])

    # --- connecting ---------------------------------------------------------------------------

    def connect_with_keys(self, values: dict[str, str]) -> TokenSet:
        tokens = TokenSet(access_token=values["access_token"])
        self._call(tokens, "GET", "/v2/merchants/me")  # proves the token works
        return tokens

    def account(self, tokens: TokenSet) -> AccountInfo:
        merchant = self._call(tokens, "GET", "/v2/merchants/me").get("merchant") or {}
        if not merchant.get("id"):
            raise ProviderRejected("Square did not say whose account this is.", code="no_account")
        return AccountInfo(
            str(merchant["id"]),
            str(merchant.get("business_name") or "Square account")[:100],
            {"country": merchant.get("country")},
        )

    # --- talking to Square --------------------------------------------------------------------

    def _call(
        self, tokens: TokenSet, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            with self._client() as http:
                response = http.request(
                    method,
                    f"{self._api()}{path}",
                    json=body,
                    headers={
                        "Authorization": f"Bearer {tokens.access_token}",
                        "Square-Version": VERSION,
                        "Accept": "application/json",
                    },
                )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("We could not reach Square. We'll try again.") from exc
        if response.status_code == 401:
            raise ReauthRequired(
                "Square did not accept the access token. Make a new one in Square and connect "
                "again."
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderUnavailable("Square is busy or not responding. We'll try again.")
        if response.status_code >= 400:
            raise ProviderRejected(
                "Square refused the request. Check the token belongs to the account you meant.",
                code="provider_rejected",
            )
        return response.json()

    # --- bringing orders in -------------------------------------------------------------------

    def sync(self, ctx: SyncContext) -> SyncOutcome:
        since = ctx.cursor.get("since") or (
            (ctx.now - timedelta(days=FIRST_SYNC_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        locations = [
            loc["id"]
            for loc in self._call(ctx.tokens, "GET", "/v2/locations").get("locations", [])
            if loc.get("status", "ACTIVE") == "ACTIVE"
        ]
        sales: list[dict[str, Any]] = []
        fetched = other_currency = 0
        names: dict[str, str | None] = {}
        for start in range(0, len(locations), 10):  # Square takes at most ten locations at once
            cursor = None
            for _ in range(MAX_PAGES):
                body: dict[str, Any] = {
                    "location_ids": locations[start : start + 10],
                    "limit": PAGE_SIZE,
                    "query": {
                        "filter": {
                            "state_filter": {"states": ["COMPLETED"]},
                            "date_time_filter": {"updated_at": {"start_at": since}},
                        },
                        "sort": {"sort_field": "UPDATED_AT", "sort_order": "ASC"},
                    },
                }
                if cursor:
                    body["cursor"] = cursor
                page = self._call(ctx.tokens, "POST", "/v2/orders/search", body)
                for order in page.get("orders", []):
                    fetched += 1
                    if (order.get("total_money") or {}).get("currency", CURRENCY) != CURRENCY:
                        other_currency += 1
                        continue
                    sales.extend(self._rows(ctx, order, names))
                ctx.progress(fetched, fetched)
                cursor = page.get("cursor")
                if not cursor:
                    break
        return SyncOutcome(
            records_fetched=fetched,
            cursor={
                "since": (ctx.now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "left_out_other_currency": other_currency,
            },
            datasets={"sales": sales},
        )

    def _customer(self, ctx: SyncContext, order: dict[str, Any], names: dict) -> str | None:
        customer_id = order.get("customer_id")
        if not customer_id:
            return None
        if customer_id not in names:
            try:
                found = self._call(ctx.tokens, "GET", f"/v2/customers/{customer_id}")
                person = found.get("customer") or {}
                name = " ".join(
                    x for x in (person.get("given_name"), person.get("family_name")) if x
                )
                names[customer_id] = (name or person.get("company_name") or "")[:200] or None
            except (ProviderRejected, ProviderUnavailable):
                names[customer_id] = None  # a name is a nicety; the sale still counts
        return names[customer_id]

    def _rows(self, ctx: SyncContext, order: dict[str, Any], names: dict) -> list[dict[str, Any]]:
        placed = _day(order.get("closed_at") or order.get("created_at"))
        tip = _pounds(order.get("total_tip_money"))
        gross = _pounds(order.get("total_money")) - tip
        vat = _pounds(order.get("total_tax_money"))
        net = gross - vat
        if placed is None or gross <= 0 or net < 0 or vat < 0:
            return []
        customer = self._customer(ctx, order, names)
        row: dict[str, Any] = {
            "kind": "sale", "sold_on": placed, "net_amount": net, "vat_amount": vat,
            "gross_amount": gross, "reference": f"sq-{order['id']}", "paid_on": placed,
            "notes": f"Square order {order['id'][:8]}", "customer_name": customer,
        }  # fmt: skip
        items = order.get("line_items") or []
        if len(items) == 1 and items[0].get("name"):  # one clearly named item, so no guessing
            row["product"] = str(items[0]["name"])[:200]
            try:
                quantity = Decimal(str(items[0].get("quantity") or 0))
            except ArithmeticError:
                quantity = Decimal(0)
            if quantity > 0:
                row["quantity"] = quantity
        rows = [row]
        for refund in order.get("refunds") or []:
            if refund.get("status") not in (None, "COMPLETED"):
                continue
            amount = _pounds(refund.get("amount_money"))
            if amount <= 0:
                continue
            # A refund names no tax, so take it off in the same proportion as the order's tax.
            tax = (amount * vat / gross).quantize(Decimal("0.0001"))
            rows.append(
                {
                    "kind": "refund",
                    "sold_on": _day(refund.get("created_at")) or placed,
                    "net_amount": -(amount - tax),
                    "vat_amount": -tax,
                    "gross_amount": -amount,
                    "reference": f"sq-refund-{refund['id']}",
                    "notes": f"Square refund on order {order['id'][:8]}",
                    "customer_name": customer,
                }  # fmt: skip
            )
        return rows
