"""
Database initialization routine for Datavia.

Creates any missing tables and indexes from the SQLAlchemy Core schema in
``schema.py``.  Works with both SQLite and PostgreSQL.
"""

import logging

from .connection import get_engine
from .schema import metadata

logger = logging.getLogger(__name__)


def initialize_database() -> None:
    """Create all schema tables and indexes that do not yet exist.

    ``MetaData.create_all(checkfirst=True)`` skips objects that already exist,
    so this function is idempotent and safe to call on every application
    start.  Tables added in later versions (e.g. ``weather_layers``) are still
    created in existing databases.

    Works transparently with both SQLite and PostgreSQL; SQLAlchemy renders the
    dialect-specific DDL (e.g. auto-generated integer primary keys).  Errors
    are logged and re-raised so a broken schema fails at startup.
    """
    engine = get_engine()
    logger.debug("Creating missing schema objects (idempotent)...")
    try:
        metadata.create_all(engine, checkfirst=True)
    except Exception:
        logger.exception("Failed to initialize database schema.")
        raise
    logger.info("Database schema up to date.")
