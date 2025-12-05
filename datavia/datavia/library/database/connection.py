"""
Unified PostgreSQL+PostGIS connection management for Datavia.

Provides engine and session for all components (Fetcher, Processor, Getter).
Reads config from datavia/config.py.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import logging
from ...config import get_config

logger = logging.getLogger(__name__)

config = get_config()
DATABASE_URL = config.database_url

engine_kwargs = {}
if config.database_config.get("connection_pooling", "true").lower() == "true":
    engine_kwargs.update(
        {
            "pool_size": 10,
            "max_overflow": 20,
            "pool_pre_ping": True,
            "pool_recycle": 3600,
        }
    )

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
