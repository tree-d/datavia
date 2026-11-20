"""End-to-end tests for the WeatherPipeline.

These tests exercise real remote services:

- **DWD Climate Data Center** (no auth required): downloads the daily
  observations of three stations as Parquet, registers them in the database,
  and performs a point interpolation query.  Kriging a month over Germany
  (Mojo) is gated behind ``DATAVIA_E2E_SLOW=1``.
- **ERA5 via Copernicus CDS** (requires ``~/.cdsapirc``): downloads a small
  NetCDF file for a one-day period; skipped automatically when credentials
  are absent.
- **HYRAS via DWD OpenData** (no auth required): the fast test only fetches
  the DWD HTML directory listing to verify version auto-discovery; the slow
  full-file download is gated behind ``DATAVIA_E2E_SLOW=1`` because each
  annual NetCDF is 20-110 MB.
- **HYRAS via DWD OpenData** (no auth required): tests version auto-discovery
  against the live DWD directory listing; the full file download is gated
  behind ``DATAVIA_E2E_SLOW=1`` because each annual NetCDF is 20-110 MB.

The tests are skipped unless the environment variable ``DATAVIA_E2E`` is set
to ``"1"`` so they are excluded from the standard unit-test run::

    DATAVIA_E2E=1 pytest tests/test_weather_e2e.py -v

No Docker container is required; the database is initialised in-memory
automatically before each test module run.
"""

import logging
import math
import os
from datetime import date, timedelta

import numpy as np
import pytest

from datavia.config import get_config
from datavia.library.database.connection import reset_engine
from datavia.library.database.start import initialize_database

logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Skip guards
# ---------------------------------------------------------------------------

_E2E_GUARD = pytest.mark.skipif(
    os.getenv("DATAVIA_E2E") != "1",
    reason="DATAVIA_E2E not set; skipping weather pipeline end-to-end tests",
)

_ERA5_GUARD = pytest.mark.skipif(
    os.getenv("DATAVIA_E2E") != "1"
    or not os.path.isfile(os.path.expanduser("~/.cdsapirc")),
    reason=(
        "DATAVIA_E2E not set or ~/.cdsapirc missing; skipping ERA5 end-to-end tests"
    ),
)

_SLOW_GUARD = pytest.mark.skipif(
    os.getenv("DATAVIA_E2E_SLOW") != "1",
    reason=(
        "DATAVIA_E2E_SLOW not set; skipping large-file download tests "
        "(set DATAVIA_E2E_SLOW=1 to enable, expect several hundred MB)"
    ),
)

# ---------------------------------------------------------------------------
# Dates and coordinates
# ---------------------------------------------------------------------------

# Use a short recent window that the DWD has published (up to yesterday).
# Stay 3-6 days before today to avoid gaps in ERA5 (which lags by ~5 days).
_DATE_END: date = date.today() - timedelta(days=3)
_DATE_START: date = _DATE_END - timedelta(days=2)

# Berlin - a well-covered point for both ERA5 and DWD stations.
_BERLIN_LAT: float = 52.52
_BERLIN_LON: float = 13.41

# ERA5 uses a much smaller bounding box around Berlin to keep download size low.
_BERLIN_SMALL_BBOX: list[float] = [53.0, 13.0, 52.0, 14.0]  # N, W, S, E

# Mid-day UTC timestamp within the download window.
_QUERY_DATETIME: str = f"{_DATE_START.isoformat()}T12:00:00"

# Plausible temperature range for Germany (°C), used as a sanity check.
_TEMP_MIN_C: float = -25.0
_TEMP_MAX_C: float = 50.0

# Variable name shared by ERA5 and the DWD stations.
_VARIABLE: str = "2m_temperature"


# ---------------------------------------------------------------------------
# Shared module fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_database():
    """Set up a clean in-memory SQLite database for the whole module.

    Injects ``sqlite:///:memory:`` into the global config, resets the engine,
    and initialises the full schema (including ``weather_layers``).  Cleans up
    after all tests in the module have finished.

    Yields
    ------
    None
        The database is available implicitly through the module-level
        connection helpers (``get_engine()``, ``session_local()``, etc.).
    """
    config = get_config()
    config.config["database"]["url"] = "sqlite:///:memory:"
    reset_engine()
    initialize_database()

    yield

    reset_engine()
    config.config.remove_option("database", "url")


# ---------------------------------------------------------------------------
# DWD Climate Data Center E2E
# ---------------------------------------------------------------------------

#: DWD station ids: Berlin-Tempelhof, Frankfurt/Main, Koeln/Bonn.
_CDC_STATIONS: list[str] = ["00433", "01420", "02667"]


@_E2E_GUARD
class TestDWDStationE2E:
    """End-to-end tests for the daily DWD station observations (CDC, no auth)."""

    def _download(self, tmp_path) -> str:
        from datavia.weather.dwd_downloader import DWDStationDownloader

        downloader = DWDStationDownloader(
            variables=[_VARIABLE],
            date_start=str(_DATE_START),
            date_end=str(_DATE_END),
            stations=_CDC_STATIONS,
            data_dir=tmp_path,
        )
        return downloader.download()

    def test_dwd_download_produces_parquet(self, live_database, tmp_path) -> None:
        """DWDStationDownloader.download() writes one row per station and day."""
        import pandas as pd

        output_path = self._download(tmp_path)
        try:
            df = pd.read_parquet(output_path)
            assert set(df["station_id"]) == set(_CDC_STATIONS)
            assert df[_VARIABLE].between(_TEMP_MIN_C, _TEMP_MAX_C).all()
            assert (df["datetime"].dt.hour == 0).all()
        finally:
            if os.path.isfile(output_path):
                os.remove(output_path)

    def test_dwd_point_query_returns_realistic_temperature(
        self, live_database, tmp_path
    ) -> None:
        """download -> save -> daily point query at Berlin gives a finite value."""
        from datavia.weather.getter_weather import GetterWeather
        from datavia.weather.saver_weather import SaverWeather

        raw_path = self._download(tmp_path)
        try:
            saver = SaverWeather.__new__(SaverWeather)
            saver.source_name = "dwd_stations"
            saver.data_dir = str(tmp_path)
            assert saver.save(raw_path) is True

            value = GetterWeather("dwd_stations").get_weather_data(
                lat=_BERLIN_LAT,
                lon=_BERLIN_LON,
                variable=_VARIABLE,
                datetime_utc=_QUERY_DATETIME,
            )
            assert math.isfinite(value)
            assert _TEMP_MIN_C <= value <= _TEMP_MAX_C
        finally:
            if os.path.isfile(raw_path):
                os.remove(raw_path)


@_E2E_GUARD
@_SLOW_GUARD
class TestDWDKrigingE2E:
    """Kriging one month of real station data over Germany (needs Mojo)."""

    def test_kriged_month_is_plausible(self, tmp_path) -> None:
        """A month of daily mean temperature kriged onto the 1 km grid."""
        pytest.importorskip("datavia.kriging")
        import pandas as pd
        from datavia.weather.dwd_grid import grid_elevation
        from datavia.weather.dwd_kriging_downloader import (
            KRIGING_SETTINGS,
            DWDKrigingDownloader,
            _cells,
        )

        month = pd.Period(_DATE_END - timedelta(days=40), "M")
        downloader = DWDKrigingDownloader(
            variables=[_VARIABLE],
            data_dir=tmp_path,
            date_start=str(month.start_time.date()),
            date_end=str(month.end_time.date()),
        )
        elevation = grid_elevation(tmp_path / "DWD_stations" / "_grid")
        land = np.isfinite(elevation)
        data = downloader.client.station_data(
            _VARIABLE, month.start_time.date(), month.end_time.date()
        )
        block, cached = downloader._krige_month(
            _VARIABLE,
            month,
            data,
            _cells(land, elevation),
            land,
            KRIGING_SETTINGS[_VARIABLE],
        )
        assert data.n_stations > 300
        assert np.isfinite(block[:, land]).mean() > 0.99
        assert np.nanmin(block) > _TEMP_MIN_C and np.nanmax(block) < _TEMP_MAX_C
        assert cached.path.exists()


# ---------------------------------------------------------------------------
# ERA5 / Copernicus CDS E2E  (requires ~/.cdsapirc)
# ---------------------------------------------------------------------------


@_ERA5_GUARD
class TestERA5E2E:
    """End-to-end tests for the ERA5 path (requires Copernicus CDS credentials)."""

    def test_era5_download_produces_netcdf(self, live_database, tmp_path) -> None:
        """ERA5Downloader.download() fetches a valid NetCDF for a one-day window.

        Uses a small bounding box around Berlin to keep the download size
        manageable.  Asserts the file is non-empty and that xarray can open it.
        """
        import xarray as xr
        from datavia.weather.era5_downloader import ERA5Downloader

        one_day = str(_DATE_START)
        downloader = ERA5Downloader(
            variables=["2m_temperature"],
            date_start=one_day,
            date_end=one_day,
            bbox=_BERLIN_SMALL_BBOX,
        )
        output_path = downloader.download()

        try:
            assert os.path.getsize(output_path) > 0, "Downloaded NetCDF is empty"
            ds = xr.open_dataset(output_path)
            assert "t2m" in ds or "2m_temperature" in ds, (
                "Expected temperature variable not found; "
                f"variables: {list(ds.data_vars)}"
            )
            ds.close()
        finally:
            if os.path.isfile(output_path):
                os.remove(output_path)

    def test_era5_save_registers_in_database(self, live_database, tmp_path) -> None:
        """SaverWeather registers a downloaded ERA5 NetCDF in weather_layers."""
        from datavia.weather.era5_downloader import ERA5Downloader
        from datavia.weather.saver_weather import SaverWeather

        from datavia.library.database.query import check_weather_source_exists

        one_day = str(_DATE_START)
        downloader = ERA5Downloader(
            variables=["2m_temperature"],
            date_start=one_day,
            date_end=one_day,
            bbox=_BERLIN_SMALL_BBOX,
        )
        raw_path = downloader.download()

        try:
            saver = SaverWeather.__new__(SaverWeather)
            saver.source_name = "era5"
            saver.data_dir = str(tmp_path)

            result = saver.save(raw_path)
            assert result is True, "SaverWeather.save() returned False for ERA5 NetCDF"
            assert check_weather_source_exists("era5"), (
                "No 'era5' row found in weather_layers after save"
            )
        finally:
            if os.path.isfile(raw_path):
                os.remove(raw_path)


# ---------------------------------------------------------------------------
# HYRAS / DWD OpenData E2E
# ---------------------------------------------------------------------------


@_E2E_GUARD
class TestHYRASE2E:
    """End-to-end tests for the HYRAS downloader.

    The fast test only fetches the DWD HTML directory listing to verify
    version auto-discovery — no large file is downloaded.  The slow test
    (gated behind ``DATAVIA_E2E_SLOW=1``) downloads a full annual NetCDF
    (~30 MB for temperature) and opens it with xarray.
    """

    def test_version_discovery_returns_filename(self, live_database) -> None:
        """_discover_latest_filename returns plausible names from live DWD listing.

        Fetches the HTML directory for ``air_temperature_mean`` and checks
        that the returned filename matches the expected pattern.  No large
        file is downloaded.
        """
        import re

        from datavia.weather.hyras_downloader import (
            _HYRAS_BASE_URL,
            _VARIABLE_MAP,
            HYRASDownloader,
        )

        mapping = _VARIABLE_MAP["2m_temperature"]
        subdir_url = f"{_HYRAS_BASE_URL}{mapping['subdir']}/"
        prefix = mapping["prefix"]

        downloader = HYRASDownloader(variables=["2m_temperature"])
        filename = downloader._discover_latest_filename(subdir_url, 2023, prefix)

        pattern = re.compile(rf"{re.escape(prefix)}_2023_v\d+-\d+_de\.nc")
        assert pattern.match(filename), (
            f"Discovered filename '{filename}' does not match expected pattern "
            f"'{pattern.pattern}'."
        )

    @_SLOW_GUARD
    def test_single_variable_year_download_opens_with_xarray(
        self, live_database
    ) -> None:
        """HYRASDownloader downloads one annual NetCDF and xarray can open it.

        Downloads ``2m_temperature`` for 2023 (~30 MB).  Asserts the file is
        non-empty, xarray can open it, and the ``tas`` variable is present
        with the expected ``x`` / ``y`` ETRS89-LAEA dimensions.

        Run with::

            DATAVIA_E2E=1 DATAVIA_E2E_SLOW=1 pytest \\
                tests/test_weather_e2e.py::TestHYRASE2E::
                test_single_variable_year_download_opens_with_xarray -v
        """
        import xarray as xr
        from datavia.weather.hyras_downloader import HYRASDownloader

        downloader = HYRASDownloader(
            variables=["2m_temperature"],
            date_start="2023-01-01",
            date_end="2023-12-31",
        )
        raw_paths_str = downloader.download()

        try:
            assert raw_paths_str, "HYRASDownloader.download() returned an empty string"
            paths = [p for p in raw_paths_str.splitlines() if p]
            assert len(paths) == 1, f"Expected 1 file, got {len(paths)}: {paths}"

            path = paths[0]
            assert os.path.isfile(path), f"Downloaded path does not exist: {path}"
            assert os.path.getsize(path) > 1_000_000, (
                f"File suspiciously small ({os.path.getsize(path)} bytes): {path}"
            )

            ds = xr.open_dataset(path)
            assert "tas" in ds.data_vars, (
                f"Variable 'tas' not found in dataset. Variables: {list(ds.data_vars)}"
            )
            assert "x" in ds.dims and "y" in ds.dims, (
                f"Expected ETRS89-LAEA 'x'/'y' dims. Got: {list(ds.dims)}"
            )
            ds.close()
        finally:
            for p in (raw_paths_str or "").splitlines():
                if p and os.path.isfile(p):
                    os.remove(p)
