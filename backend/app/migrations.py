import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


logger = logging.getLogger(__name__)

# Columns added after the initial schema. create_all() only creates missing
# tables, so pre-existing tables need these bolted on at startup.
COLUMN_ADDITIONS: dict[str, dict[str, str]] = {
    "clips": {
        "team": "VARCHAR(255)",
        "player": "VARCHAR(255)",
        "tier": "VARCHAR(255)",
        "extension": "VARCHAR(16)",
    },
    "videos": {"duration_seconds": "FLOAT", "metadata_json": "JSON"},
    "render_jobs": {"heartbeat_at": "DATETIME"},
    "movies": {
        "transition_style": "VARCHAR(32)",
        "transition_hold_seconds": "FLOAT",
    },
}

# Columns whose NOT NULL constraint has since been relaxed.
COLUMN_RELAXATIONS: dict[str, dict[str, str]] = {
    "render_job_clip_results": {"clip_id": "VARCHAR(36)"},
}


def ensure_media_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, columns in COLUMN_ADDITIONS.items():
            if not inspector.has_table(table):
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for column, sql_type in columns.items():
                if column not in existing:
                    logger.info("adding column %s.%s", table, column)
                    connection.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
                    )


def ensure_nullable_columns(engine: Engine) -> None:
    """Relax NOT NULL on columns that later became optional.

    SQLite cannot alter a column in place, but it is only used for tests where
    the schema is built fresh from the models, so skipping it is safe.
    """
    if engine.dialect.name != "mysql":
        return

    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, columns in COLUMN_RELAXATIONS.items():
            if not inspector.has_table(table):
                continue
            current = {column["name"]: column for column in inspector.get_columns(table)}
            for column, sql_type in columns.items():
                existing = current.get(column)
                if existing is not None and not existing["nullable"]:
                    logger.info("relaxing NOT NULL on %s.%s", table, column)
                    connection.execute(
                        text(f"ALTER TABLE {table} MODIFY {column} {sql_type} NULL")
                    )
