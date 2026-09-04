from __future__ import annotations

import logging
import signal
import time

from backend.app.database import SessionLocal
from backend.app.logging_config import configure_logging
from backend.app.services.render_worker import process_next_job, requeue_stale_jobs


# Explicit, not __name__: this module is executed as `python -m
# backend.app.worker`, which makes __name__ "__main__". That logger sits
# outside the configured "backend" tree and inherits root's WARNING level, so
# every worker INFO line was being dropped.
logger = logging.getLogger("backend.app.worker")

IDLE_SLEEP_SECONDS = 2
ERROR_BACKOFF_SECONDS = 5
STARTUP_RETRY_SECONDS = 3

_shutdown = False


def _install_signal_handlers() -> None:
    """Finish the job in hand before exiting.

    `docker stop` sends SIGTERM; without this the default handler kills the
    process mid-render, orphaning the job in "running" until the stale sweep
    reclaims it.
    """

    def _request_shutdown(signum, _frame):
        global _shutdown
        logger.info("received signal %s; finishing current job then exiting", signum)
        _shutdown = True

    for received in (signal.SIGTERM, signal.SIGINT):
        signal.signal(received, _request_shutdown)


def _recover_stale_jobs(attempts: int = 10) -> None:
    """Sweep abandoned jobs, tolerating a database that is not up yet.

    In a container the worker can win the race against MySQL accepting
    connections even when it waits on the API's healthcheck, and an exception
    here would kill the process before the main loop ever starts.
    """
    for attempt in range(1, attempts + 1):
        try:
            with SessionLocal() as db:
                recovered = requeue_stale_jobs(db)
            if recovered:
                logger.info("requeued %d stale job(s) at startup", recovered)
            return
        except Exception:
            if attempt == attempts:
                logger.exception("could not sweep stale jobs; continuing anyway")
                return
            logger.warning(
                "database not ready (attempt %d/%d); retrying", attempt, attempts
            )
            time.sleep(STARTUP_RETRY_SECONDS)


def main() -> None:
    configure_logging()
    logger.info("render worker starting")
    _install_signal_handlers()
    _recover_stale_jobs()

    while not _shutdown:
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

        if processed is None and not _shutdown:
            time.sleep(IDLE_SLEEP_SECONDS)

    logger.info("render worker stopped")


if __name__ == "__main__":
    main()
