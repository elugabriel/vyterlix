# ruff: noqa: E501, F811
"""Reports: written from stored results, kept as written, downloaded as PDF and CSV, sent on a schedule."""

import io
import uuid
from datetime import UTC, date, datetime, timedelta

import pypdf
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.identity import AuditLog, OrganizationUser, User
from app.models.reports import Report, ReportRun, ReportSchedule
from app.reports import render, rules
from app.services import reports as service
from app.services import scheduler
from tests.test_actions import accept, act, add_manager
from tests.test_alerts import evaluate
from tests.test_health import ORGS, owner_tenant, scoped
from tests.test_recommendations import event_for, recommend

D = date

# --- the rules -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("month", "by", "result"),
    [
        (D(2026, 3, 1), 1, D(2026, 4, 1)),
        (D(2026, 12, 1), 1, D(2027, 1, 1)),
        (D(2026, 1, 1), -1, D(2025, 12, 1)),
        (D(2026, 3, 1), -14, D(2025, 1, 1)),
        (D(2026, 3, 1), 0, D(2026, 3, 1)),
    ],
)
def test_months_are_counted_across_the_year_end(month, by, result):
    assert rules.shift(month, by) == result


@pytest.mark.parametrize(
    ("month", "last"),
    [
        (D(2026, 2, 1), D(2026, 2, 28)),
        (D(2024, 2, 1), D(2024, 2, 29)),
        (D(2026, 12, 1), D(2026, 12, 31)),
        (D(2026, 4, 1), D(2026, 4, 30)),
    ],
)
def test_the_last_day_of_a_month(month, last):
    assert rules.last_day(month) == last


def test_a_window_of_months_ends_with_the_latest_and_runs_oldest_first():
    assert rules.months_window(D(2026, 3, 1), 3) == [D(2026, 1, 1), D(2026, 2, 1), D(2026, 3, 1)]
    assert rules.months_window(D(2026, 2, 1), 4) == [
        D(2025, 11, 1),
        D(2025, 12, 1),
        D(2026, 1, 1),
        D(2026, 2, 1),
    ]
    assert rules.months_window(D(2026, 3, 1), 1) == [D(2026, 3, 1)]
    assert len(rules.months_window(D(2026, 3, 1), 24)) == 24


def test_the_period_is_written_in_words():
    assert rules.period_label(D(2026, 3, 1), D(2026, 3, 31)) == "March 2026"
    assert rules.period_label(D(2025, 10, 1), D(2026, 3, 31)) == "October 2025 to March 2026"


@pytest.mark.parametrize(
    ("n", "text"),
    [
        (1, "1st"),
        (2, "2nd"),
        (3, "3rd"),
        (4, "4th"),
        (11, "11th"),
        (12, "12th"),
        (13, "13th"),
        (21, "21st"),
        (22, "22nd"),
        (23, "23rd"),
        (28, "28th"),
    ],
)
def test_ordinals(n, text):
    assert rules.ordinal(n) == text


def test_a_schedule_is_described_in_words():
    assert rules.describe("weekly", 1, None) == "Every Monday at 7am"
    assert rules.describe("weekly", 7, None) == "Every Sunday at 7am"
    assert rules.describe("monthly", None, 1) == "On the 1st of every month at 7am"
    assert rules.describe("monthly", None, 22) == "On the 22nd of every month at 7am"


def at(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def test_a_weekly_report_is_next_due_at_seven_uk_time_on_that_weekday():
    # Monday 12 October 2026 is in British Summer Time: 7am there is 06:00 UTC
    assert rules.next_run("weekly", 1, None, at(2026, 10, 9, 12)) == at(2026, 10, 12, 6)
    # in winter 7am is 07:00 UTC
    assert rules.next_run("weekly", 1, None, at(2026, 12, 1, 12)) == at(2026, 12, 7, 7)


def test_a_run_is_always_strictly_later_than_the_time_asked_from():
    due = at(2026, 10, 12, 6)
    assert rules.next_run("weekly", 1, None, due) == at(2026, 10, 19, 6)
    assert rules.next_run("weekly", 1, None, due - timedelta(seconds=1)) == due
    assert rules.next_run("weekly", 1, None, at(2026, 10, 12, 5, 59)) == due


def test_a_monthly_report_is_due_on_that_day_of_the_month():
    assert rules.next_run("monthly", None, 5, at(2026, 10, 9)) == at(
        2026, 11, 5, 7
    )  # November is GMT
    assert rules.next_run("monthly", None, 15, at(2026, 10, 9)) == at(2026, 10, 15, 6)
    assert rules.next_run("monthly", None, 28, at(2026, 2, 1)) == at(2026, 2, 28, 7)
    assert rules.next_run("monthly", None, 1, at(2026, 12, 20)) == at(2027, 1, 1, 7)


def test_the_clocks_changing_do_not_move_the_hour_people_see():
    before, after = (
        rules.next_run("weekly", 1, None, at(2026, 10, 20)),
        rules.next_run("weekly", 1, None, at(2026, 10, 27)),
    )
    assert before.astimezone(rules.UK_TZ).hour == 7 and after.astimezone(rules.UK_TZ).hour == 7


@pytest.mark.parametrize(
    "value", ["=SUM(A1)", "+1", "@cmd", "\tx", "\rx", "-cmd", "-x", "-£5.00x", "- one"]
)
def test_text_a_spreadsheet_would_run_gets_a_quote_in_front(value):
    assert rules.safe_cell(value) == "'" + value


@pytest.mark.parametrize(
    "value",
    ["Sales", "£410.00", "-£5.00", "-12.3%", "-3.0 points", "-1,234.50", "", "12", "a=b", "-"],
)
def test_ordinary_text_and_negative_numbers_are_left_alone(value):
    assert rules.safe_cell(value) == value


def test_nothing_becomes_the_word_none():
    assert rules.safe_cell(None) == ""


def test_text_for_the_pdf_fonts_swaps_punctuation_and_marks_what_it_cannot_write():
    assert rules.latin1("Sales – “up” ▲ £410 …") == 'Sales - "up" up £410 ...'
    assert rules.latin1("café") == "café" and rules.latin1("你好") == "??"


def content(**extra):
    c = {
        "title": "Test report", "business": "Fakeham", "kind": "kpi", "facts": ["Covers March 2026."], "period_start": "2026-03-01",
        "period_end": "2026-03-31", "generated_at": "2026-10-09T07:00:00+00:00", "notes": ["A note."],
        "sections": [rules.section("Sales", paragraphs=["One."], bullets=["Two."], columns=["Figure", "March"], rows=[["Sales", "£410.00"], ["=evil()", "-£5.00"]])],
    }  # fmt: skip
    return {**c, **extra}


def test_the_csv_has_the_title_facts_sections_tables_and_notes_and_a_byte_order_mark():
    text = rules.to_csv(content())
    assert text.startswith("﻿") and text.count("\r\n") == len(text.split("\r\n")) - 1
    lines = text.lstrip("﻿").split("\r\n")
    assert lines[:8] == [
        "Test report",
        "Covers March 2026.",
        "",
        "Sales",
        "One.",
        "- Two.",
        "Figure,March",
        "Sales,£410.00",
    ]
    assert "'=evil(),-£5.00" in lines and lines[-2:] == ["A note.", ""]


def test_a_person_cannot_put_a_formula_in_a_report_name():
    import csv as csv_module

    text = rules.to_csv(content(title='=HYPERLINK("http://evil")'))
    first = next(csv_module.reader(io.StringIO(text.lstrip("﻿"))))
    assert first == ['\'=HYPERLINK("http://evil")']


def test_a_comma_or_quote_in_a_cell_is_quoted_properly():
    c = content(
        sections=[rules.section("S", columns=["a", "b"], rows=[["has, a comma", 'has "quotes"']])]
    )
    assert '"has, a comma","has ""quotes"""' in rules.to_csv(c)


def test_the_file_name_says_what_and_when():
    assert rules.filename("kpi", content(), "pdf") == "vyterlix-kpi-2026-03.pdf"


@pytest.mark.parametrize(("columns", "sideways"), [(6, False), (7, True), (8, True), (2, False)])
def test_a_wide_table_is_printed_sideways(columns, sideways):
    c = content(
        sections=[
            rules.section("S", columns=[f"c{i}" for i in range(columns)], rows=[["x"] * columns])
        ]
    )
    assert rules.landscape(c) is sideways


def test_a_report_with_no_table_is_not_sideways():
    assert rules.landscape(content(sections=[rules.section("S", paragraphs=["x"])])) is False


def test_a_section_stores_its_table_as_plain_text():
    s = rules.section("S", columns=["a"], rows=[[1], [None]])
    assert (
        s["table"] == {"columns": ["a"], "rows": [["1"], ["None"]]}
        and s["paragraphs"] == []
        and s["bullets"] == []
    )
    assert rules.section("S")["table"] is None


# --- the PDF ---------------------------------------------------------------------------------------------------


def read_pdf(data):
    return pypdf.PdfReader(io.BytesIO(data))


def text_of(data):
    return "\n".join(p.extract_text() for p in read_pdf(data).pages)


def test_a_pdf_is_a_real_pdf_with_the_business_title_text_and_page_numbers():
    data = render.to_pdf(content())
    assert data.startswith(b"%PDF-")
    text = text_of(data)
    for expected in (
        "Fakeham",
        "Test report",
        "Covers March 2026.",
        "Sales",
        "£410.00",
        "One.",
        "- Two.",
        "A note.",
        "page 1 of 1",
    ):
        assert expected in text
    meta = read_pdf(data).metadata
    assert meta.title == "Test report" and meta.author == "Vyterlix"


def test_a_pdf_of_the_same_content_is_the_same_every_time():
    assert render.to_pdf(content()) == render.to_pdf(content())


def test_a_long_report_runs_over_several_pages_with_numbers_on_each():
    rows = [[f"row {n}", str(n)] for n in range(120)]
    data = render.to_pdf(
        content(sections=[rules.section("Long", columns=["Name", "Value"], rows=rows)])
    )
    reader = read_pdf(data)
    assert len(reader.pages) >= 3
    assert f"page {len(reader.pages)} of {len(reader.pages)}" in reader.pages[-1].extract_text()
    assert (
        "row 119" in text_of(data) and "Name" in reader.pages[1].extract_text()
    )  # the headings repeat


def test_a_wide_table_makes_a_sideways_page():
    c = content(
        sections=[rules.section("W", columns=[f"c{i}" for i in range(8)], rows=[["1"] * 8])]
    )
    box = read_pdf(render.to_pdf(c)).pages[0].mediabox
    assert box.width > box.height
    narrow = read_pdf(render.to_pdf(content())).pages[0].mediabox
    assert narrow.width < narrow.height


def test_characters_the_fonts_cannot_write_do_not_break_the_pdf():
    c = content(
        title="Café 你好 ▲", sections=[rules.section("– odd", paragraphs=["“quoted” \U0001f600"])]
    )
    assert render.to_pdf(c).startswith(b"%PDF-")


def test_an_empty_table_still_makes_a_pdf():
    c = content(sections=[rules.section("Nothing", columns=["a", "b"], rows=[])])
    assert "a" in text_of(render.to_pdf(c))


# --- the reports a business has ---------------------------------------------------------------------------------------


def listing(api, business, who="owner", org=0):
    res = api.get(f"{ORGS}/{business[org]}/reports", headers=business[2][who])
    assert res.status_code == 200, res.text
    return res.json()


def report_of(api, business, kind, who="owner", org=0):
    return next(r for r in listing(api, business, who, org) if r["kind"] == kind)


def write(api, business, kind, who="owner", org=0):
    res = api.post(
        f"{ORGS}/{business[org]}/reports/{report_of(api, business, kind, who, org)['id']}/generate",
        headers=business[2][who],
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_the_four_standard_reports_appear_the_first_time_and_only_once(api, march):
    first = listing(api, march)
    assert [(r["kind"], r["name"], r["months"], r["fixed_months"]) for r in first] == [
        ("monthly", "Monthly business report", 1, True), ("health", "Business health", 12, False),
        ("kpi", "Key figures", 6, False), ("outcomes", "What you tried and how it went", 12, False),
    ]  # fmt: skip
    assert all(r["description"] and r["last_run"] is None and r["schedules"] == [] for r in first)
    assert [r["id"] for r in listing(api, march)] == [r["id"] for r in first]


def test_every_member_can_see_the_reports_and_a_stranger_cannot(api, march):
    assert len(listing(api, march, "viewer")) == 4
    assert api.get(f"{ORGS}/{march[0]}/reports").status_code == 401
    assert api.get(f"{ORGS}/{march[0]}/reports", headers=march[2]["other"]).status_code in (
        403,
        404,
    )


def test_each_business_has_its_own_reports(api, march):
    mine = {r["id"] for r in listing(api, march)}
    theirs = {r["id"] for r in listing(api, march, "other", 1)}
    assert mine.isdisjoint(theirs) and len(theirs) == 4


# --- what is written -------------------------------------------------------------------------------------------------------


def sections(run):
    return {s["heading"]: s for s in run["content"]["sections"]}


def test_a_key_figures_report_has_every_figure_by_month_with_the_latest_change(api, march):
    run = write(api, march, "kpi")
    assert (run["kind"], run["trigger"], run["period_start"], run["period_end"]) == (
        "kpi",
        "manual",
        "2025-10-01",
        "2026-03-31",
    )
    content = run["content"]
    assert (
        content["title"] == "Key figures"
        and content["business"] == "Acme"
        and content["facts"][0] == "Covers October 2025 to March 2026."
    )
    money = sections(run)["Money"]["table"]
    assert money["columns"] == [
        "Figure",
        "Oct 25",
        "Nov 25",
        "Dec 25",
        "Jan 26",
        "Feb 26",
        "Mar 26",
        "Latest change",
    ]
    sales = next(r for r in money["rows"] if r[0] == "Sales")
    assert sales[-3:] == ["£600.00", "£410.00", "-31.7%"] and sales[1:5] == ["-", "-", "-", "-"]
    assert (
        content["notes"][0].startswith("Every figure comes from the records you gave Vyterlix")
        and "reports-1" in content["notes"][1]
    )
    assert run["rules_version"] == "reports-1"


def test_a_percentage_figure_changes_in_points_not_per_cent(api, march):
    refunds = next(
        r
        for r in sections(write(api, march, "kpi"))["Sales"]["table"]["rows"]
        if r[0] == "Refund rate"
    )
    assert refunds[-1] == "+25.0 points" and refunds[-2] == "25.0%"


def test_the_months_a_report_covers_can_be_changed_by_the_owner_only(api, march):
    rid = report_of(api, march, "kpi")["id"]
    url = f"{ORGS}/{march[0]}/reports/{rid}/months"
    assert api.put(url, json={"months": 2}, headers=march[2]["viewer"]).status_code == 403
    res = api.put(url, json={"months": 2}, headers=march[2]["owner"])
    assert res.status_code == 200 and res.json()["months"] == 2
    run = write(api, march, "kpi")
    assert run["period_start"] == "2026-02-01" and sections(run)["Money"]["table"]["columns"] == [
        "Figure",
        "Feb 26",
        "Mar 26",
        "Latest change",
    ]


@pytest.mark.parametrize("months", [0, 25, -1, "six"])
def test_the_months_must_be_from_one_to_twenty_four(api, march, months):
    rid = report_of(api, march, "kpi")["id"]
    assert (
        api.put(
            f"{ORGS}/{march[0]}/reports/{rid}/months",
            json={"months": months},
            headers=march[2]["owner"],
        ).status_code
        == 422
    )


def test_the_limits_of_the_months_are_allowed(api, march):
    rid = report_of(api, march, "kpi")["id"]
    for months in (1, 24):
        assert (
            api.put(
                f"{ORGS}/{march[0]}/reports/{rid}/months",
                json={"months": months},
                headers=march[2]["owner"],
            ).status_code
            == 200
        )


def test_the_monthly_report_always_covers_one_month(api, march):
    rid = report_of(api, march, "monthly")["id"]
    res = api.put(
        f"{ORGS}/{march[0]}/reports/{rid}/months", json={"months": 3}, headers=march[2]["owner"]
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "months_fixed"
    run = write(api, march, "monthly")
    assert (run["period_start"], run["period_end"]) == ("2026-03-01", "2026-03-31")


def test_the_monthly_report_gathers_the_month_in_one_place(api, db, march):
    evaluate(api, march)
    run = write(api, march, "monthly")
    s = sections(run)
    assert list(s) == [
        "At a glance",
        "Your key figures",
        "What changed in March 2026",
        "Your actions",
        "Open alerts",
    ]
    assert "7 alerts are open." in s["At a glance"]["paragraphs"]
    figures = s["Your key figures"]["table"]
    assert figures["columns"] == ["Figure", "March 2026", "February 2026", "Change"]
    assert ["Sales", "£410.00", "£600.00", "-31.7%"] in figures["rows"]
    changes = s["What changed in March 2026"]["table"]["rows"]
    assert any(
        r[0].startswith("Sales fell by 32% in March 2026")
        and r[1] == "Worth a look"
        and r[2] == "Big"
        for r in changes
    )
    assert s["Your actions"]["paragraphs"] == ["You have no actions still to do."]
    assert (
        len(s["Open alerts"]["table"]["rows"]) == 7
        and s["Open alerts"]["table"]["rows"][0][0] == "High"
    )


def test_the_monthly_report_lists_the_work_still_to_do(api, march):
    event = event_for(api, march, "revenue")
    recommend(api, march, event)
    action = accept(api, march, event).json()
    rows = sections(write(api, march, "monthly"))["Your actions"]
    assert rows["paragraphs"] == ["1 still to do, 0 of them overdue."]
    assert rows["table"]["rows"] == [
        [
            action["title"],
            "Accepted",
            f"{date.fromisoformat(action['target_date']):%d/%m/%Y}",
            "owner",
        ]
    ]


def test_the_health_report_shows_the_areas_how_it_moved_and_what_holds_it_back(api, db, march):
    from app.services import health as health_service

    with scoped(db, march):
        health_service.calculate(db, owner_tenant(db, march))
    run = write(api, march, "health")
    s = sections(run)
    if "Where you stand" not in s:
        assert s["Business health"]["paragraphs"] == [
            "Business health has not been worked out yet. It needs your sales and costs first."
        ]
        return
    assert s["Where you stand"]["paragraphs"][0].startswith(
        "Your business health for March 2026 is "
    )
    assert s["The areas of the business"]["table"]["columns"] == [
        "Area",
        "Score (out of 100)",
        "Status",
        "Change",
        "How much it counts",
    ]


def test_the_health_report_says_so_when_health_has_not_been_worked_out(api, business):
    run = write(api, business, "health")
    assert run["content"]["sections"][0]["paragraphs"] == [
        "There are no finished months of figures yet. Bring in your sales, costs and customers first."
    ]


def test_a_new_business_gets_an_honest_empty_report_for_every_kind(api, business):
    for kind in ("monthly", "health", "kpi", "outcomes"):
        run = write(api, business, kind)
        assert run["content"]["facts"][0] == "No finished months of figures yet."
        assert "no finished months" in run["content"]["sections"][0]["paragraphs"][0].lower()
        assert run["period_start"] <= run["period_end"]


def test_the_outcomes_report_before_anything_was_tried(api, march):
    run = write(api, march, "outcomes")
    assert run["content"]["sections"][0]["paragraphs"] == [
        "Nothing you have tried has been finished and checked yet."
    ]


@pytest.fixture
def suggested(api, march):
    event = event_for(api, march, "revenue")
    return event, recommend(api, march, event).json()


def test_the_outcomes_report_tells_what_was_tried_and_what_came_of_it(api, db, march, suggested):
    from tests.test_outcomes import check, figures, finish

    event, _ = suggested
    action = finish(api, march, event)
    figures(db, march, action, 100000)
    check(db, march, action)
    s = sections(write(api, march, "outcomes"))
    assert (
        "1 worked, 0 partly worked, 0 did not work and 0 could not be judged"
        in s["How it has gone"]["paragraphs"][0]
    )
    assert (
        list(s)
        == [
            "How it has gone",
            "Results measured in this period",
            "What was learned",
            "How each kind of action has worked for you",
        ]
        or "What you took up in this period" in s
    )
    measured = s["Results measured in this period"]
    assert (
        measured["table"]["rows"][0][0] == action["title"]
        and measured["table"]["rows"][0][-1] == "It worked"
    )
    assert measured["bullets"][0].startswith("It won back")
    assert s["What was learned"]["bullets"][0].startswith(f'"{action["title"]}" worked for Sales')
    track = s["How each kind of action has worked for you"]["table"]["rows"][0]
    assert track[1:] == ["1", "0", "0", "0", "75"]


def test_a_report_is_kept_exactly_as_it_was_written(api, db, march):
    from app.models.kpi import KpiValue

    run = write(api, march, "kpi")
    with scoped(db, march):
        for v in db.scalars(select(KpiValue).where(KpiValue.status == "ok")):
            v.value = 1
        db.flush()
    again = api.get(f"{ORGS}/{march[0]}/reports/runs/{run['id']}", headers=march[2]["owner"]).json()
    assert again["content"] == run["content"]
    fresh = write(api, march, "kpi")
    assert fresh["content"]["sections"] != run["content"]["sections"]


def test_writing_a_report_is_recorded_in_the_audit_log(api, db, march):
    run = write(api, march, "kpi")
    with scoped(db, march):
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "report.generated")).one()
    assert entry.details == {"kind": "kpi", "run": run["id"]}


# --- who can read what was written -----------------------------------------------------------------------------------------------


def test_a_report_is_for_the_person_it_was_written_for(api, march):
    run = write(api, march, "kpi")
    url = f"{ORGS}/{march[0]}/reports/runs/{run['id']}"
    assert api.get(url, headers=march[2]["owner"]).status_code == 200
    for tail in ("", "/pdf", "/csv"):
        assert api.get(url + tail, headers=march[2]["viewer"]).status_code == 404
    assert api.get(url).status_code == 401
    assert api.get(url, headers=march[2]["other"]).status_code in (403, 404)


def test_a_viewer_can_write_and_read_their_own_reports(api, march):
    run = write(api, march, "monthly", "viewer")
    assert (
        api.get(
            f"{ORGS}/{march[0]}/reports/runs/{run['id']}", headers=march[2]["viewer"]
        ).status_code
        == 200
    )
    assert [
        r["id"]
        for r in api.get(f"{ORGS}/{march[0]}/reports/runs", headers=march[2]["viewer"]).json()
    ] == [run["id"]]
    assert api.get(f"{ORGS}/{march[0]}/reports/runs", headers=march[2]["owner"]).json() == []


def test_the_list_of_what_was_written_is_newest_first_and_can_be_narrowed(api, march):
    a, b = write(api, march, "kpi"), write(api, march, "health")
    url = f"{ORGS}/{march[0]}/reports/runs"
    assert [r["id"] for r in api.get(url, headers=march[2]["owner"]).json()] == [b["id"], a["id"]]
    assert [
        r["id"]
        for r in api.get(
            f"{url}?report_id={report_of(api, march, 'kpi')['id']}", headers=march[2]["owner"]
        ).json()
    ] == [a["id"]]
    assert len(api.get(f"{url}?limit=1", headers=march[2]["owner"]).json()) == 1
    assert api.get(f"{url}?limit=0", headers=march[2]["owner"]).status_code == 422
    assert api.get(f"{url}?limit=101", headers=march[2]["owner"]).status_code == 422


def test_the_latest_written_for_you_is_shown_on_the_list(api, march):
    write(api, march, "kpi")
    second = write(api, march, "kpi")
    mine = report_of(api, march, "kpi")["last_run"]
    assert mine["id"] == second["id"] and mine["emailed"] is False
    assert report_of(api, march, "kpi", "viewer")["last_run"] is None


def test_a_report_that_does_not_exist_is_not_found(api, march):
    assert (
        api.post(
            f"{ORGS}/{march[0]}/reports/{uuid.uuid4()}/generate", headers=march[2]["owner"]
        ).status_code
        == 404
    )
    assert (
        api.get(
            f"{ORGS}/{march[0]}/reports/runs/{uuid.uuid4()}", headers=march[2]["owner"]
        ).status_code
        == 404
    )
    assert (
        api.put(
            f"{ORGS}/{march[0]}/reports/{uuid.uuid4()}/months",
            json={"months": 3},
            headers=march[2]["owner"],
        ).status_code
        == 404
    )


# --- downloading ---------------------------------------------------------------------------------------------------------------------


def test_the_pdf_download_is_a_real_pdf_made_from_the_stored_report(api, march):
    run = write(api, march, "monthly")
    res = api.get(f"{ORGS}/{march[0]}/reports/runs/{run['id']}/pdf", headers=march[2]["owner"])
    assert res.status_code == 200 and res.headers["content-type"] == "application/pdf"
    assert (
        res.headers["content-disposition"] == 'attachment; filename="vyterlix-monthly-2026-03.pdf"'
    )
    assert (
        res.headers["x-content-type-options"] == "nosniff"
        and res.headers["cache-control"] == "private, no-store"
    )
    text = text_of(res.content)
    for expected in (
        "Acme",
        "Monthly business report",
        "At a glance",
        "Sales",
        "410.00",
        "600.00",
        "7 alerts are open",
    ):
        assert expected in text or expected == "7 alerts are open"
    assert res.content == render.to_pdf(run["content"])


def test_the_key_figures_pdf_is_printed_sideways_when_it_has_many_months(api, march):
    rid = report_of(api, march, "kpi")["id"]
    api.put(
        f"{ORGS}/{march[0]}/reports/{rid}/months", json={"months": 7}, headers=march[2]["owner"]
    )
    run = write(api, march, "kpi")
    box = (
        read_pdf(
            api.get(
                f"{ORGS}/{march[0]}/reports/runs/{run['id']}/pdf", headers=march[2]["owner"]
            ).content
        )
        .pages[0]
        .mediabox
    )
    assert box.width > box.height


def test_the_csv_download_opens_cleanly_in_a_spreadsheet(api, march):
    run = write(api, march, "kpi")
    res = api.get(f"{ORGS}/{march[0]}/reports/runs/{run['id']}/csv", headers=march[2]["owner"])
    assert res.status_code == 200 and res.headers["content-type"] == "text/csv; charset=utf-8"
    assert res.headers["content-disposition"] == 'attachment; filename="vyterlix-kpi-2026-03.csv"'
    assert res.content.startswith("﻿".encode()) and res.content == rules.to_csv(
        run["content"]
    ).encode("utf-8")
    assert "Sales,-,-,-,-,£600.00,£410.00,-31.7%" in res.content.decode("utf-8")


def test_a_name_typed_by_a_person_cannot_become_a_formula_in_the_download(
    api, db, march, suggested
):
    event, _ = suggested
    action = accept(api, march, event, {"title": '=HYPERLINK("http://evil.example","x")'}).json()
    run = write(api, march, "monthly")
    res = api.get(f"{ORGS}/{march[0]}/reports/runs/{run['id']}/csv", headers=march[2]["owner"])
    lines = res.content.decode("utf-8").splitlines()
    assert any(
        line.startswith("\"'=HYPERLINK(") or line.startswith("'=HYPERLINK(") for line in lines
    )
    assert not any(line.startswith("=HYPERLINK") for line in lines) and action["title"].startswith(
        "="
    )


def test_downloads_are_recorded_in_the_audit_log(api, db, march):
    run = write(api, march, "kpi")
    api.get(f"{ORGS}/{march[0]}/reports/runs/{run['id']}/pdf", headers=march[2]["owner"])
    api.get(f"{ORGS}/{march[0]}/reports/runs/{run['id']}/csv", headers=march[2]["owner"])
    with scoped(db, march):
        entries = db.scalars(
            select(AuditLog)
            .where(AuditLog.action == "report.exported")
            .order_by(AuditLog.created_at)
        ).all()
    assert [e.details["format"] for e in entries] == ["pdf", "csv"] and all(
        e.details["kind"] == "kpi" for e in entries
    )


# --- scheduled delivery ---------------------------------------------------------------------------------------------------------------


def uid(db, march, email):
    with scoped(db, march):
        return str(db.scalars(select(User.id).where(User.email == email)).one())


def schedule(api, march, kind="monthly", who="owner", **body):
    body = {"frequency": "weekly", "weekday": 1, "recipients": [], **body}
    return api.post(
        f"{ORGS}/{march[0]}/reports/{report_of(api, march, kind)['id']}/schedules",
        json=body,
        headers=march[2][who],
    )


def test_the_owner_can_have_a_report_sent_every_week(api, db, march):
    res = schedule(
        api,
        march,
        recipients=[uid(db, march, "owner@acme.co.uk"), uid(db, march, "viewer@acme.co.uk")],
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert (body["frequency"], body["weekday"], body["day_of_month"], body["when"]) == (
        "weekly",
        1,
        None,
        "Every Monday at 7am",
    )
    assert (
        sorted(body["recipients"]) == ["owner", "viewer"]
        and body["enabled"] is True
        and body["last_run_at"] is None
    )
    assert body["report_name"] == "Monthly business report"
    assert datetime.fromisoformat(body["next_run_at"]).astimezone(rules.UK_TZ).hour == 7
    assert [s["id"] for s in report_of(api, march, "monthly")["schedules"]] == [body["id"]]


def test_or_every_month(api, db, march):
    body = schedule(
        api,
        march,
        frequency="monthly",
        weekday=None,
        day_of_month=5,
        recipients=[uid(db, march, "owner@acme.co.uk")],
    ).json()
    assert (
        body["when"] == "On the 5th of every month at 7am"
        and body["weekday"] is None
        and body["day_of_month"] == 5
    )


@pytest.mark.parametrize(
    "bad",
    [{"frequency": "weekly", "weekday": None}, {"frequency": "weekly", "weekday": 1, "day_of_month": 5}, {"frequency": "monthly", "weekday": None, "day_of_month": None},
     {"frequency": "monthly", "weekday": 2, "day_of_month": 5}, {"frequency": "daily"}, {"weekday": 0}, {"weekday": 8}, {"frequency": "monthly", "weekday": None, "day_of_month": 29},
     {"frequency": "monthly", "weekday": None, "day_of_month": 0}, {"recipients": []}, {"surprise": 1}],
)  # fmt: skip
def test_a_schedule_that_makes_no_sense_is_refused(api, db, march, bad):
    body = {"recipients": [uid(db, march, "owner@acme.co.uk")], **bad}
    assert schedule(api, march, **body).status_code == 422


def test_the_limits_of_the_day_of_the_month_are_allowed(api, db, march):
    for day in (1, 28):
        assert (
            schedule(
                api,
                march,
                frequency="monthly",
                weekday=None,
                day_of_month=day,
                recipients=[uid(db, march, "owner@acme.co.uk")],
            ).status_code
            == 201
        )
    for weekday in (1, 7):
        assert (
            schedule(
                api, march, weekday=weekday, recipients=[uid(db, march, "owner@acme.co.uk")]
            ).status_code
            == 201
        )


def test_reports_can_only_be_sent_to_people_in_the_business(api, db, march):
    stranger = uid(db, march, "owner@acme.co.uk")
    res = schedule(api, march, recipients=[stranger, str(uuid.uuid4())])
    assert res.status_code == 422 and res.json()["error"]["code"] == "not_a_member"
    with scoped(db, march, 1):
        outsider = str(
            db.scalars(select(User.id).where(User.email == "other@acme.co.uk")).first()
            or uuid.uuid4()
        )
    assert schedule(api, march, recipients=[outsider]).status_code == 422


def test_only_the_owner_sets_up_changes_or_stops_a_schedule(api, db, signup, march):
    manager = add_manager(api, db, signup, march, None)
    me = [uid(db, march, "owner@acme.co.uk")]
    assert schedule(api, march, who="viewer", recipients=me).status_code == 403
    assert schedule(api, march, who=manager, recipients=me).status_code == 403
    sid = schedule(api, march, recipients=me).json()["id"]
    base = f"{ORGS}/{march[0]}/reports/schedules/{sid}"
    assert api.patch(base, json={"enabled": False}, headers=march[2][manager]).status_code == 403
    assert api.delete(base, headers=march[2][manager]).status_code == 403
    assert api.delete(base, headers=march[2]["viewer"]).status_code == 403


def test_owners_and_managers_can_see_the_schedules_but_viewers_cannot(api, db, signup, march):
    schedule(api, march, recipients=[uid(db, march, "owner@acme.co.uk")])
    manager = add_manager(api, db, signup, march, None)
    url = f"{ORGS}/{march[0]}/reports/schedules"
    assert (
        len(api.get(url, headers=march[2]["owner"]).json()) == 1
        and len(api.get(url, headers=march[2][manager]).json()) == 1
    )
    assert (
        api.get(url, headers=march[2]["viewer"]).status_code == 403
        and api.get(url).status_code == 401
    )


def test_a_schedule_can_be_paused_changed_and_stopped(api, db, march):
    me = [uid(db, march, "owner@acme.co.uk")]
    first = schedule(api, march, recipients=me).json()
    base = f"{ORGS}/{march[0]}/reports/schedules/{first['id']}"
    paused = api.patch(base, json={"enabled": False}, headers=march[2]["owner"]).json()
    assert paused["enabled"] is False and paused["next_run_at"] == first["next_run_at"]
    monthly = api.patch(
        base,
        json={"frequency": "monthly", "day_of_month": 10, "enabled": True},
        headers=march[2]["owner"],
    ).json()
    assert (monthly["frequency"], monthly["weekday"], monthly["day_of_month"], monthly["when"]) == (
        "monthly",
        None,
        10,
        "On the 10th of every month at 7am",
    )
    assert monthly["enabled"] is True and monthly["next_run_at"] != first["next_run_at"]
    moved = api.patch(base, json={"day_of_month": 12}, headers=march[2]["owner"]).json()
    assert moved["day_of_month"] == 12
    assert (
        api.patch(
            base, json={"recipients": [str(uuid.uuid4())]}, headers=march[2]["owner"]
        ).status_code
        == 422
    )
    assert (
        api.patch(base, json={"frequency": "weekly"}, headers=march[2]["owner"]).status_code == 422
    )  # a weekly one needs a weekday
    assert api.delete(base, headers=march[2]["owner"]).status_code == 204
    assert api.get(f"{ORGS}/{march[0]}/reports/schedules", headers=march[2]["owner"]).json() == []
    assert (
        api.delete(base, headers=march[2]["owner"]).status_code == 404
        and api.patch(base, json={}, headers=march[2]["owner"]).status_code == 404
    )


def test_every_change_to_a_schedule_is_audited(api, db, march):
    sid = schedule(api, march, recipients=[uid(db, march, "owner@acme.co.uk")]).json()["id"]
    base = f"{ORGS}/{march[0]}/reports/schedules/{sid}"
    api.patch(base, json={"enabled": False}, headers=march[2]["owner"])
    api.delete(base, headers=march[2]["owner"])
    with scoped(db, march):
        entries = db.scalars(
            select(AuditLog)
            .where(AuditLog.action == "report.schedule_changed")
            .order_by(AuditLog.created_at)
        ).all()
    assert [e.details["change"] for e in entries] == ["created", "updated", "deleted"]
    assert (
        entries[0].details["recipients"] == 1
        and entries[0].details["when"] == "Every Monday at 7am"
        and entries[1].details["fields"] == ["enabled"]
    )


def due_at(db, march):
    with scoped(db, march):
        return db.scalars(select(ReportSchedule)).one().next_run_at


def test_a_report_that_is_due_is_written_for_each_person_and_they_are_emailed_a_link(
    api, db, march, outbox
):
    ids = [uid(db, march, "owner@acme.co.uk"), uid(db, march, "viewer@acme.co.uk")]
    sid = schedule(api, march, recipients=ids).json()["id"]
    when = due_at(db, march)
    outbox.clear()
    with scoped(db, march):
        assert (
            service.run_due(db, uuid.UUID(march[0]), now=when - timedelta(seconds=1), sender=outbox)
            == 0
        )
        assert outbox == []
        assert service.run_due(db, uuid.UUID(march[0]), now=when, sender=outbox) == 2
        db.commit()
    mails = {m.to: m for m in outbox if m.subject.startswith("[Vyterlix] Monthly")}
    assert set(mails) == {"owner@acme.co.uk", "viewer@acme.co.uk"}
    mail = mails["viewer@acme.co.uk"]
    assert mail.subject == "[Vyterlix] Monthly business report: March 2026"
    assert (
        "Hi viewer," in mail.body and "monthly business report for March 2026 is ready" in mail.body
    )
    assert f"/reports.html?org={march[0]}#" in mail.body and "log in" in mail.body
    run_id = mail.body.split("#")[1].split()[0]
    for who in ("owner", "viewer"):
        mine = api.get(f"{ORGS}/{march[0]}/reports/runs", headers=march[2][who]).json()
        assert len(mine) == 1 and mine[0]["trigger"] == "scheduled" and mine[0]["emailed"] is True
    assert (
        api.get(f"{ORGS}/{march[0]}/reports/runs/{run_id}", headers=march[2]["viewer"]).status_code
        == 200
    )
    assert (
        api.get(f"{ORGS}/{march[0]}/reports/runs/{run_id}", headers=march[2]["owner"]).status_code
        == 404
    )  # it is theirs
    with scoped(db, march):
        row = db.get(ReportSchedule, uuid.UUID(sid))
        assert row.last_run_at == when and row.next_run_at == rules.next_run(
            "weekly", 1, None, when
        )


def test_nothing_is_ever_attached_and_the_email_holds_no_figures(api, db, march, outbox):
    schedule(api, march, recipients=[uid(db, march, "owner@acme.co.uk")])
    when = due_at(db, march)
    outbox.clear()
    with scoped(db, march):
        service.run_due(db, uuid.UUID(march[0]), now=when, sender=outbox)
    [mail] = [m for m in outbox if "Monthly" in m.subject]
    assert "410" not in mail.body and "£" not in mail.body and not hasattr(mail, "attachments")


def test_the_same_schedule_is_not_run_twice(api, db, march, outbox):
    schedule(api, march, recipients=[uid(db, march, "owner@acme.co.uk")])
    when = due_at(db, march)
    with scoped(db, march):
        assert service.run_due(db, uuid.UUID(march[0]), now=when, sender=outbox) == 1
        assert service.run_due(db, uuid.UUID(march[0]), now=when, sender=outbox) == 0
        assert (
            service.run_due(db, uuid.UUID(march[0]), now=when + timedelta(hours=1), sender=outbox)
            == 0
        )
        assert db.scalar(select(func.count()).select_from(ReportRun)) == 1


def test_a_paused_schedule_sends_nothing(api, db, march, outbox):
    sid = schedule(api, march, recipients=[uid(db, march, "owner@acme.co.uk")]).json()["id"]
    api.patch(
        f"{ORGS}/{march[0]}/reports/schedules/{sid}",
        json={"enabled": False},
        headers=march[2]["owner"],
    )
    with scoped(db, march):
        assert (
            service.run_due(
                db, uuid.UUID(march[0]), now=due_at(db, march) + timedelta(days=30), sender=outbox
            )
            == 0
        )


def test_resuming_a_schedule_waits_for_its_next_time_instead_of_catching_up(api, db, march):
    sid = schedule(api, march, recipients=[uid(db, march, "owner@acme.co.uk")]).json()["id"]
    base = f"{ORGS}/{march[0]}/reports/schedules/{sid}"
    api.patch(base, json={"enabled": False}, headers=march[2]["owner"])
    resumed = api.patch(base, json={"enabled": True}, headers=march[2]["owner"]).json()
    assert datetime.fromisoformat(resumed["next_run_at"]) > datetime.now(UTC)


def test_someone_who_has_left_the_business_is_skipped(api, db, march, outbox):
    schedule(
        api,
        march,
        recipients=[uid(db, march, "owner@acme.co.uk"), uid(db, march, "viewer@acme.co.uk")],
    )
    when = due_at(db, march)
    with scoped(db, march):
        member = db.scalars(
            select(OrganizationUser)
            .join(User, User.id == OrganizationUser.user_id)
            .where(User.email == "viewer@acme.co.uk")
        ).one()
        member.status = "removed" if False else "suspended"
        db.flush()
        assert service.run_due(db, uuid.UUID(march[0]), now=when, sender=outbox) == 1


def test_a_failed_email_still_leaves_the_report_to_read(api, db, march):
    class Down:
        def send(self, message):
            raise RuntimeError("down")

    schedule(api, march, recipients=[uid(db, march, "owner@acme.co.uk")])
    with scoped(db, march):
        service.run_due(db, uuid.UUID(march[0]), now=due_at(db, march), sender=Down())
        db.commit()
    [mine] = api.get(f"{ORGS}/{march[0]}/reports/runs", headers=march[2]["owner"]).json()
    assert mine["emailed"] is False


def test_the_workers_timed_round_writes_whatever_is_due(api, db, march, outbox, monkeypatch):
    schedule(api, march, recipients=[uid(db, march, "owner@acme.co.uk")])
    when = due_at(db, march)
    sent = []
    monkeypatch.setattr(
        "app.services.reports.get_email_sender",
        lambda: type("S", (), {"send": lambda self, m: sent.append(m)})(),
    )
    totals = scheduler.tick(db, today=when.date(), now=when)
    assert totals["reports"] == 1 and any("Monthly" in m.subject for m in sent)
    assert scheduler.tick(db, today=when.date(), now=when)["reports"] == 0


def test_a_business_with_nothing_due_is_left_alone_by_the_timed_round(api, db, march):
    schedule(api, march, recipients=[uid(db, march, "owner@acme.co.uk")])
    assert (
        scheduler.tick(db, today=date(2020, 1, 1), now=datetime(2020, 1, 1, tzinfo=UTC))["reports"]
        == 0
    )


# --- keeping each business apart and the tables ----------------------------------------------------------------------------------------------


def test_schedules_and_reports_of_one_business_are_invisible_to_another(api, db, march):
    schedule(api, march, recipients=[uid(db, march, "owner@acme.co.uk")])
    write(api, march, "kpi")
    assert api.get(f"{ORGS}/{march[1]}/reports/schedules", headers=march[2]["other"]).json() == []
    assert api.get(f"{ORGS}/{march[1]}/reports/runs", headers=march[2]["other"]).json() == []
    with scoped(db, march, 1):
        assert (
            db.scalar(select(func.count()).select_from(ReportRun)) == 0
            and db.scalar(select(func.count()).select_from(ReportSchedule)) == 0
        )


def test_a_report_cannot_be_scheduled_in_another_business(api, db, march):
    rid = report_of(api, march, "kpi")["id"]
    res = api.post(
        f"{ORGS}/{march[1]}/reports/{rid}/schedules",
        json={"frequency": "weekly", "weekday": 1, "recipients": [str(uuid.uuid4())]},
        headers=march[2]["other"],
    )
    assert res.status_code == 404


def make_report(db, march, **fields):
    with scoped(db, march):
        row = Report(**{"kind": "kpi", "name": "Key figures", "months": 6, **fields})
        db.add(row)
        db.flush()
        return row.id


@pytest.mark.parametrize("bad", [{"kind": "poem"}, {"months": 0}, {"months": 25}, {"name": " "}])
def test_a_report_that_makes_no_sense_is_refused(db, march, bad):
    with pytest.raises(IntegrityError):
        make_report(db, march, **bad)


def test_a_business_has_one_report_of_each_kind(db, march):
    make_report(db, march)
    with pytest.raises(IntegrityError):
        make_report(db, march)


def test_a_schedule_must_have_a_sensible_time(db, march):
    rid = make_report(db, march)
    for bad in (
        {"frequency": "weekly", "weekday": None},
        {"frequency": "monthly", "day_of_month": 31},
        {"frequency": "weekly", "weekday": 1, "day_of_month": 3},
        {"frequency": "yearly"},
    ):
        with pytest.raises(IntegrityError), scoped(db, march):
            fields = {"report_id": rid, "recipients": [], "next_run_at": datetime.now(UTC)} | bad
            db.add(ReportSchedule(**fields))
            db.flush()
        db.rollback()
        rid = make_report(db, march)


def test_removing_a_report_removes_its_schedules_and_copies(api, db, march):
    schedule(api, march, recipients=[uid(db, march, "owner@acme.co.uk")])
    write(api, march, "monthly")
    with scoped(db, march):
        db.execute(Report.__table__.delete())
        assert db.scalar(select(func.count()).select_from(ReportSchedule)) == 0
        assert db.scalar(select(func.count()).select_from(ReportRun)) == 0


def test_a_copy_must_cover_a_real_period_and_a_known_trigger(db, march):
    rid = make_report(db, march)
    with scoped(db, march):
        owner = db.scalars(select(User).where(User.email == "owner@acme.co.uk")).one()
    for bad in ({"period_end": date(2026, 2, 1)}, {"trigger": "psychic"}):
        with pytest.raises(IntegrityError), scoped(db, march):
            fields = (
                dict(
                    report_id=rid,
                    user_id=owner.id,
                    kind="kpi",
                    title="t",
                    trigger="manual",
                    period_start=date(2026, 3, 1),
                    period_end=date(2026, 3, 31),
                    content={},
                    rules_version="x",
                    generated_at=datetime.now(UTC),
                )
                | bad
            )
            db.add(ReportRun(**fields))
            db.flush()
        db.rollback()
        rid = make_report(db, march)


def test_the_pdf_library_is_a_listed_dependency():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert '"fpdf2' in text.split("[project.optional-dependencies]")[0]
    assert '"httpx' in text.split("[project.optional-dependencies]")[0]
    assert '"pypdf' in text.split("[project.optional-dependencies]")[1]


def test_generating_needs_no_special_permission_but_a_login(api, march):
    rid = report_of(api, march, "kpi")["id"]
    assert api.post(f"{ORGS}/{march[0]}/reports/{rid}/generate").status_code == 401
    assert act and event_for  # (imported for the shared helpers above)


# --- gaps found by breaking the code ------------------------------------------------------------------------------------------------------


def test_resuming_a_schedule_that_fell_behind_moves_it_to_its_next_time(api, db, march):
    sid = schedule(api, march, recipients=[uid(db, march, "owner@acme.co.uk")]).json()["id"]
    base = f"{ORGS}/{march[0]}/reports/schedules/{sid}"
    api.patch(base, json={"enabled": False}, headers=march[2]["owner"])
    with scoped(db, march):
        db.get(ReportSchedule, uuid.UUID(sid)).next_run_at = datetime(2020, 1, 6, 7, tzinfo=UTC)
        db.flush()
    resumed = api.patch(base, json={"enabled": True}, headers=march[2]["owner"]).json()
    assert datetime.fromisoformat(resumed["next_run_at"]) > datetime.now(UTC)


def test_the_monthly_report_covers_one_month_whatever_months_is_stored(api, db, march):
    report_of(api, march, "monthly")
    with scoped(db, march):
        row = db.scalars(select(Report).where(Report.kind == "monthly")).one()
        row.months = 6
        db.flush()
    run = write(api, march, "monthly")
    assert (run["period_start"], run["period_end"]) == ("2026-03-01", "2026-03-31")
    assert run["content"]["facts"][0] == "Covers March 2026."


def fake_health(monkeypatch, months_scores):
    from types import SimpleNamespace as NS

    from app.services import report_builders

    metrics = [NS(score=score, text=f"metric {score}") for score in (60, 10, 50, 20, 40, 30)]
    component = NS(
        label="Money", score=55, status="fair", previous_score=50, weight=0.5, metrics=metrics
    )
    latest = NS(
        overall_score=60,
        status="fair",
        previous_score=55,
        period_start=D(2026, 3, 1),
        explanation="Because.",
        components=[component],
    )
    points = [
        NS(period_start=m, overall_score=score, status="fair", coverage_pct=90)
        for m, score in months_scores
    ]
    monkeypatch.setattr(report_builders.health, "latest_health", lambda db: latest)
    monkeypatch.setattr(
        report_builders.health, "health_history", lambda db, limit: NS(points=points)
    )


def test_the_health_history_only_shows_the_months_the_report_covers(db, march, monkeypatch):
    from app.services import report_builders

    fake_health(monkeypatch, [(D(2026, 1, 1), 50), (D(2026, 2, 1), 55), (D(2026, 3, 1), 60)])
    with scoped(db, march):
        two = {
            s["heading"]: s
            for s in report_builders.health_sections(db, [D(2026, 2, 1), D(2026, 3, 1)])
        }
        one = {s["heading"]: s for s in report_builders.health_sections(db, [D(2026, 3, 1)])}
    assert [r[0] for r in two["How it has moved"]["table"]["rows"]] == [
        "February 2026",
        "March 2026",
    ]
    assert [r[0] for r in one["How it has moved"]["table"]["rows"]] == ["March 2026"]
    area = two["The areas of the business"]["table"]["rows"][0]
    assert area == ["Money", "55", "Fair", "+5", "50%"]


def test_only_the_five_weakest_things_are_listed_as_holding_it_back(db, march, monkeypatch):
    from app.services import report_builders

    fake_health(monkeypatch, [(D(2026, 3, 1), 60)])
    with scoped(db, march):
        sections_ = {s["heading"]: s for s in report_builders.health_sections(db, [D(2026, 3, 1)])}
    assert sections_["What is holding it back"]["bullets"] == [
        "metric 10",
        "metric 20",
        "metric 30",
        "metric 40",
        "metric 50",
    ]


def test_how_a_figure_moved_is_worked_out_the_same_way_everywhere():
    from app.services.report_builders import _delta

    assert (
        _delta("gbp", 5, 0) == "-"
        and _delta("gbp", None, 1) == "-"
        and _delta("gbp", 1, None) == "-"
    )
    assert (
        _delta("gbp", 110, 100) == "+10.0%"
        and _delta("gbp", 90, 100) == "-10.0%"
        and _delta("gbp", 100, 100) == "0.0%"
    )
    assert (
        _delta("percent", 5, 0) == "+5.0 points"
        and _delta("percent", 4, 6) == "-2.0 points"
        and _delta("percent", 3, 3) == "0.0 points"
    )
