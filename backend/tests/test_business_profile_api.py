import uuid

import pytest
from sqlalchemy import select, update

from app.core.uk import normalise_postcode, normalise_vat_number, today_uk
from app.models.business import Industry
from app.models.identity import AuditLog, OrganizationUser, Role, User

ORGS = "/api/v1/organizations"
MINIMAL = {"industry_code": "retail", "financial_year_start": {"month": 4, "day": 1}}


@pytest.fixture
def business(api, db, signup):
    """Acme with owner@ and viewer@. Returns (org_id, {role: auth header})."""
    auth = {"owner": signup("owner@acme.co.uk"), "viewer": signup("viewer@acme.co.uk")}
    org_id = api.post(ORGS, json={"name": "Acme Retail"}, headers=auth["owner"]).json()["id"]
    db.add(
        OrganizationUser(
            organization_id=uuid.UUID(org_id),
            user_id=db.scalars(select(User.id).where(User.email == "viewer@acme.co.uk")).one(),
            role_id=db.scalars(select(Role.id).where(Role.code == "viewer")).first(),
        )
    )
    db.flush()
    return org_id, auth


def url(org_id):
    return f"{ORGS}/{org_id}/profile"


def put(api, business, body, who="owner"):
    org_id, auth = business
    return api.put(url(org_id), json=body, headers=auth[who])


def patch(api, business, body, who="owner"):
    org_id, auth = business
    return api.patch(url(org_id), json=body, headers=auth[who])


def error_messages(res):
    return " ".join(str(d.get("msg")) for d in res.json()["error"]["details"] or [])


# --- UK helpers --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("sw1a1aa", "SW1A 1AA"),
        (" SW1A  1AA ", "SW1A 1AA"),
        ("m11ae", "M1 1AE"),
        ("bt11aa", "BT1 1AA"),
        ("gir0aa", "GIR 0AA"),
        ("EC1A 1BB", "EC1A 1BB"),
    ],
)
def test_postcodes_are_tidied(raw, clean):
    assert normalise_postcode(raw) == clean


@pytest.mark.parametrize("raw", ["90210", "SW1A", "12345 6789", "Q1 1AA 1", ""])
def test_non_uk_postcodes_refused(raw):
    with pytest.raises(ValueError, match="UK postcode"):
        normalise_postcode(raw)


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("GB 123 4567 89", "GB123456789"),
        ("123456789", "GB123456789"),
        ("gb123456789012", "GB123456789012"),
        ("GB-123.456.789", "GB123456789"),
    ],
)
def test_vat_numbers_are_tidied(raw, clean):
    assert normalise_vat_number(raw) == clean


@pytest.mark.parametrize("raw", ["IE1234567X", "DE123456789", "GB12345", "12345678"])
def test_non_gb_vat_numbers_refused(raw):
    with pytest.raises(ValueError, match="UK VAT number"):
        normalise_vat_number(raw)


# --- industries ------------------------------------------------------------------------


def test_industry_list_for_the_form(api):
    res = api.get("/api/v1/industries")
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 14
    assert body[0] == {"code": "retail", "label": "Retail (shops)"}


def test_inactive_industries_are_hidden_and_refused(api, db, business):
    db.execute(update(Industry).where(Industry.code == "technology").values(is_active=False))
    assert "technology" not in {i["code"] for i in api.get("/api/v1/industries").json()}
    res = put(api, business, MINIMAL | {"industry_code": "technology"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "unknown_industry"


# --- creating and reading ------------------------------------------------------------------


def test_profile_not_set_up_yet(api, business):
    org_id, auth = business
    res = api.get(url(org_id), headers=auth["owner"])
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "profile_not_set_up"


def test_minimal_profile_with_only_required_fields(api, business):
    res = put(api, business, MINIMAL)
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "Acme Retail"
    assert body["industry"] == {"code": "retail", "label": "Retail (shops)"}
    assert body["financial_year_start"] == {"month": 4, "day": 1}
    assert (body["currency"], body["country"]) == ("GBP", "GB")
    assert body["postcode"] is None and body["onboarding_completed_at"] is None


def test_full_profile_is_tidied_to_uk_formats(api, business):
    res = put(
        api,
        business,
        MINIMAL
        | {
            "sic_code": " 47110 ",
            "region": "north_west",
            "town_city": "  Manchester ",
            "postcode": "m1 1ae",
            "business_size": "small",
            "business_model": "b2c",
            "team_size": 14,
            "founded_year": 2016,
            "vat_number": "gb 123 4567 89",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["postcode"] == "M1 1AE"
    assert body["town_city"] == "Manchester"
    assert body["sic_code"] == "47110"
    assert body["vat_number"] == "GB123456789"
    assert body["vat_registered"] is True  # implied by giving a VAT number
    assert body["years_operating"] == today_uk().year - 2016


def test_any_member_can_read_the_profile(api, business):
    org_id, auth = business
    put(api, business, MINIMAL)
    assert api.get(url(org_id), headers=auth["viewer"]).json()["industry"]["code"] == "retail"


def test_creation_is_audited_with_field_names_only(api, db, business):
    put(api, business, MINIMAL | {"postcode": "SW1A 1AA"})
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "business.profile_created")).one()
    assert "postcode" in entry.details["fields"]
    assert "SW1A" not in str(entry.details)


# --- UK-first and validation ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("extra", "expect"),
    [
        ({"currency": "USD"}, "GBP"),
        ({"currency": "EUR"}, "GBP"),
        ({"country": "US"}, "GB"),
        ({"region": "california"}, "north_east"),
        ({"postcode": "90210"}, "UK postcode"),
        ({"vat_number": "IE1234567X"}, "UK VAT number"),
        ({"sic_code": "4711"}, "pattern"),
        ({"financial_year_start": {"month": 4, "day": 31}}, "doesn't exist every year"),
        ({"financial_year_start": {"month": 2, "day": 29}}, "doesn't exist every year"),
        ({"founded_year": today_uk().year + 1}, "can't be in the future"),
        ({"vat_registered": False, "vat_number": "GB123456789"}, "isn't VAT registered"),
        ({"team_size": -3}, "greater than or equal"),
    ],
)
def test_invalid_or_non_uk_input_rejected_with_a_clear_message(api, business, extra, expect):
    res = put(api, business, MINIMAL | extra)
    assert res.status_code == 422
    assert expect in error_messages(res)


@pytest.mark.parametrize("missing", ["industry_code", "financial_year_start"])
def test_required_fields_are_required(api, business, missing):
    body = {k: v for k, v in MINIMAL.items() if k != missing}
    assert put(api, business, body).status_code == 422


def test_unknown_fields_rejected(api, business):
    assert (
        put(api, business, MINIMAL | {"onboarding_completed_at": "2026-01-01"}).status_code == 422
    )


# --- updating ---------------------------------------------------------------------------


def test_put_replaces_and_clears_omitted_optional_fields(api, business):
    put(api, business, MINIMAL | {"town_city": "Leeds", "postcode": "LS1 1AA"})
    body = put(api, business, MINIMAL | {"town_city": "York"}).json()
    assert (body["town_city"], body["postcode"]) == ("York", None)


def test_patch_changes_only_what_is_sent(api, db, business):
    put(api, business, MINIMAL | {"town_city": "Leeds", "postcode": "LS1 1AA"})
    body = patch(api, business, {"postcode": "ls2 7hy"}).json()
    assert (body["town_city"], body["postcode"]) == ("Leeds", "LS2 7HY")
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "business.profile_updated")).one()
    assert entry.details == {"fields": ["postcode"]}


def test_patch_can_change_required_fields_but_not_clear_them(api, business):
    put(api, business, MINIMAL)
    ok = patch(api, business, {"financial_year_start": {"month": 1, "day": 1}})
    assert ok.json()["financial_year_start"] == {"month": 1, "day": 1}
    assert patch(api, business, {"industry_code": None}).status_code == 422
    assert patch(api, business, {}).status_code == 422


def test_switching_off_vat_registration_clears_the_number(api, business):
    put(api, business, MINIMAL | {"vat_number": "GB123456789"})
    body = patch(api, business, {"vat_registered": False}).json()
    assert (body["vat_registered"], body["vat_number"]) == (False, None)


def test_patch_before_setup_is_not_found(api, business):
    res = patch(api, business, {"town_city": "Bristol"})
    assert res.json()["error"]["code"] == "profile_not_set_up"


def test_no_change_is_not_audited(api, db, business):
    put(api, business, MINIMAL | {"town_city": "Leeds"})
    patch(api, business, {"town_city": "Leeds"})
    assert (
        db.scalars(select(AuditLog).where(AuditLog.action == "business.profile_updated")).all()
        == []
    )


# --- permissions and separation ---------------------------------------------------------------


@pytest.mark.parametrize("method", ["put", "patch"])
def test_viewers_cannot_change_the_profile(api, business, method):
    put(api, business, MINIMAL)
    send = put if method == "put" else patch
    res = send(api, business, MINIMAL, who="viewer")
    assert res.status_code == 403
    assert res.json()["error"]["details"] == {"required_permission": "org.manage"}


def test_each_business_has_its_own_profile(api, business, signup):
    put(api, business, MINIMAL | {"town_city": "Leeds"})
    other = signup("other@acme.co.uk")
    other_id = api.post(ORGS, json={"name": "Other Ltd"}, headers=other).json()["id"]
    assert api.get(url(other_id), headers=other).json()["error"]["code"] == "profile_not_set_up"
    api.put(url(other_id), json=MINIMAL | {"town_city": "Cardiff"}, headers=other)

    org_id, auth = business
    assert api.get(url(org_id), headers=auth["owner"]).json()["town_city"] == "Leeds"
    assert api.get(url(other_id), headers=other).json()["town_city"] == "Cardiff"
    # And an outsider can't read ours at all.
    assert api.get(url(org_id), headers=other).status_code == 404
