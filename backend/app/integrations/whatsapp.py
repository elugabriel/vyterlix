# ruff: noqa: E501
"""WhatsApp Business: hear when customers message you and when you reply, to show how fast you answer.

WhatsApp is different from the other connections. It has no list of past messages to fetch: it only
tells you about messages as they happen, by sending a notice (a "webhook") to an address you give it.
So connecting has two steps. The owner types in three things from their own Meta app (the phone number
id, an access token, and the app secret) and Vyterlix checks them. Then Vyterlix shows the owner the
address and the check word to put into that Meta app (WhatsApp, Configuration, Webhook), and from then
on messages are counted as they arrive. See services/whatsapp.py for what happens to a notice.

Read-only and private: Vyterlix never sends a message, and never keeps what a message says. Only when
it was sent, which way it went, and a one-way code for the customer are kept.

Vyterlix does not have to be registered with Meta for this: each business uses its own Meta app, so
there is nothing to approve on Vyterlix's side. (The business's own WhatsApp number must be set up on
the WhatsApp Business Platform, which Meta approves for the business, not for Vyterlix.)

Written from Meta's documented WhatsApp Cloud API and tested against pretend notices. It has to be
tried with a real WhatsApp Business number before it is relied on.
"""

import hashlib
import hmac
import re
import uuid
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

GRAPH = "https://graph.facebook.com"
_PHONE_ID = re.compile(r"^\d{5,25}$")


def verify_token(app_secret: str, phone_number_id: str) -> str:
    """The check word Meta sends back when the address is set up. Worked out from the app secret
    (never stored), so only the owner of the Meta app and Vyterlix can know it."""
    message = f"vyterlix-whatsapp:{phone_number_id}".encode()
    return hmac.new(app_secret.encode(), message, hashlib.sha256).hexdigest()[:24]


def webhook_address(integration_id: uuid.UUID | str) -> str:
    base = get_settings().public_api_url.rstrip("/")
    return f"{base}/api/v1/webhooks/whatsapp/{integration_id}"


class WhatsAppProvider(Provider):
    key = "whatsapp"
    label = "WhatsApp Business"
    permissions = (
        "Hear when a customer messages you and when you reply, so we can show how fast you answer.",
        "Vyterlix never reads what your messages say. It keeps only when each was sent, which "
        "way it went, and a one-way code for the customer, never their phone number.",
        "Vyterlix never sends a message from your number.",
        "You can delete the webhook in your Meta app at any time to cut Vyterlix off.",
    )
    connect_fields = (
        ConnectField(
            "phone_number_id",
            "Phone number ID",
            "In your Meta app: WhatsApp, API Setup. It is a long number, not your phone number.",
        ),
        ConnectField(
            "access_token",
            "Access token",
            "A permanent token for your WhatsApp account (Meta Business settings, System users). "
            "It only needs the whatsapp_business_management permission.",
            secret=True,
        ),
        ConnectField(
            "app_secret",
            "App secret",
            "In your Meta app: App settings, Basic. It is how we know a notice really came from "
            "WhatsApp.",
            secret=True,
        ),
    )
    # Tests put a pretend Meta here; in real use it stays None and the real one is called.
    transport: httpx.BaseTransport | None = None

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=30.0, transport=self.transport, follow_redirects=False)

    # --- connecting ---------------------------------------------------------------------------

    def connect_with_keys(self, values: dict[str, str]) -> TokenSet:
        phone_id = values["phone_number_id"].strip()
        if not _PHONE_ID.match(phone_id):
            raise ProviderRejected(
                "The phone number ID is a long number, found in your Meta app under WhatsApp, "
                "API Setup. It is not your phone number.",
                code="bad_phone_number_id",
            )
        tokens = TokenSet(
            access_token=values["access_token"].strip(),
            refresh_token=values["app_secret"].strip(),  # kept encrypted with the token
            extra={"phone_number_id": phone_id},
        )
        self._number(tokens)  # proves the token can see this number
        return tokens

    def _number(self, tokens: TokenSet) -> dict[str, Any]:
        phone_id = tokens.extra["phone_number_id"]
        version = get_settings().meta_graph_version
        try:
            with self._client() as http:
                response = http.get(
                    f"{GRAPH}/{version}/{phone_id}",
                    params={"fields": "display_phone_number,verified_name"},
                    headers={"Authorization": f"Bearer {tokens.access_token}"},
                )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("We could not reach WhatsApp. We'll try again.") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderUnavailable("WhatsApp is busy or not responding. We'll try again.")
        if response.status_code in (401, 403):
            raise ReauthRequired(
                "WhatsApp did not accept the access token. Make a new one and connect again."
            )
        if response.status_code >= 400:
            code = (
                ((response.json() or {}).get("error") or {}).get("code")
                if _is_json(response)
                else None
            )
            if code == 190:  # Meta's code for an expired or invalid token
                raise ReauthRequired(
                    "WhatsApp did not accept the access token. Make a new one and connect again."
                )
            raise ProviderRejected(
                "We could not find that WhatsApp number. Check the phone number ID, and that "
                "the token is for the same WhatsApp account.",
                code="not_found",
            )
        return response.json() if _is_json(response) else {}

    def account(self, tokens: TokenSet) -> AccountInfo:
        phone_id = tokens.extra["phone_number_id"]
        try:
            info = self._number(tokens)
        except (ProviderUnavailable, ProviderRejected, ReauthRequired):
            info = {}
        name = str(info.get("verified_name") or info.get("display_phone_number") or phone_id)
        settings = {
            "phone_number_id": phone_id,
            "display_phone_number": str(info.get("display_phone_number") or ""),
            "verify_token": verify_token(tokens.refresh_token or "", phone_id),
        }
        return AccountInfo(phone_id, name[:100], settings)

    def setup_notes(self, integration_id: uuid.UUID, settings: dict[str, Any]) -> list[str]:
        return [
            "To finish: in your Meta app, open WhatsApp, Configuration, Webhook, and choose Edit.",
            f"Callback URL: {webhook_address(integration_id)}",
            f"Verify token: {settings.get('verify_token', '')}",
            "Then press Verify and save, and under Webhook fields subscribe to messages.",
            "Messages are counted from then on. Past messages cannot be fetched from WhatsApp.",
        ]

    # --- keeping in touch ---------------------------------------------------------------------

    def sync(self, ctx: SyncContext) -> SyncOutcome:
        """Messages arrive by notice, so there is nothing to fetch. "Update now" checks that the
        token still works, so the person is told if it needs replacing."""
        self._number(ctx.tokens)
        return SyncOutcome(cursor={**ctx.cursor, "checked": ctx.now.strftime("%Y-%m-%dT%H:%M:%SZ")})


def _is_json(response: httpx.Response) -> bool:
    try:
        response.json()
    except ValueError:
        return False
    return True
