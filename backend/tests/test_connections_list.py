# ruff: noqa: E501
"""The Connections page lists every system Vyterlix is meant to connect to, 11 in all, each with an
honest status (client feedback 15/11/2026)."""

from app.core.config import Settings
from app.integrations import registry
from tests.test_integrations import auth, url

ELEVEN = [
    "xero", "quickbooks", "zoho", "shopify", "woocommerce", "square", "google_analytics", "meta",
    "hubspot", "whatsapp", "truelayer",
]  # fmt: skip
NOT_BUILT = {"shopify", "meta", "hubspot", "whatsapp"}


def bare(monkeypatch, **credentials):
    """The listing as an installation with nothing registered yet (or only what is given) sees it."""
    monkeypatch.setattr(
        registry, "get_settings", lambda: Settings(env="test", _env_file=None, **credentials)
    )


def test_all_eleven_systems_are_listed_in_order(monkeypatch):
    bare(monkeypatch)
    keys = [i.key for i in registry.listings() if i.key != "sandbox"]
    assert keys == ELEVEN


def test_the_four_not_built_yet_say_coming_soon_and_have_nothing_to_connect(monkeypatch):
    bare(monkeypatch)
    soon = {i.key: i for i in registry.listings() if i.status == "coming_soon"}
    assert set(soon) == NOT_BUILT
    assert all(i.provider is None for i in soon.values())


def test_a_built_system_that_is_not_registered_yet_says_it_needs_setting_up(monkeypatch):
    bare(monkeypatch)
    by_key = {i.key: i for i in registry.listings()}
    assert by_key["xero"].status == "needs_setup"
    assert by_key["google_analytics"].status == "needs_setup"
    assert by_key["truelayer"].status == "needs_setup"


def test_registering_with_a_system_makes_it_ready(monkeypatch):
    bare(monkeypatch, xero_client_id="id", xero_client_secret="secret")
    by_key = {i.key: i for i in registry.listings()}
    assert by_key["xero"].status == "ready"
    assert by_key["quickbooks"].status == "needs_setup"


def test_systems_that_connect_with_keys_need_no_registration(monkeypatch):
    bare(monkeypatch)
    by_key = {i.key: i for i in registry.listings()}
    assert by_key["woocommerce"].status == "ready"  # the shop owner types in their own keys


def test_every_system_has_a_group_and_one_plain_sentence(monkeypatch):
    bare(monkeypatch)
    for item in registry.listings():
        assert item.group and item.summary.endswith("."), item.key


def test_a_not_ready_system_cannot_be_connected(api, business, monkeypatch):
    bare(monkeypatch)
    res = api.post(url(business, "/connect"), json={"provider": "xero"}, headers=auth(business))
    assert res.status_code == 422 and res.json()["error"]["code"] == "unknown_provider"
    res = api.post(url(business, "/connect"), json={"provider": "shopify"}, headers=auth(business))
    assert res.status_code == 422


def test_the_page_gets_the_whole_list_from_the_api(api, business, monkeypatch):
    bare(monkeypatch)
    res = api.get(url(business, "/providers"), headers=auth(business))
    assert res.status_code == 200
    rows = [p for p in res.json() if p["key"] != "sandbox"]
    assert [p["key"] for p in rows] == ELEVEN
    soon = [p for p in rows if p["status"] == "coming_soon"]
    assert {p["key"] for p in soon} == NOT_BUILT
    assert all(p["permissions"] == [] and p["fields"] == [] for p in soon)
    assert all(p["group"] and p["summary"] for p in rows)
