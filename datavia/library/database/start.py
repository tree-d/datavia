"""
Database initialization routine for Datavia.

Checks schema and runs init.sql if needed.
"""

import logging
from pathlib import Path

from sqlalchemy import text

from .connection import engine

logger = logging.getLogger(__name__)


def initialize_database() -> None:
    """Run init.sql to set up schema if needed."""
    init_sql_path = Path(__file__).parent / "init.sql"
    if not init_sql_path.exists():
        logger.error("init.sql not found in database directory.")
        return

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        result = conn.execute(text("SELECT to_regclass('public.raster_layers');"))
        exists = result.scalar()
        if not exists:
            logger.info("Initializing database schema from init.sql...")
            with open(init_sql_path) as f:
                sql = f.read()
            # Split SQL by semicolon and execute each statement
            # enable autocommit for DDL statements
            for statement in sql.split(";"):
                stmt = statement.strip()
                if stmt:
                    try:
                        conn.execute(text(stmt))
                    except Exception as e:
                        logger.error(f"Error executing statement: {stmt}\n{e}")
            logger.info("Database initialized.")
        else:
            logger.info("Database already initialized.")
