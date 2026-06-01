"""Unit tests for ZarrStoreManager.

All tests are isolated from the real data directory via the injectable
``store_root=tmp_path`` parameter.  No CDS credentials or network access
are required.

Covers:
- :meth:`~datavia.weather.zarr_store_manager.ZarrStoreManager.store_path`
- :meth:`~datavia.weather.zarr_store_manager.ZarrStoreManager.ensure_store`
- :meth:`~datavia.weather.zarr_store_manager.ZarrStoreManager.write_dataset`
- :meth:`~datavia.weather.zarr_store_manager.ZarrStoreManager.open_store`
- :meth:`~datavia.weather.zarr_store_manager.ZarrStoreManager.open_multi_year`
- :meth:`~datavia.weather.zarr_store_manager.ZarrStoreManager.delete_store`
- :meth:`~datavia.weather.zarr_store_manager.ZarrStoreManager.list_available_variables`
- :meth:`~datavia.weather.zarr_store_manager.ZarrStoreManager.migrate_nc_file`
- Private helpers: ``_build_time_index``, ``_normalise_time``, ``_select_variable``
- Module-level helpers: :func:`~datavia.weather.zarr_store_manager._snap_coords`,
  :func:`~datavia.weather.zarr_store_manager._check_sentinel`
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from conftest import _MOCK_GRID
from datavia.weather.source_registry import SOURCE_REGISTRY
from datavia.weather.zarr_store_manager import (
    _SENTINEL,
    ZarrStoreManager,
    _check_sentinel,
    _snap_coords,
)

# ---------------------------------------------------------------------------
# Shared test fixtures and helpers
# ---------------------------------------------------------------------------

#: Minimal source name injected into SOURCE_REGISTRY for every test.
_MOCK_SOURCE = "_datavia_test_source"


@pytest.fixture()
def mock_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a tiny test source into SOURCE_REGISTRY for the duration of a test."""
    monkeypatch.setitem(
        SOURCE_REGISTRY,
        _MOCK_SOURCE,
        {
            "grid_downloader": None,
            "conversions": {},
            "nc_variable_map": {"t_raw": "temperature"},
            "zarr_grid": _MOCK_GRID,
        },
    )


@pytest.fixture()
def manager(tmp_path: Path, mock_registry: None) -> ZarrStoreManager:
    """Return a ZarrStoreManager pointing at an isolated temporary directory."""
    return ZarrStoreManager(
        data_dir=str(tmp_path),
        source_name=_MOCK_SOURCE,
        store_root=tmp_path / _MOCK_SOURCE,
    )


def _make_synthetic_dataset(
    variable: str = "temperature",
    year: int = 2024,
    n_hours: int = 48,
    lat: list[float] | None = None,
    lon: list[float] | None = None,
    add_nans: bool = False,
) -> xr.Dataset:
    """Build a minimal synthetic Dataset shaped like a weather download.

    Parameters
    ----------
    variable : str
        Variable name used as the data-variable key in the dataset.
    year : int
        Starting year for the time axis.
    n_hours : int
        Number of hourly time steps starting from ``{year}-01-01 00:00``.
    lat : list[float] or None
        Latitude values.  Defaults to the mock grid latitudes.
    lon : list[float] or None
        Longitude values.  Defaults to the mock grid longitudes.
    add_nans : bool
        When ``True`` fill the first half of the time axis with NaN so that
        covered-bbox tests can distinguish written from missing regions.

    Returns
    -------
    xr.Dataset
        Dataset with a single float32 data variable and UTC-naive time axis.
    """
    if lat is None:
        lat = list(_MOCK_GRID["latitude"])
    if lon is None:
        lon = list(_MOCK_GRID["longitude"])

    times = pd.date_range(start=f"{year}-01-01", periods=n_hours, freq="1h")
    data = np.random.default_rng(42).random(
        (n_hours, len(lat), len(lon)), dtype=np.float32
    )
    if add_nans:
        data[: n_hours // 2, :, :] = float("nan")

    return xr.Dataset(
        {variable: xr.DataArray(data, dims=["time", "latitude", "longitude"])},
        coords={
            "time": times,
            "latitude": ("latitude", np.array(lat, dtype=float)),
            "longitude": ("longitude", np.array(lon, dtype=float)),
        },
    )


# ---------------------------------------------------------------------------
# ZarrStoreManager construction
# ---------------------------------------------------------------------------


class TestConstruction:
    """Test ZarrStoreManager constructor validation."""

    def test_unknown_source_raises(self, tmp_path: Path) -> None:
        """KeyError is raised when the source is not in SOURCE_REGISTRY."""
        with pytest.raises(KeyError, match="not registered"):
            ZarrStoreManager(str(tmp_path), "does_not_exist")

    def test_source_without_zarr_grid_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """KeyError is raised when the source has no zarr_grid entry."""
        monkeypatch.setitem(
            SOURCE_REGISTRY,
            "_no_grid",
            {"grid_downloader": None, "conversions": {}},
        )
        with pytest.raises(KeyError, match="zarr_grid"):
            ZarrStoreManager(str(tmp_path), "_no_grid")


# ---------------------------------------------------------------------------
# store_path
# ---------------------------------------------------------------------------


class TestStorePath:
    """Verify the path layout produced by store_path."""

    def test_default_root(self, tmp_path: Path, mock_registry: None) -> None:
        """Path follows <data_dir>/<source_name>/<variable>/<year>.zarr."""
        mgr = ZarrStoreManager(str(tmp_path), _MOCK_SOURCE)
        path = mgr.store_path("temperature", 2024)
        assert path == tmp_path / _MOCK_SOURCE / "temperature" / "2024.zarr"

    def test_override_root(self, manager: ZarrStoreManager, tmp_path: Path) -> None:
        """When store_root is provided it replaces the data_dir/source_name prefix."""
        path = manager.store_path("temperature", 2024)
        assert str(path).endswith("temperature/2024.zarr")
        # store_root is tmp_path/_datavia_test_source
        assert str(path).startswith(str(tmp_path))


# ---------------------------------------------------------------------------
# ensure_store
# ---------------------------------------------------------------------------


class TestEnsureStore:
    """Tests for skeleton Zarr store creation."""

    def test_creates_store_directory(self, manager: ZarrStoreManager) -> None:
        """ensure_store creates the store directory on disk."""
        manager.ensure_store("temperature", 2024)
        assert manager.store_path("temperature", 2024).exists()

    def test_store_has_coordinate_arrays(self, manager: ZarrStoreManager) -> None:
        """Opened store contains time, latitude, longitude coordinates."""
        manager.ensure_store("temperature", 2024)
        ds = xr.open_zarr(
            str(manager.store_path("temperature", 2024)), consolidated=False
        )
        try:
            assert "time" in ds.coords
            assert "latitude" in ds.coords
            assert "longitude" in ds.coords
        finally:
            ds.close()

    def test_is_idempotent(self, manager: ZarrStoreManager) -> None:
        """Calling ensure_store twice does not raise and the store is unchanged."""
        manager.ensure_store("temperature", 2024)
        mtime_before = manager.store_path("temperature", 2024).stat().st_mtime
        manager.ensure_store("temperature", 2024)
        mtime_after = manager.store_path("temperature", 2024).stat().st_mtime
        assert mtime_before == mtime_after

    def test_unwritten_cells_return_nan(self, manager: ZarrStoreManager) -> None:
        """All data values in a freshly created store are NaN (fill_value)."""
        manager.ensure_store("temperature", 2024)
        ds = xr.open_zarr(
            str(manager.store_path("temperature", 2024)), consolidated=False
        )
        try:
            # Select a small slice to avoid loading the full year.
            sample = ds["temperature"].isel(time=slice(0, 2)).values
            assert np.all(np.isnan(sample))
        finally:
            ds.close()

    def test_time_axis_length_non_leap_year(self, manager: ZarrStoreManager) -> None:
        """Non-leap year 2023 has 8760 hourly steps."""
        manager.ensure_store("temperature", 2023)
        ds = xr.open_zarr(
            str(manager.store_path("temperature", 2023)), consolidated=False
        )
        try:
            assert ds.sizes["time"] == 8760
        finally:
            ds.close()

    def test_time_axis_length_leap_year(self, manager: ZarrStoreManager) -> None:
        """Leap year 2024 has 8784 hourly steps."""
        manager.ensure_store("temperature", 2024)
        ds = xr.open_zarr(
            str(manager.store_path("temperature", 2024)), consolidated=False
        )
        try:
            assert ds.sizes["time"] == 8784
        finally:
            ds.close()


# ---------------------------------------------------------------------------
# write_dataset
# ---------------------------------------------------------------------------


class TestWriteDataset:
    """Tests for write_dataset — writing and reading back data."""

    def test_roundtrip_values(self, manager: ZarrStoreManager) -> None:
        """Values written to the store can be read back with the same shape."""
        ds = _make_synthetic_dataset("temperature", year=2024, n_hours=24)
        manager.write_dataset(ds, "temperature")

        result = manager.open_store("temperature", 2024)
        try:
            written = result["temperature"].sel(time=slice("2024-01-01", "2024-01-01"))
            assert written.sizes["time"] == 24
        finally:
            result.close()

    def test_sentinel_removed_after_successful_write(
        self, manager: ZarrStoreManager
    ) -> None:
        """The .write_in_progress sentinel is absent after a clean write."""
        ds = _make_synthetic_dataset("temperature", year=2024, n_hours=1)
        manager.write_dataset(ds, "temperature")
        sentinel = manager.store_path("temperature", 2024) / _SENTINEL
        assert not sentinel.exists()

    def test_valid_time_coord_is_normalised(self, manager: ZarrStoreManager) -> None:
        """Datasets with a ``valid_time`` coordinate are accepted and normalised."""
        ds = _make_synthetic_dataset("temperature", year=2024, n_hours=6)
        ds = ds.rename({"time": "valid_time"})
        manager.write_dataset(ds, "temperature")
        # If normalisation succeeded the write does not raise and the store exists.
        assert manager.store_path("temperature", 2024).exists()

    def test_nc_variable_map_rename(self, manager: ZarrStoreManager) -> None:
        """ERA5-style short name (t_raw) is mapped
        to the pipeline name (temperature)."""
        ds = _make_synthetic_dataset("t_raw", year=2024, n_hours=6)
        manager.write_dataset(ds, "temperature")
        store = manager.open_store("temperature", 2024)
        try:
            assert "temperature" in store.data_vars
        finally:
            store.close()

    def test_dataset_spanning_two_years(self, manager: ZarrStoreManager) -> None:
        """A dataset crossing a year boundary writes to two separate stores."""
        times = pd.date_range("2023-12-31 20:00", periods=8, freq="1h")
        lat = list(_MOCK_GRID["latitude"])
        lon = list(_MOCK_GRID["longitude"])
        data = np.ones((8, len(lat), len(lon)), dtype=np.float32)
        ds = xr.Dataset(
            {"temperature": xr.DataArray(data, dims=["time", "latitude", "longitude"])},
            coords={"time": times, "latitude": lat, "longitude": lon},
        )
        manager.write_dataset(ds, "temperature")
        assert manager.store_path("temperature", 2023).exists()
        assert manager.store_path("temperature", 2024).exists()

    def test_out_of_tolerance_coordinate_raises(
        self, manager: ZarrStoreManager
    ) -> None:
        """A coordinate deviating more than half a grid cell raises ValueError."""
        ds = _make_synthetic_dataset("temperature", year=2024, n_hours=2, lat=[55.099])
        with pytest.raises(ValueError, match="Latitude"):
            manager.write_dataset(ds, "temperature")

    def test_unknown_variable_raises(self, manager: ZarrStoreManager) -> None:
        """KeyError is raised when the variable cannot be found in the dataset."""
        ds = _make_synthetic_dataset("other_var", year=2024, n_hours=2)
        with pytest.raises(KeyError):
            manager.write_dataset(ds, "temperature")


# ---------------------------------------------------------------------------
# open_store
# ---------------------------------------------------------------------------


class TestOpenStore:
    """Tests for open_store error paths."""

    def test_missing_store_raises_file_not_found(
        self, manager: ZarrStoreManager
    ) -> None:
        """FileNotFoundError is raised when the store directory does not exist."""
        with pytest.raises(FileNotFoundError):
            manager.open_store("temperature", 9999)

    def test_sentinel_raises_runtime_error(self, manager: ZarrStoreManager) -> None:
        """RuntimeError is raised when a .write_in_progress sentinel is present."""
        manager.ensure_store("temperature", 2024)
        (manager.store_path("temperature", 2024) / _SENTINEL).write_text(
            "interrupted\n", encoding="utf-8"
        )
        with pytest.raises(RuntimeError, match=_SENTINEL):
            manager.open_store("temperature", 2024)


# ---------------------------------------------------------------------------
# open_multi_year
# ---------------------------------------------------------------------------


class TestOpenMultiYear:
    """Tests for multi-year concatenation."""

    def test_skips_missing_years(self, manager: ZarrStoreManager) -> None:
        """Years without a store on disk are silently skipped."""
        manager.ensure_store("temperature", 2024)
        ds = manager.open_multi_year("temperature", [2023, 2024])
        try:
            assert ds.sizes["time"] == 8784  # Only 2024 (leap year)
        finally:
            ds.close()

    def test_skips_sentinel_year(self, manager: ZarrStoreManager) -> None:
        """Years whose store has an active sentinel are silently skipped."""
        manager.ensure_store("temperature", 2023)
        manager.ensure_store("temperature", 2024)
        (manager.store_path("temperature", 2023) / _SENTINEL).write_text(
            "interrupted\n", encoding="utf-8"
        )
        ds = manager.open_multi_year("temperature", [2023, 2024])
        try:
            # Only 2024 is clean; 8784 steps expected.
            assert ds.sizes["time"] == 8784
        finally:
            ds.close()

    def test_raises_when_no_stores_available(self, manager: ZarrStoreManager) -> None:
        """FileNotFoundError is raised when all requested years are missing."""
        with pytest.raises(FileNotFoundError):
            manager.open_multi_year("temperature", [2020, 2021])

    def test_two_complete_years(self, manager: ZarrStoreManager) -> None:
        """Two complete stores concatenate without alignment errors."""
        manager.ensure_store("temperature", 2022)
        manager.ensure_store("temperature", 2023)
        ds = manager.open_multi_year("temperature", [2022, 2023])
        try:
            # 2022: 8760 h, 2023: 8760 h
            assert ds.sizes["time"] == 17520
        finally:
            ds.close()


# ---------------------------------------------------------------------------
# delete_store
# ---------------------------------------------------------------------------


class TestDeleteStore:
    """Tests for filesystem deletion via delete_store."""

    def test_confirmed_deletes_directory(self, manager: ZarrStoreManager) -> None:
        """Confirmed delete removes the store directory from disk."""
        manager.ensure_store("temperature", 2024)
        assert manager.store_path("temperature", 2024).exists()
        manager.delete_store("temperature", 2024, confirmed=True)
        assert not manager.store_path("temperature", 2024).exists()

    def test_unconfirmed_is_noop(self, manager: ZarrStoreManager) -> None:
        """Unconfirmed delete leaves the store directory untouched."""
        manager.ensure_store("temperature", 2024)
        manager.delete_store("temperature", 2024, confirmed=False)
        assert manager.store_path("temperature", 2024).exists()

    def test_missing_store_raises_on_confirmed(self, manager: ZarrStoreManager) -> None:
        """FileNotFoundError is raised when deleting a non-existent store."""
        with pytest.raises(FileNotFoundError):
            manager.delete_store("temperature", 9999, confirmed=True)


# ---------------------------------------------------------------------------
# list_available_variables
# ---------------------------------------------------------------------------


class TestListAvailableVariables:
    """Tests for listing variables with at least one store on disk."""

    def test_empty_when_no_stores(self, manager: ZarrStoreManager) -> None:
        """Returns an empty set when no stores have been created yet."""
        assert manager.list_available_variables() == set()

    def test_returns_variable_after_ensure_store(
        self, manager: ZarrStoreManager
    ) -> None:
        """Variable appears after its first store is created."""
        manager.ensure_store("temperature", 2024)
        assert "temperature" in manager.list_available_variables()

    def test_two_variables_listed_independently(
        self, manager: ZarrStoreManager
    ) -> None:
        """Two distinct variables are both returned."""
        manager.ensure_store("temperature", 2024)
        manager.ensure_store("humidity", 2024)
        variables = manager.list_available_variables()
        assert {"temperature", "humidity"} <= variables


# ---------------------------------------------------------------------------
# migrate_nc_file
# ---------------------------------------------------------------------------


class TestMigrateNcFile:
    """Tests for NetCDF → Zarr migration."""

    def test_existing_nc_file_is_imported(
        self, manager: ZarrStoreManager, tmp_path: Path
    ) -> None:
        """A NetCDF file written to disk is successfully read and imported."""
        nc_path = tmp_path / "synthetic.nc"
        ds = _make_synthetic_dataset("temperature", year=2024, n_hours=3)
        ds.to_netcdf(str(nc_path))

        manager.migrate_nc_file(str(nc_path), "temperature")
        assert manager.store_path("temperature", 2024).exists()


# ---------------------------------------------------------------------------
# _build_time_index
# ---------------------------------------------------------------------------


class TestBuildTimeIndex:
    """Tests for the private _build_time_index helper."""

    def test_hourly_non_leap_year(self, manager: ZarrStoreManager) -> None:
        """Non-leap year produces 8760-step hourly index."""
        idx = manager._build_time_index(2023)
        assert len(idx) == 8760
        assert idx[0] == pd.Timestamp("2023-01-01 00:00:00")
        assert idx[-1] == pd.Timestamp("2023-12-31 23:00:00")

    def test_hourly_leap_year(self, manager: ZarrStoreManager) -> None:
        """Leap year produces 8784-step hourly index."""
        idx = manager._build_time_index(2024)
        assert len(idx) == 8784

    def test_daily_frequency(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sources with time_freq='1D' produce a daily index."""
        daily_grid = dict(_MOCK_GRID)
        daily_grid["time_freq"] = "1D"
        monkeypatch.setitem(
            SOURCE_REGISTRY,
            "_daily_source",
            {"grid_downloader": None, "conversions": {}, "zarr_grid": daily_grid},
        )
        mgr = ZarrStoreManager(
            str(tmp_path),
            "_daily_source",
            store_root=tmp_path / "_daily_source",
        )
        idx = mgr._build_time_index(2024)
        assert len(idx) == 366  # 2024 is a leap year


# ---------------------------------------------------------------------------
# _normalise_time
# ---------------------------------------------------------------------------


class TestNormaliseTime:
    """Tests for the private _normalise_time helper."""

    def test_renames_valid_time_to_time(self, manager: ZarrStoreManager) -> None:
        """valid_time coordinate is renamed to time."""
        times = pd.date_range("2024-01-01", periods=5, freq="1h")
        ds = xr.Dataset(coords={"valid_time": times})
        result = manager._normalise_time(ds)
        assert "time" in result.coords
        assert "valid_time" not in result.coords

    def test_strips_timezone(self, manager: ZarrStoreManager) -> None:
        """UTC-aware timestamps are converted to UTC-naive datetime64[ns]."""
        times = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")
        ds = xr.Dataset(
            {"x": xr.DataArray(np.zeros(5), dims=["time"])},
            coords={"time": times},
        )
        result = manager._normalise_time(ds)
        assert result.time.values.dtype == np.dtype("datetime64[ns]")


# ---------------------------------------------------------------------------
# _select_variable
# ---------------------------------------------------------------------------


class TestSelectVariable:
    """Tests for the private _select_variable helper."""

    def test_direct_variable_found(self, manager: ZarrStoreManager) -> None:
        """Variable already named as expected is returned unchanged."""
        ds = _make_synthetic_dataset("temperature", year=2024, n_hours=2)
        result = manager._select_variable(ds, "temperature")
        assert "temperature" in result.data_vars

    def test_nc_variable_map_rename(self, manager: ZarrStoreManager) -> None:
        """NC short name (t_raw) is translated to pipeline name (temperature)."""
        ds = _make_synthetic_dataset("t_raw", year=2024, n_hours=2)
        result = manager._select_variable(ds, "temperature")
        assert "temperature" in result.data_vars
        assert "t_raw" not in result.data_vars

    def test_unknown_variable_raises(self, manager: ZarrStoreManager) -> None:
        """KeyError is raised when neither direct nor mapped name is present."""
        ds = _make_synthetic_dataset("other", year=2024, n_hours=2)
        with pytest.raises(KeyError):
            manager._select_variable(ds, "temperature")


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestSnapCoords:
    """Tests for the module-level _snap_coords helper."""

    def test_exact_match(self) -> None:
        """Exact grid values are returned unchanged."""
        grid = np.array([10.0, 10.1, 10.2])
        result = _snap_coords(
            grid.copy(),
            grid,
            step=0.1,
            axis_name="longitude",
            source_name="S",
            variable="v",
            year=2024,
        )
        np.testing.assert_array_almost_equal(result, grid)

    def test_near_tolerance_snaps(self) -> None:
        """Values within half a step are snapped to the nearest grid point."""
        grid = np.array([10.0, 10.1, 10.2])
        values = np.array([10.049, 10.151])  # within tolerance of 10.0 and 10.2
        result = _snap_coords(
            values,
            grid,
            step=0.1,
            axis_name="longitude",
            source_name="S",
            variable="v",
            year=2024,
        )
        assert result[0] == pytest.approx(10.0)
        assert result[1] == pytest.approx(10.2)

    def test_out_of_tolerance_raises(self) -> None:
        """Values more than half a step from any grid point raise ValueError."""
        grid = np.array([10.0, 10.1, 10.2])
        # 10.3 is 0.1° from the nearest grid point (10.2), which exceeds the
        # 0.05° tolerance (half a 0.1° step).
        values = np.array([10.3])
        with pytest.raises(ValueError, match="Longitude"):
            _snap_coords(
                values,
                grid,
                step=0.1,
                axis_name="longitude",
                source_name="S",
                variable="v",
                year=2024,
            )


class TestCheckSentinel:
    """Tests for the module-level _check_sentinel helper."""

    def test_no_sentinel_does_not_raise(self, tmp_path: Path) -> None:
        """A directory without a sentinel passes without error."""
        _check_sentinel(tmp_path)  # must not raise

    def test_sentinel_present_raises(self, tmp_path: Path) -> None:
        """RuntimeError is raised when .write_in_progress is present."""
        (tmp_path / _SENTINEL).write_text("in progress\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match=_SENTINEL):
            _check_sentinel(tmp_path)


# ---------------------------------------------------------------------------
# SaverWeather.save_nc_to_zarr
# ---------------------------------------------------------------------------


class TestSaveNcToZarr:
    """Tests for SaverWeather.save_nc_to_zarr.

    Uses the mock source registry entry that contains an ``nc_variable_map``
    so that CF short-name mapping is exercised as well as the no-mapping path.
    All file I/O is isolated under ``tmp_path``; the real data directory is
    never touched.
    """

    @pytest.fixture()
    def saver(
        self,
        tmp_path: Path,
        mock_registry: None,
        sqlite_db: None,
    ):
        """Return a SaverWeather pointing at tmp_path with the mock source name."""
        from datavia.weather.saver_weather import SaverWeather

        s = SaverWeather.__new__(SaverWeather)
        s.source_name = _MOCK_SOURCE
        s.data_dir = str(tmp_path)
        return s

    def _write_nc(self, tmp_path, variable: str = "t_raw", year: int = 2024) -> str:
        """Write a tiny NetCDF file and return its path.

        Uses the CF short name ``t_raw`` so that ``nc_variable_map``
        (``{"t_raw": "temperature"}``) is exercised by save_nc_to_zarr.
        """
        lat = list(_MOCK_GRID["latitude"])
        lon = list(_MOCK_GRID["longitude"])
        times = pd.date_range(start=f"{year}-01-01", periods=24, freq="1h")
        data = np.ones((24, len(lat), len(lon)), dtype=np.float32)
        ds = xr.Dataset(
            {variable: xr.DataArray(data, dims=["time", "latitude", "longitude"])},
            coords={
                "time": times,
                "latitude": ("latitude", np.array(lat)),
                "longitude": ("longitude", np.array(lon)),
            },
        )
        nc_path = tmp_path / f"{variable}_{year}.nc"
        ds.to_netcdf(str(nc_path))
        return str(nc_path)

    def test_returns_true_on_success(self, saver, tmp_path: Path) -> None:
        """save_nc_to_zarr returns True when the conversion succeeds."""
        nc_path = self._write_nc(tmp_path)
        result = saver.save_nc_to_zarr(nc_path)
        assert result is True

    def test_zarr_store_created(self, saver, tmp_path: Path) -> None:
        """A .zarr directory is written under data_dir/source/variable/year.zarr."""
        nc_path = self._write_nc(tmp_path)
        saver.save_nc_to_zarr(nc_path)
        # nc_variable_map maps "t_raw" → "temperature", so the store name is
        # "temperature".
        expected = tmp_path / _MOCK_SOURCE / "temperature" / "2024.zarr"
        assert expected.is_dir()

    def test_nc_file_not_copied_to_data_dir(self, saver, tmp_path: Path) -> None:
        """No .nc copy is written to data_dir; only the Zarr store is created."""
        nc_path = self._write_nc(tmp_path)
        saver.save_nc_to_zarr(nc_path)
        nc_copies = list(tmp_path.glob("*.nc"))
        # The original temp file is inside tmp_path, so filter it out.
        nc_copies = [p for p in nc_copies if str(p) != nc_path]
        assert nc_copies == [], f"Unexpected NC copies: {nc_copies}"

    def test_maps_cf_name_to_pipeline_name(self, saver, tmp_path: Path) -> None:
        """The store uses the pipeline variable name, not the CF short name."""
        nc_path = self._write_nc(tmp_path, variable="t_raw")
        saver.save_nc_to_zarr(nc_path)
        # Store must be under "temperature" (pipeline name), not "t_raw" (CF).
        assert (tmp_path / _MOCK_SOURCE / "temperature" / "2024.zarr").is_dir()
        assert not (tmp_path / _MOCK_SOURCE / "t_raw").exists()

    def test_no_mapping_uses_variable_name_as_is(self, saver, tmp_path: Path) -> None:
        """Variables not in nc_variable_map are stored under their own name."""
        nc_path = self._write_nc(tmp_path, variable="temperature")
        saver.save_nc_to_zarr(nc_path)
        assert (tmp_path / _MOCK_SOURCE / "temperature" / "2024.zarr").is_dir()

    def test_returns_false_for_unreadable_file(self, saver, tmp_path: Path) -> None:
        """Returns False when the NC file cannot be opened."""
        bad_path = str(tmp_path / "not_a_real_file.nc")
        result = saver.save_nc_to_zarr(bad_path)
        assert result is False
