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

# Foreign keys whose ON DELETE behaviour changed after the initial schema.
FK_REDEFINITIONS: list[tuple[str, str, str, str, str]] = [
    # (table, constraint-ish column, referenced table, referenced column, on_delete)
    ("render_job_clip_results", "movie_slot_id", "movie_slots", "id", "SET NULL"),
]


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


def ensure_foreign_key_actions(engine: Engine) -> None:
    """Re-point foreign keys whose ON DELETE action changed.

    MySQL only; SQLite builds its schema fresh from the models in tests.
    Dropping and recreating a constraint is not idempotent by name, so the
    existing constraint is looked up first.
    """
    if engine.dialect.name != "mysql":
        return

    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, column, ref_table, ref_column, on_delete in FK_REDEFINITIONS:
            if not inspector.has_table(table):
                continue
            for fk in inspector.get_foreign_keys(table):
                if fk.get("constrained_columns") != [column]:
                    continue
                if (fk.get("options") or {}).get("ondelete", "").upper() == on_delete:
                    break
                name = fk.get("name")
                if name:
                    logger.info("re-pointing FK %s.%s -> ON DELETE %s", table, column, on_delete)
                    connection.execute(text(f"ALTER TABLE {table} DROP FOREIGN KEY {name}"))
                    connection.execute(
                        text(
                            f"ALTER TABLE {table} ADD CONSTRAINT {name} "
                            f"FOREIGN KEY ({column}) REFERENCES {ref_table}({ref_column}) "
                            f"ON DELETE {on_delete}"
                        )
                    )
                break
