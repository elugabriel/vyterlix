import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.tenant import tenant_scope
from app.models.business import BusinessListItem
from app.models.identity import AuditLog, OrganizationUser, Role, User
from app.services.business_lists import MAX_ITEMS_PER_LIST, SUGGESTIONS

ORGS = "/api/v1/organizations"


@pytest.fixture
def business(api, db, signup):
    """Acme with owner@ and viewer@. Returns (org_id, {role: auth})."""
    auth = {"owner": signup("owner@acme.co.uk"), "viewer": signup("viewer@acme.co.uk")}
    org_id = api.post(ORGS, json={"name": "Acme"}, headers=auth["owner"]).json()["id"]
    db.add(
        OrganizationUser(
            organization_id=uuid.UUID(org_id),
            user_id=db.scalars(select(User.id).where(User.email == "viewer@acme.co.uk")).one(),
            role_id=db.scalars(select(Role.id).where(Role.code == "viewer")).first(),
        )
    )
    db.flush()
    return org_id, auth


def lists_url(org_id, kind=None, item_id=None):
    url = f"{ORGS}/{org_id}/lists"
    if kind:
        url += f"/{kind}"
    if item_id:
        url += f"/{item_id}"
    return url


def add(api, business, kind, name, who="owner", **extra):
    org_id, auth = business
    return api.post(lists_url(org_id, kind), json={"name": name, **extra}, headers=auth[who])


def patch(api, business, kind, item_id, body, who="owner"):
    org_id, auth = business
    return api.patch(lists_url(org_id, kind, item_id), json=body, headers=auth[who])


# --- suggestions -----------------------------------------------------------------------


def test_uk_suggestions_for_onboarding(api):
    res = api.get("/api/v1/business-list-suggestions")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"offering", "sales_channel", "customer_type", "cost_category"}
    assert "Business rates" in body["cost_category"]
    assert "Employer's NI & pensions" in body["cost_category"]
    assert body["offering"] == []  # offerings are too business-specific to suggest


# --- adding --------------------------------------------------------------------------------


def test_add_an_item(api, db, business):
    res = add(api, business, "sales_channel", "  Own   website ")
    assert res.status_code == 201
    item = res.json()
    assert (item["kind"], item["name"], item["is_active"]) == ("sales_channel", "Own website", True)
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "business.list_item_added")).one()
    assert entry.details == {"kind": "sales_channel"}


def test_duplicate_names_are_refused_whatever_the_capitals(api, business):
    add(api, business, "sales_channel", "Website")
    res = add(api, business, "sales_channel", "WEBSITE")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "already_exists"


def test_same_name_is_fine_in_a_different_list(api, business):
    add(api, business, "offering", "Wholesale")
    assert add(api, business, "sales_channel", "Wholesale").status_code == 201


def test_archived_duplicate_points_to_restoring_it(api, business):
    item_id = add(api, business, "customer_type", "Consumers").json()["id"]
    patch(api, business, "customer_type", item_id, {"is_active": False})
    res = add(api, business, "customer_type", "consumers")
    assert res.status_code == 409
    assert "restore it instead" in res.json()["error"]["message"]
    assert res.json()["error"]["details"] == {"id": item_id, "is_active": False}


def test_database_refuses_duplicates_even_if_code_forgets(db):
    """Belt and braces: the unique index catches races the API check can't."""
    from app.models.identity import Organization

    org = Organization(name="Acme")
    db.add(org)
    db.flush()
    with tenant_scope(db, org.id):
        db.add(BusinessListItem(kind="offering", name="Bread"))
        db.flush()
        with pytest.raises(IntegrityError, match="uq_business_list_items_name"):
            with db.begin_nested():
                db.add(BusinessListItem(kind="offering", name="BREAD"))
                db.flush()


def test_cost_of_sales_flag_only_for_cost_categories(api, business):
    ok = add(api, business, "cost_category", "Flour & ingredients", is_cost_of_sales=True)
    assert ok.json()["is_cost_of_sales"] is True
    bad = add(api, business, "sales_channel", "Shop", is_cost_of_sales=True)
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "cost_of_sales_only_costs"


@pytest.mark.parametrize("name", ["", "   ", "x" * 101])
def test_invalid_names_rejected(api, business, name):
    assert add(api, business, "offering", name).status_code == 422


def test_unknown_list_kind_rejected(api, business):
    assert add(api, business, "favourite_colours", "Blue").status_code == 422


def test_list_has_a_size_limit(api, business):
    org_id, auth = business
    names = [f"Channel {i}" for i in range(MAX_ITEMS_PER_LIST)]
    for chunk in (names[:50], names[50:]):
        api.post(
            lists_url(org_id, "sales_channel") + "/bulk",
            json={"names": chunk},
            headers=auth["owner"],
        )
    res = add(api, business, "sales_channel", "One too many")
    assert res.json()["error"]["code"] == "list_full"


# --- adding several at once (onboarding) ---------------------------------------------------


def test_bulk_add_skips_names_already_there(api, business):
    org_id, auth = business
    add(api, business, "sales_channel", "Amazon")
    res = api.post(
        lists_url(org_id, "sales_channel") + "/bulk",
        json={"names": ["amazon", "eBay", "Etsy", "etsy"]},
        headers=auth["owner"],
    )
    assert res.status_code == 200
    body = res.json()
    assert [i["name"] for i in body["added"]] == ["eBay", "Etsy"]
    assert body["already_there"] == ["amazon", "etsy"]


def test_bulk_adding_suggested_costs_marks_cost_of_sales(api, business):
    org_id, auth = business
    res = api.post(
        lists_url(org_id, "cost_category") + "/bulk",
        json={"names": ["Stock & materials", "Business rates"]},
        headers=auth["owner"],
    )
    flags = {i["name"]: i["is_cost_of_sales"] for i in res.json()["added"]}
    assert flags == {"Stock & materials": True, "Business rates": False}


def test_all_suggestions_can_be_added(api, business):
    org_id, auth = business
    for kind, names in SUGGESTIONS.items():
        if names:
            res = api.post(
                lists_url(org_id, kind) + "/bulk", json={"names": names}, headers=auth["owner"]
            )
            assert len(res.json()["added"]) == len(names)


# --- reading -----------------------------------------------------------------------------


def test_all_lists_grouped_and_sorted(api, business):
    org_id, auth = business
    for name in ("Website", "amazon", "Shop"):
        add(api, business, "sales_channel", name)
    add(api, business, "offering", "Bread")
    body = api.get(lists_url(org_id), headers=auth["viewer"]).json()  # any member can read
    assert [i["name"] for i in body["sales_channel"]] == ["amazon", "Shop", "Website"]
    assert [i["name"] for i in body["offering"]] == ["Bread"]
    assert body["customer_type"] == [] and body["cost_category"] == []


def test_sort_order_comes_before_alphabetical(api, business):
    org_id, auth = business
    ids = {
        n: add(api, business, "offering", n).json()["id"] for n in ("Bread", "Cakes", "Catering")
    }
    patch(api, business, "offering", ids["Catering"], {"sort_order": 1})
    names = [
        i["name"] for i in api.get(lists_url(org_id, "offering"), headers=auth["owner"]).json()
    ]
    assert names == ["Catering", "Bread", "Cakes"]


def test_archived_items_hidden_unless_asked_for(api, business):
    org_id, auth = business
    item_id = add(api, business, "offering", "Old product line").json()["id"]
    patch(api, business, "offering", item_id, {"is_active": False})
    assert api.get(lists_url(org_id, "offering"), headers=auth["owner"]).json() == []
    everything = api.get(
        lists_url(org_id, "offering"), params={"include_archived": True}, headers=auth["owner"]
    ).json()
    assert [(i["name"], i["is_active"]) for i in everything] == [("Old product line", False)]


# --- changing ----------------------------------------------------------------------------


def test_rename_archive_and_restore_are_audited(api, db, business):
    item_id = add(api, business, "sales_channel", "Instagram").json()["id"]
    renamed = patch(api, business, "sales_channel", item_id, {"name": "Instagram Shop"})
    assert renamed.json()["name"] == "Instagram Shop"
    patch(api, business, "sales_channel", item_id, {"is_active": False})
    restored = patch(api, business, "sales_channel", item_id, {"is_active": True})
    assert restored.json()["is_active"] is True

    details = [
        e.details
        for e in db.scalars(
            select(AuditLog)
            .where(AuditLog.action == "business.list_item_updated")
            .order_by(AuditLog.created_at)
        )
    ]
    assert details == [
        {"kind": "sales_channel", "fields": ["name"]},
        {"kind": "sales_channel", "fields": ["is_active"], "archived": True},
        {"kind": "sales_channel", "fields": ["is_active"], "archived": False},
    ]


def test_rename_to_an_existing_name_refused(api, business):
    add(api, business, "offering", "Bread")
    cakes = add(api, business, "offering", "Cakes").json()["id"]
    res = patch(api, business, "offering", cakes, {"name": "bread"})
    assert res.status_code == 409


def test_rename_only_changing_capitals_is_allowed(api, business):
    item_id = add(api, business, "offering", "bread").json()["id"]
    assert patch(api, business, "offering", item_id, {"name": "Bread"}).json()["name"] == "Bread"


def test_item_must_be_changed_through_its_own_list(api, business):
    item_id = add(api, business, "offering", "Bread").json()["id"]
    res = patch(api, business, "sales_channel", item_id, {"name": "Bakery"})
    assert res.status_code == 404


@pytest.mark.parametrize("body", [{}, {"name": None}, {"is_active": None}, {"sort_order": -1}])
def test_invalid_updates_rejected(api, business, body):
    item_id = add(api, business, "offering", "Bread").json()["id"]
    assert patch(api, business, "offering", item_id, body).status_code == 422


# --- permissions and separation ----------------------------------------------------------------


def test_viewers_can_read_but_not_change(api, business):
    item_id = add(api, business, "offering", "Bread").json()["id"]
    assert add(api, business, "offering", "Cakes", who="viewer").status_code == 403
    assert patch(api, business, "offering", item_id, {"name": "x"}, who="viewer").status_code == 403


def test_another_business_cannot_see_or_change_our_lists(api, business, signup):
    item_id = add(api, business, "offering", "Secret recipe").json()["id"]
    rival = signup("rival@acme.co.uk")
    rival_org = api.post(ORGS, json={"name": "Rival"}, headers=rival).json()["id"]

    assert api.get(lists_url(rival_org, "offering"), headers=rival).json() == []
    res = api.patch(
        lists_url(rival_org, "offering", item_id), json={"name": "Stolen"}, headers=rival
    )
    assert res.status_code == 404
    # And the rival can use the same name for their own list.
    assert (
        api.post(
            lists_url(rival_org, "offering"), json={"name": "Secret recipe"}, headers=rival
        ).status_code
        == 201
    )
