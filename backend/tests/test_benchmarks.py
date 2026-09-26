from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.cli.benchmarks import main as cli_main
from app.core.uk import today_uk
from app.models.business import BusinessBenchmark
from app.services.benchmarks import load_csv

COLUMNS = [
    "industry_code", "sic_code", "region", "size_band", "kpi_code", "period_year",
    "value", "unit", "source", "source_url", "notes",
]  # fmt: skip
HEADER = ",".join(COLUMNS)
ONS = "ONS Annual Business Survey"
ORGS = "/api/v1/organizations"


def write_csv(tmp_path, *rows, header=HEADER, bom=False):
    path = tmp_path / "benchmarks.csv"
    text = "\n".join([header, *rows]) + "\n"
    path.write_text(("﻿" if bom else "") + text, encoding="utf-8")
    return path


def count(db):
    return db.scalar(select(func.count()).select_from(BusinessBenchmark))


# --- loading ---------------------------------------------------------------------------------


def test_load_a_valid_file_including_from_excel(db, tmp_path):
    path = write_csv(
        tmp_path,
        f"hospitality,,,,gross_margin,2024,64.5,percent,{ONS},https://www.ons.gov.uk/,",
        f"hospitality,,london,small,gross_margin,2024,61.2,percent,{ONS},,London small cafés",
        bom=True,  # Excel's "CSV UTF-8" adds a byte-order mark
    )
    report = load_csv(db, path)
    assert report.errors == []
    assert (report.rows, report.added, report.updated) == (2, 2, 0)
    london = db.scalars(select(BusinessBenchmark).where(BusinessBenchmark.region == "london")).one()
    assert london.value == Decimal("61.2000")
    assert london.notes == "London small cafés"


def test_blank_cells_mean_all_regions_and_sizes(db, tmp_path):
    load_csv(db, write_csv(tmp_path, f"retail,,,,gross_margin,2024,40,percent,{ONS},,"))
    row = db.scalars(select(BusinessBenchmark)).one()
    assert (row.region, row.size_band, row.sic_code) == (None, None, None)


def test_reloading_updates_instead_of_duplicating(db, tmp_path):
    load_csv(db, write_csv(tmp_path, f"retail,,,,gross_margin,2024,40,percent,{ONS},,"))
    report = load_csv(
        db, write_csv(tmp_path, f"retail,,,,gross_margin,2024,41.5,percent,{ONS} (revised),,")
    )
    assert (report.added, report.updated) == (0, 1)
    assert count(db) == 1
    assert db.scalars(select(BusinessBenchmark.value)).one() == Decimal("41.5000")


def test_dry_run_saves_nothing(db, tmp_path):
    report = load_csv(
        db, write_csv(tmp_path, f"retail,,,,gross_margin,2024,40,percent,{ONS},,"), dry_run=True
    )
    assert (report.errors, report.added) == ([], 1)
    assert count(db) == 0


@pytest.mark.parametrize(
    ("row", "problem"),
    [
        (f"retail,,texas,,gross_margin,2024,40,percent,{ONS},,", "region"),
        (f"retail,4711,,,gross_margin,2024,40,percent,{ONS},,", "sic_code"),
        (f"retail,,,,gross_margin,2024,40,dollars,{ONS},,", "unit"),
        ("retail,,,,gross_margin,2024,40,percent,,,", "source"),
        (f"retail,,,,gross_margin,{today_uk().year + 1},40,percent,{ONS},,", "future"),
        (f"retail,,,,gross_margin,2024,forty,percent,{ONS},,", "value"),
        (f"retail,,,huge,gross_margin,2024,40,percent,{ONS},,", "size_band"),
        (f"retail,,,,gross_margin,2024,40,percent,{ONS},http://insecure.example,", "source_url"),
        (f"space_mining,,,,gross_margin,2024,40,percent,{ONS},,", "unknown industry"),
    ],
)
def test_bad_rows_are_reported_and_nothing_is_saved(db, tmp_path, row, problem):
    good = f"retail,,,,gross_margin,2024,40,percent,{ONS},,"
    report = load_csv(db, write_csv(tmp_path, good, row))
    assert any(problem in e and e.startswith("row 3") for e in report.errors), report.errors
    assert count(db) == 0  # the good row wasn't saved either: all or nothing


def test_duplicate_segment_within_a_file_rejected(db, tmp_path):
    row = f"retail,,,,gross_margin,2024,40,percent,{ONS},,"
    report = load_csv(db, write_csv(tmp_path, row, row))
    assert report.errors == ["row 3: duplicate of an earlier row for the same segment"]


def test_missing_columns_rejected(db, tmp_path):
    path = write_csv(tmp_path, "retail,gross_margin", header="industry_code,kpi_code")
    report = load_csv(db, path)
    assert report.errors == ["missing columns: period_year, source, unit, value"]


def test_database_refuses_bad_units_and_missing_sources(db):
    for fields, constraint in [
        ({"unit": "dollars"}, "unit_valid"),
        ({"source": "  "}, "source_required"),
    ]:
        with pytest.raises(IntegrityError, match=constraint), db.begin_nested():
            db.add(
                BusinessBenchmark(
                    **{
                        "industry_code": "retail",
                        "kpi_code": "k",
                        "period_year": 2024,
                        "value": 1,
                        "unit": "percent",
                        "source": ONS,
                    }
                    | fields
                )
            )
            db.flush()


def test_cli_reports_a_missing_file(tmp_path, capsys):
    assert cli_main(["load", str(tmp_path / "nope.csv")]) == 2
    assert "File not found" in capsys.readouterr().err


# --- reading -----------------------------------------------------------------------------------


@pytest.fixture
def figures(db, tmp_path):
    """Hospitality gross margin at several levels of detail, plus an older year."""
    load_csv(
        db,
        write_csv(
            tmp_path,
            f"hospitality,,,,gross_margin,2023,63.0,percent,{ONS},,",
            f"hospitality,,,,gross_margin,2024,64.5,percent,{ONS},,",
            f"hospitality,,london,,gross_margin,2024,62.0,percent,{ONS},,",
            f"hospitality,,london,small,gross_margin,2024,61.2,percent,{ONS},,",
            f"hospitality,56101,,,gross_margin,2024,66.0,percent,{ONS},,",
            f"hospitality,,,,staff_cost_ratio,2024,31.0,percent,{ONS},,",
            f"retail,,,,gross_margin,2024,40.0,percent,{ONS},,",
        ),
    )


def profile(api, auth, org_id, **fields):
    body = {"industry_code": "hospitality", "financial_year_start": {"month": 4, "day": 1}} | fields
    assert api.put(f"{ORGS}/{org_id}/profile", json=body, headers=auth).status_code == 200


def my_benchmarks(api, signup, **profile_fields):
    auth = signup("owner@acme.co.uk")
    org_id = api.post(ORGS, json={"name": "Café"}, headers=auth).json()["id"]
    profile(api, auth, org_id, **profile_fields)
    res = api.get(f"{ORGS}/{org_id}/benchmarks", headers=auth)
    assert res.status_code == 200
    return {b["kpi_code"]: b for b in res.json()}


def test_most_specific_match_wins(api, signup, figures):
    got = my_benchmarks(api, signup, region="london", business_size="small")
    margin = got["gross_margin"]
    assert margin["value"] == "61.2000"
    assert margin["matched_on"] == ["sector", "London", "small businesses"]
    # A KPI with only a UK-wide figure still comes back, labelled as such.
    assert got["staff_cost_ratio"]["matched_on"] == ["sector", "whole UK", "all sizes"]


def test_falls_back_to_region_then_whole_uk(api, signup, figures):
    medium = my_benchmarks(api, signup, region="london", business_size="medium")
    assert medium["gross_margin"]["matched_on"] == ["sector", "London", "all sizes"]


def test_whole_uk_and_newest_year_when_nothing_more_specific(api, signup, figures):
    got = my_benchmarks(api, signup, region="scotland")
    assert (got["gross_margin"]["value"], got["gross_margin"]["period_year"]) == ("64.5000", 2024)


def test_sic_code_is_the_most_specific(api, signup, figures):
    got = my_benchmarks(api, signup, sic_code="56101", region="london", business_size="small")
    assert got["gross_margin"]["matched_on"] == ["SIC 56101", "whole UK", "all sizes"]


def test_other_industries_never_match(api, signup, figures):
    assert "40.0000" not in {b["value"] for b in my_benchmarks(api, signup).values()}


def test_no_profile_means_no_comparison_yet(api, signup, figures):
    auth = signup("owner@acme.co.uk")
    org_id = api.post(ORGS, json={"name": "Café"}, headers=auth).json()["id"]
    res = api.get(f"{ORGS}/{org_id}/benchmarks", headers=auth)
    assert res.json()["error"]["code"] == "profile_not_set_up"


def test_empty_until_figures_are_loaded(api, signup):
    assert my_benchmarks(api, signup) == {}


def test_search_the_whole_table(api, signup, figures):
    auth = signup("reader@acme.co.uk")
    res = api.get(
        "/api/v1/benchmarks",
        params={"industry_code": "hospitality", "region": "london"},
        headers=auth,
    )
    assert {(b["region"], b["size_band"]) for b in res.json()} == {
        ("london", None),
        ("london", "small"),
    }


def test_search_needs_a_verified_login(api, signup, figures):
    assert api.get("/api/v1/benchmarks").status_code == 401
    unverified = signup("new@acme.co.uk", verified=False)
    assert api.get("/api/v1/benchmarks", headers=unverified).status_code == 403


def test_no_api_can_change_benchmarks(api, signup):
    auth = signup("owner@acme.co.uk")
    for method in ("post", "put", "patch", "delete"):
        assert getattr(api, method)("/api/v1/benchmarks", headers=auth).status_code == 405
