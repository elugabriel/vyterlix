"""Business A must never see or change business B's data — tested at the database layer
and through the API."""

import uuid

import pytest
from sqlalchemy import delete, func, select, update

from app.db.tenant import ACROSS_TENANTS, TenantScopeError, current_tenant, tenant_scope
from app.models.identity import Organization, OrganizationUser, Role, User

# --- setup helpers ---------------------------------------------------------------


def _user(db, email):
    user = User(email=email, password_hash="x", full_name=email.split("@")[0])
    db.add(user)
    db.flush()
    return user


def _role(db, code):
    return db.scalars(select(Role).where(Role.code == code, Role.organization_id.is_(None))).one()


@pytest.fixture
def two_businesses(db):
    """Business A (alice, carol) and business B (bob). Returns (a_id, b_id)."""
    a, b = Organization(name="A Ltd"), Organization(name="B Ltd")
    db.add_all([a, b])
    db.flush()
    alice, bob, carol = (_user(db, f"{n}@acme.co.uk") for n in ("alice", "bob", "carol"))
    owner, viewer = _role(db, "owner"), _role(db, "viewer")
    db.add_all(
        [
            OrganizationUser(organization_id=a.id, user_id=alice.id, role_id=owner.id),
            OrganizationUser(organization_id=a.id, user_id=carol.id, role_id=viewer.id),
            OrganizationUser(organization_id=b.id, user_id=bob.id, role_id=owner.id),
        ]
    )
    db.flush()
    return a.id, b.id


def _count_memberships(db, **options):
    stmt = select(func.count()).select_from(OrganizationUser)
    return db.scalar(stmt.execution_options(**options) if options else stmt)


# --- fail closed: no organisation in scope --------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        select(OrganizationUser),
        select(func.count()).select_from(OrganizationUser),
        select(Organization).join(
            OrganizationUser, OrganizationUser.organization_id == Organization.id
        ),
        select(User).where(User.id.in_(select(OrganizationUser.user_id))),
        update(OrganizationUser).values(status="suspended"),
        delete(OrganizationUser),
    ],
    ids=["select", "count", "join", "subquery", "update", "delete"],
)
def test_business_data_query_without_scope_raises(db, two_businesses, statement):
    with pytest.raises(TenantScopeError):
        db.execute(statement)


def test_non_business_tables_need_no_scope(db, two_businesses):
    assert len(db.scalars(select(User)).all()) == 3
    assert len(db.scalars(select(Organization)).all()) == 2


def test_explicit_opt_out_sees_all_businesses(db, two_businesses):
    assert _count_memberships(db, **ACROSS_TENANTS) == 3


# --- scoped: everything is limited to one organisation ---------------------------------


def test_scoped_select_only_returns_that_business(db, two_businesses):
    a_id, b_id = two_businesses
    with tenant_scope(db, a_id):
        rows = db.scalars(select(OrganizationUser)).all()
        assert {r.organization_id for r in rows} == {a_id}
        assert len(rows) == 2
    with tenant_scope(db, b_id):
        assert _count_memberships(db) == 1


def test_asking_for_another_business_by_id_returns_nothing(db, two_businesses):
    a_id, b_id = two_businesses
    with tenant_scope(db, a_id):
        stolen = select(OrganizationUser).where(OrganizationUser.organization_id == b_id)
        assert db.scalars(stolen).all() == []


def test_scoped_join_and_subquery_are_filtered_too(db, two_businesses):
    a_id, _ = two_businesses
    with tenant_scope(db, a_id):
        names = db.scalars(
            select(User.email).where(User.id.in_(select(OrganizationUser.user_id)))
        ).all()
        assert sorted(names) == ["alice@acme.co.uk", "carol@acme.co.uk"]


def test_scoped_update_and_delete_cannot_touch_the_other_business(db, two_businesses):
    a_id, b_id = two_businesses
    with tenant_scope(db, a_id):
        updated = db.execute(update(OrganizationUser).values(status="suspended")).rowcount
        deleted = db.execute(delete(OrganizationUser)).rowcount
    assert (updated, deleted) == (2, 2)

    with tenant_scope(db, b_id):
        survivor = db.scalars(select(OrganizationUser)).one()
        assert survivor.status == "active"


def test_new_rows_are_stamped_with_the_scoped_business(db, two_businesses):
    a_id, _ = two_businesses
    dave = _user(db, "dave@acme.co.uk")
    with tenant_scope(db, a_id):
        membership = OrganizationUser(user_id=dave.id, role_id=_role(db, "viewer").id)
        db.add(membership)
        db.flush()
    assert membership.organization_id == a_id


def test_writing_a_row_for_another_business_is_refused(db, two_businesses):
    a_id, b_id = two_businesses
    dave = _user(db, "dave@acme.co.uk")
    with tenant_scope(db, a_id):
        db.add(
            OrganizationUser(organization_id=b_id, user_id=dave.id, role_id=_role(db, "viewer").id)
        )
        with pytest.raises(TenantScopeError, match="different organisation"):
            db.flush()


def test_scope_cannot_be_switched_mid_request(db, two_businesses):
    a_id, b_id = two_businesses
    with tenant_scope(db, a_id):
        with pytest.raises(TenantScopeError):
            with tenant_scope(db, b_id):
                pass


def test_scope_ends_with_the_block(db, two_businesses):
    a_id, _ = two_businesses
    with tenant_scope(db, a_id):
        assert current_tenant(db) == a_id
    assert current_tenant(db) is None
    with pytest.raises(TenantScopeError):
        _count_memberships(db)


# --- through the API ----------------------------------------------------------------


def _create_org(api, auth, name):
    res = api.post("/api/v1/organizations", json={"name": name}, headers=auth)
    assert res.status_code == 201
    return res.json()["id"]


def _add_member(db, org_id, email, role="viewer"):
    user_id = db.scalars(select(User.id).where(User.email == email)).one()
    db.add(
        OrganizationUser(
            organization_id=uuid.UUID(org_id), user_id=user_id, role_id=_role(db, role).id
        )
    )
    db.flush()


def members(api, auth, org_id):
    return api.get(f"/api/v1/organizations/{org_id}/members", headers=auth)


def test_members_list_shows_only_that_business(api, db, signup):
    alice, bob = signup("alice@acme.co.uk"), signup("bob@acme.co.uk")
    signup("carol@acme.co.uk")
    a = _create_org(api, alice, "A Ltd")
    b = _create_org(api, bob, "B Ltd")
    _add_member(db, a, "carol@acme.co.uk")

    res = members(api, alice, a)
    assert res.status_code == 200
    assert {(m["email"], m["role"]) for m in res.json()} == {
        ("alice@acme.co.uk", "owner"),
        ("carol@acme.co.uk", "viewer"),
    }
    assert [m["email"] for m in members(api, bob, b).json()] == ["bob@acme.co.uk"]


def test_outsider_cannot_list_another_business_even_with_its_real_id(api, signup):
    alice, bob = signup("alice@acme.co.uk"), signup("bob@acme.co.uk")
    a = _create_org(api, alice, "A Ltd")
    _create_org(api, bob, "B Ltd")

    stolen = members(api, bob, a)
    assert stolen.status_code == 404
    assert stolen.json()["error"]["code"] == "organization_not_found"
    assert "alice" not in stolen.text


def test_member_of_two_businesses_sees_each_separately(api, db, signup):
    alice, bob = signup("alice@acme.co.uk"), signup("bob@acme.co.uk")
    a = _create_org(api, alice, "A Ltd")
    b = _create_org(api, bob, "B Ltd")
    _add_member(db, b, "alice@acme.co.uk")

    in_a = {m["email"] for m in members(api, alice, a).json()}
    in_b = {m["email"] for m in members(api, alice, b).json()}
    assert in_a == {"alice@acme.co.uk"}
    assert in_b == {"alice@acme.co.uk", "bob@acme.co.uk"}


def test_a_scoped_request_does_not_leak_scope_into_the_next(api, signup):
    alice = signup("alice@acme.co.uk")
    a = _create_org(api, alice, "A Ltd")
    _create_org(api, alice, "Another Ltd")
    members(api, alice, a)
    # Listing all my businesses afterwards still sees both.
    assert len(api.get("/api/v1/organizations", headers=alice).json()) == 2


def test_suspended_business_is_closed_to_members(api, db, signup):
    alice = signup("alice@acme.co.uk")
    a = _create_org(api, alice, "A Ltd")
    db.execute(update(Organization).values(status="suspended"))
    res = members(api, alice, a)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "organization_suspended"


def test_closed_business_looks_like_it_does_not_exist(api, db, signup):
    alice = signup("alice@acme.co.uk")
    a = _create_org(api, alice, "A Ltd")
    db.execute(update(Organization).values(status="closed"))
    assert members(api, alice, a).status_code == 404


def test_unverified_user_cannot_enter_a_business(api, db, signup):
    alice = signup("alice@acme.co.uk")
    a = _create_org(api, alice, "A Ltd")
    db.execute(update(User).values(email_verified_at=None))
    res = members(api, alice, a)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "email_not_verified"
