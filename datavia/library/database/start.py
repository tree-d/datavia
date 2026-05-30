"""
Database initialization routine for Datavia.

Checks whether the schema already exists (cross-backend) and runs init.sql
if the tables are absent.  Works with both SQLite and PostgreSQL.
"""

import logging
from pathlib import Path

from sqlalchemy import text

from .connection import get_engine

logger = logging.getLogger(__name__)


def initialize_database() -> None:
    """Apply init.sql to ensure all schema tables exist.

    Every statement in ``init.sql`` uses ``CREATE TABLE IF NOT EXISTS`` /
    ``CREATE INDEX IF NOT EXISTS``, so this function is fully idempotent and
    safe to call on every application start.  It no longer skips execution
    when ``raster_layers`` already exists; that old guard prevented new tables
    added in later versions (e.g. ``weather_layers``) from being created in
    existing databases.

    Works transparently with both SQLite and PostgreSQL without relying on any
    PostGIS- or PostgreSQL-specific SQL.
    """
    init_sql_path = Path(__file__).parent / "init.sql"
    if not init_sql_path.exists():
        logger.error("init.sql not found in database directory.")
        return

    engine = get_engine()
    logger.debug("Applying init.sql schema (idempotent)...")
    with open(init_sql_path) as f:
        sql = f.read()

    with engine.connect() as conn:
        for statement in sql.split(";"):
            stmt = statement.strip()
            if stmt:
                try:
                    conn.execute(text(stmt))
                except Exception as e:
                    logger.error("Error executing statement: %s\n%s", stmt, e)
        conn.commit()
    logger.info("Database schema up to date.")
