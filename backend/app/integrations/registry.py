"""Which providers exist. A provider appears here once its connector is written; whether it can
actually be used also depends on `is_configured` (the app registered with that provider)."""

from dataclasses import dataclass
from typing import Literal

from app.core.config import get_settings
from app.core.errors import AppError
from app.integrations.base import Provider
from app.integrations.google_analytics import GoogleAnalyticsProvider
from app.integrations.meta import MetaProvider
from app.integrations.quickbooks import QuickBooksProvider
from app.integrations.sandbox import SandboxProvider
from app.integrations.shopify import ShopifyProvider
from app.integrations.square import SquareProvider
from app.integrations.truelayer import TrueLayerProvider
from app.integrations.woocommerce import WooCommerceProvider
from app.integrations.xero import XeroProvider
from app.integrations.zoho import ZohoProvider

PROVIDERS: dict[str, Provider] = {
    p.key: p
    for p in (
        SandboxProvider(),
        XeroProvider(),
        GoogleAnalyticsProvider(),
        MetaProvider(),
        QuickBooksProvider(),
        ShopifyProvider(),
        SquareProvider(),
        TrueLayerProvider(),
        WooCommerceProvider(),
        ZohoProvider(),
    )
}


def get_provider(key: str) -> Provider:
    provider = PROVIDERS.get(key)
    if provider is None or not provider.is_configured(get_settings()):
        raise AppError(
            "That kind of connection isn't available.", code="unknown_provider", status_code=422
        )
    return provider


def available_providers() -> list[Provider]:
    settings = get_settings()
    return [p for p in PROVIDERS.values() if p.is_configured(settings)]


# --- the whole list, for the Connections page ---------------------------------------------------
# The page shows every system Vyterlix is meant to connect to, with an honest status: ready to
# connect, built but not yet registered with that system on this installation, or still to come.

Status = Literal["ready", "needs_setup", "coming_soon"]

# key, group, what it brings in (one plain sentence), in the order they are shown
SYSTEMS: tuple[tuple[str, str, str], ...] = (
    ("xero", "Accounting and stock", "Your invoices, bills and credit notes."),
    ("quickbooks", "Accounting and stock", "Your invoices, bills and credit notes."),
    ("zoho", "Accounting and stock", "Your invoices, credit notes and bills from Zoho Inventory."),
    ("shopify", "Shops and tills", "The orders from your Shopify shop."),
    ("woocommerce", "Shops and tills", "The orders from your WooCommerce shop."),
    ("square", "Shops and tills", "The sales from your Square till or online shop."),
    ("google_analytics", "Website and marketing", "How many people visit your website."),
    ("meta", "Website and marketing", "What you spend on Facebook and Instagram adverts."),
    ("hubspot", "Customers and messages", "Your customers, leads and deals."),
    ("whatsapp", "Customers and messages", "Customer messages, and how fast you reply."),
    ("truelayer", "Your bank", "The money going into and out of your business bank account."),
)  # fmt: skip
NOT_BUILT = {
    "hubspot": "HubSpot",
    "whatsapp": "WhatsApp Business",
}


@dataclass(frozen=True)
class Listing:
    key: str
    label: str
    group: str
    summary: str
    status: Status
    provider: Provider | None  # None when it is not built yet


def listings() -> list[Listing]:
    """Every system in the order shown, each with where it stands."""
    settings = get_settings()
    out: list[Listing] = []
    sandbox = PROVIDERS.get("sandbox")
    if sandbox is not None and sandbox.is_configured(settings):  # only while developing
        out.append(
            Listing(
                "sandbox",
                sandbox.label,
                "Practice",
                "Pretend sales, to try the steps.",
                "ready",
                sandbox,
            )
        )
    for key, group, summary in SYSTEMS:
        provider = PROVIDERS.get(key)
        if provider is None:
            out.append(Listing(key, NOT_BUILT[key], group, summary, "coming_soon", None))
        else:
            status: Status = "ready" if provider.is_configured(settings) else "needs_setup"
            out.append(Listing(key, provider.label, group, summary, status, provider))
    shown = {listing.key for listing in out}
    for key, provider in PROVIDERS.items():  # anything else registered is still listed
        if key not in shown:
            status = "ready" if provider.is_configured(settings) else "needs_setup"
            out.append(Listing(key, provider.label, "Other", "", status, provider))
    return out
