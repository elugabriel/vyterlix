"""Housekeeping for uploaded files.

    python -m app.cli.uploads purge            # delete originals older than 90 days
    python -m app.cli.uploads purge --dry-run  # say what would be deleted

The retention period is VYTERLIX_UPLOAD_RETENTION_DAYS (default 90). Imported records
are never touched; only the original files and the raw row copies go.
"""

import argparse
import sys

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.services.storage import get_file_storage
from app.services.uploads import purge_expired_files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli.uploads")
    commands = parser.add_subparsers(dest="command", required=True)
    purge = commands.add_parser("purge", help="delete uploaded files past retention")
    purge.add_argument("--dry-run", action="store_true", help="report only, delete nothing")
    args = parser.parse_args(argv)

    days = get_settings().upload_retention_days
    with get_sessionmaker()() as db:
        report = purge_expired_files(db, get_file_storage(), dry_run=args.dry_run)
        if not args.dry_run:
            db.commit()
    if args.dry_run:
        print(
            f"Would purge {report.expired} upload(s) older than {days} days "
            f"({report.files_deleted} file(s) on disk)."
        )
    else:
        print(
            f"Purged {report.expired} upload(s) older than {days} days "
            f"({report.files_deleted} file(s) deleted)."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
