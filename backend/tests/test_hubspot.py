# ruff: noqa: E501
"""HubSpot: connecting with a read-only token, and bringing deals and customers in, against a
pretend account."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal as D
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import select

from app.integrations.base import (
    AccountInfo,
    ProviderRejected,
    ProviderUnavailable,
    ReauthRequired,
    SyncContext,
    TokenSet,
)
from app.integrations.hubspot import HubSpotProvider
from app.integrations.registry import PROVIDERS
from app.models.data import Customer, Opportunity
from tests.test_health import scoped

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
VALUES = {"access_token": "pat-secret"}

STAGES = {
    "results": [
        {
            "id": "default",
            "stages": [
                {"id": "new", "label": "Appointment scheduled", "metadata": {"isClosed": "false", "probability": "0.2"}},
                {"id": "quote", "label": "Proposal sent", "metadata": {"isClosed": "false", "probability": "0.6"}},
                {"id": "won", "label": "Closed won", "metadata": {"isClosed": "true", "probability": "1.0"}},
                {"id": "lost", "label": "Closed lost", "metadata": {"isClosed": "true", "probability": "0.0"}},
            ],
        }
    ]
}  # fmt: skip


def contact(cid, first="Jo", last="Baker", email="Jo@Example.co.uk", company="", life="lead"):
    return {
        "id": str(cid),
        "properties": {"firstname": first, "lastname": last, "email": email, "company": company, "lifecyclestage": life},
    }  # fmt: skip


def deal(did, stage="new", contacts=("1",), **props):
    base = {
        "dealname": f"Deal {did}", "amount": "1200.00", "dealstage": stage,
        "createdate": "2026-09-01T09:00:00.000Z", "closedate": None, "dealtype": "newbusiness",
        "hs_is_closed": "false", "hs_is_closed_won": "false",
        "hs_lastmodifieddate": "2026-09-20T09:00:00.000Z",
    }  # fmt: skip
    return {
        "id": str(did),
        "properties": {**base, **props},
        "associations": {
            "contacts": {"results": [{"id": c, "type": "deal_to_contact"} for c in contacts]}
        },
    }


def account(contacts=(), deals=(), *, status=200, pipelines_status=200, page_size=100):
    """A pretend HubSpot. `seen` collects the requests."""
    seen = []

    def page(items, request):
        q = parse_qs(request.url.query.decode())
        size = min(int(q.get("limit", ["100"])[0]), page_size)
        start = int(q.get("after", ["0"])[0])
        body = {"results": list(items)[start : start + size]}
        if start + size < len(items):
            body["paging"] = {"next": {"after": str(start + size)}}
        return httpx.Response(200, json=body)

    def handler(request):
        seen.append(request)
        path = request.url.path
        if status != 200 and path.startswith("/crm/v3/objects"):
            return httpx.Response(status)
        if path == "/account-info/v3/details":
            return httpx.Response(200, json={"portalId": 4242, "companyName": "Baker Street Bakes"})
        if path == "/crm/v3/pipelines/deals":
            return httpx.Response(pipelines_status, json=STAGES)
        if path == "/crm/v3/objects/contacts":
            return page(contacts, request)
        if path == "/crm/v3/objects/deals":
            return page(deals, request)
        return httpx.Response(404)

    provider = HubSpotProvider()
    provider.transport = httpx.MockTransport(handler)
    provider.seen = seen
    return provider


def tokens():
    return TokenSet("pat-secret", extra={"portal": "4242"})


def ctx():
    return SyncContext(
        tokens=tokens(), account=AccountInfo("4242", "Baker Street Bakes"), cursor={},
        settings={}, progress=lambda done, total: None, now=NOW,
    )  # fmt: skip


def run_sync_job(db, storage):
    """Run the worker until the sync itself has run (a figures job may be waiting ahead of it)."""
    from tests.test_integrations import run_worker

    for _ in range(5):
        job = run_worker(db, storage)
        if job is None or job.kind == "integration.sync":
            return job
    raise AssertionError("the sync job never ran")


def run(contacts=(), deals=(), **kw):
    return account(contacts, deals, **kw).sync(ctx())


# --- what it is --------------------------------------------------------------------------------------------


def test_it_is_always_available_reads_only_and_asks_for_one_token():
    provider = HubSpotProvider()
    assert provider.is_configured(None) and "hubspot" in PROVIDERS
    assert [f.name for f in provider.connect_fields] == ["access_token"]
    assert [f.secret for f in provider.connect_fields] == [True]
    assert any("never changes anything in HubSpot" in p for p in provider.permissions)
    assert "crm.objects.deals.read" in provider.connect_fields[0].help


def test_it_has_no_sign_in_page():
    with pytest.raises(ProviderRejected):
        HubSpotProvider().authorize_url(state="s", redirect_uri="x", code_challenge="c")
    with pytest.raises(ReauthRequired):
        HubSpotProvider().refresh(tokens())


# --- connecting ----------------------------------------------------------------------------------------------


def test_a_good_token_connects_and_the_account_name_is_read_from_hubspot():
    provider = account()
    got = provider.connect_with_keys(VALUES)
    assert (got.access_token, got.extra) == ("pat-secret", {"portal": "4242"})
    info = provider.account(got)
    assert (info.external_id, info.name) == ("4242", "Baker Street Bakes")
    paths = [r.url.path for r in provider.seen]
    assert (
        "/crm/v3/objects/deals" in paths and "/crm/v3/objects/contacts" in paths
    )  # both are proved readable
    assert all(r.method == "GET" and r.url.host == "api.hubapi.com" for r in provider.seen)
    assert provider.seen[0].headers["authorization"] == "Bearer pat-secret"


def test_without_an_account_number_the_token_stands_in_and_is_stable():
    def handler(request):
        if request.url.path == "/account-info/v3/details":
            return httpx.Response(403)
        return httpx.Response(200, json={"results": []})

    provider = HubSpotProvider()
    provider.transport = httpx.MockTransport(handler)
    a, b = provider.connect_with_keys(VALUES), provider.connect_with_keys(VALUES)
    assert a.extra["portal"] == b.extra["portal"] and a.extra["portal"].startswith("token-")
    assert "pat-secret" not in a.extra["portal"]
    assert provider.account(a).name == "HubSpot"


def test_a_blank_token_is_refused_plainly():
    with pytest.raises(ProviderRejected) as e:
        account().connect_with_keys({"access_token": "  "})
    assert e.value.code == "bad_token"


def test_a_token_hubspot_does_not_accept_says_so_plainly():
    with pytest.raises(ReauthRequired) as e:
        account(status=401).connect_with_keys(VALUES)
    assert "did not accept the access token" in e.value.message


def test_an_app_without_permission_to_read_says_so_plainly():
    with pytest.raises(ProviderRejected) as e:
        account(status=403).connect_with_keys(VALUES)
    assert e.value.code == "missing_permission" and "crm.objects.contacts.read" in e.value.message


def test_a_busy_or_unreachable_hubspot_is_tried_again_later():
    for status in (429, 503):
        with pytest.raises(ProviderUnavailable):
            account(status=status).connect_with_keys(VALUES)

    def down(request):
        raise httpx.ConnectError("no route")

    provider = HubSpotProvider()
    provider.transport = httpx.MockTransport(down)
    with pytest.raises(ProviderUnavailable):
        provider.connect_with_keys(VALUES)


# --- deals become opportunities ------------------------------------------------------------------------------


def test_an_open_deal_is_a_lead_and_carries_its_client_and_worth():
    out = run([contact(1)], [deal(10)])
    [row] = out.datasets["opportunities"]
    assert row["reference"] == "hubspot-deal-10" and row["name"] == "Deal 10"
    assert (row["stage"], row["kind"], row["value"], row["closed_on"]) == (
        "lead",
        "new",
        D("1200"),
        None,
    )
    assert row["created_on"] == date(2026, 9, 1)
    assert (row["customer_name"], row["customer_email"]) == ("Jo Baker", "jo@example.co.uk")


def test_a_stage_named_like_a_proposal_is_a_proposal():
    [row] = run([contact(1)], [deal(11, stage="quote")]).datasets["opportunities"]
    assert row["stage"] == "proposal" and row["closed_on"] is None


def test_closed_won_and_closed_lost_have_the_day_they_were_decided():
    won = deal(
        12,
        stage="won",
        hs_is_closed="true",
        hs_is_closed_won="true",
        closedate="2026-09-15T00:00:00.000Z",
    )
    lost = deal(13, stage="lost", hs_is_closed="true", closedate="2026-09-16T00:00:00.000Z")
    a, b = run([contact(1)], [won, lost]).datasets["opportunities"]
    assert (a["stage"], a["closed_on"]) == ("won", date(2026, 9, 15))
    assert (b["stage"], b["closed_on"]) == ("lost", date(2026, 9, 16))


def test_a_deal_with_no_close_date_uses_the_day_it_last_changed_and_never_closes_before_it_began():
    one = deal(14, stage="won", hs_is_closed="true", hs_is_closed_won="true")
    early = deal(15, stage="lost", hs_is_closed="true", closedate="2025-01-01T00:00:00.000Z")
    a, b = run([contact(1)], [one, early]).datasets["opportunities"]
    assert a["closed_on"] == date(2026, 9, 20)
    assert b["closed_on"] == date(2026, 9, 1)


def test_the_pipeline_says_won_or_lost_when_the_deal_itself_does_not():
    only_stage = deal(
        16,
        stage="won",
        hs_is_closed=None,
        hs_is_closed_won=None,
        closedate="2026-09-15T00:00:00.000Z",
    )
    lost = deal(
        17,
        stage="lost",
        hs_is_closed=None,
        hs_is_closed_won=None,
        closedate="2026-09-15T00:00:00.000Z",
    )
    a, b = run([contact(1)], [only_stage, lost]).datasets["opportunities"]
    assert (a["stage"], b["stage"]) == ("won", "lost")


def test_if_the_pipelines_cannot_be_read_open_deals_are_simply_leads():
    [row] = run([contact(1)], [deal(18, stage="quote")], pipelines_status=403).datasets[
        "opportunities"
    ]
    assert row["stage"] == "lead"


def test_a_renewal_is_marked_and_other_currencies_leave_the_worth_out():
    renewal = deal(19, dealtype="existingbusiness")
    dollars = deal(20, deal_currency_code="USD")
    pounds = deal(21, deal_currency_code="gbp", amount="-5")
    out = run([contact(1)], [renewal, dollars, pounds])
    a, b, c = out.datasets["opportunities"]
    assert a["kind"] == "renewal" and a["value"] == D("1200")
    assert b["value"] is None and c["value"] is None
    assert out.cursor["left_out_other_currency"] == 1


def test_a_deal_with_no_name_or_no_start_day_is_left_out():
    out = run([contact(1)], [deal(1, dealname=""), deal(2, createdate=None), deal(3)])
    assert [r["reference"] for r in out.datasets["opportunities"]] == ["hubspot-deal-3"]
    assert out.records_fetched == 1 + 3  # one contact, three deals looked at


def test_a_client_is_the_first_contact_who_can_be_found_and_a_company_names_a_nameless_one():
    contacts = [contact(2, first="", last="", email="", company="Acme Ltd")]
    a, b, c = run(
        contacts, [deal(1, contacts=("99", "2")), deal(2, contacts=()), deal(3, contacts=("2",))]
    ).datasets["opportunities"]
    assert (a["customer_name"], a["customer_email"]) == ("Acme Ltd", None)
    assert (b["customer_name"], b["customer_email"]) == (None, None)
    assert c["customer_name"] == "Acme Ltd"


# --- contacts who are customers ------------------------------------------------------------------------------


def test_only_contacts_marked_as_customers_become_customers():
    contacts = [
        contact(1, life="customer"),
        contact(2, first="Al", last="Lead", email="al@x.co.uk", life="lead"),
        contact(3, first="Eve", last="Fan", email="eve@x.co.uk", life="evangelist"),
        contact(4, first="", last="", email="", company="", life="customer"),
    ]
    out = run(contacts)
    assert out.datasets["customers"] == [
        {"customer_name": "Jo Baker", "customer_email": "jo@example.co.uk"},
        {"customer_name": "Eve Fan", "customer_email": "eve@x.co.uk"},
    ]


def test_every_page_of_both_is_read_and_only_reads():
    provider = account(
        [contact(i) for i in range(1, 6)], [deal(i) for i in range(1, 6)], page_size=2
    )
    out = provider.sync(ctx())
    assert len(out.datasets["opportunities"]) == 5 and out.records_fetched == 10
    deal_pages = [r for r in provider.seen if r.url.path == "/crm/v3/objects/deals"]
    assert len(deal_pages) == 3 and all(r.method == "GET" for r in provider.seen)


def test_a_token_that_stops_working_during_a_sync_asks_to_be_connected_again():
    with pytest.raises(ReauthRequired):
        account(status=401).sync(ctx())


# --- the whole connect and sync through the framework --------------------------------------------------------


def test_a_token_connects_hubspot_and_the_sync_brings_deals_and_customers_in_without_showing_the_token(
    api, db, business, storage, monkeypatch
):
    from app.models.integrations import Integration
    from tests.test_integrations import auth, request_sync, run_worker, url

    contacts = [
        contact(1, life="customer"),
        contact(2, first="Al", last="Lead", email="al@x.co.uk"),
    ]
    deals = [
        deal(10, contacts=("1",)),
        deal(
            11,
            stage="won",
            contacts=("2",),
            hs_is_closed="true",
            hs_is_closed_won="true",
            closedate="2026-09-15T00:00:00.000Z",
        ),
    ]
    monkeypatch.setitem(PROVIDERS, "hubspot", account(contacts, deals))
    listed = api.get(url(business, "/providers"), headers=auth(business)).json()
    entry = next(p for p in listed if p["key"] == "hubspot")
    assert [f["name"] for f in entry["fields"]] == ["access_token"]

    res = api.post(
        url(business, "/connect-keys"),
        json={"provider": "hubspot", "values": VALUES},
        headers=auth(business),
    )
    assert res.status_code == 200, res.text
    conn = res.json()
    assert conn["provider"] == "hubspot" and conn["account_name"] == "Baker Street Bakes"
    assert "pat-secret" not in json.dumps(conn)

    request_sync(api, business, conn["id"])
    ran = run_worker(db, storage)
    assert (
        ran.status == "succeeded" and ran.result["records_created"] == 3
    )  # 2 deals + 1 customer from a contact
    with scoped(db, business):
        opps = list(db.scalars(select(Opportunity).order_by(Opportunity.source_ref)))
        assert [(o.source_ref, o.stage, o.source) for o in opps] == [
            ("hubspot-deal-10", "lead", "hubspot"),
            ("hubspot-deal-11", "won", "hubspot"),
        ]
        names = sorted(c.name for c in db.scalars(select(Customer)))
        assert names == [
            "Al Lead",
            "Jo Baker",
        ]  # Al is there because a deal is for them, not as a bare lead
        saved = db.scalars(select(Integration)).one()
        assert saved.external_account_id == "4242"
        stored = (
            saved.credentials
            if isinstance(saved.credentials, bytes)
            else bytes(saved.credentials, "utf8")
        )
        assert b"pat-secret" not in stored


def test_a_second_sync_updates_a_deal_that_moved_on_and_adds_nothing_twice(
    api, db, business, storage, monkeypatch
):
    from tests.test_integrations import auth, request_sync, run_worker, url

    contacts = [contact(1, life="customer")]
    monkeypatch.setitem(PROVIDERS, "hubspot", account(contacts, [deal(10)]))
    conn = api.post(
        url(business, "/connect-keys"),
        json={"provider": "hubspot", "values": VALUES},
        headers=auth(business),
    ).json()
    request_sync(api, business, conn["id"])
    assert run_worker(db, storage).status == "succeeded"

    won = deal(
        10,
        stage="won",
        hs_is_closed="true",
        hs_is_closed_won="true",
        closedate="2026-10-01T00:00:00.000Z",
        amount="1500.00",
        dealname="Deal 10 renamed",
    )
    monkeypatch.setitem(PROVIDERS, "hubspot", account(contacts, [won]))
    request_sync(api, business, conn["id"])
    ran = run_sync_job(db, storage)
    assert ran.status == "succeeded" and ran.result["records_created"] == 0
    with scoped(db, business):
        [opp] = db.scalars(select(Opportunity)).all()
        assert (opp.stage, opp.value, opp.closed_on, opp.name) == (
            "won",
            D("1500"),
            date(2026, 10, 1),
            "Deal 10 renamed",
        )
        assert len(list(db.scalars(select(Customer)))) == 1  # Jo, once


def test_a_deal_that_is_reopened_loses_its_close_day(api, db, business, storage, monkeypatch):
    from tests.test_integrations import auth, request_sync, run_worker, url

    won = deal(
        10,
        stage="won",
        hs_is_closed="true",
        hs_is_closed_won="true",
        closedate="2026-09-15T00:00:00.000Z",
    )
    monkeypatch.setitem(PROVIDERS, "hubspot", account([contact(1)], [won]))
    conn = api.post(
        url(business, "/connect-keys"),
        json={"provider": "hubspot", "values": VALUES},
        headers=auth(business),
    ).json()
    request_sync(api, business, conn["id"])
    run_worker(db, storage)
    monkeypatch.setitem(PROVIDERS, "hubspot", account([contact(1)], [deal(10)]))
    request_sync(api, business, conn["id"])
    assert run_sync_job(db, storage).status == "succeeded"
    with scoped(db, business):
        [opp] = db.scalars(select(Opportunity)).all()
        assert (opp.stage, opp.closed_on) == ("lead", None)


def test_a_customer_already_known_by_email_is_not_added_again(
    api, db, business, storage, monkeypatch
):
    from tests.test_integrations import auth, request_sync, run_worker, url

    with scoped(db, business):
        db.add(Customer(name="Joanna Baker", email="jo@example.co.uk"))
        db.flush()
    monkeypatch.setitem(PROVIDERS, "hubspot", account([contact(1, life="customer")], [deal(10)]))
    conn = api.post(
        url(business, "/connect-keys"),
        json={"provider": "hubspot", "values": VALUES},
        headers=auth(business),
    ).json()
    request_sync(api, business, conn["id"])
    ran = run_worker(db, storage)
    assert ran.status == "succeeded" and ran.result["records_created"] == 1  # the deal only
    with scoped(db, business):
        assert [c.name for c in db.scalars(select(Customer))] == ["Joanna Baker"]
        assert db.scalars(select(Opportunity)).one().customer_id is not None


def test_missing_boxes_and_wrong_tokens_are_refused_plainly_and_nothing_is_saved(
    api, db, business, monkeypatch
):
    from app.models.integrations import Integration
    from tests.test_integrations import auth, url

    monkeypatch.setitem(PROVIDERS, "hubspot", account())
    res = api.post(
        url(business, "/connect-keys"),
        json={"provider": "hubspot", "values": {}},
        headers=auth(business),
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "missing_fields"
    assert "Private app access token" in res.json()["error"]["message"]
    monkeypatch.setitem(PROVIDERS, "hubspot", account(status=401))
    res = api.post(
        url(business, "/connect-keys"),
        json={"provider": "hubspot", "values": VALUES},
        headers=auth(business),
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "reauth_required"
    with scoped(db, business):
        assert db.scalars(select(Integration)).first() is None


def test_only_the_owner_can_connect_hubspot(api, business, monkeypatch):
    from tests.test_integrations import url

    monkeypatch.setitem(PROVIDERS, "hubspot", account())
    res = api.post(url(business, "/connect-keys"), json={"provider": "hubspot", "values": VALUES})
    assert res.status_code in (401, 403)
