"""Shared pytest fixtures for the Datavia test suite.

Provides a lightweight in-memory SQLite database fixture that replaces the
former Docker/PostGIS setup for unit and integration tests.
"""

import numpy as np
import pytest

from datavia.config import get_config
from datavia.library.database.connection import reset_engine
from datavia.library.database.start import initialize_database

#: Tiny 3x3 geographic grid shared across Zarr-related test modules.
#: Mirrors the ERA5-Land EPSG:4326 structure but at minimal size so store
#: creation and writes complete in milliseconds.
_MOCK_GRID: dict = {
    "latitude": np.array([55.0, 54.9, 54.8], dtype=float),
    "longitude": np.array([10.0, 10.1, 10.2], dtype=float),
    "time_freq": "1h",
    "dtype": "float32",
    "fill_value": float("nan"),
    "chunks": {"time": 24, "latitude": 3, "longitude": 3},
    "codec": {"cname": "zstd", "clevel": 3, "shuffle": "shuffle"},
}


@pytest.fixture
def sqlite_db():
    """Provide a clean in-memory SQLite database for each test.

    Configures the global ``DataviaConfig`` singleton to use
    ``sqlite:///:memory:`` and resets the SQLAlchemy engine so it rebuilds
    with the new URL.  The schema is initialised from ``init.sql`` before the
    test body runs.  After the test completes the engine is disposed and the
    injected URL is removed, restoring the config to its previous state.

    Yields
    ------
    None
        The caller receives nothing; the database is implicitly available
        through the module-level connection helpers
        (``get_engine()``, ``session_local()``, etc.).
    """
    config = get_config()
    # Inject the in-memory SQLite URL directly into the live config object so
    # the engine rebuilds with it on the next access.
    config.config["database"]["url"] = "sqlite:///:memory:"
    reset_engine()
    initialize_database()

    yield

    # Teardown: dispose the in-memory engine and restore config state.
    reset_engine()
    config.config.remove_option("database", "url")
