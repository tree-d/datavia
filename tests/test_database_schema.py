"""Tests for the SQLAlchemy Core schema and initialize_database()."""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from datavia.library.database import start
from datavia.library.database.connection import get_engine
from datavia.library.database.schema import metadata

EXPECTED_TABLES = {"raster_layers", "raster_band_metadata", "weather_layers"}


def test_postgres_ddl_has_generated_ids() -> None:
    """Every id column must auto-generate on PostgreSQL (no AUTOINCREMENT)."""
    for table in metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert "SERIAL" in ddl or "IDENTITY" in ddl, table.name
        assert "AUTOINCREMENT" not in ddl


def test_initialize_creates_tables_and_indexes(sqlite_db) -> None:
    insp = inspect(get_engine())
    assert EXPECTED_TABLES <= set(insp.get_table_names())
    names = {i["name"] for i in insp.get_indexes("weather_layers")}
    assert "idx_weather_source_variable_time" in names
    assert "idx_weather_source_variable_format" in names


def test_initialize_is_idempotent(sqlite_db) -> None:
    start.initialize_database()


def test_ids_autogenerate_without_explicit_id(sqlite_db) -> None:
    with get_engine().begin() as conn:
        for _ in range(2):
            conn.execute(text("INSERT INTO weather_layers (layer_name) VALUES ('x')"))
        ids = [r[0] for r in conn.execute(text("SELECT id FROM weather_layers"))]
    assert ids == [1, 2]


def test_initialize_propagates_errors(sqlite_db, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(metadata, "create_all", boom)
    with pytest.raises(RuntimeError):
        start.initialize_database()
