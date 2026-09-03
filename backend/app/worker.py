from __future__ import annotations

import logging
import time

from backend.app.database import SessionLocal
from backend.app.logging_config import configure_logging
from backend.app.services.render_worker import process_next_job, requeue_stale_jobs


logger = logging.getLogger(__name__)

IDLE_SLEEP_SECONDS = 2
ERROR_BACKOFF_SECONDS = 5


def main() -> None:
    configure_logging()
    logger.info("render worker starting")

    with SessionLocal() as db:
        recovered = requeue_stale_jobs(db)
    if recovered:
        logger.info("requeued %d stale job(s) at startup", recovered)

    while True:
        try:
            with SessionLocal() as db:
                processed = process_next_job(db)
        except KeyboardInterrupt:
            logger.info("render worker stopping")
            return
        except Exception:
            # The job SELECT and the claiming UPDATE sit outside
            # process_next_job's own try block, so a database blip here used to
            # kill the worker process outright.
            logger.exception("worker loop failed; backing off")
            time.sleep(ERROR_BACKOFF_SECONDS)
            continue

        if processed is None:
            time.sleep(IDLE_SLEEP_SECONDS)


if __name__ == "__main__":
    main()
