"""Shopify: connect a shop with a read-only access token, and bring in its orders as sales.

Read-only: Vyterlix only ever reads from the shop. There is no sign-in page and Vyterlix does not
have to be registered with Shopify: the owner makes a small private app in their own shop (Settings,
Apps and sales channels, Develop apps) with only the "read orders" permission, and types the shop
address and the access token in. The token is kept encrypted and the app can be deleted in the shop
at any time to cut Vyterlix off.

Orders become sales. A paid order counts on the day it was placed; each refund becomes a negative
sale on the day the refund was made. Orders still waiting for payment, voided or test orders are
left out. An order in another currency than pounds is left out, and counted, rather than guessed at.

Only a `<name>.myshopify.com` address is ever called (that is the only address Shopify's own
interface accepts), so a typed-in address cannot be used to reach other machines.

Shopify only lets a plain read-orders app see the last 60 days. The owner adds the "read all orders"
permission to bring in more history; the connection page says so.

Written from Shopify's documented Admin REST API and tested against a pretend shop. It has to be
tried against a real development shop before it is relied on.
"""

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

import httpx

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

API_VERSION = "2025-04"
PAGE_SIZE = 250
MAX_PAGES = 200
FIRST_SYNC_DAYS = 730  # the first time, go back two years (Shopify may allow less; see above)
CURRENCY = "GBP"
PAID = ("paid", "partially_refunded", "refunded")  # refunded: the refund rows cancel the order out
SUFFIX = ".myshopify.com"
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")


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


def _shop_domain(text: str) -> str:
    """myshop.myshopify.com from whatever was typed or pasted: the short name, the full address,
    or the address of the Shopify admin page (admin.shopify.com/store/myshop)."""
    text = text.strip().lower()
    bad = ProviderRejected(
        "Please type your shop's Shopify address, for example myshop.myshopify.com. You can see "
        "it in your Shopify admin under Settings, Domains.",
        code="bad_address",
    )
    if not text:
        raise bad
    parts = urlsplit(text if "://" in text else "https://" + text)
    host = parts.hostname or ""
    if parts.username or parts.password or parts.scheme not in ("http", "https"):
        raise bad
    if host == "admin.shopify.com":
        segments = [s for s in parts.path.split("/") if s]
        name = segments[1] if len(segments) >= 2 and segments[0] == "store" else ""
    elif host.endswith(SUFFIX):
        name = host[: -len(SUFFIX)]
    elif "." not in host:
        name = host
    else:
        raise bad
    if not _NAME.match(name):
        raise bad
    return name + SUFFIX


class ShopifyProvider(Provider):
    key = "shopify"
    label = "Shopify"
    permissions = (
        "Read your shop's orders, so we can show your sales.",
        "Read refunds, so they are taken off your sales.",
        "Read the names of your customers and what they bought.",
        "Vyterlix never changes anything in your shop.",
        "You can delete the app in your shop at any time to cut Vyterlix off.",
    )
    connect_fields = (
        ConnectField(
            "shop_domain",
            "Your shop's Shopify address",
            "For example myshop.myshopify.com (Shopify admin, Settings, Domains).",
        ),
        ConnectField(
            "access_token",
            "Admin API access token",
            "In your shop: Settings, Apps and sales channels, Develop apps, Create an app. "
            "Give it only Read orders (and Read all orders, to bring in more than 60 days), "
            "install it, then copy the token. Shopify shows it once.",
            secret=True,
        ),
    )
    # Tests put a pretend shop here; in real use it stays None and the real one is called.
    transport: httpx.BaseTransport | None = None

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=30.0, transport=self.transport, follow_redirects=False)

    # --- connecting ---------------------------------------------------------------------------

    def connect_with_keys(self, values: dict[str, str]) -> TokenSet:
        domain = _shop_domain(values["shop_domain"])
        token = values["access_token"].strip()
        tokens = TokenSet(access_token=token, extra={"shop": domain})
        self._get(
            tokens, "orders.json", {"limit": 1, "status": "any"}
        )  # keys work, can read orders
        return tokens

    def account(self, tokens: TokenSet) -> AccountInfo:
        domain = tokens.extra["shop"]
        name = domain.removesuffix(SUFFIX)
        try:
            shop = self._get(tokens, "shop.json", {}).json().get("shop") or {}
            name = str(shop.get("name") or name)[:100]
        except (ProviderUnavailable, ProviderRejected, ReauthRequired, ValueError):
            pass  # the name is a nicety; the address says who it is
        return AccountInfo(domain, name, {"shop": domain})

    # --- talking to the shop ------------------------------------------------------------------

    def _fetch(self, tokens: TokenSet, url: str, params: dict[str, Any] | None) -> httpx.Response:
        # params=None leaves the address as it is: a "next page" link carries its own cursor.
        try:
            with self._client() as http:
                response = http.get(
                    url,
                    params=params,
                    headers={
                        "X-Shopify-Access-Token": tokens.access_token,
                        "Accept": "application/json",
                    },
                )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("We could not reach your shop. We'll try again.") from exc
        if response.status_code == 401:
            raise ReauthRequired(
                "Shopify did not accept the access token. Make a new one in your shop and "
                "connect again."
            )
        if response.status_code == 403:
            raise ProviderRejected(
                "Your Shopify app is not allowed to read orders. Give it the Read orders "
                "permission and connect again.",
                code="missing_permission",
            )
        if response.status_code == 404:
            raise ProviderRejected(
                "We could not find a Shopify shop at that address. Check it is spelled correctly.",
                code="not_shopify",
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderUnavailable("Shopify is busy or not responding. We'll try again.")
        if response.status_code >= 400:
            raise ProviderRejected(
                "Shopify refused the request. Check the app can read orders.",
                code="provider_rejected",
            )
        return response

    def _get(self, tokens: TokenSet, path: str, params: dict[str, Any]) -> httpx.Response:
        domain = tokens.extra["shop"]
        return self._fetch(tokens, f"https://{domain}/admin/api/{API_VERSION}/{path}", params)

    # --- bringing orders in -------------------------------------------------------------------

    def sync(self, ctx: SyncContext) -> SyncOutcome:
        domain = ctx.tokens.extra["shop"]
        if ctx.cursor.get("since"):
            window = {"updated_at_min": ctx.cursor["since"]}
        else:
            first = ctx.now - timedelta(days=FIRST_SYNC_DAYS)
            window = {"created_at_min": first.strftime("%Y-%m-%dT%H:%M:%SZ")}
        sales: list[dict[str, Any]] = []
        fetched = other_currency = 0
        url = f"https://{domain}/admin/api/{API_VERSION}/orders.json"
        params: dict[str, Any] | None = {"status": "any", "limit": PAGE_SIZE, **window}
        for _ in range(MAX_PAGES):
            response = self._fetch(ctx.tokens, url, params)
            orders = response.json().get("orders") or []
            for order in orders:
                fetched += 1
                if order.get("currency", CURRENCY) != CURRENCY:
                    other_currency += 1
                    continue
                sales.extend(self._rows(order))
            nxt = (response.links.get("next") or {}).get("url")
            ctx.progress(fetched, fetched + (PAGE_SIZE if nxt else 0))
            # Only ever follow a "next page" that stays on this shop (the token goes with it).
            if not nxt or (urlsplit(nxt).hostname or "") != domain:
                break
            url, params = nxt, None
        return SyncOutcome(
            records_fetched=fetched,
            cursor={
                "since": (ctx.now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "left_out_other_currency": other_currency,
            },
            datasets={"sales": sales},
        )

    @staticmethod
    def _customer(order: dict[str, Any]) -> tuple[str | None, str | None]:
        who = order.get("customer") or {}
        name = " ".join(x for x in (who.get("first_name"), who.get("last_name")) if x).strip()
        if not name:
            name = ((order.get("billing_address") or {}).get("company") or "").strip()
        email = (who.get("email") or order.get("email") or "").strip().lower()
        return (name[:200] or None), (email[:254] or None)

    @staticmethod
    def _refunded(refund: dict[str, Any]) -> Decimal:
        """What was actually paid back: the successful refund payments, not the items listed."""
        return sum(
            (
                abs(_money(t.get("amount")))
                for t in refund.get("transactions") or []
                if t.get("kind") == "refund" and t.get("status", "success") == "success"
            ),
            Decimal(0),
        )

    def _rows(self, order: dict[str, Any]) -> list[dict[str, Any]]:
        if order.get("test") or order.get("financial_status") not in PAID:
            return []
        placed = _day(order.get("processed_at") or order.get("created_at"))
        total, vat = _money(order.get("total_price")), _money(order.get("total_tax"))
        net = total - vat
        if placed is None or total == 0 or net < 0 or vat < 0:
            return []
        name, email = self._customer(order)
        row: dict[str, Any] = {
            "kind": "sale", "sold_on": placed, "net_amount": net, "vat_amount": vat,
            "gross_amount": total, "reference": f"shopify-{order['id']}",
            "notes": f"Shopify order {order.get('name') or order['id']}",
            "customer_name": name, "customer_email": email, "paid_on": placed,
        }  # fmt: skip
        items = order.get("line_items") or []
        if len(items) == 1 and items[0].get("title"):  # one clearly named item, so no guessing
            row["product"] = str(items[0]["title"])[:200]
            if items[0].get("quantity"):
                row["quantity"] = _money(abs(Decimal(str(items[0]["quantity"]))))
        rows = [row]
        for refund in order.get("refunds") or []:
            amount = self._refunded(refund)
            if amount == 0:
                continue
            # A refund names no tax, so take it off in the same proportion as the order's tax.
            tax = (amount * vat / total).quantize(Decimal("0.0001"))
            rows.append(
                {
                    "kind": "refund",
                    "sold_on": _day(refund.get("created_at")) or placed,
                    "net_amount": -(amount - tax),
                    "vat_amount": -tax,
                    "gross_amount": -amount,
                    "reference": f"shopify-refund-{refund['id']}",
                    "notes": f"Shopify refund on order {order.get('name') or order['id']}",
                    "customer_name": name,
                    "customer_email": email,
                }  # fmt: skip
            )
        return rows
