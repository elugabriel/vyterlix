"""Phase 3 tables: UK-first rules and data validity are enforced by the database itself."""

from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.tenant import TenantScopeError, tenant_scope
from app.models.business import (
    UK_REGIONS,
    BusinessBenchmark,
    BusinessGoal,
    BusinessProfile,
    BusinessSeason,
    BusinessSettings,
    Industry,
)
from app.models.identity import Organization


@pytest.fixture
def org_id(db):
    org = Organization(name="Acme Retail")
    db.add(org)
    db.flush()
    with tenant_scope(db, org.id):
        yield org.id


def profile(**overrides) -> BusinessProfile:
    fields = {"industry_code": "retail", "fiscal_year_start_month": 4, "fiscal_year_start_day": 1}
    return BusinessProfile(**(fields | overrides))


def season(**overrides) -> BusinessSeason:
    fields = {"name": "Christmas", "start_month": 12, "start_day": 1, "end_month": 1, "end_day": 5}
    return BusinessSeason(**(fields | overrides))


def saved(db, obj):
    db.add(obj)
    db.flush()
    db.refresh(obj)
    return obj


def rejected(db, obj, constraint: str):
    """Flush inside a savepoint so the test transaction survives the failure."""
    with pytest.raises(IntegrityError, match=constraint):
        with db.begin_nested():
            db.add(obj)
            db.flush()


# --- industries --------------------------------------------------------------------


def test_industry_list_is_seeded_in_order(db):
    codes = db.scalars(select(Industry.code).order_by(Industry.sort_order)).all()
    assert len(codes) == 14
    assert codes[0] == "retail" and codes[-1] == "other"
    assert db.get(Industry, "hospitality").label.startswith("Hospitality")


# --- business profile: UK-first --------------------------------------------------------


def test_profile_defaults_are_uk(db, org_id):
    p = saved(db, profile())
    assert (p.currency, p.country) == ("GBP", "GB")
    assert p.organization_id == org_id  # stamped by tenant scoping
    assert p.onboarding_completed_at is None


def test_full_uk_profile_is_accepted(db, org_id):
    p = saved(
        db,
        profile(
            sic_code="47110",
            region="london",
            town_city="London",
            postcode="SW1A 1AA",
            business_size="small",
            business_model="b2c",
            team_size=12,
            founded_year=2015,
            vat_registered=True,
            vat_number="GB123456789",
        ),
    )
    assert p.postcode == "SW1A 1AA"


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"currency": "USD"}, "currency_gbp"),
        ({"currency": "EUR"}, "currency_gbp"),
        ({"country": "US"}, "country_gb"),
        ({"country": "IE"}, "country_gb"),
        ({"region": "california"}, "region_valid"),
        ({"postcode": "90210"}, "postcode_format"),  # US ZIP
        ({"postcode": "sw1a 1aa"}, "postcode_format"),  # must be stored normalised
        ({"postcode": "SW1A1AA"}, "postcode_format"),
        ({"sic_code": "4711"}, "sic_code_format"),
        ({"vat_number": "123456789"}, "vat_number_format"),
        ({"vat_number": "IE1234567X"}, "vat_number_format"),
        ({"business_size": "huge"}, "business_size_valid"),
        ({"business_model": "pyramid"}, "business_model_valid"),
        ({"team_size": -1}, "team_size_non_negative"),
        ({"founded_year": 1066}, "founded_year_range"),
        ({"industry_code": "space_mining"}, "fk_business_profiles_industry_code_industries"),
    ],
)
def test_invalid_or_non_uk_profile_rejected(db, org_id, overrides, constraint):
    rejected(db, profile(**overrides), constraint)


@pytest.mark.parametrize(
    "postcode", ["M1 1AE", "B33 8TH", "CR2 6XH", "DN55 1PT", "EH1 1YZ", "BT1 1AA", "GIR 0AA"]
)
def test_real_uk_postcode_shapes_accepted(db, org_id, postcode):
    assert saved(db, profile(postcode=postcode)).postcode == postcode


@pytest.mark.parametrize(("month", "day"), [(4, 31), (2, 29), (2, 30), (13, 1), (0, 1), (6, 0)])
def test_impossible_financial_year_start_rejected(db, org_id, month, day):
    rejected(
        db,
        profile(fiscal_year_start_month=month, fiscal_year_start_day=day),
        "fiscal_year_start_valid",
    )


@pytest.mark.parametrize(("month", "day"), [(4, 1), (4, 6), (1, 1), (12, 31), (2, 28)])
def test_real_financial_year_starts_accepted(db, org_id, month, day):
    saved(db, profile(fiscal_year_start_month=month, fiscal_year_start_day=day))


def test_one_profile_per_business(db, org_id):
    saved(db, profile())
    rejected(db, profile(), "uq_business_profiles_organization_id")


def test_every_uk_region_accepted(db, org_id):
    assert len(UK_REGIONS) == 12
    p = saved(db, profile())
    for region in UK_REGIONS:
        p.region = region
        db.flush()
    assert p.region == "northern_ireland"


# --- settings ------------------------------------------------------------------------


def test_settings_default_to_uk(db, org_id):
    s = saved(db, BusinessSettings())
    assert (s.timezone, s.locale, s.week_start_day) == ("Europe/London", "en-GB", 1)


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"timezone": "America/New_York"}, "timezone_uk"),
        ({"locale": "en-US"}, "locale_en_gb"),
        ({"week_start_day": 8}, "week_start_day_valid"),
        ({"quiet_hours_start": time(22, 0)}, "quiet_hours_pair"),
    ],
)
def test_invalid_settings_rejected(db, org_id, fields, constraint):
    rejected(db, BusinessSettings(**fields), constraint)


def test_quiet_hours_may_cross_midnight(db, org_id):
    s = saved(db, BusinessSettings(quiet_hours_start=time(22, 0), quiet_hours_end=time(7, 0)))
    assert s.quiet_hours_start > s.quiet_hours_end


# --- goals ---------------------------------------------------------------------------


def test_goal_with_target(db, org_id):
    g = saved(
        db,
        BusinessGoal(
            title="Grow revenue 20% this year",
            goal_type="increase_revenue",
            target_value=Decimal("250000.00"),
            target_unit="gbp",
            target_date=date(2027, 3, 31),
        ),
    )
    assert (g.priority, g.status) == (3, "active")
    assert g.target_value == Decimal("250000.0000")  # NUMERIC, never float


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"goal_type": "world_domination"}, "goal_type_valid"),
        ({"priority": 6}, "priority_range"),
        ({"status": "forgotten"}, "status_valid"),
        ({"target_value": Decimal("10")}, "target_value_has_unit"),
        ({"target_value": Decimal("10"), "target_unit": "usd"}, "target_unit_valid"),
    ],
)
def test_invalid_goals_rejected(db, org_id, fields, constraint):
    rejected(db, BusinessGoal(**({"title": "Goal", "goal_type": "other"} | fields)), constraint)


# --- seasons -------------------------------------------------------------------------


def test_season_may_cross_new_year(db, org_id):
    s = saved(db, season(expected_change_pct=Decimal("40")))
    assert (s.source, s.status) == ("user", "active")
    assert (s.end_month, s.end_day) < (s.start_month, s.start_day)


def test_detected_season_can_wait_for_confirmation(db, org_id):
    s = saved(db, season(source="detected", status="suggested", name="Summer lull"))
    assert s.status == "suggested"


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"start_month": 2, "start_day": 30}, "start_valid"),
        ({"end_month": 13}, "end_valid"),
        ({"source": "guess"}, "source_valid"),
        ({"status": "maybe"}, "status_valid"),
        ({"status": "suggested"}, "only_detected_are_suggested"),
        ({"expected_change_pct": Decimal("-150")}, "expected_change_pct_range"),
    ],
)
def test_invalid_seasons_rejected(db, org_id, fields, constraint):
    rejected(db, season(**fields), constraint)


# --- benchmarks (shared reference data) --------------------------------------------------


def _benchmark(**overrides):
    fields = {
        "industry_code": "retail",
        "kpi_code": "gross_margin",
        "period_year": 2025,
        "value": Decimal("42.5"),
        "unit": "percent",
        "source": "ONS Annual Business Survey",
    }
    return BusinessBenchmark(**(fields | overrides))


def test_benchmarks_are_shared_not_per_business(db):
    """No tenant scope needed: benchmarks aren't any one business's data."""
    saved(db, _benchmark(region="london", size_band="small"))
    assert len(db.scalars(select(BusinessBenchmark)).all()) == 1


def test_duplicate_benchmark_segment_rejected_even_with_blank_parts(db):
    saved(db, _benchmark())  # whole UK, all sizes (NULL region/size)
    rejected(db, _benchmark(value=Decimal("40")), "uq_business_benchmarks_segment")


def test_benchmark_region_must_be_uk(db):
    rejected(db, _benchmark(region="texas"), "region_valid")


# --- the new tables are tenant-isolated automatically ------------------------------------


@pytest.mark.parametrize("model", [BusinessProfile, BusinessSettings, BusinessGoal, BusinessSeason])
def test_business_tables_need_a_business_in_scope(db, model):
    with pytest.raises(TenantScopeError):
        db.execute(select(model))


def test_one_business_cannot_see_anothers_profile(db):
    a, b = Organization(name="A"), Organization(name="B")
    db.add_all([a, b])
    db.flush()
    with tenant_scope(db, a.id):
        saved(db, profile(town_city="Leeds"))
    with tenant_scope(db, b.id):
        saved(db, profile(town_city="Cardiff"))
        assert [p.town_city for p in db.scalars(select(BusinessProfile))] == ["Cardiff"]
