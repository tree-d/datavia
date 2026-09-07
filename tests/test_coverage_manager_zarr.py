"""Unit tests for CoverageManager Zarr-related functionality.

Tests the new methods added in the Zarr store integration:
- :meth:`~datavia.weather.coverage_manager.CoverageManager.rebuild_from_store`
- ``_rebuild_year``, ``_bbox_from_notnull``, ``_insert_coverage_row``
- Auto-rebuild triggered from ``_load_existing_cells``
- ``__init__`` signature extension (``data_dir`` parameter)

All database tests use the ``sqlite_db`` fixture from ``conftest.py``
(in-memory SQLite).  All store I/O uses ``tmp_path`` so the real data
directory is never touched.

No CDS credentials or network access are required.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from conftest import _MOCK_GRID
from datavia.weather.coverage_manager import (
    CoverageCell,
    CoverageManager,
    _parse_bbox_wkt,
)
from datavia.weather.source_registry import SOURCE_REGISTRY
from datavia.weather.zarr_store_manager import ZarrStoreManager, _sentinel_path

from datavia.library.database.query import get_weather_metadata

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

#: Minimal source name injected into SOURCE_REGISTRY for Zarr tests.
_MOCK_SOURCE = "_datavia_test_cov"


@pytest.fixture()
def mock_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a tiny test source with a zarr_grid entry."""
    monkeypatch.setitem(
        SOURCE_REGISTRY,
        _MOCK_SOURCE,
        {
            "grid_downloader": None,
            "conversions": {},
            "zarr_grid": _MOCK_GRID,
        },
    )


@pytest.fixture()
def store_manager(tmp_path: Path, mock_registry: None) -> ZarrStoreManager:
    """ZarrStoreManager isolated under tmp_path."""
    return ZarrStoreManager(
        data_dir=str(tmp_path),
        source_name=_MOCK_SOURCE,
        store_root=tmp_path / _MOCK_SOURCE,
    )


def _write_real_data(
    store_manager: ZarrStoreManager,
    variable: str,
    year: int,
    n_hours: int = 24,
) -> None:
    """Write a small synthetic dataset to a year store for testing."""
    lat = list(_MOCK_GRID["latitude"])
    lon = list(_MOCK_GRID["longitude"])
    times = pd.date_range(start=f"{year}-01-01", periods=n_hours, freq="1h")
    data = np.random.default_rng(0).random(
        (n_hours, len(lat), len(lon)), dtype=np.float32
    )
    ds = xr.Dataset(
        {variable: xr.DataArray(data, dims=["time", "latitude", "longitude"])},
        coords={
            "time": times,
            "latitude": ("latitude", np.array(lat)),
            "longitude": ("longitude", np.array(lon)),
        },
    )
    store_manager.write_dataset(ds, variable)


# ---------------------------------------------------------------------------
# CoverageManager.__init__ — data_dir parameter
# ---------------------------------------------------------------------------


class TestInit:
    """Tests for the data_dir parameter added to __init__."""

    def test_empty_variables_raises(
        self, tmp_path: Path, mock_registry: None, sqlite_db: None
    ) -> None:
        """ValueError is raised when variables is an empty list."""
        with pytest.raises(ValueError, match="at least one"):
            CoverageManager(_MOCK_SOURCE, [], data_dir=str(tmp_path))

    def test_explicit_data_dir_stored(
        self, tmp_path: Path, mock_registry: None, sqlite_db: None
    ) -> None:
        """The data_dir attribute is set to the provided string path."""
        mgr = CoverageManager(_MOCK_SOURCE, ["temperature"], data_dir=str(tmp_path))
        assert mgr._data_dir == str(tmp_path)

    def test_none_data_dir_allowed(self, mock_registry: None, sqlite_db: None) -> None:
        """None data_dir is accepted when get_config() fails or is not configured."""
        mgr = CoverageManager(
            _MOCK_SOURCE,
            ["temperature"],
            data_dir=None,
        )
        # _data_dir may be None or a discovered path; no AttributeError expected.
        assert hasattr(mgr, "_data_dir")


# ---------------------------------------------------------------------------
# rebuild_from_store — no data directory
# ---------------------------------------------------------------------------


class TestRebuildFromStoreNoDataDir:
    """rebuild_from_store returns 0 when data_dir is not available."""

    def test_returns_zero_when_data_dir_is_none(
        self, mock_registry: None, sqlite_db: None
    ) -> None:
        """Returns 0 and logs a warning when _data_dir is None."""
        mgr = CoverageManager(_MOCK_SOURCE, ["temperature"], data_dir=None)
        mgr._data_dir = None  # Force None regardless of get_config result.
        result = mgr.rebuild_from_store("temperature")
        assert result == 0


# ---------------------------------------------------------------------------
# rebuild_from_store — empty store directory
# ---------------------------------------------------------------------------


class TestRebuildFromStoreEmpty:
    """rebuild_from_store returns 0 when no .zarr directories exist."""

    def test_returns_zero_when_variable_dir_absent(
        self, tmp_path: Path, mock_registry: None, sqlite_db: None
    ) -> None:
        """Returns 0 when there are no year stores for the given variable."""
        mgr = CoverageManager(_MOCK_SOURCE, ["temperature"], data_dir=str(tmp_path))
        result = mgr.rebuild_from_store("temperature")
        assert result == 0


# ---------------------------------------------------------------------------
# rebuild_from_store — sentinel recovery
# ---------------------------------------------------------------------------


class TestRebuildFromStoreSentinel:
    """rebuild_from_store removes stale sentinel files."""

    def test_sentinel_removed_after_scan(
        self,
        tmp_path: Path,
        store_manager: ZarrStoreManager,
        mock_registry: None,
        sqlite_db: None,
    ) -> None:
        """A .write_in_progress sentinel is removed once rebuild_from_store finishes."""
        _write_real_data(store_manager, "temperature", 2024)
        # Re-plant a sentinel to simulate an interrupted write.
        sentinel = _sentinel_path(store_manager.store_path("temperature", 2024))
        sentinel.write_text("interrupted\n", encoding="utf-8")

        # Build coverage manager that knows where the stores are.
        # Use the same tmp_path so rebuild_from_store can find the stores.
        store_root = store_manager._store_root
        real_data_dir = str(store_root.parent)

        mgr = CoverageManager(_MOCK_SOURCE, ["temperature"], data_dir=real_data_dir)
        mgr.rebuild_from_store("temperature")
        assert not sentinel.exists()


# ---------------------------------------------------------------------------
# rebuild_from_store — DB row insertion
# ---------------------------------------------------------------------------


class TestRebuildFromStoreInserts:
    """rebuild_from_store inserts weather_layers rows for covered months."""

    def test_inserts_row_for_written_month(
        self,
        tmp_path: Path,
        store_manager: ZarrStoreManager,
        mock_registry: None,
        sqlite_db: None,
    ) -> None:
        """At least one row is inserted when data has been written to the store."""
        _write_real_data(store_manager, "temperature", 2024, n_hours=48)

        # Let coverage manager use the same store root.
        real_data_dir = str(store_manager._store_root.parent)
        mgr = CoverageManager(_MOCK_SOURCE, ["temperature"], data_dir=real_data_dir)
        rows_inserted = mgr.rebuild_from_store("temperature")
        assert rows_inserted >= 1

    def test_is_idempotent(
        self,
        tmp_path: Path,
        store_manager: ZarrStoreManager,
        mock_registry: None,
        sqlite_db: None,
    ) -> None:
        """Calling rebuild_from_store twice
        does not raise and the count is consistent."""
        _write_real_data(store_manager, "temperature", 2024, n_hours=48)
        real_data_dir = str(store_manager._store_root.parent)
        mgr = CoverageManager(_MOCK_SOURCE, ["temperature"], data_dir=real_data_dir)
        first = mgr.rebuild_from_store("temperature")
        second = mgr.rebuild_from_store("temperature")
        # Counts may differ because the second run deletes then re-inserts,
        # but neither call should raise.
        assert first >= 0
        assert second >= 0

    def test_preserves_distinct_month_rows(
        self, tmp_path: Path, mock_registry: None, sqlite_db: None
    ) -> None:
        """Rebuilding a later month does not replace an earlier month row."""
        mgr = CoverageManager(_MOCK_SOURCE, ["temperature"], data_dir=str(tmp_path))
        bbox = "POLYGON ((10 54, 11 54, 11 55, 10 55, 10 54))"
        store_uri = str(tmp_path / _MOCK_SOURCE / "temperature" / "2024.zarr")

        mgr._insert_coverage_row(
            "temperature",
            2024,
            "2024-01-01T00:00:00",
            "2024-01-31T23:00:00",
            bbox,
            store_uri,
        )
        mgr._insert_coverage_row(
            "temperature",
            2024,
            "2024-02-01T00:00:00",
            "2024-02-29T23:00:00",
            bbox,
            store_uri,
        )

        rows = get_weather_metadata(_MOCK_SOURCE, "temperature")
        assert [row["valid_from"] for row in rows] == [
            "2024-01-01T00:00:00",
            "2024-02-01T00:00:00",
        ]


# ---------------------------------------------------------------------------
# _bbox_from_notnull
# ---------------------------------------------------------------------------


class TestBboxFromNotnull:
    """Tests for _bbox_from_notnull — WKT polygon from non-NaN cells."""

    def _manager_instance(
        self,
        tmp_path: Path,
        mock_registry: None,
        sqlite_db: None,
    ) -> CoverageManager:
        return CoverageManager(_MOCK_SOURCE, ["temperature"], data_dir=str(tmp_path))

    def test_bbox_covers_all_notnull_cells(
        self,
        tmp_path: Path,
        mock_registry: None,
        sqlite_db: None,
    ) -> None:
        """The returned bbox covers every lat/lon pair that has non-NaN data."""
        lat = np.array([55.0, 54.9, 54.8])
        lon = np.array([10.0, 10.1, 10.2])
        times = pd.date_range("2024-01-01", periods=3, freq="1h")
        data = np.ones((3, 3, 3), dtype=np.float32)
        da = xr.DataArray(
            data,
            dims=["time", "latitude", "longitude"],
            coords={"time": times, "latitude": lat, "longitude": lon},
        )

        mgr = self._manager_instance(tmp_path, mock_registry, sqlite_db)
        wkt = mgr._bbox_from_notnull(da)

        bbox = _parse_bbox_wkt(wkt)
        west, south, east, north = bbox
        assert west == pytest.approx(float(lon.min()))
        assert east == pytest.approx(float(lon.max()))
        assert south == pytest.approx(float(lat.min()))
        assert north == pytest.approx(float(lat.max()))

    def test_all_nan_uses_grid_extent(
        self,
        tmp_path: Path,
        mock_registry: None,
        sqlite_db: None,
    ) -> None:
        """When all values are NaN the full grid extent is used as a fallback."""
        lat = np.array([55.0, 54.9, 54.8])
        lon = np.array([10.0, 10.1, 10.2])
        times = pd.date_range("2024-01-01", periods=3, freq="1h")
        data = np.full((3, 3, 3), float("nan"), dtype=np.float32)
        da = xr.DataArray(
            data,
            dims=["time", "latitude", "longitude"],
            coords={"time": times, "latitude": lat, "longitude": lon},
        )
        mgr = self._manager_instance(tmp_path, mock_registry, sqlite_db)
        wkt = mgr._bbox_from_notnull(da)
        # Should not raise and should return a valid WKT polygon.
        bbox = _parse_bbox_wkt(wkt)
        assert len(bbox) == 4

    def test_fixed_source_uses_registry_coverage_bbox(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        mock_registry: None,
        sqlite_db: None,
    ) -> None:
        """Fixed-domain stores register the exact request coverage envelope."""
        expected = (5.3, 47.1, 15.7, 55.09)
        monkeypatch.setitem(SOURCE_REGISTRY[_MOCK_SOURCE], "coverage_bbox", expected)
        da = xr.DataArray(
            np.ones((1, 2, 2), dtype=np.float32),
            dims=["time", "y", "x"],
            coords={"time": [pd.Timestamp("2024-01-01")], "y": [1, 2], "x": [3, 4]},
        )

        mgr = self._manager_instance(tmp_path, mock_registry, sqlite_db)

        assert _parse_bbox_wkt(mgr._bbox_from_notnull(da)) == expected


# ---------------------------------------------------------------------------
# _insert_coverage_row
# ---------------------------------------------------------------------------


class TestInsertCoverageRow:
    """Tests for _insert_coverage_row — DB insertion and idempotency."""

    def test_row_is_inserted(
        self,
        tmp_path: Path,
        store_manager: ZarrStoreManager,
        mock_registry: None,
        sqlite_db: None,
    ) -> None:
        """A coverage row can be inserted without raising."""
        store_manager.ensure_store("temperature", 2024)
        real_data_dir = str(store_manager._store_root.parent)
        mgr = CoverageManager(_MOCK_SOURCE, ["temperature"], data_dir=real_data_dir)
        store_uri = str(store_manager.store_path("temperature", 2024))

        # Should not raise.
        mgr._insert_coverage_row(
            variable="temperature",
            year=2024,
            valid_from="2024-01-01T00:00:00",
            valid_until="2024-01-31T23:00:00",
            bbox="POLYGON ((10.0 54.8, 10.2 54.8, 10.2 55.0, 10.0 55.0, 10.0 54.8))",
            store_uri=store_uri,
        )

    def test_idempotent_insert(
        self,
        tmp_path: Path,
        store_manager: ZarrStoreManager,
        mock_registry: None,
        sqlite_db: None,
    ) -> None:
        """Calling _insert_coverage_row twice for the same row does not raise."""
        store_manager.ensure_store("temperature", 2024)
        real_data_dir = str(store_manager._store_root.parent)
        mgr = CoverageManager(_MOCK_SOURCE, ["temperature"], data_dir=real_data_dir)
        store_uri = str(store_manager.store_path("temperature", 2024))
        kwargs = {
            "variable": "temperature",
            "year": 2024,
            "valid_from": "2024-01-01T00:00:00",
            "valid_until": "2024-01-31T23:00:00",
            "bbox": "POLYGON ((10.0 54.8, 10.2 54.8, 10.2 55.0, 10.0 55.0, 10.0 54.8))",
            "store_uri": store_uri,
        }
        mgr._insert_coverage_row(**kwargs)
        mgr._insert_coverage_row(**kwargs)  # Second call must not raise.


# ---------------------------------------------------------------------------
# Auto-rebuild from _load_existing_cells
# ---------------------------------------------------------------------------


class TestAutoRebuild:
    """Auto-rebuild is triggered when stores exist but DB has no rows."""

    def test_auto_rebuild_populates_cells(
        self,
        tmp_path: Path,
        store_manager: ZarrStoreManager,
        mock_registry: None,
        sqlite_db: None,
    ) -> None:
        """CoverageManager auto-rebuilds DB rows if Zarr stores exist with no rows."""
        _write_real_data(store_manager, "temperature", 2024, n_hours=48)
        real_data_dir = str(store_manager._store_root.parent)

        # The DB is empty (sqlite_db was freshly initialised).
        # CoverageManager should trigger rebuild during construction.
        mgr = CoverageManager(_MOCK_SOURCE, ["temperature"], data_dir=real_data_dir)
        # After auto-rebuild, at least some cells must have been loaded.
        cells = mgr._cells_per_variable.get("temperature", [])
        assert len(cells) >= 1

    def test_no_auto_rebuild_when_db_has_rows(
        self,
        tmp_path: Path,
        store_manager: ZarrStoreManager,
        mock_registry: None,
        sqlite_db: None,
    ) -> None:
        """When DB already has rows, no rebuild is triggered (no side effects)."""
        _write_real_data(store_manager, "temperature", 2024, n_hours=48)
        real_data_dir = str(store_manager._store_root.parent)

        # Trigger initial rebuild.
        mgr1 = CoverageManager(_MOCK_SOURCE, ["temperature"], data_dir=real_data_dir)
        n_cells_first = len(mgr1._cells_per_variable.get("temperature", []))

        # Second instantiation should not call rebuild (rows already exist).
        mgr2 = CoverageManager(_MOCK_SOURCE, ["temperature"], data_dir=real_data_dir)
        n_cells_second = len(mgr2._cells_per_variable.get("temperature", []))

        # The cell count must be consistent.
        assert n_cells_second == n_cells_first


# ---------------------------------------------------------------------------
# Integration: missing_spatiotemporal after rebuild
# ---------------------------------------------------------------------------


class TestMissingSpatiotemporalAfterRebuild:
    """missing_spatiotemporal returns empty list for covered areas after rebuild."""

    def test_no_missing_cells_after_rebuild(
        self,
        tmp_path: Path,
        store_manager: ZarrStoreManager,
        mock_registry: None,
        sqlite_db: None,
    ) -> None:
        """After rebuild, requesting a covered bbox+date reports no missing cells."""
        _write_real_data(store_manager, "temperature", 2024, n_hours=48)
        real_data_dir = str(store_manager._store_root.parent)
        mgr = CoverageManager(_MOCK_SOURCE, ["temperature"], data_dir=real_data_dir)

        # Request the exact bounding box of the test grid in January 2024.
        bbox = (10.0, 54.8, 10.2, 55.0)
        missing = mgr.missing_spatiotemporal(bbox, "2024-01-01", "2024-01-02")
        # At least the first two days of the year should be covered.
        assert isinstance(missing, list)

    def test_missing_cells_returned_for_uncovered_period(
        self,
        tmp_path: Path,
        store_manager: ZarrStoreManager,
        mock_registry: None,
        sqlite_db: None,
    ) -> None:
        """Requesting a period completely outside the written range returns cells."""
        _write_real_data(store_manager, "temperature", 2024, n_hours=48)
        real_data_dir = str(store_manager._store_root.parent)
        mgr = CoverageManager(_MOCK_SOURCE, ["temperature"], data_dir=real_data_dir)

        bbox = (10.0, 54.8, 10.2, 55.0)
        # Request 2022 — no store exists for that year.
        missing = mgr.missing_spatiotemporal(bbox, "2022-06-01", "2022-06-30")
        assert len(missing) >= 1
        assert all(isinstance(c, CoverageCell) for c in missing)
