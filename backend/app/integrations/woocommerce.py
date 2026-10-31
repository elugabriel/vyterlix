"""WooCommerce: connect an online shop with read-only keys, and bring in its orders as sales.

Read-only: Vyterlix only ever reads from the shop. There is no sign-in page. The owner makes a key
pair in their own shop (WooCommerce > Settings > Advanced > REST API, with "Read" access) and types
the shop address and the two keys in. The keys are kept encrypted and can be deleted in the shop at
any time to cut Vyterlix off.

Orders become sales. A paid order (completed or processing) counts on the day it was placed; each
refund becomes a negative sale on the day it was made. Orders still waiting for payment, cancelled,
failed or draft orders are left out. An order in another currency than pounds is left out, and
counted, rather than guessed at.

Because the shop address is typed in by a customer, it is checked before it is called: https only,
and never an address inside a private network, so it cannot be used to reach other machines.

Written from WooCommerce's documented REST API (v3) and tested against a pretend shop. It has to be
tried against the real test shop before it is relied on.
"""

import ipaddress
import socket
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

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

PAGE_SIZE = 100
MAX_PAGES = 200
FIRST_SYNC_DAYS = 730  # the first time, go back two years
CURRENCY = "GBP"
PAID = ("completed", "processing", "refunded")  # refunded: the refund rows cancel the order out


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


def _shop_address(text: str) -> str:
    """https://shop.example.co.uk from whatever was typed or pasted (a wp-admin address is fine)."""
    text = text.strip()
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    allowed = ("https",) if get_settings().env not in ("dev", "test") else ("https", "http")
    if parts.scheme not in allowed or not parts.hostname or parts.username or parts.password:
        raise ProviderRejected(
            "Please type your shop's web address, starting with https://, for example "
            "https://myshop.co.uk.",
            code="bad_address",
        )
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname.lower()}{port}"


def _resolve(host: str) -> list[str]:
    try:
        return [info[4][0] for info in socket.getaddrinfo(host, None)]
    except OSError as exc:
        raise ProviderRejected(
            "We could not find that web address. Check it is spelled correctly.",
            code="address_not_found",
        ) from exc


def _check_public(address: str) -> None:
    """Refuse an address that points inside a private network."""
    host = urlsplit(address).hostname or ""
    for ip in _resolve(host):
        if not ipaddress.ip_address(ip).is_global:
            raise ProviderRejected(
                "That address is not a public web address, so we can't connect to it.",
                code="bad_address",
            )


class WooCommerceProvider(Provider):
    key = "woocommerce"
    label = "WooCommerce"
    permissions = (
        "Read your shop's orders, so we can show your sales.",
        "Read refunds, so they are taken off your sales.",
        "Read the names of your customers and what they bought.",
        "Vyterlix never changes anything in your shop.",
        "You can delete the keys in your shop at any time to cut Vyterlix off.",
    )
    connect_fields = (
        ConnectField(
            "store_url",
            "Your shop's web address",
            "For example https://myshop.co.uk",
        ),
        ConnectField(
            "consumer_key",
            "Consumer key",
            "In your shop: WooCommerce, Settings, Advanced, REST API, Add key. Choose Read access.",
            secret=True,
        ),
        ConnectField(
            "consumer_secret", "Consumer secret", "Shown once, next to the key.", secret=True
        ),
    )
    # Tests put a pretend shop here; in real use it stays None and the real one is called.
    transport: httpx.BaseTransport | None = None

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=30.0, transport=self.transport, follow_redirects=False)

    # --- connecting ---------------------------------------------------------------------------

    def connect_with_keys(self, values: dict[str, str]) -> TokenSet:
        address = _shop_address(values["store_url"])
        if self.transport is None:
            _check_public(address)
        tokens = TokenSet(
            access_token=values["consumer_key"],
            refresh_token=values["consumer_secret"],
            extra={"store_url": address},
        )
        self._get(tokens, "orders", {"per_page": 1})  # proves the keys work and can read orders
        return tokens

    def account(self, tokens: TokenSet) -> AccountInfo:
        address = tokens.extra["store_url"]
        name = urlsplit(address).hostname or address
        try:
            with self._client() as http:
                info = http.get(f"{address}/wp-json", headers={"Accept": "application/json"})
            if info.status_code == 200:
                name = str(info.json().get("name") or name)[:100]
        except (httpx.HTTPError, ValueError):
            pass  # the name is a nicety; the shop address says who it is
        return AccountInfo(address, name, {"store_url": address})

    # --- talking to the shop ------------------------------------------------------------------

    def _get(self, tokens: TokenSet, path: str, params: dict[str, Any]) -> httpx.Response:
        address = tokens.extra["store_url"]
        try:
            with self._client() as http:
                response = http.get(
                    f"{address}/wp-json/wc/v3/{path}",
                    params=params,
                    auth=(tokens.access_token, tokens.refresh_token or ""),
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("We could not reach your shop. We'll try again.") from exc
        if response.status_code in (401, 403):
            raise ReauthRequired(
                "Your shop did not accept the keys. Make a new Read key in WooCommerce and "
                "connect again."
            )
        if response.status_code == 404:
            raise ProviderRejected(
                "That address does not look like a WooCommerce shop. Check the address, and "
                "that WooCommerce is installed.",
                code="not_woocommerce",
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderUnavailable("Your shop is busy or not responding. We'll try again.")
        if response.status_code >= 400:
            raise ProviderRejected(
                "Your shop refused the request. Check the keys have Read access.",
                code="provider_rejected",
            )
        return response

    # --- bringing orders in -------------------------------------------------------------------

    def sync(self, ctx: SyncContext) -> SyncOutcome:
        if ctx.cursor.get("since"):
            window = {"modified_after": ctx.cursor["since"]}
        else:
            first = ctx.now - timedelta(days=FIRST_SYNC_DAYS)
            window = {"after": first.strftime("%Y-%m-%dT%H:%M:%S")}
        sales: list[dict[str, Any]] = []
        fetched = other_currency = 0
        for page in range(1, MAX_PAGES + 1):
            response = self._get(
                ctx.tokens,
                "orders",
                {"per_page": PAGE_SIZE, "page": page, "status": "any", "orderby": "id", **window},
            )
            orders = response.json()
            for order in orders:
                fetched += 1
                if order.get("currency", CURRENCY) != CURRENCY:
                    other_currency += 1
                    continue
                sales.extend(self._rows(order))
            ctx.progress(fetched, fetched + (PAGE_SIZE if len(orders) == PAGE_SIZE else 0))
            if len(orders) < PAGE_SIZE:
                break
        return SyncOutcome(
            records_fetched=fetched,
            cursor={
                "since": (ctx.now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S"),
                "left_out_other_currency": other_currency,
            },
            datasets={"sales": sales},
        )

    @staticmethod
    def _customer(order: dict[str, Any]) -> tuple[str | None, str | None]:
        billing = order.get("billing") or {}
        name = " ".join(x for x in (billing.get("first_name"), billing.get("last_name")) if x)
        name = name.strip() or (billing.get("company") or "").strip()
        email = (billing.get("email") or "").strip().lower()
        return (name[:200] or None), (email[:254] or None)

    def _rows(self, order: dict[str, Any]) -> list[dict[str, Any]]:
        if order.get("status") not in PAID:
            return []
        placed = _day(order.get("date_created"))
        total, vat = _money(order.get("total")), _money(order.get("total_tax"))
        net = total - vat
        if placed is None or total == 0 or net < 0 or vat < 0:
            return []
        name, email = self._customer(order)
        number = order.get("number") or order["id"]
        row: dict[str, Any] = {
            "kind": "sale", "sold_on": placed, "net_amount": net, "vat_amount": vat,
            "gross_amount": total, "reference": f"woo-{order['id']}",
            "notes": f"WooCommerce order {number}", "customer_name": name, "customer_email": email,
            "paid_on": _day(order.get("date_paid")) or placed,
        }  # fmt: skip
        items = order.get("line_items") or []
        if len(items) == 1 and items[0].get("name"):  # one clearly named item, so no guessing
            row["product"] = str(items[0]["name"])[:200]
            if items[0].get("quantity"):
                row["quantity"] = _money(abs(Decimal(str(items[0]["quantity"]))))
        rows = [row]
        for refund in order.get("refunds") or []:
            amount = abs(_money(refund.get("total")))
            if amount == 0:
                continue
            # A refund carries no date in the order list, so it is put on the order's day.
            # It names no tax, so take it off in the same proportion as the order's tax.
            tax = (amount * vat / total).quantize(Decimal("0.0001"))
            rows.append(
                {
                    "kind": "refund",
                    "sold_on": placed,
                    "net_amount": -(amount - tax),
                    "vat_amount": -tax,
                    "gross_amount": -amount,
                    "reference": f"woo-refund-{refund['id']}",
                    "notes": f"WooCommerce refund on order {number}",
                    "customer_name": name,
                    "customer_email": email,
                }  # fmt: skip
            )
        return rows
