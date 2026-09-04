import logging.config


def configure_logging(level: str = "INFO") -> None:
    """Install a single console handler for the API and the worker.

    Both entry points call this so a render can be followed end to end without
    reading the database.
    """
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "format": "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "standard",
                    "stream": "ext://sys.stderr",
                }
            },
            "loggers": {
                "backend": {"handlers": ["console"], "level": level, "propagate": False},
                # Entry points run via `python -m` report __name__ as
                # "__main__"; without this their records fall through to root.
                "__main__": {"handlers": ["console"], "level": level, "propagate": False},
                "uvicorn.error": {"handlers": ["console"], "level": level, "propagate": False},
                "uvicorn.access": {"handlers": ["console"], "level": level, "propagate": False},
            },
            "root": {"handlers": ["console"], "level": "WARNING"},
        }
    )
