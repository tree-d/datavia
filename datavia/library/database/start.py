"""
Database initialization routine for Datavia.

Creates any missing tables and indexes from the SQLAlchemy Core schema in
``schema.py``, including indexes added to tables that already exist.  Works
with both SQLite and PostgreSQL.
"""

import logging

from .connection import get_engine
from .schema import metadata

logger = logging.getLogger(__name__)


def initialize_database() -> None:
    """Create all schema tables and indexes that do not yet exist.

    ``MetaData.create_all(checkfirst=True)`` skips tables that already exist,
    together with their indexes, so each index is then created separately
    with ``checkfirst=True``.  The function is idempotent and safe to call on
    every application start; tables and indexes added in later versions are
    still created in existing databases.

    Works transparently with both SQLite and PostgreSQL; SQLAlchemy renders the
    dialect-specific DDL (e.g. auto-generated integer primary keys).  Errors
    are logged and re-raised so a broken schema fails at startup.
    """
    engine = get_engine()
    logger.debug("Creating missing schema objects (idempotent)...")
    try:
        metadata.create_all(engine, checkfirst=True)
        for table in metadata.sorted_tables:
            for index in table.indexes:
                index.create(engine, checkfirst=True)
    except Exception:
        logger.exception("Failed to initialize database schema.")
        raise
    logger.info("Database schema up to date.")
