"""The background job queue: queueing, running, failing, retrying, recovering, isolation."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.config import get_settings
from app.db.tenant import ACROSS_TENANTS
from app.models.identity import OrganizationUser, Role, User
from app.models.imports import DataImport
from app.models.jobs import Job
from app.services import import_validation, jobs
from app.services.auth import RequestMeta
from tests.test_import_run import INC, ORGS, ROWS, SALES_MAP, base, csv

pytestmark = pytest.mark.usefixtures("business")

FILE = csv(*ROWS)


def mapped(api, business, content=FILE, org=0, who="owner"):
    """Upload and map a file (not yet checked); returns the import id."""
    headers = business[2][who]
    res = api.post(
        f"{ORGS}/{business[org]}/imports",
        files={"file": ("file.csv", content)},
        data={"dataset": "sales"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    import_id = res.json()["id"]
    res = api.put(
        base(business, import_id, org) + "/mapping",
        json={"mapping": SALES_MAP, "options": INC},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return import_id


def start(api, business, import_id, action, who="owner", org=0):
    return api.post(
        base(business, import_id, org) + "/jobs", json={"action": action}, headers=business[2][who]
    )


def work(db, storage, worker="test-worker"):
    return jobs.work_once(db, worker, storage=storage)


def job_row(db, job_id) -> Job:
    return db.scalars(
        select(Job)
        .where(Job.id == uuid.UUID(str(job_id)))
        .execution_options(populate_existing=True, **ACROSS_TENANTS)
    ).one()


def import_row(db, import_id) -> DataImport:
    return db.scalars(
        select(DataImport)
        .where(DataImport.id == uuid.UUID(import_id))
        .execution_options(populate_existing=True, **ACROSS_TENANTS)
    ).one()


# --- queueing ----------------------------------------------------------------------------


def test_starting_a_job_returns_at_once_and_does_nothing_until_a_worker_runs_it(api, db, business):
    import_id = mapped(api, business)
    res = start(api, business, import_id, "validate")
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["status"] == "queued" and body["kind"] == "import.validate"
    assert body["percent"] is None and body["result"] is None
    assert import_row(db, import_id).status == "mapped"  # nothing ran yet


def test_a_second_job_on_the_same_upload_is_refused_while_one_is_active(api, business):
    import_id = mapped(api, business)
    assert start(api, business, import_id, "validate").status_code == 202
    again = start(api, business, import_id, "import")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "job_already_running"


def test_a_new_job_is_allowed_once_the_previous_one_has_finished(api, db, business, storage):
    import_id = mapped(api, business)
    start(api, business, import_id, "validate")
    work(db, storage)
    assert start(api, business, import_id, "import").status_code == 202


def test_viewers_cannot_start_or_read_jobs(api, business):
    import_id = mapped(api, business)
    job_id = start(api, business, import_id, "validate").json()["id"]
    assert start(api, business, import_id, "validate", who="viewer").status_code == 403
    res = api.get(f"{ORGS}/{business[0]}/jobs/{job_id}", headers=business[2]["viewer"])
    assert res.status_code == 403


def test_another_business_cannot_see_or_start_jobs_on_this_upload(api, business):
    import_id = mapped(api, business)
    job_id = start(api, business, import_id, "validate").json()["id"]
    theirs = business[2]["other"]
    assert api.get(f"{ORGS}/{business[1]}/jobs/{job_id}", headers=theirs).status_code == 404
    res = api.post(
        f"{ORGS}/{business[1]}/imports/{import_id}/jobs",
        json={"action": "validate"},
        headers=theirs,
    )
    assert res.status_code == 404
    res = api.get(f"{ORGS}/{business[1]}/imports/{import_id}/jobs", headers=theirs)
    assert res.status_code == 404


def test_unknown_upload_and_unknown_action_are_rejected(api, business):
    res = start(api, business, str(uuid.uuid4()), "validate")
    assert res.status_code == 404
    import_id = mapped(api, business)
    assert start(api, business, import_id, "explode").status_code == 422


def test_jobs_for_an_upload_lists_newest_first(api, db, business, storage):
    import_id = mapped(api, business)
    first = start(api, business, import_id, "validate").json()["id"]
    work(db, storage)
    second = start(api, business, import_id, "import").json()["id"]
    res = api.get(base(business, import_id) + "/jobs", headers=business[2]["owner"])
    assert [j["id"] for j in res.json()] == [second, first]


# --- running ----------------------------------------------------------------------------


def test_the_whole_flow_runs_in_the_background(api, db, business, storage):
    import_id = mapped(api, business)

    job = start(api, business, import_id, "validate").json()
    ran = work(db, storage)
    assert ran is not None and str(ran.id) == job["id"]
    got = api.get(f"{ORGS}/{business[0]}/jobs/{job['id']}", headers=business[2]["owner"]).json()
    assert got["status"] == "succeeded" and got["percent"] == 100
    assert got["result"]["valid"] == 3 and got["result"]["can_import"] is True
    assert import_row(db, import_id).status == "validated"

    job = start(api, business, import_id, "import").json()
    work(db, storage)
    got = api.get(f"{ORGS}/{business[0]}/jobs/{job['id']}", headers=business[2]["owner"]).json()
    assert got["status"] == "succeeded"
    assert got["result"]["created"]["sales"] == 3
    assert import_row(db, import_id).status == "imported"

    job = start(api, business, import_id, "undo").json()
    work(db, storage)
    got = api.get(f"{ORGS}/{business[0]}/jobs/{job['id']}", headers=business[2]["owner"]).json()
    assert got["status"] == "succeeded" and got["result"]["removed"]["sales"] == 3
    assert import_row(db, import_id).status == "undone"


def test_the_worker_does_nothing_when_the_queue_is_empty(db, storage):
    assert work(db, storage) is None


def test_an_expected_failure_fails_at_once_with_the_same_message_as_the_web_request(
    api, db, business, storage
):
    import_id = mapped(api, business)  # never checked, so it can't be imported
    job = start(api, business, import_id, "import").json()
    ran = work(db, storage)
    assert ran.status == "failed" and ran.attempts == 1  # not retried
    assert ran.error_code == "not_validated"
    assert ran.error_message == "Check the data before importing it"
    got = api.get(f"{ORGS}/{business[0]}/jobs/{job['id']}", headers=business[2]["owner"]).json()
    assert got["status"] == "failed" and got["finished_at"] is not None


def test_jobs_are_taken_oldest_first_and_each_runs_in_its_own_business(api, db, business, storage):
    mine = mapped(api, business)
    theirs = mapped(api, business, org=1, who="other")
    first = start(api, business, mine, "validate").json()["id"]
    second = start(api, business, theirs, "validate", who="other", org=1).json()["id"]
    assert str(work(db, storage).id) == first
    assert str(work(db, storage).id) == second
    assert import_row(db, mine).organization_id == uuid.UUID(business[0])
    assert import_row(db, theirs).status == "validated"
    assert import_row(db, theirs).organization_id == uuid.UUID(business[1])
    assert import_row(db, mine).valid_count == 3


def test_a_job_that_is_not_due_yet_is_left_alone(api, db, business, storage):
    import_id = mapped(api, business)
    job_id = start(api, business, import_id, "validate").json()["id"]
    db.execute(
        update(Job)
        .where(Job.id == uuid.UUID(job_id))
        .values(run_after=datetime.now(UTC) + timedelta(hours=1))
        .execution_options(**ACROSS_TENANTS)
    )
    assert work(db, storage) is None
    assert job_row(db, job_id).status == "queued"


def test_running_jobs_are_not_taken_again(api, db, business, storage):
    import_id = mapped(api, business)
    job_id = start(api, business, import_id, "validate").json()["id"]
    claimed = jobs.claim_next(db, "worker-a")
    assert str(claimed.id) == job_id and claimed.status == "running" and claimed.attempts == 1
    assert jobs.claim_next(db, "worker-b") is None


# --- permissions are re-checked when the job runs ---------------------------------------


def test_a_job_fails_if_the_requester_lost_their_role_before_it_ran(api, db, business, storage):
    import_id = mapped(api, business)
    start(api, business, import_id, "validate")
    owner = db.scalars(select(User.id).where(User.email == "owner@acme.co.uk")).one()
    viewer_role = db.scalars(select(Role.id).where(Role.code == "viewer")).first()
    db.execute(
        update(OrganizationUser)
        .where(OrganizationUser.user_id == owner)
        .values(role_id=viewer_role)
        .execution_options(**ACROSS_TENANTS)
    )
    ran = work(db, storage)
    assert ran.status == "failed" and ran.error_code == "permission_removed"
    assert import_row(db, import_id).status == "mapped"  # nothing was checked


def test_a_job_fails_if_the_requester_left_the_business(api, db, business, storage):
    import_id = mapped(api, business)
    start(api, business, import_id, "validate")
    owner = db.scalars(select(User.id).where(User.email == "owner@acme.co.uk")).one()
    db.execute(
        OrganizationUser.__table__.delete()
        .where(OrganizationUser.user_id == owner)
        .execution_options(**ACROSS_TENANTS)
    )
    ran = work(db, storage)
    assert ran.status == "failed" and ran.error_code == "access_removed"


# --- unexpected failures retry, then give up ----------------------------------------------


@pytest.fixture
def exploding(monkeypatch):
    from app.core.permissions import Perm

    calls = []

    def boom(ctx):
        calls.append(ctx.job.attempts)
        raise RuntimeError("database fell over")

    monkeypatch.setitem(jobs.HANDLERS, "import.validate", jobs.Handler(boom, Perm.DATA_MANAGE))
    return calls


def test_a_crash_puts_the_job_back_for_a_later_retry_then_fails_it(
    api, db, business, storage, exploding
):
    import_id = mapped(api, business)
    job_id = start(api, business, import_id, "validate").json()["id"]

    first = work(db, storage)
    assert first.status == "queued" and first.attempts == 1
    assert first.error_code == "internal_error" and "database fell over" not in first.error_message
    assert first.run_after > datetime.now(UTC)  # waits before trying again
    assert work(db, storage) is None  # not due yet

    for attempt in (2, 3):
        db.execute(
            update(Job)
            .where(Job.id == uuid.UUID(job_id))
            .values(run_after=datetime.now(UTC) - timedelta(seconds=1))
            .execution_options(**ACROSS_TENANTS)
        )
        ran = work(db, storage)
        assert ran.attempts == attempt
    assert ran.status == "failed" and ran.finished_at is not None
    assert exploding == [1, 2, 3]
    assert "contact support" in ran.error_message


def test_a_failed_job_frees_the_upload_for_a_new_one(api, db, business, storage):
    import_id = mapped(api, business)
    start(api, business, import_id, "import")
    assert work(db, storage).status == "failed"
    assert start(api, business, import_id, "validate").status_code == 202


# --- a worker that died ------------------------------------------------------------------


def test_a_silent_running_job_is_queued_again_and_later_failed_when_out_of_tries(
    api, db, business, storage
):
    import_id = mapped(api, business)
    job_id = start(api, business, import_id, "validate").json()["id"]
    claimed = jobs.claim_next(db, "worker-that-died")
    stale = datetime.now(UTC) + timedelta(seconds=get_settings().job_stale_after_seconds + 5)

    assert jobs.recover_stale(db, now=datetime.now(UTC)) == 0  # still fresh
    assert jobs.recover_stale(db, now=stale) == 1
    row = job_row(db, job_id)
    assert row.status == "queued" and row.error_code == "worker_lost" and row.locked_by is None

    claimed.attempts = 3  # out of tries
    db.execute(
        update(Job)
        .where(Job.id == claimed.id)
        .values(status="running", attempts=3, heartbeat_at=datetime.now(UTC))
        .execution_options(**ACROSS_TENANTS)
    )
    assert jobs.recover_stale(db, now=stale + timedelta(hours=1)) == 1
    row = job_row(db, job_id)
    assert row.status == "failed" and row.finished_at is not None
    assert "interrupted" in row.error_message


def test_progress_keeps_a_running_job_from_looking_stale(api, db, business, storage):
    import_id = mapped(api, business)
    job_id = start(api, business, import_id, "validate").json()["id"]
    claimed = jobs.claim_next(db, "w")
    before = job_row(db, job_id).heartbeat_at
    jobs._progress_reporter(claimed, db, None)(50, 200)
    row = job_row(db, job_id)
    assert row.heartbeat_at > before
    assert (row.progress_done, row.progress_total) == (50, 200)
    out = api.get(f"{ORGS}/{business[0]}/jobs/{job_id}", headers=business[2]["owner"]).json()
    assert out["percent"] == 25


# --- progress from the real services ----------------------------------------------------------


def test_validation_reports_progress_after_each_batch(api, db, business, storage, monkeypatch):
    monkeypatch.setattr(import_validation, "BATCH", 2)
    import_id = mapped(api, business, csv(*ROWS, "01/10/2026,1004,6.00,1.00"))
    seen = []
    from app.db.tenant import tenant_scope

    data_import = import_row(db, import_id)
    owner = db.scalars(select(User).where(User.email == "owner@acme.co.uk")).one()
    with tenant_scope(db, data_import.organization_id):
        import_validation.validate_import(
            db,
            storage,
            jobs.JobTenant(data_import.organization_id, owner),
            data_import.id,
            RequestMeta(),
            progress=lambda done, total: seen.append((done, total)),
        )
    assert seen == [(2, 4), (4, 4)]


# --- big files must use the queue ------------------------------------------------------------


def test_big_files_are_refused_by_the_direct_endpoints(api, business, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_inline_rows", 2)
    import_id = mapped(api, business)  # 3 rows
    headers = business[2]["owner"]
    for action in ("validate", "import"):
        res = api.post(base(business, import_id) + f"/{action}", headers=headers)
        assert res.status_code == 409 and res.json()["error"]["code"] == "use_background_job"
    assert start(api, business, import_id, "validate").status_code == 202


def test_small_files_still_work_directly(api, business):
    import_id = mapped(api, business)
    res = api.post(base(business, import_id) + "/validate", headers=business[2]["owner"])
    assert res.status_code == 200


# --- housekeeping ----------------------------------------------------------------------------


def test_old_finished_jobs_are_pruned_but_recent_and_active_ones_stay(api, db, business, storage):
    old_import = mapped(api, business)
    start(api, business, old_import, "import")
    old_id = work(db, storage).id  # failed
    recent_import = mapped(api, business)
    start(api, business, recent_import, "import")
    recent_id = work(db, storage).id
    queued_import = mapped(api, business)
    queued = uuid.UUID(start(api, business, queued_import, "validate").json()["id"])

    db.execute(
        update(Job)
        .where(Job.id == old_id)
        .values(finished_at=datetime.now(UTC) - timedelta(days=31))
        .execution_options(**ACROSS_TENANTS)
    )
    assert jobs.prune_finished(db) == 1
    left = set(db.scalars(select(Job.id).execution_options(**ACROSS_TENANTS)))
    assert left == {recent_id, queued}


def test_a_job_is_deleted_with_its_business(db):
    from app.models.jobs import Job as J

    assert J.__table__.c.organization_id.foreign_keys.pop().ondelete == "CASCADE"


def test_a_retried_job_shows_no_old_error_while_it_runs_again(
    api, db, business, storage, exploding
):
    import_id = mapped(api, business)
    job_id = start(api, business, import_id, "validate").json()["id"]
    assert work(db, storage).error_code == "internal_error"  # first try crashed
    db.execute(
        update(Job)
        .where(Job.id == uuid.UUID(job_id))
        .values(run_after=datetime.now(UTC) - timedelta(seconds=1))
        .execution_options(**ACROSS_TENANTS)
    )
    claimed = jobs.claim_next(db, "w")
    assert claimed.status == "running" and claimed.attempts == 2
    assert claimed.error_code is None and claimed.error_message is None
