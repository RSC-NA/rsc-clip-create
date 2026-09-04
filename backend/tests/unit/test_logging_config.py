"""The worker runs as `python -m backend.app.worker`, so its module __name__ is
"__main__". That must still reach a handler."""

import logging

from backend.app.logging_config import configure_logging


def test_backend_loggers_emit_info():
    configure_logging()
    logger = logging.getLogger("backend.app.worker")
    assert logger.isEnabledFor(logging.INFO)
    assert logger.handlers or logger.parent.handlers


def test_main_logger_is_configured():
    """Regression: worker INFO lines were silently dropped because __main__
    fell through to root, which sits at WARNING."""
    configure_logging()
    logger = logging.getLogger("__main__")
    assert logger.isEnabledFor(logging.INFO)
    assert logger.handlers, "__main__ must have its own handler"


def test_root_stays_quiet_so_third_party_libraries_do_not_flood():
    configure_logging()
    assert logging.getLogger().level == logging.WARNING
