from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def ensure_media_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    additions = {
        "clips": {"team": "VARCHAR(255)", "player": "VARCHAR(255)", "tier": "VARCHAR(255)", "extension": "VARCHAR(16)"},
        "videos": {"duration_seconds": "FLOAT", "metadata_json": "JSON"},
    }
    with engine.begin() as connection:
        for table, columns in additions.items():
            existing = {column["name"] for column in inspector.get_columns(table)}
            for column, sql_type in columns.items():
                if column not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
