"""Update the sprint version shown on the About page.

Run once per release, for example from the deploy pipeline:

    python frontend/update_sprint_version.py "Sprint 8"
    python frontend/update_sprint_version.py "Sprint 8" --release-date 2026-11-14
    python frontend/update_sprint_version.py "Sprint 8" --dry-run
    python frontend/update_sprint_version.py --show

The About page shows the newest Project_Metadata row, so that row's
sprint_version and release_date are updated. The release date defaults to
today in the program's time zone.

Database settings come from DB_HOST, DB_USER, DB_PASSWORD and DB_NAME,
falling back to the team database.
"""

import argparse
import os
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pymysql


DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "cpsc4910-f26.cobd8enwsupz.us-east-1.rds.amazonaws.com"),
    "user": os.environ.get("DB_USER", "Team10"),
    "password": os.environ.get("DB_PASSWORD"),
    "database": os.environ.get("DB_NAME", "Team10_DB"),
}

MAX_VERSION_LENGTH = 50


def today():
    return datetime.now(ZoneInfo(os.environ.get("APP_TIMEZONE", "America/New_York"))).date()


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Update the sprint version on the About page.")
    parser.add_argument("version", nargs="?", help='New sprint version, e.g. "Sprint 8"')
    parser.add_argument("--release-date", type=date.fromisoformat,
                        help="Release date as YYYY-MM-DD (default: today)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without saving it")
    parser.add_argument("--show", action="store_true",
                        help="Only print the current sprint version and release date")
    args = parser.parse_args(argv)

    if not args.show:
        if args.version is None:
            parser.error("a version is required unless --show is used")
        args.version = " ".join(args.version.split())
        if not args.version:
            parser.error("the version can't be blank")
        if len(args.version) > MAX_VERSION_LENGTH:
            parser.error(f"the version must be {MAX_VERSION_LENGTH} characters or fewer")

    return args


def main(argv=None, connect=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    connect = connect or (lambda: pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor))

    conn = connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT metadata_id, sprint_version, release_date
                FROM Project_Metadata
                ORDER BY metadata_id DESC
                LIMIT 1
                FOR UPDATE
                """
            )
            current = cursor.fetchone()

            if not current:
                print("No Project_Metadata row exists yet. Add one before running this script.", file=sys.stderr)
                return 1

            print(f"Current: {current['sprint_version']} (released {current['release_date']})")

            if args.show:
                return 0

            release_date = (args.release_date or today()).isoformat()

            if (current["sprint_version"] == args.version
                    and str(current["release_date"]) == release_date):
                print("Already up to date.")
                return 0

            if args.dry_run:
                print(f"Would set: {args.version} (released {release_date}). Nothing was saved.")
                return 0

            cursor.execute(
                """
                UPDATE Project_Metadata
                SET sprint_version = %s,
                    release_date = %s
                WHERE metadata_id = %s
                """,
                (args.version, release_date, current["metadata_id"])
            )

        conn.commit()
        print(f"Updated: {args.version} (released {release_date})")
        return 0

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
