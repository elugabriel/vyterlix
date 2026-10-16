# ruff: noqa: E501, F811
"""Today answers five questions (client feedback 15/10/2026, section 1): what is going well, what
needs attention today, what to do first, what I am waiting for, and whether my actions helped."""

from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from app.core.uk import today_uk
from app.dashboard import rules
from app.schemas.dashboard import FigureGlance
from app.schemas.kpi import PlainOut
from app.services import dashboard as service
from tests.test_actions import accept, add_manager, set_dates
from tests.test_alerts import evaluate
from tests.test_dashboard import dash
from tests.test_health import owner_tenant, scoped
from tests.test_outcomes import check, done, figures, finish, make_due, suggested  # noqa: F401

# --- did my actions help? in one line -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("counts", "sentence"),
    [
        (
            (0, 0, 0, 0, 0),
            "No action has been checked yet. Finish one and we will tell you whether it helped.",
        ),
        ((1, 1, 0, 0, 0), "The one action we have checked worked."),
        ((1, 0, 1, 0, 0), "The one action we have checked partly worked."),
        ((1, 0, 0, 1, 0), "The one action we have checked did not work."),
        ((1, 0, 0, 0, 1), "The one action we have checked is one we cannot tell about yet."),
        ((2, 2, 0, 0, 0), "Of the 2 actions we have checked, 2 worked."),
        (
            (4, 2, 1, 1, 0),
            "Of the 4 actions we have checked, 2 worked, 1 partly worked and 1 did not work.",
        ),
        (
            (5, 1, 1, 1, 2),
            "Of the 5 actions we have checked, 1 worked, 1 partly worked, 1 did not work and for 2 we cannot tell.",
        ),
        ((3, 0, 0, 0, 3), "Of the 3 actions we have checked, for 3 we cannot tell."),
    ],
)
def test_the_answer_to_did_they_help_is_one_plain_line(counts, sentence):
    assert rules.results_sentence(*counts) == sentence


# --- the five parts, from the API --------------------------------------------------------------------------


def test_the_front_screen_carries_all_five_answers(api, march):
    body = dash(api, march)
    assert {"going_well", "attention", "do_first", "waiting_for", "results"} <= body.keys()
    assert isinstance(body["going_well"], list) and isinstance(body["waiting_for"], list)
    assert body["results"]["measured"] == 0 and body["results"]["latest"] == []
    assert body["results"]["sentence"].startswith("No action has been checked yet.")


def test_what_to_do_first_is_the_most_serious_thing_that_needs_attention(api, march):
    evaluate(api, march)
    body = dash(api, march)
    assert body["attention"] and body["do_first"] == body["attention"][0]
    assert body["do_first"]["can_act"] is True


def test_with_nothing_needing_attention_there_is_nothing_to_do_first(api, march):
    evaluate(api, march)
    other = dash(api, march, "other", org=1)  # a business with nothing in it
    assert other["attention"] == [] and other["do_first"] is None
    assert other["going_well"] == [] and other["waiting_for"] == []
    assert other["results"]["measured"] == 0


def test_a_viewer_is_shown_the_same_answers_but_given_nothing_to_do(api, march):
    evaluate(api, march)
    body = dash(api, march, "viewer")
    assert body["do_first"]["can_act"] is False and body["can_act"] is False


# --- what am I waiting for -----------------------------------------------------------------------------------


@pytest.fixture
def started(api, march, suggested):
    event, _ = suggested
    return accept(api, march, event).json()


def test_work_under_way_is_listed_with_who_has_it_and_when_it_is_due(api, db, march, started):
    set_dates(
        db, march, started["id"], today_uk() - timedelta(days=1), today_uk() + timedelta(days=9)
    )
    waiting = dash(api, march)["waiting_for"]
    mine = next(w for w in waiting if w["id"] == f"action:{started['id']}")
    assert mine["title"] == started["title"]
    assert mine["detail"].endswith(
        f", due {today_uk() + timedelta(days=9):%d/%m/%Y}."
    ) and not mine["detail"].startswith("Nobody")
    assert mine["link"] == f"actions.html#{started['id']}" and mine["tone"] == "neutral"


def test_work_due_soon_is_marked_to_watch(api, db, march, started):
    set_dates(
        db, march, started["id"], today_uk() - timedelta(days=1), today_uk() + timedelta(days=1)
    )
    waiting = dash(api, march)["waiting_for"]
    assert next(w for w in waiting if w["id"] == f"action:{started['id']}")["tone"] == "watch"


def test_late_work_is_in_what_needs_attention_not_in_what_i_am_waiting_for(api, db, march, started):
    set_dates(
        db, march, started["id"], today_uk() - timedelta(days=9), today_uk() - timedelta(days=2)
    )
    body = dash(api, march)
    assert all(w["id"] != f"action:{started['id']}" for w in body["waiting_for"])
    assert any(i["id"] == f"action:{started['id']}" for i in body["attention"])


def test_a_finished_action_waiting_to_be_checked_says_when_it_will_be(api, db, march, done):
    waiting = dash(api, march)["waiting_for"]
    result = next(w for w in waiting if w["id"] == f"result:{done['id']}")
    assert result["title"] == f'Result of "{done["title"]}"'
    assert (
        result["detail"]
        == f"We will check how it went from {date.fromisoformat(done['follow_up']['due_date']):%d/%m/%Y}."
    )
    assert all(w["id"] != f"action:{done['id']}" for w in waiting)  # no longer work under way


def test_once_the_check_is_due_it_is_a_thing_to_do_not_something_to_wait_for(api, db, march, done):
    due, _ = make_due(db, march)
    body = dash(api, march)
    assert any(w["id"] == f"result:{done['id']}" for w in body["waiting_for"])
    with scoped(db, march):
        from app.services import dashboard

        rows = dashboard._waiting_for(db, owner_tenant(db, march), "owner", None, due)
    assert all(w.id != f"result:{done['id']}" for w in rows)


def test_a_manager_sees_what_they_put_forward_waiting_for_approval_but_not_other_areas(
    api, db, signup, march, suggested
):
    event, _ = suggested  # a fall in sales: a financial/sales matter
    add_manager(api, db, signup, march, ["customer"])
    proposed = accept(api, march, event, who="manager").json()
    assert proposed["status"] == "pending"
    mine = dash(api, march, "manager")["waiting_for"]
    assert all(
        w["id"] != f"approval:{proposed['id']}" for w in mine
    )  # outside their area, so not shown to them
    owner_view = dash(api, march)
    assert any(
        i["id"] == f"approve:{proposed['id']}" for i in owner_view["attention"]
    )  # the owner decides it


def test_what_i_am_waiting_for_is_a_short_list(api, db, march, done):
    assert len(dash(api, march)["waiting_for"]) <= service.WAITING_SHOWN


# --- did my previous actions help --------------------------------------------------------------------------------


def measured(api, db, march, done):
    value = D(done["decision"]["baseline_value"]) + D(done["decision"]["expected_impact_value"])
    figures(db, march, done, value)
    check(db, march, done)


def test_a_result_is_counted_and_told_in_plain_words(api, db, march, done):
    measured(api, db, march, done)
    r = dash(api, march)["results"]
    assert r["measured"] == 1 and r["successful"] == 1
    assert r["sentence"] == "The one action we have checked worked."
    [latest] = r["latest"]
    assert (
        latest["title"] == done["title"]
        and latest["outcome"] == "successful"
        and latest["outcome_label"] == "It worked"
    )
    assert latest["reason"] and latest["link"] == f"actions.html#{done['id']}"


def test_a_result_no_longer_waits_to_be_checked(api, db, march, done):
    measured(api, db, march, done)
    assert all(w["id"] != f"result:{done['id']}" for w in dash(api, march)["waiting_for"])


def test_a_recent_win_is_part_of_what_is_going_well(api, db, march, done):
    measured(api, db, march, done)
    wins = [g for g in dash(api, march)["going_well"] if g["id"] == f"result:{done['id']}"]
    assert (
        len(wins) == 1
        and wins[0]["title"] == f'It worked: "{done["title"]}"'
        and wins[0]["tone"] == "good"
    )


def test_an_old_win_is_not_news_any_more(db, march, done, api):
    measured(api, db, march, done)
    far_ahead = today_uk() + timedelta(days=service.GOING_WELL_DAYS + 5)
    with scoped(db, march):
        rows = service._going_well(db, "owner", None, [], far_ahead)
    assert all(not g.id.startswith("result:") for g in rows)


# --- what is going well -------------------------------------------------------------------------------------------


def figure(code, tone, says="You sold more than last month."):
    return FigureGlance(
        code=code, name=code.title(), unit="gbp", period="2026-03-01", value="10.00", change_pct="5.0",
        direction="up_good", plain=None if tone is None else PlainOut(says=says, why="Good.", tone=tone),
    )  # fmt: skip


def test_the_good_figures_are_what_is_going_well_and_the_bad_ones_are_not(db, march):
    figs = [
        figure("a", "bad"),
        figure("b", "good"),
        figure("c", "watch"),
        figure("d", "good", "Fewer refunds."),
        figure("e", None),
    ]
    with scoped(db, march):
        rows = service._going_well(db, "owner", None, figs, today_uk())
    got = [(g.id, g.detail) for g in rows if g.id.startswith("figure:")]
    assert got == [("figure:b", "You sold more than last month."), ("figure:d", "Fewer refunds.")]
    assert all(g.tone == "good" for g in rows) and rows[0].link == "kpis.html#b"


def test_no_more_than_three_figures_are_shown_as_going_well(db, march):
    figs = [figure(c, "good") for c in "abcdef"]
    with scoped(db, march):
        rows = service._going_well(db, "owner", None, figs, today_uk())
    assert len([g for g in rows if g.id.startswith("figure:")]) == service.GOING_WELL_FIGURES == 3


def test_nothing_going_well_is_an_empty_list_not_an_invented_one(db, march):
    with scoped(db, march):
        rows = service._going_well(db, "owner", None, [figure("a", "bad")], today_uk())
    assert [g for g in rows if g.id.startswith("figure:")] == []


def test_a_rise_in_health_is_good_news_and_a_fall_is_not(db, march, monkeypatch):
    from types import SimpleNamespace

    def health(score, previous):
        return SimpleNamespace(overall_score=score, previous_score=previous)

    def going_well():
        with scoped(db, march):
            return service._going_well(db, "owner", None, [], today_uk())

    monkeypatch.setattr(service.health, "latest_health", lambda db: health(72, 60))
    rows = going_well()
    assert (
        rows[0].id == "health"
        and rows[0].detail == "From 60 to 72 out of 100."
        and rows[0].tone == "good"
    )
    monkeypatch.setattr(service.health, "latest_health", lambda db: health(60, 72))
    assert going_well() == []
    monkeypatch.setattr(service.health, "latest_health", lambda db: health(60, None))
    assert going_well() == []
    monkeypatch.setattr(service.health, "latest_health", lambda db: None)
    assert going_well() == []
