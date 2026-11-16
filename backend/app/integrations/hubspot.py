"""HubSpot: connect with a read-only access token, and bring in its deals and customers.

Read-only: Vyterlix only ever reads from HubSpot. There is no sign-in page and Vyterlix does not
have to be registered with HubSpot: the owner makes a private app in their own HubSpot (Settings,
Integrations, Private apps) with only the two "read" permissions below, and types the access token
in. The token is kept encrypted and the app can be deleted in HubSpot at any time to cut Vyterlix
off.

Deals become opportunities, the way a file of leads would: the day it came in, where it is up to
(lead, proposal, won or lost), what it is worth, who it is for. Contacts that HubSpot marks as
customers are added as customers, unless they are already known by email or name. Contacts who are
only leads are not made customers: a lead is counted when it has a deal.

A deal in another currency than pounds is kept, but its worth is left out, and counted, rather than
guessed at.

Written from HubSpot's documented CRM API (v3) and tested against a pretend account. It has to be
tried against a real HubSpot developer test account before it is relied on.
"""

import hashlib
from datetime import date
from decimal import Decimal
from typing import Any

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

BASE = "https://api.hubapi.com"
PAGE_SIZE = 100
MAX_PAGES = 200
CURRENCY = "GBP"
CUSTOMER_STAGES = ("customer", "evangelist")  # HubSpot's own lifecycle stages
# Words in a stage's name that say a proposal has gone out (HubSpot stage names are the owner's).
PROPOSAL_WORDS = ("proposal", "quote", "quotation", "contract", "negotiat", "offer")
DEAL_PROPERTIES = (
    "dealname", "amount", "dealstage", "pipeline", "closedate", "createdate", "dealtype",
    "hs_is_closed", "hs_is_closed_won", "hs_lastmodifieddate", "deal_currency_code",
)  # fmt: skip
CONTACT_PROPERTIES = ("firstname", "lastname", "email", "company", "lifecyclestage")


def _day(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def _money(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.0001"))
    except ArithmeticError:
        return None
    return amount if amount >= 0 else None


def _true(value: Any) -> bool:
    return str(value).lower() == "true"


def _person(contact: dict[str, Any]) -> tuple[str | None, str | None]:
    """(name, email) of a HubSpot contact. A contact with no name is named by their company."""
    props = contact.get("properties") or {}
    name = " ".join(x for x in (props.get("firstname"), props.get("lastname")) if x).strip()
    name = name or (props.get("company") or "").strip()
    email = (props.get("email") or "").strip().lower()
    return (name[:200] or None), (email[:254] or None)


class HubSpotProvider(Provider):
    key = "hubspot"
    label = "HubSpot"
    permissions = (
        "Read your deals, so we can show your leads, proposals and wins.",
        "Read your contacts, so we know who each deal is for and who your customers are.",
        "Vyterlix never changes anything in HubSpot.",
        "You can delete the app in HubSpot at any time to cut Vyterlix off.",
    )
    connect_fields = (
        ConnectField(
            "access_token",
            "Private app access token",
            "In HubSpot: Settings, Integrations, Private apps, Create a private app. Give it "
            "only crm.objects.deals.read and crm.objects.contacts.read, create it, then copy "
            "the access token.",
            secret=True,
        ),
    )
    # Tests put a pretend HubSpot here; in real use it stays None and the real one is called.
    transport: httpx.BaseTransport | None = None

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=30.0, transport=self.transport, follow_redirects=False)

    # --- connecting ---------------------------------------------------------------------------

    def connect_with_keys(self, values: dict[str, str]) -> TokenSet:
        token = values["access_token"].strip()
        if not token:
            raise ProviderRejected("Please paste your HubSpot access token.", code="bad_token")
        tokens = TokenSet(access_token=token)
        # Proves the token works and can read both deals and contacts.
        self._get(tokens, "/crm/v3/objects/deals", {"limit": 1})
        self._get(tokens, "/crm/v3/objects/contacts", {"limit": 1})
        details = self._details(tokens)
        portal = str(details.get("portalId") or "")
        if not portal:  # no account number to be had: a fingerprint of the token stands in
            portal = "token-" + hashlib.sha256(token.encode()).hexdigest()[:16]
        return TokenSet(access_token=token, extra={"portal": portal})

    def _details(self, tokens: TokenSet) -> dict[str, Any]:
        try:
            return self._get(tokens, "/account-info/v3/details", {}).json() or {}
        except (ProviderUnavailable, ProviderRejected, ReauthRequired, ValueError):
            return {}  # a nicety: the account number and name

    def account(self, tokens: TokenSet) -> AccountInfo:
        portal = str(tokens.extra.get("portal") or "")
        details = self._details(tokens)
        name = str(details.get("companyName") or details.get("portalId") or "HubSpot")[:100]
        return AccountInfo(portal, name, {"portal": portal})

    # --- talking to HubSpot -------------------------------------------------------------------

    def _get(self, tokens: TokenSet, path: str, params: dict[str, Any]) -> httpx.Response:
        try:
            with self._client() as http:
                response = http.get(
                    BASE + path,
                    params=params,
                    headers={
                        "Authorization": f"Bearer {tokens.access_token}",
                        "Accept": "application/json",
                    },
                )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("We could not reach HubSpot. We'll try again.") from exc
        if response.status_code == 401:
            raise ReauthRequired(
                "HubSpot did not accept the access token. Make a new one in your private app "
                "and connect again."
            )
        if response.status_code == 403:
            raise ProviderRejected(
                "Your HubSpot app is not allowed to read this. Give it the permissions "
                "crm.objects.deals.read and crm.objects.contacts.read and connect again.",
                code="missing_permission",
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderUnavailable("HubSpot is busy or not responding. We'll try again.")
        if response.status_code >= 400:
            raise ProviderRejected(
                "HubSpot refused the request. Check the app can read deals and contacts.",
                code="provider_rejected",
            )
        return response

    def _pages(self, ctx: SyncContext, path: str, params: dict[str, Any]):
        """Every record of one kind, page by page."""
        after: str | None = None
        for _ in range(MAX_PAGES):
            query = {"limit": PAGE_SIZE, **params, **({"after": after} if after else {})}
            body = self._get(ctx.tokens, path, query).json()
            yield from body.get("results") or []
            after = ((body.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                return

    def _stage_names(self, ctx: SyncContext) -> dict[str, dict[str, Any]]:
        """{stage id: {"label", "closed", "won"}} from the deal pipelines. If HubSpot will not say
        (the app may not be allowed), every open deal is simply a lead."""
        try:
            body = self._get(ctx.tokens, "/crm/v3/pipelines/deals", {}).json()
        except (ProviderRejected, ValueError):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for pipeline in body.get("results") or []:
            for stage in pipeline.get("stages") or []:
                meta = stage.get("metadata") or {}
                closed = _true(meta.get("isClosed"))
                try:
                    won = closed and Decimal(str(meta.get("probability") or 0)) >= 1
                except ArithmeticError:
                    won = False
                out[str(stage.get("id"))] = {
                    "label": str(stage.get("label") or ""),
                    "closed": closed,
                    "won": won,
                }
        return out

    # --- bringing deals and customers in ------------------------------------------------------

    def sync(self, ctx: SyncContext) -> SyncOutcome:
        contacts: dict[str, dict[str, Any]] = {}
        customers: list[dict[str, Any]] = []
        fetched = 0
        for contact in self._pages(
            ctx, "/crm/v3/objects/contacts", {"properties": ",".join(CONTACT_PROPERTIES)}
        ):
            fetched += 1
            contacts[str(contact["id"])] = contact
            name, email = _person(contact)
            stage = ((contact.get("properties") or {}).get("lifecyclestage") or "").lower()
            if stage in CUSTOMER_STAGES and (name or email):
                customers.append({"customer_name": name, "customer_email": email})
        ctx.progress(fetched, fetched)

        stages = self._stage_names(ctx)
        deals: list[dict[str, Any]] = []
        other_currency = 0
        params = {"properties": ",".join(DEAL_PROPERTIES), "associations": "contacts"}
        for deal in self._pages(ctx, "/crm/v3/objects/deals", params):
            fetched += 1
            row, left_out = self._deal(deal, contacts, stages)
            other_currency += left_out
            if row:
                deals.append(row)
        ctx.progress(fetched, fetched)
        return SyncOutcome(
            records_fetched=fetched,
            cursor={
                "synced": ctx.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "left_out_other_currency": other_currency,
            },
            datasets={"customers": customers, "opportunities": deals},
        )

    def _deal(
        self,
        deal: dict[str, Any],
        contacts: dict[str, dict[str, Any]],
        stages: dict[str, dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, int]:
        """(opportunity row or None, 1 if its worth was left out for being in another currency)."""
        props = deal.get("properties") or {}
        created = _day(props.get("createdate"))
        name = (props.get("dealname") or "").strip()
        if created is None or not name:
            return None, 0
        stage_info = stages.get(str(props.get("dealstage")), {})
        closed = _true(props.get("hs_is_closed")) or stage_info.get("closed", False)
        won = _true(props.get("hs_is_closed_won")) or (closed and stage_info.get("won", False))
        if closed:
            stage = "won" if won else "lost"
            decided = _day(props.get("closedate")) or _day(props.get("hs_lastmodifieddate"))
            closed_on = max(decided, created) if decided else created
        else:
            label = stage_info.get("label", "").lower()
            stage = "proposal" if any(w in label for w in PROPOSAL_WORDS) else "lead"
            closed_on = None
        currency = (props.get("deal_currency_code") or CURRENCY).upper()
        value = _money(props.get("amount")) if currency == CURRENCY else None
        left_out = 1 if currency != CURRENCY and props.get("amount") not in (None, "") else 0
        kind = (
            "renewal" if str(props.get("dealtype") or "").lower() == "existingbusiness" else "new"
        )
        who: tuple[str | None, str | None] = (None, None)
        linked = ((deal.get("associations") or {}).get("contacts") or {}).get("results") or []
        for link in linked:
            contact = contacts.get(str(link.get("id")))
            if contact:
                who = _person(contact)
                break
        return (
            {
                "name": name[:200], "stage": stage, "kind": kind, "value": value,
                "created_on": created, "proposal_on": None, "closed_on": closed_on,
                "customer_name": who[0], "customer_email": who[1],
                "notes": "HubSpot deal", "reference": f"hubspot-deal-{deal['id']}",
            },
            left_out,
        )  # fmt: skip
