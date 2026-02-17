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

_ENGINE: Engine | None = None
_SESSIONMAKER: sessionmaker | None = None


def _build_engine() -> Engine:
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


def get_engine() -> Engine:
    """Get or create the SQLAlchemy engine lazily."""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = _build_engine()
    return _ENGINE


def get_sessionmaker() -> sessionmaker:
    """Get or create the SQLAlchemy sessionmaker lazily."""
    global _SESSIONMAKER
    if _SESSIONMAKER is None:
        _SESSIONMAKER = sessionmaker(
            autocommit=False, autoflush=False, bind=get_engine()
        )
    return _SESSIONMAKER


def SessionLocal():
    """Return a new SQLAlchemy session from the lazy sessionmaker."""
    return get_sessionmaker()()
