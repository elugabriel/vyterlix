"""The background worker: runs queued jobs (checking, importing and undoing big files).

    python -m app.cli.worker run             # keep running until Ctrl+C
    python -m app.cli.worker run --once      # run what is waiting, then stop
    python -m app.cli.worker status          # how many jobs are in each state
    python -m app.cli.worker prune           # delete finished jobs older than 30 days

Start one next to the web server. Several can run at once; they never take the same job.
Ctrl+C lets the current job finish first (a second Ctrl+C stops at once; a job cut off like
that is picked up again automatically, and imports are all-or-nothing so nothing is half done).
"""

import argparse
import logging
import os
import signal
import socket
import sys
import time

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.db.tenant import ACROSS_TENANTS
from app.models.jobs import Job
from app.services import job_handlers  # noqa: F401  (registers the handlers)
from app.services.jobs import prune_finished, work_once

logger = logging.getLogger("vyterlix.worker")


def run(*, once: bool, poll_seconds: float) -> int:
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    sessions = get_sessionmaker()
    stopping = False

    def _stop(signum, frame):
        nonlocal stopping
        if stopping:
            raise KeyboardInterrupt
        stopping = True
        print("Finishing the current job, then stopping (Ctrl+C again to stop now)...")

    signal.signal(signal.SIGINT, _stop)
    print(f"Worker {worker_id} started. Waiting for jobs...")
    done = 0
    while not stopping:
        with sessions() as db:
            job = work_once(db, worker_id, own_session=sessions)
        if job is not None:
            done += 1
            print(
                f"{job.kind} {job.id}: {job.status}"
                + (f" ({job.error_message})" if job.error_message else "")
            )
            continue  # look for more straight away
        if once:
            break
        time.sleep(poll_seconds)
    print(f"Worker stopped after {done} job(s).")
    return 0


def status() -> int:
    with get_sessionmaker()() as db:
        rows = db.execute(
            select(Job.status, func.count())
            .group_by(Job.status)
            .execution_options(**ACROSS_TENANTS)
        ).all()
    counts = dict(rows)
    for name in ("queued", "running", "succeeded", "failed"):
        print(f"{name:10} {counts.get(name, 0)}")
    return 0


def prune() -> int:
    with get_sessionmaker()() as db:
        removed = prune_finished(db)
    print(f"Deleted {removed} finished job(s) older than {get_settings().job_keep_days} days.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli.worker")
    commands = parser.add_subparsers(dest="command", required=True)
    runner = commands.add_parser("run", help="run queued jobs")
    runner.add_argument("--once", action="store_true", help="stop when nothing is waiting")
    runner.add_argument("--poll", type=float, default=2.0, help="seconds between checks when idle")
    commands.add_parser("status", help="count jobs by state")
    commands.add_parser("prune", help="delete old finished jobs")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if args.command == "run":
        return run(once=args.once, poll_seconds=args.poll)
    return status() if args.command == "status" else prune()


if __name__ == "__main__":
    sys.exit(main())
