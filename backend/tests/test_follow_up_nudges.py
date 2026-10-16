# ruff: noqa: E501, F811
"""Follow-up that nudges and asks (client feedback 15/10/2026, section 3 and examples B and D)."""

import uuid
from datetime import date, timedelta
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.uk import today_uk
from app.models.actions import ActionReminder, BusinessAction
from app.models.alerts import Notification
from app.outcomes import rules
from app.services import outcomes, reminders, scheduler
from tests.test_actions import accept, act, set_dates
from tests.test_health import ORGS, owner_tenant, scoped
from tests.test_outcomes import done, finish, suggested  # noqa: F401  (fixtures)

# --- sales are not the whole story ---------------------------------------------------------------------


def good(reason="It won back £100.00, against the £100.00 we expected (100%)."):
    return rules.Verdict("successful", reason, 100, D(100))


def gbp(v):
    return outcomes.show(v, "gbp")


def check(verdict=None, before=D(1000), after=D(1000), words="profit on what you sold"):
    return rules.profit_check(verdict or good(), words=words, before=before, after=after, show=gbp)


def test_sales_up_and_profit_up_is_still_a_success_and_says_so():
    result = check(before=D(1000), after=D(1200))
    assert result.outcome == "successful"
    assert result.reason.endswith(
        "Your profit on what you sold held up too (£1,000.00 to £1,200.00)."
    )


def test_sales_up_but_profit_down_is_only_a_partial_success():
    result = check(before=D(1000), after=D(800))
    assert result.outcome == "partially_successful"
    assert result.reason.endswith(
        "But your profit on what you sold fell from £1,000.00 to £800.00, so we cannot call it a success."
    )
    assert (result.achieved_pct, result.adjusted) == (
        100,
        D(100),
    )  # the sales figures are still on record


def test_a_small_dip_in_profit_is_just_wobble_but_the_line_is_exact():
    assert check(before=D(1000), after=D(950)).outcome == "successful"  # exactly 5% down
    assert check(before=D(1000), after=D("949.99")).outcome == "partially_successful"
    assert check(before=D(1000), after=D(1000)).outcome == "successful"


def test_a_loss_getting_deeper_counts_as_profit_falling():
    assert check(before=D(-100), after=D(-300)).outcome == "partially_successful"
    assert (
        check(before=D(-100), after=D(-102)).outcome == "successful"
    )  # within 5% of the earlier figure
    assert check(before=D(0), after=D(-1)).outcome == "partially_successful"


@pytest.mark.parametrize("before,after", [(None, D(500)), (D(500), None), (None, None)])
def test_without_the_profit_figures_it_says_so_and_does_not_claim_the_action_paid_off(
    before, after
):
    result = check(before=before, after=after)
    assert result.outcome == "successful"
    assert result.reason.endswith(
        "We could not check what it did to your profit on what you sold, so we cannot be sure it paid off."
    )


@pytest.mark.parametrize("outcome", ["partially_successful", "unsuccessful", "inconclusive"])
def test_a_result_that_was_not_a_success_is_left_alone(outcome):
    verdict = rules.Verdict(outcome, "As it was.", 40, D(40))
    assert check(verdict, before=D(1000), after=D(1)) is verdict


def test_only_actions_that_give_something_away_or_spend_money_are_checked_against_profit(db):
    from app.models.recommendations import Intervention

    library = set(db.scalars(select(Intervention.code)))
    assert set(rules.PROFIT_CHECKED) <= library, set(rules.PROFIT_CHECKED) - library
    assert {
        "revenue",
        "sales_count",
    } <= rules.SALES_FIGURES and "gross_profit" not in rules.SALES_FIGURES
    assert rules.PROFIT_CHECKED["local_marketing"][0] == "net_profit"


def stub_service(monkeypatch, then, now):
    """The service reading profit for the month the action was accepted and the month measured."""
    values = {date(2026, 2, 1): then, date(2026, 4, 1): now}
    monkeypatch.setattr(
        outcomes,
        "_value",
        lambda db, kpi_id, month: (
            None if values[month] is None else SimpleNamespace(value=values[month])
        ),
    )


def run_guard(db, kpi_code="revenue", library_code="promote_product"):
    kpi = SimpleNamespace(code=kpi_code)
    intervention = SimpleNamespace(library_code=library_code, baseline_period=date(2026, 2, 1))
    plan = SimpleNamespace(measure_month=date(2026, 4, 1))
    return outcomes._check_profit(db, kpi, intervention, plan, good())


def test_the_service_holds_a_sales_win_back_when_profit_fell(db, monkeypatch):
    stub_service(monkeypatch, D(900), D(500))
    result = run_guard(db)
    assert (
        result.outcome == "partially_successful" and "fell from £900.00 to £500.00" in result.reason
    )


def test_the_service_leaves_a_sales_win_alone_when_profit_held(db, monkeypatch):
    stub_service(monkeypatch, D(900), D(950))
    assert run_guard(db).outcome == "successful"


def test_the_service_says_so_when_profit_is_not_there(db, monkeypatch):
    stub_service(monkeypatch, None, D(950))
    assert "We could not check" in run_guard(db).reason


def test_marketing_is_checked_against_profit_after_running_costs(db, monkeypatch):
    stub_service(monkeypatch, D(900), D(100))
    assert "your profit fell" in run_guard(db, library_code="local_marketing").reason


@pytest.mark.parametrize(
    ("kpi", "library"),
    [
        ("gross_margin_pct", "promote_product"),
        ("revenue", "win_back_regulars"),
        ("customer_churn_pct", "win_back_regulars"),
        ("revenue", "an_unknown_action"),
    ],
)
def test_other_figures_and_other_actions_are_not_second_guessed(db, monkeypatch, kpi, library):
    stub_service(monkeypatch, D(900), D(1))
    result = run_guard(db, kpi, library)
    assert result.reason == good().reason


# --- what was done ----------------------------------------------------------------------------------------


def status(api, march, action_id, body, who="owner"):
    return act(api, march, action_id, "/status", "post", json=body, who=who)


@pytest.fixture
def started(api, march, suggested):
    event, _ = suggested
    return accept(api, march, event).json()


def test_finishing_an_action_keeps_what_was_done_and_how_many_it_reached(api, march, started):
    res = status(
        api,
        march,
        started["id"],
        {"status": "completed", "what_was_done": "Rang 12 regulars", "reached": 12},
    )
    assert res.status_code == 200, res.text
    last = res.json()["updates"][-1]
    assert last["kind"] == "status" and last["to_status"] == "completed"
    assert last["note"] == "Rang 12 regulars" and last["details"] == {"reached": 12}


def test_part_finished_work_can_say_what_has_been_done_so_far(api, march, started):
    res = status(
        api,
        march,
        started["id"],
        {"status": "partially_completed", "what_was_done": "Did the first half", "reached": 0},
    )
    assert res.status_code == 200 and res.json()["updates"][-1]["details"] == {"reached": 0}


def test_the_old_way_of_finishing_still_works(api, march, started):
    res = status(api, march, started["id"], {"status": "completed", "note": "All done"})
    assert res.status_code == 200
    last = res.json()["updates"][-1]
    assert last["note"] == "All done" and last["details"] == {}


@pytest.mark.parametrize(
    "body",
    [
        {"status": "in_progress", "what_was_done": "x"},
        {"status": "cancelled", "reached": 3},
        {"status": "completed", "reached": -1},
        {"status": "completed", "reached": 10_000_001},
    ],
)
def test_a_report_of_what_was_done_only_makes_sense_when_it_is_done(api, march, started, body):
    assert status(api, march, started["id"], body).status_code == 422


# --- reminders ----------------------------------------------------------------------------------------------


def tenant(db, march):
    return owner_tenant(db, march)


def sweep(db, march, today):
    with scoped(db, march):
        return reminders.sweep(db, tenant(db, march), today=today)


def inbox(api, march, who="owner"):
    return api.get(f"{ORGS}/{march[0]}/notifications", headers=march[2][who]).json()["items"]


def due_in(db, march, started, days):
    set_dates(
        db, march, started["id"], today_uk() - timedelta(days=1), today_uk() + timedelta(days=days)
    )


def test_the_person_doing_it_is_told_two_days_before_and_on_the_day_each_once(
    api, db, march, started
):
    due_in(db, march, started, 2)
    assert sweep(db, march, today_uk()) == {"due_soon": 1, "due_today": 0, "check_in": 0}
    assert sweep(db, march, today_uk()) == {
        "due_soon": 0,
        "due_today": 0,
        "check_in": 0,
    }  # once only
    titles = [n["title"] for n in inbox(api, march)]
    assert f'Due in 2 days: "{started["title"]}"' in titles
    assert sweep(db, march, today_uk() + timedelta(days=1)) == {
        "due_soon": 0,
        "due_today": 0,
        "check_in": 0,
    }  # already told for this date
    assert sweep(db, march, today_uk() + timedelta(days=2)) == {
        "due_soon": 0,
        "due_today": 1,
        "check_in": 0,
    }
    assert f'Due today: "{started["title"]}"' in [n["title"] for n in inbox(api, march)]


def test_the_wording_is_plain_and_says_what_to_do(api, db, march, started):
    due_in(db, march, started, 1)
    sweep(db, march, today_uk())
    note = next(n for n in inbox(api, march) if n["title"].startswith("Due tomorrow"))
    assert note["category"] == "action" and note["link"] == f"actions.html#{started['id']}"
    assert (
        "you do not need to do anything" in note["body"]
        and "move the date or hand it to someone" in note["body"]
    )


@pytest.mark.parametrize("days", [3, 10, 60])
def test_nothing_is_sent_while_it_is_not_close(db, march, started, days):
    due_in(db, march, started, days)
    assert sweep(db, march, today_uk()) == {"due_soon": 0, "due_today": 0, "check_in": 0}


def test_a_reminder_that_was_missed_is_still_sent_the_day_after_it_would_have_been(
    db, march, started
):
    due_in(db, march, started, 2)
    assert (
        sweep(db, march, today_uk() + timedelta(days=1))["due_soon"] == 1
    )  # the worker was down on the day


def test_moving_the_date_earns_a_new_reminder(db, march, started):
    due_in(db, march, started, 2)
    sweep(db, march, today_uk())
    due_in(db, march, started, 9)
    assert (
        sweep(db, march, today_uk() + timedelta(days=7))["due_soon"] == 1
    )  # two days before the new date
    with scoped(db, march):
        rows = db.scalars(
            select(ActionReminder).where(ActionReminder.action_id == uuid.UUID(started["id"]))
        ).all()
    assert len(rows) == 2 and len({r.for_date for r in rows}) == 2


def test_late_work_is_left_to_the_overdue_alert_and_finished_work_is_not_chased(
    api, db, march, started
):
    set_dates(
        db, march, started["id"], today_uk() - timedelta(days=9), today_uk() - timedelta(days=1)
    )
    assert sweep(db, march, today_uk()) == {"due_soon": 0, "due_today": 0, "check_in": 0}
    due_in(db, march, started, 1)
    status(api, march, started["id"], {"status": "completed"})
    assert sweep(db, march, today_uk())["due_soon"] == 0


def test_work_nobody_owns_is_not_chased(db, march, started):
    due_in(db, march, started, 1)
    with scoped(db, march):
        db.execute(
            BusinessAction.__table__.update()
            .where(BusinessAction.id == uuid.UUID(started["id"]))
            .values(owner_user_id=None)
        )
    assert sweep(db, march, today_uk()) == {"due_soon": 0, "due_today": 0, "check_in": 0}


def test_the_reminder_goes_to_the_person_it_is_assigned_to_not_to_whoever_set_it_up(
    api, db, march, started
):
    members = api.get(f"{ORGS}/{march[0]}/members", headers=march[2]["owner"]).json()
    other = next(m for m in members if m["role"] != "owner")
    due_in(db, march, started, 1)
    with scoped(db, march):
        db.execute(
            BusinessAction.__table__.update()
            .where(BusinessAction.id == uuid.UUID(started["id"]))
            .values(owner_user_id=uuid.UUID(other["user_id"]))
        )
        before = len(
            db.scalars(
                select(Notification).where(Notification.user_id == uuid.UUID(other["user_id"]))
            ).all()
        )
    sweep(db, march, today_uk())
    with scoped(db, march):
        mine = db.scalars(
            select(Notification).where(Notification.user_id == uuid.UUID(other["user_id"]))
        ).all()
        owners = db.scalars(
            select(Notification).where(
                Notification.title.like("Due tomorrow%"),
                Notification.user_id != uuid.UUID(other["user_id"]),
            )
        ).all()
    assert len(mine) == before + 1 and owners == []


# --- the check-in ------------------------------------------------------------------------------------------


def test_two_weeks_after_finishing_it_asks_how_it_is_going_once(api, db, march, done):
    finished = today_uk()
    assert sweep(db, march, finished + timedelta(days=13))["check_in"] == 0
    assert sweep(db, march, finished + timedelta(days=14))["check_in"] == 1
    assert sweep(db, march, finished + timedelta(days=20))["check_in"] == 0
    note = next(n for n in inbox(api, march) if n["title"].startswith("How is "))
    assert (
        note["link"] == f"actions.html#{done['id']}"
        and "how many customers you reached" in note["body"]
    )


def test_no_check_in_once_the_result_has_been_measured(db, march, done):
    from app.models.outcomes import FollowUpSchedule

    with scoped(db, march):
        db.execute(
            FollowUpSchedule.__table__.update()
            .where(FollowUpSchedule.action_id == uuid.UUID(done["id"]))
            .values(status="done")
        )
    assert sweep(db, march, today_uk() + timedelta(days=30))["check_in"] == 0


def test_the_timed_round_sends_them_and_counts_them(db, march, started):
    due_in(db, march, started, 1)
    org = uuid.UUID(march[0])
    totals = scheduler.tick(db, today=today_uk())
    assert totals["reminded"] >= 1 and totals["businesses"] >= 1
    with scoped(db, march):
        assert db.scalars(select(ActionReminder).where(ActionReminder.organization_id == org)).all()
    assert scheduler.tick(db, today=today_uk())["reminded"] == 0  # and not again


def test_a_business_with_only_finished_work_waiting_to_be_checked_is_still_visited(db, march, done):
    with scoped(db, march):
        pass
    assert uuid.UUID(march[0]) in scheduler._businesses_with_work(db, today_uk())
