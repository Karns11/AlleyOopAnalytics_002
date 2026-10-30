"""Long-running ingestion loop for the Docker `ingest` service.

On start: full backfill if the database has never been loaded, otherwise an
incremental run if the last successful ingest is stale. Then sleeps until
INGEST_HOUR_UTC each day and runs an incremental ingest.
"""
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from app import config
from app.db import connect, init_db
from ingest import ingest

log = logging.getLogger("scheduler")

INGEST_HOUR_UTC = int(os.getenv("INGEST_HOUR_UTC", "9"))  # 09:00 UTC = 5am ET, after West Coast games
STALE_AFTER = timedelta(hours=20)
RETRY_AFTER_FAILURE = timedelta(hours=1)


def last_success() -> datetime | None:
    conn = connect()
    try:
        init_db(conn)
        row = conn.execute("SELECT value FROM meta WHERE key = 'last_successful_ingest'").fetchone()
        return datetime.fromisoformat(row[0]) if row else None
    finally:
        conn.close()


def next_run_at(now: datetime) -> datetime:
    run = now.replace(hour=INGEST_HOUR_UTC, minute=0, second=0, microsecond=0)
    return run if run > now else run + timedelta(days=1)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("Scheduler started; daily incremental ingest at %02d:00 UTC; DB at %s", INGEST_HOUR_UTC, config.DB_PATH)

    last = last_success()
    if last is None:
        log.info("No data yet: running full backfill")
        ok = ingest.main(["--mode", "full", "--resume"]) == 0
    elif datetime.now(timezone.utc) - last > STALE_AFTER:
        log.info("Last successful ingest %s is stale: running incremental", last.isoformat())
        ok = ingest.main(["--mode", "incremental"]) == 0
    else:
        ok = True

    while True:
        now = datetime.now(timezone.utc)
        wake = now + RETRY_AFTER_FAILURE if not ok else next_run_at(now)
        log.info("Next ingest at %s", wake.isoformat())
        time.sleep(max(1, (wake - now).total_seconds()))
        mode = "full" if last_success() is None else "incremental"
        args = ["--mode", mode] + (["--resume"] if mode == "full" else [])
        ok = ingest.main(args) == 0


if __name__ == "__main__":
    main()
