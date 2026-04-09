"""
Database initialization routine for Datavia.

Checks whether the schema already exists (cross-backend) and runs init.sql
if the tables are absent.  Works with both SQLite and PostgreSQL.
"""

import logging
from pathlib import Path

from sqlalchemy import inspect, text

from .connection import get_engine

logger = logging.getLogger(__name__)


def initialize_database() -> None:
    """Run init.sql to create schema tables when they do not yet exist.

    Uses :func:`sqlalchemy.inspect` for a cross-backend table presence check
    so the function works transparently with both SQLite and PostgreSQL without
    relying on any PostGIS- or PostgreSQL-specific SQL.
    """
    init_sql_path = Path(__file__).parent / "init.sql"
    if not init_sql_path.exists():
        logger.error("init.sql not found in database directory.")
        return

    engine = get_engine()
    if inspect(engine).has_table("raster_layers"):
        logger.info("Database already initialized.")
        return

    logger.info("Initializing database schema from init.sql...")
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
    logger.info("Database initialized.")
