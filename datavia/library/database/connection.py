"""
Unified PostgreSQL+PostGIS connection management for Datavia.

Provides engine and session for all components (Fetcher, Processor, Getter).
Reads config from datavia/config.py.
"""

import logging
from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker

from ...config import get_config

logger = logging.getLogger(__name__)


class _DatabaseManager:
    """Singleton-like manager for database engine and sessionmaker instances."""

    def __init__(self) -> None:
        self._engine: Engine | None = None
        self._sessionmaker: sessionmaker | None = None

    def _build_engine(self) -> Engine:
        """Build a new SQLAlchemy engine with configuration from config."""
        config = get_config()
        database_url = config.database_url

        engine_kwargs: dict[str, Any] = {}
        if config.database_config.get("connection_pooling", "true").lower() == "true":
            engine_kwargs.update(
                {
                    "pool_size": 10,
                    "max_overflow": 20,
                    "pool_pre_ping": True,
                    "pool_recycle": 3600,
                }
            )

        return create_engine(database_url, **engine_kwargs)

    def get_engine(self) -> Engine:
        """Get or create the SQLAlchemy engine lazily."""
        if self._engine is None:
            self._engine = self._build_engine()
        return self._engine

    def get_sessionmaker(self) -> sessionmaker:
        """Get or create the SQLAlchemy sessionmaker lazily."""
        if self._sessionmaker is None:
            self._sessionmaker = sessionmaker(
                autocommit=False, autoflush=False, bind=self.get_engine()
            )
        return self._sessionmaker

    def reset_engine(self) -> None:
        """Dispose the current engine and clear all cached connection state.

        Forces the next call to :meth:`get_engine` (or
        :meth:`get_sessionmaker`) to build a fresh engine with a new
        connection pool.  Must be called whenever the database server is
        stopped or restarted so that stale pooled connections do not cause
        ``sqlalchemy.exc.OperationalError`` on the next access.
        """
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
        self._sessionmaker = None


# Create module-level singleton instance to replace global variables
_db_manager = _DatabaseManager()


def get_engine() -> Engine:
    """Get or create the SQLAlchemy engine lazily."""
    return _db_manager.get_engine()


def get_sessionmaker() -> sessionmaker:
    """Get or create the SQLAlchemy sessionmaker lazily."""
    return _db_manager.get_sessionmaker()


def reset_engine() -> None:
    """Dispose and clear the cached engine and sessionmaker.

    Forces the next database operation to build a fresh engine with a new
    connection pool.  Call this whenever the database server has been
    stopped or restarted to avoid stale pooled connections causing
    ``sqlalchemy.exc.OperationalError``.
    """
    _db_manager.reset_engine()


def session_local() -> Any:
    """Return a new SQLAlchemy session from the lazy sessionmaker."""
    return get_sessionmaker()()
