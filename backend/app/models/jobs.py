"""Background jobs (Phase 4 step 10): work too slow for a web request, run by a worker.

A job is one row. The worker (`python -m app.cli.worker run`) claims queued rows with
`FOR UPDATE SKIP LOCKED`, so several workers can run side by side and never take the same
job. No Redis or message broker: PostgreSQL is the queue.

- subject_type/subject_id say what the job is about (an import). At most one job per subject
  can be queued or running, which stops a double click from importing a file twice.
- attempts/max_attempts/run_after: an unexpected failure is retried a little later; an
  expected one (for example "check the data before importing") fails straight away.
- heartbeat_at: refreshed as the job reports progress. A running job with an old heartbeat
  belongs to a worker that died, and is put back in the queue (or failed).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.identity import _one_of

JOB_STATUSES = ("queued", "running", "succeeded", "failed")
ACTIVE_JOB_STATUSES = ("queued", "running")


class Job(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(_one_of("status", JOB_STATUSES), name="status_valid"),
        CheckConstraint("kind ~ '^[a-z][a-z_]*\\.[a-z][a-z_]*$'", name="kind_format"),
        CheckConstraint("subject_type ~ '^[a-z][a-z_]*$'", name="subject_type_format"),
        CheckConstraint("progress_done >= 0 AND progress_total >= 0", name="progress_not_negative"),
        CheckConstraint("attempts >= 0 AND max_attempts >= 1", name="attempts_valid"),
        CheckConstraint(
            "status NOT IN ('succeeded', 'failed') OR finished_at IS NOT NULL",
            name="finished_has_time",
        ),
        # One active job per subject: a second click gets "already running", not a second job.
        Index(
            "uq_jobs_one_active_per_subject",
            "subject_type",
            "subject_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        # What the worker scans: the next queued job that is due.
        Index(
            "ix_jobs_queue",
            "run_after",
            postgresql_where=text("status = 'queued'"),
        ),
        Index("ix_jobs_subject", "subject_type", "subject_id", "created_at"),
        Index("ix_jobs_finished", "finished_at"),
    )

    kind: Mapped[str] = mapped_column(String(40))  # e.g. "import.validate"
    status: Mapped[str] = mapped_column(String(10), server_default="queued")
    subject_type: Mapped[str] = mapped_column(String(30))
    subject_id: Mapped[uuid.UUID] = mapped_column()
    # Who asked, for the audit trail and to re-check their permission when the job runs.
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))

    progress_done: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    progress_total: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(String(500))  # plain English, user-facing

    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, server_default=text("3"))
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    locked_by: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
