"""Unit tests for the weather pipeline components.

Covers (all without network access or real files):

- :func:`~datavia.library.interpolation.blend_gridded_and_station` —
  blending logic and boundary conditions.
- :class:`~datavia.weather.saver_weather.SaverWeather` —
  save() DB insert via the ``sqlite_db`` fixture, sync helpers.
- :class:`~datavia.weather.getter_weather.GetterWeather` —
  get_existing_layers(), get_data() dispatch and blending.
- :class:`~datavia.weather.pipeline.WeatherPipeline` —
  __call__ lazy initialisation, update_data() path splitting.
- :class:`~datavia.weather.composite_downloader.CompositeWeatherDownloader` —
  downloaders property and download() delegation.
- :func:`~datavia.library.database.query.check_weather_source_exists` and
  :func:`~datavia.library.database.query.get_weather_paths` — basic DB
  round-trip via the ``sqlite_db`` fixture.
"""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_weather_layer(
    source_name: str,
    layer_name: str,
    variable: str,
    file_format: str,
    valid_from: str,
    valid_until: str,
    uri: str,
) -> None:
    """Insert a single row into ``weather_layers`` for test setup.

    Parameters
    ----------
    source_name : str
    layer_name : str
    variable : str
    file_format : str
    valid_from : str
        ISO-8601 datetime string.
    valid_until : str
        ISO-8601 datetime string.
    uri : str
        Absolute file path (does not need to exist).
    """
    from sqlalchemy import text

    from datavia.library.database.connection import session_local

    session = session_local()
    try:
        session.execute(
            text(
                """
                INSERT INTO weather_layers
                    (layer_name, source_name, variable, file_format,
                     valid_from, valid_until, uri, crs, bbox, metadata)
                VALUES
                    (:layer_name, :source_name, :variable, :file_format,
                     :valid_from, :valid_until, :uri, 'EPSG:4326', NULL, NULL)
                """
            ),
            {
                "layer_name": layer_name,
                "source_name": source_name,
                "variable": variable,
                "file_format": file_format,
                "valid_from": valid_from,
                "valid_until": valid_until,
                "uri": uri,
            },
        )
        session.commit()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# blend_gridded_and_station
# ---------------------------------------------------------------------------


class TestBlendGriddedAndStation:
    """Tests for the blending utility function."""

    def test_pure_station_weight(self) -> None:
        """station_weight=1.0 returns the station value exactly."""
        from datavia.library.interpolation import blend_gridded_and_station

        result = blend_gridded_and_station(10.0, 5.0, station_weight=1.0)
        assert result == pytest.approx(5.0)

    def test_pure_gridded_weight(self) -> None:
        """station_weight=0.0 returns the gridded value exactly."""
        from datavia.library.interpolation import blend_gridded_and_station

        result = blend_gridded_and_station(10.0, 5.0, station_weight=0.0)
        assert result == pytest.approx(10.0)

    def test_equal_blend(self) -> None:
        """station_weight=0.5 returns the arithmetic mean."""
        from datavia.library.interpolation import blend_gridded_and_station

        result = blend_gridded_and_station(10.0, 6.0, station_weight=0.5)
        assert result == pytest.approx(8.0)

    def test_default_weight_favours_station(self) -> None:
        """Default weight (0.6) gives more influence to the station value."""
        from datavia.library.interpolation import blend_gridded_and_station

        gridded, station = 10.0, 5.0
        expected = 0.4 * gridded + 0.6 * station
        assert blend_gridded_and_station(gridded, station) == pytest.approx(expected)

    def test_invalid_weight_raises(self) -> None:
        """Out-of-range station_weight raises ValueError."""
        from datavia.library.interpolation import blend_gridded_and_station

        with pytest.raises(ValueError, match="station_weight"):
            blend_gridded_and_station(1.0, 2.0, station_weight=1.5)

        with pytest.raises(ValueError, match="station_weight"):
            blend_gridded_and_station(1.0, 2.0, station_weight=-0.1)


# ---------------------------------------------------------------------------
# DB query helpers (require sqlite_db fixture)
# ---------------------------------------------------------------------------


class TestWeatherQueryHelpers:
    """Tests for check_weather_source_exists and get_weather_paths."""

    def test_check_source_empty_db(self, sqlite_db: None) -> None:
        """Returns False when no rows exist for the source."""
        from datavia.library.database.query import check_weather_source_exists

        assert check_weather_source_exists("era5") is False

    def test_check_source_after_insert(self, sqlite_db: None) -> None:
        """Returns True when a matching row exists."""
        from datavia.library.database.query import check_weather_source_exists

        _insert_weather_layer(
            source_name="era5",
            layer_name="era5_temperature_2m",
            variable="temperature_2m",
            file_format="netcdf",
            valid_from="2024-01-01T00:00:00",
            valid_until="2024-01-31T23:00:00",
            uri="/data/era5_temperature_2m.nc",
        )
        assert check_weather_source_exists("era5") is True
        assert check_weather_source_exists("dwd_stations") is False

    def test_check_source_with_variable_filter(self, sqlite_db: None) -> None:
        """Variable filter narrows the search correctly."""
        from datavia.library.database.query import check_weather_source_exists

        _insert_weather_layer(
            source_name="era5",
            layer_name="era5_temperature_2m",
            variable="temperature_2m",
            file_format="netcdf",
            valid_from="2024-01-01T00:00:00",
            valid_until="2024-01-31T23:00:00",
            uri="/data/era5_temperature_2m.nc",
        )
        assert check_weather_source_exists("era5", variable="temperature_2m") is True
        assert check_weather_source_exists("era5", variable="precipitation") is False

    def test_get_weather_paths_overlap(self, sqlite_db: None) -> None:
        """Overlapping layers are returned; non-overlapping layers are excluded."""
        from datavia.library.database.query import get_weather_paths

        _insert_weather_layer(
            source_name="era5",
            layer_name="era5_temperature_2m_jan",
            variable="temperature_2m",
            file_format="netcdf",
            valid_from="2024-01-01T00:00:00",
            valid_until="2024-01-31T23:00:00",
            uri="/data/era5_temperature_2m_jan.nc",
        )
        _insert_weather_layer(
            source_name="era5",
            layer_name="era5_temperature_2m_mar",
            variable="temperature_2m",
            file_format="netcdf",
            valid_from="2024-03-01T00:00:00",
            valid_until="2024-03-31T23:00:00",
            uri="/data/era5_temperature_2m_mar.nc",
        )

        # Query for January: only January layer should match.
        paths = get_weather_paths(
            "era5",
            "temperature_2m",
            "2024-01-10T00:00:00",
            "2024-01-20T00:00:00",
        )
        assert len(paths) == 1
        assert "jan" in paths[0]

        # Query spanning February: no match.
        paths_feb = get_weather_paths(
            "era5",
            "temperature_2m",
            "2024-02-01T00:00:00",
            "2024-02-28T00:00:00",
        )
        assert paths_feb == []


# ---------------------------------------------------------------------------
# SaverWeather (requires sqlite_db fixture and a real temp file)
# ---------------------------------------------------------------------------


class TestSaverWeather:
    """Tests for SaverWeather.save() and check_data_exists()."""

    def test_save_netcdf_inserts_db_row(self, sqlite_db: None, tmp_path) -> None:
        """A NetCDF file is copied and a DB row is inserted."""
        from datavia.library.database.query import check_weather_source_exists
        from datavia.weather.saver_weather import SaverWeather

        # Create a dummy .nc file — content irrelevant; only the save() path
        # logic is tested here; metadata extraction is mocked out.
        nc_file = tmp_path / "era5_temperature_2m.nc"
        nc_file.write_bytes(b"FAKE_NC_CONTENT")

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "era5"
        saver.data_dir = str(tmp_path)

        with patch(
            "datavia.weather.saver_weather.extract_netcdf_layer_metadata",
            return_value={
                "valid_from": "2024-01-01T00:00:00",
                "valid_until": "2024-01-31T23:00:00",
                "bbox": "POLYGON ((5.9 47.3, 15.0 47.3, 15.0 55.1, 5.9 55.1, 5.9 47.3))",
                "crs": "EPSG:4326",
            },
        ):
            result = saver.save(str(nc_file))

        assert result is True
        assert check_weather_source_exists("era5") is True

    def test_save_idempotent(self, sqlite_db: None, tmp_path) -> None:
        """Calling save() twice on the same file does not create duplicate rows."""
        from sqlalchemy import text

        from datavia.library.database.connection import session_local
        from datavia.weather.saver_weather import SaverWeather

        nc_file = tmp_path / "era5_temperature_2m.nc"
        nc_file.write_bytes(b"FAKE")

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "era5"
        saver.data_dir = str(tmp_path)

        meta = {
            "valid_from": "2024-01-01T00:00:00",
            "valid_until": "2024-01-31T23:00:00",
            "bbox": None,
            "crs": "EPSG:4326",
        }
        with patch(
            "datavia.weather.saver_weather.extract_netcdf_layer_metadata",
            return_value=meta,
        ):
            saver.save(str(nc_file))
            saver.save(str(nc_file))

        session = session_local()
        count = session.execute(
            text("SELECT COUNT(*) FROM weather_layers WHERE source_name='era5'")
        ).fetchone()[0]
        session.close()
        assert count == 1

    def test_check_data_exists_delegates(self, sqlite_db: None, tmp_path) -> None:
        """check_data_exists returns True when a matching row exists."""
        from datavia.weather.saver_weather import SaverWeather

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "era5"
        saver.data_dir = str(tmp_path)

        _insert_weather_layer(
            source_name="era5",
            layer_name="era5_temperature_2m",
            variable="temperature_2m",
            file_format="netcdf",
            valid_from="2024-01-01T00:00:00",
            valid_until="2024-01-31T23:00:00",
            uri=str(tmp_path / "era5_temperature_2m.nc"),
        )

        assert saver.check_data_exists("temperature_2m") is True
        assert saver.check_data_exists("precipitation") is False


# ---------------------------------------------------------------------------
# GetterWeather (requires sqlite_db fixture; interpolation is mocked)
# ---------------------------------------------------------------------------


class TestGetterWeather:
    """Tests for GetterWeather.get_existing_layers() and get_data()."""

    def test_get_existing_layers_empty(self, sqlite_db: None) -> None:
        """Returns an empty set when the database contains no rows."""
        from datavia.weather.getter_weather import GetterWeather

        getter = GetterWeather("ERA5_land")
        assert getter.get_existing_layers() == set()

    def test_get_existing_layers_after_insert(self, sqlite_db: None) -> None:
        """Returns the set of variables registered in the DB."""
        from datavia.weather.getter_weather import GetterWeather

        _insert_weather_layer(
            source_name="ERA5_land",
            layer_name="ERA5_land_temperature_2m",
            variable="temperature_2m",
            file_format="netcdf",
            valid_from="2024-01-01T00:00:00",
            valid_until="2024-01-31T23:00:00",
            uri="/data/ERA5_land_temperature_2m.nc",
        )
        getter = GetterWeather("ERA5_land")
        assert getter.get_existing_layers() == {"temperature_2m"}

    def test_get_data_missing_variable_raises(self, sqlite_db: None) -> None:
        """ValueError is raised when 'variable' is not supplied."""
        from datavia.weather.getter_weather import GetterWeather

        getter = GetterWeather("ERA5_land")
        coords = np.array([[13.4, 52.5]])
        with pytest.raises(ValueError, match="variable"):
            getter.get_data(coords, datetime_utc="2024-01-15T12:00:00")

    def test_get_data_missing_datetime_raises(self, sqlite_db: None) -> None:
        """ValueError is raised when 'datetime_utc' is not supplied."""
        from datavia.weather.getter_weather import GetterWeather

        getter = GetterWeather("ERA5_land")
        coords = np.array([[13.4, 52.5]])
        with pytest.raises(ValueError, match="datetime_utc"):
            getter.get_data(coords, variable="temperature_2m")

    def test_get_data_no_files_raises(self, sqlite_db: None) -> None:
        """RuntimeError is raised when no matching weather files exist."""
        from datavia.weather.getter_weather import GetterWeather

        getter = GetterWeather("ERA5_land")
        coords = np.array([[13.4, 52.5]])
        with pytest.raises(RuntimeError, match="No weather files found"):
            getter.get_data(
                coords,
                variable="temperature_2m",
                datetime_utc="2024-01-15T12:00:00",
            )

    def test_get_data_uses_netcdf_interpolation(self, sqlite_db: None) -> None:
        """When only a NetCDF path is found, interpolate_netcdf is called."""
        from datavia.weather.getter_weather import GetterWeather

        _insert_weather_layer(
            source_name="ERA5_land",
            layer_name="ERA5_land_temperature_2m",
            variable="temperature_2m",
            file_format="netcdf",
            valid_from="2024-01-01T00:00:00",
            valid_until="2024-01-31T23:00:00",
            uri="/data/era5_temperature_2m.nc",
        )

        getter = GetterWeather("ERA5_land")
        coords = np.array([[13.4, 52.5]])

        with (
            patch(
                "datavia.weather.getter_weather.interpolate_netcdf",
                return_value=5.3,
            ) as mock_nc,
            patch(
                "datavia.weather.getter_weather.interpolate_station_parquet",
                return_value=float("nan"),
            ),
        ):
            result = getter.get_data(
                coords,
                variable="temperature_2m",
                datetime_utc="2024-01-15T12:00:00",
            )

        assert mock_nc.called
        assert np.isfinite(result[0])
        assert result[0] == pytest.approx(5.3)

    def test_get_weather_data_single_point(self, sqlite_db: None) -> None:
        """Convenience method returns a scalar float."""
        from datavia.weather.getter_weather import GetterWeather

        _insert_weather_layer(
            source_name="ERA5_land",
            layer_name="ERA5_land_temperature_2m",
            variable="temperature_2m",
            file_format="netcdf",
            valid_from="2024-01-01T00:00:00",
            valid_until="2024-01-31T23:00:00",
            uri="/data/era5_temperature_2m.nc",
        )

        getter = GetterWeather("ERA5_land")
        with patch(
            "datavia.weather.getter_weather.interpolate_netcdf",
            return_value=7.1,
        ):
            value = getter.get_weather_data(
                lat=52.5,
                lon=13.4,
                variable="temperature_2m",
                datetime_utc="2024-01-15T12:00:00",
            )

        assert isinstance(value, float)
        assert value == pytest.approx(7.1)


# ---------------------------------------------------------------------------
# CompositeWeatherDownloader
# ---------------------------------------------------------------------------


class TestCompositeWeatherDownloader:
    """Tests for the composite downloader structure and delegation."""

    _BASE_CFG: ClassVar[dict] = {
        "variables": ["2m_temperature"],
        "date_start": "2024-01-01",
        "date_end": "2024-01-31",
    }

    def test_era5_only_mode(self) -> None:
        """ERA5_land source without dwd_stations yields one ERA5 downloader."""
        from datavia.weather.composite_downloader import CompositeWeatherDownloader
        from datavia.weather.era5_downloader import ERA5Downloader

        composite = CompositeWeatherDownloader(
            config={"source": "ERA5_land", **self._BASE_CFG}
        )
        assert len(composite.downloaders) == 1
        assert isinstance(composite.downloaders[0], ERA5Downloader)
        assert composite._dwd is None

    def test_era5_with_dwd_mode(self) -> None:
        """ERA5_land source with dwd_stations yields ERA5 + DWD downloaders."""
        from datavia.weather.composite_downloader import CompositeWeatherDownloader
        from datavia.weather.dwd_downloader import DWDStationDownloader
        from datavia.weather.era5_downloader import ERA5Downloader

        composite = CompositeWeatherDownloader(
            config={"source": "ERA5_land", "dwd_stations": [], **self._BASE_CFG}
        )
        assert len(composite.downloaders) == 2
        assert isinstance(composite.downloaders[0], ERA5Downloader)
        assert isinstance(composite.downloaders[1], DWDStationDownloader)

    def test_hyras_only_mode(self) -> None:
        """HYRAS source without dwd_stations yields one HYRAS downloader."""
        from datavia.weather.composite_downloader import CompositeWeatherDownloader
        from datavia.weather.hyras_downloader import HYRASDownloader

        composite = CompositeWeatherDownloader(
            config={"source": "HYRAS", **self._BASE_CFG}
        )
        assert len(composite.downloaders) == 1
        assert isinstance(composite.downloaders[0], HYRASDownloader)
        assert composite._dwd is None

    def test_hyras_with_dwd_mode(self) -> None:
        """HYRAS source with dwd_stations yields HYRAS + DWD downloaders."""
        from datavia.weather.composite_downloader import CompositeWeatherDownloader
        from datavia.weather.dwd_downloader import DWDStationDownloader
        from datavia.weather.hyras_downloader import HYRASDownloader

        composite = CompositeWeatherDownloader(
            config={"source": "HYRAS", "dwd_stations": [], **self._BASE_CFG}
        )
        assert len(composite.downloaders) == 2
        assert isinstance(composite.downloaders[0], HYRASDownloader)
        assert isinstance(composite.downloaders[1], DWDStationDownloader)

    def test_dwd_only_mode(self) -> None:
        """DWD_stations source yields one DWD downloader and no grid downloader."""
        from datavia.weather.composite_downloader import CompositeWeatherDownloader
        from datavia.weather.dwd_downloader import DWDStationDownloader

        composite = CompositeWeatherDownloader(
            config={"source": "DWD_stations", **self._BASE_CFG}
        )
        assert len(composite.downloaders) == 1
        assert isinstance(composite.downloaders[0], DWDStationDownloader)
        assert composite._grid is None

    def test_download_combines_paths(self) -> None:
        """download() joins grid and DWD paths with a newline."""
        from datavia.weather.composite_downloader import CompositeWeatherDownloader

        composite = CompositeWeatherDownloader(
            config={"source": "ERA5_land", "dwd_stations": [], **self._BASE_CFG}
        )
        composite._grid.download = MagicMock(return_value="/tmp/era5.nc")
        composite._dwd.download = MagicMock(return_value="/tmp/dwd.parquet")

        result = composite.download()

        assert result == "/tmp/era5.nc\n/tmp/dwd.parquet"

    def test_download_grid_error_falls_back_to_dwd(self) -> None:
        """RuntimeError from grid download is caught; DWD path is still returned."""
        from datavia.weather.composite_downloader import CompositeWeatherDownloader

        composite = CompositeWeatherDownloader(
            config={"source": "ERA5_land", "dwd_stations": [], **self._BASE_CFG}
        )
        composite._grid.download = MagicMock(
            side_effect=RuntimeError("CDS unavailable")
        )
        composite._dwd.download = MagicMock(return_value="/tmp/dwd.parquet")

        result = composite.download()

        assert result == "/tmp/dwd.parquet"
        composite._dwd.download.assert_called_once()

    def test_download_grid_import_error_falls_back_to_dwd(self) -> None:
        """ImportError (missing cdsapi) from grid is caught; DWD path returned."""
        from datavia.weather.composite_downloader import CompositeWeatherDownloader

        composite = CompositeWeatherDownloader(
            config={"source": "ERA5_land", "dwd_stations": [], **self._BASE_CFG}
        )
        composite._grid.download = MagicMock(
            side_effect=ImportError("No module named 'cdsapi'")
        )
        composite._dwd.download = MagicMock(return_value="/tmp/dwd.parquet")

        result = composite.download()

        assert result == "/tmp/dwd.parquet"
        composite._dwd.download.assert_called_once()


# ---------------------------------------------------------------------------
# WeatherPipeline
# ---------------------------------------------------------------------------


class TestWeatherPipeline:
    """Tests for WeatherPipeline initialisation and update_data()."""

    def test_call_initialises_components(self) -> None:
        """__call__ creates downloader, saver, and getter instances."""
        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(
            config={
                "source": "ERA5_land",
                "variables": ["2m_temperature"],
                "date_start": "2024-01-01",
                "date_end": "2024-01-31",
            }
        )
        pipe()

        assert pipe.downloader is not None
        assert pipe.saver is not None
        assert pipe.getter is not None

    def test_update_data_splits_paths(self) -> None:
        """update_data() calls saver.save() for each path in the combined string."""
        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(
            config={
                "source": "ERA5_land",
                "variables": ["2m_temperature"],
                "date_start": "2024-01-01",
                "date_end": "2024-01-31",
            }
        )
        pipe.downloader = MagicMock()
        pipe.downloader.download.return_value = "/tmp/era5.nc\n/tmp/dwd.parquet"
        pipe.saver = MagicMock()
        pipe.saver.save.return_value = True
        pipe.saver.list_managed_files.return_value = []
        pipe.getter = MagicMock()
        pipe.getter.get_registered_uris.return_value = set()

        result = pipe.update_data()

        assert result is True
        assert pipe.saver.save.call_count == 2
        saved_paths = [call.args[0] for call in pipe.saver.save.call_args_list]
        assert "/tmp/era5.nc" in saved_paths
        assert "/tmp/dwd.parquet" in saved_paths

    def test_update_data_returns_false_on_download_failure(self) -> None:
        """update_data() returns False when download() returns 'failed'."""
        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(
            config={
                "source": "ERA5_land",
                "variables": ["2m_temperature"],
                "date_start": "2024-01-01",
                "date_end": "2024-01-31",
            }
        )
        pipe.downloader = MagicMock()
        pipe.downloader.download.return_value = "failed"
        pipe.saver = MagicMock()
        pipe.saver.list_managed_files.return_value = []
        pipe.getter = MagicMock()
        pipe.getter.get_registered_uris.return_value = set()

        assert pipe.update_data() is False
        pipe.saver.save.assert_not_called()

    def test_update_data_returns_false_on_partial_save_failure(self) -> None:
        """update_data() returns False when at least one save() fails."""
        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(
            config={
                "source": "ERA5_land",
                "variables": ["2m_temperature"],
                "date_start": "2024-01-01",
                "date_end": "2024-01-31",
            }
        )
        pipe.downloader = MagicMock()
        pipe.downloader.download.return_value = "/tmp/era5.nc\n/tmp/dwd.parquet"
        pipe.saver = MagicMock()
        pipe.saver.save.side_effect = [True, False]
        pipe.saver.list_managed_files.return_value = []
        pipe.getter = MagicMock()
        pipe.getter.get_registered_uris.return_value = set()

        assert pipe.update_data() is False


# ---------------------------------------------------------------------------
# ERA5Downloader._build_request_date_fields
# ---------------------------------------------------------------------------


class TestERA5DownloaderDateFields:
    """Tests for ERA5Downloader._build_request_date_fields().

    Verifies that the helper produces correct, sorted, zero-padded year/month/day
    lists for various date ranges without touching the network or cdsapi.
    """

    def test_single_day_produces_single_entries(self) -> None:
        """A one-day range yields exactly one year, one month, one day."""
        from datavia.weather.era5_downloader import ERA5Downloader

        years, months, days = ERA5Downloader._build_request_date_fields(
            "2024-01-15", "2024-01-15"
        )
        assert years == ["2024"]
        assert months == ["01"]
        assert days == ["15"]

    def test_multi_day_same_month(self) -> None:
        """A range within one month yields one year, one month, all spanned days."""
        from datavia.weather.era5_downloader import ERA5Downloader

        years, months, days = ERA5Downloader._build_request_date_fields(
            "2024-03-10", "2024-03-12"
        )
        assert years == ["2024"]
        assert months == ["03"]
        assert days == ["10", "11", "12"]

    def test_multi_month_same_year(self) -> None:
        """A range spanning two months collects the correct months and days."""
        from datavia.weather.era5_downloader import ERA5Downloader

        years, months, days = ERA5Downloader._build_request_date_fields(
            "2024-01-30", "2024-02-02"
        )
        assert years == ["2024"]
        assert months == ["01", "02"]
        # Days 30, 31 from January and 01, 02 from February.
        assert days == ["01", "02", "30", "31"]

    def test_multi_year_range(self) -> None:
        """A range crossing a year boundary yields both years."""
        from datavia.weather.era5_downloader import ERA5Downloader

        years, months, days = ERA5Downloader._build_request_date_fields(
            "2023-12-30", "2024-01-02"
        )
        assert years == ["2023", "2024"]
        assert months == ["01", "12"]
        # Days: 30, 31 from Dec-2023 and 01, 02 from Jan-2024.
        assert days == ["01", "02", "30", "31"]

    def test_end_before_start_raises(self) -> None:
        """ValueError is raised when date_end precedes date_start."""
        from datavia.weather.era5_downloader import ERA5Downloader

        with pytest.raises(ValueError, match="date_end"):
            ERA5Downloader._build_request_date_fields("2024-03-01", "2024-02-01")

    def test_lists_are_sorted(self) -> None:
        """All returned lists are lexicographically sorted."""
        from datavia.weather.era5_downloader import ERA5Downloader

        years, months, days = ERA5Downloader._build_request_date_fields(
            "2023-11-28", "2024-02-03"
        )
        assert years == sorted(years)
        assert months == sorted(months)
        assert days == sorted(days)


# ---------------------------------------------------------------------------
# ERA5Downloader._snap_bbox (issue #8)
# ---------------------------------------------------------------------------


class TestERA5SnapBbox:
    """Tests for the ERA5-Land 0.1° grid-snapping helper."""

    def test_already_aligned_bbox_unchanged(self) -> None:
        """A bbox already on 0.1° boundaries should be returned as-is."""
        from datavia.weather.era5_downloader import ERA5Downloader

        bbox = [55.1, 5.9, 47.3, 15.0]
        result = ERA5Downloader._snap_bbox(bbox)
        assert round(result[0], 6) == 55.1  # north — ceil → same
        assert round(result[1], 6) == 5.9  # west  — floor → same
        assert round(result[2], 6) == 47.3  # south — floor → same
        assert round(result[3], 6) == 15.0  # east  — ceil → same

    def test_north_east_rounded_up(self) -> None:
        """North and east edges are ceiled to the next 0.1° boundary."""
        from datavia.weather.era5_downloader import ERA5Downloader

        bbox = [55.04, 5.91, 47.31, 15.04]
        result = ERA5Downloader._snap_bbox(bbox)
        # north 55.04 → ceil to 55.1
        assert round(result[0], 6) == round(55.1, 6)
        # east 15.04 → ceil to 15.1
        assert round(result[3], 6) == round(15.1, 6)

    def test_south_west_rounded_down(self) -> None:
        """South and west edges are floored to the previous 0.1° boundary."""
        from datavia.weather.era5_downloader import ERA5Downloader

        bbox = [55.0, 5.96, 47.36, 15.0]
        result = ERA5Downloader._snap_bbox(bbox)
        # west 5.96 → floor to 5.9
        assert round(result[1], 6) == round(5.9, 6)
        # south 47.36 → floor to 47.3
        assert round(result[2], 6) == round(47.3, 6)

    def test_snap_applied_in_init(self) -> None:
        """ERA5Downloader stores the snapped bbox, not the raw input."""
        from datavia.weather.era5_downloader import ERA5Downloader

        raw_bbox = [55.04, 5.91, 47.31, 15.04]
        dl = ERA5Downloader(bbox=raw_bbox)
        # snapped values must differ from the raw non-aligned inputs
        assert dl.bbox != raw_bbox


# ---------------------------------------------------------------------------
# unit_conversions (issue #5)
# ---------------------------------------------------------------------------


class TestUnitConversions:
    """Tests for the ERA5 unit conversion utilities."""

    def test_kelvin_to_celsius_scalar(self) -> None:
        """0 K converts to -273.15 °C."""
        from datavia.library.unit_conversions import kelvin_to_celsius

        assert kelvin_to_celsius(273.15) == pytest.approx(0.0)

    def test_kelvin_to_celsius_array(self) -> None:
        """Array conversion preserves shape and values."""
        from datavia.library.unit_conversions import kelvin_to_celsius

        import numpy as np

        values = np.array([273.15, 373.15])
        result = kelvin_to_celsius(values)
        assert result == pytest.approx([0.0, 100.0])

    def test_precipitation_m_to_mm(self) -> None:
        """0.001 m converts to 1 mm."""
        from datavia.library.unit_conversions import precipitation_m_to_mm

        assert precipitation_m_to_mm(0.001) == pytest.approx(1.0)

    def test_ssrd_to_par_zero(self) -> None:
        """Zero SSRD yields zero PAR."""
        from datavia.library.unit_conversions import ssrd_to_par

        assert ssrd_to_par(0.0) == pytest.approx(0.0)

    def test_ssrd_to_par_known_value(self) -> None:
        """86400 J m⁻² day⁻¹ should equal 0.5 * 4.57 µmol m⁻² s⁻¹."""
        from datavia.library.unit_conversions import ssrd_to_par

        # 86400 J/m2/day / 86400 s/day x 0.5 x 4.57 = 2.285 umol/m2/s
        expected = 1.0 * 0.5 * 4.57
        assert ssrd_to_par(86400.0) == pytest.approx(expected)

    def test_convert_era5_variable_temperature(self) -> None:
        """Dispatches temperature to kelvin_to_celsius."""
        from datavia.library.unit_conversions import convert_era5_variable

        result = convert_era5_variable(300.0, "2m_temperature")
        assert result == pytest.approx(300.0 - 273.15)

    def test_convert_era5_variable_precipitation(self) -> None:
        """Dispatches precipitation to precipitation_m_to_mm."""
        from datavia.library.unit_conversions import convert_era5_variable

        assert convert_era5_variable(0.005, "total_precipitation") == pytest.approx(5.0)

    def test_convert_era5_variable_unknown_passthrough(self) -> None:
        """Unknown variable names are returned unchanged."""
        from datavia.library.unit_conversions import convert_era5_variable

        assert convert_era5_variable(42.0, "u_component_of_wind") == pytest.approx(42.0)


# ---------------------------------------------------------------------------
# interpolate_station_parquet — index alignment fix (issue #4)
# ---------------------------------------------------------------------------


class TestInterpolateStationParquetIndexAlignment:
    """Tests for the IDW index-alignment bug fix in interpolate_station_parquet."""

    def test_returns_correct_weighted_value(self, tmp_path) -> None:
        """IDW returns the correct value even when original DataFrame indices
        are non-contiguous (simulating a pre-filtered DataFrame)."""
        try:
            import pandas as pd
        except ImportError:
            pytest.skip("pandas not installed")

        from datavia.library.interpolation import interpolate_station_parquet

        # Build a DataFrame with stations spread around the target point
        # (52.5N, 13.4E).  We intentionally create multiple rows then select
        # a subset to reproduce the non-contiguous-index scenario.
        data = {
            "station_id": ["A", "B", "C", "D"],
            "latitude": [52.5, 52.6, 48.0, 48.0],  # A and B are within 50 km
            "longitude": [13.4, 13.5, 8.0, 8.0],  # C and D are far away
            "datetime": ["2024-06-15T12:00:00"] * 4,
            "temperature_2m": [20.0, 22.0, 99.0, 99.0],
        }
        df = pd.DataFrame(data)
        parquet_path = str(tmp_path / "test_stations.parquet")
        df.to_parquet(parquet_path, index=False)

        result = interpolate_station_parquet(
            parquet_path=parquet_path,
            lat=52.5,
            lon=13.4,
            variable="temperature_2m",
            datetime_utc="2024-06-15T12:00:00",
            radius_km=50.0,
        )

        # The result should be a valid number close to the near stations'
        # values (20-22 degrees C), not NaN and not a spurious out-of-range value.
        assert result == result, "Result must not be NaN"
        assert 19.0 < result < 23.0, f"Unexpected IDW result: {result}"

    def test_no_stations_returns_nan(self, tmp_path) -> None:
        """Returns NaN (float) when no stations are within the search radius."""
        try:
            import pandas as pd
        except ImportError:
            pytest.skip("pandas not installed")

        from datavia.library.interpolation import interpolate_station_parquet

        data = {
            "station_id": ["A"],
            "latitude": [48.0],
            "longitude": [8.0],
            "datetime": ["2024-06-15T12:00:00"],
            "temperature_2m": [15.0],
        }
        df = pd.DataFrame(data)
        parquet_path = str(tmp_path / "far_station.parquet")
        df.to_parquet(parquet_path, index=False)

        result = interpolate_station_parquet(
            parquet_path=parquet_path,
            lat=52.5,
            lon=13.4,
            variable="temperature_2m",
            datetime_utc="2024-06-15T12:00:00",
            radius_km=50.0,
        )
        import math

        assert math.isnan(result)


# ---------------------------------------------------------------------------
# SaverWeather — explicit variable parameter (issue #6)
# ---------------------------------------------------------------------------


class TestSaverWeatherExplicitVariable:
    """Tests for the explicit variable parameter added to SaverWeather.save()."""

    def test_explicit_variable_stored_in_db(self, sqlite_db: None, tmp_path) -> None:
        """When variable is passed explicitly it is stored as-is in the DB."""
        from sqlalchemy import text

        from datavia.library.database.connection import session_local
        from datavia.weather.saver_weather import SaverWeather

        nc_file = tmp_path / "era5_tmpXYZabc.nc"
        nc_file.write_bytes(b"FAKE_NC")

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "weather"
        saver.data_dir = str(tmp_path)

        with patch(
            "datavia.weather.saver_weather.extract_netcdf_layer_metadata",
            return_value={
                "valid_from": "2024-06-01T00:00:00",
                "valid_until": "2024-06-30T23:00:00",
                "bbox": None,
                "crs": "EPSG:4326",
                "variables": ["2m_temperature"],
            },
        ):
            result = saver.save(str(nc_file), variable="2m_temperature")

        assert result is True

        session = session_local()
        row = session.execute(
            text("SELECT variable FROM weather_layers WHERE source_name='weather'")
        ).fetchone()
        session.close()
        assert row is not None
        # The stored variable must be the explicit value, not a random stem fragment.
        assert row[0] == "2m_temperature"

    def test_auto_detect_variables_from_netcdf(self, sqlite_db: None, tmp_path) -> None:
        """When variable=None the variables are read from the NC file metadata."""
        from sqlalchemy import text

        from datavia.library.database.connection import session_local
        from datavia.weather.saver_weather import SaverWeather

        nc_file = tmp_path / "era5_tmpABC.nc"
        nc_file.write_bytes(b"FAKE_NC")

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "weather"
        saver.data_dir = str(tmp_path)

        with patch(
            "datavia.weather.saver_weather.extract_netcdf_layer_metadata",
            return_value={
                "valid_from": "2024-06-01T00:00:00",
                "valid_until": "2024-06-30T23:00:00",
                "bbox": None,
                "crs": "EPSG:4326",
                "variables": ["2m_temperature", "total_precipitation"],
            },
        ):
            result = saver.save(str(nc_file))

        assert result is True

        session = session_local()
        rows = session.execute(
            text("SELECT variable FROM weather_layers WHERE source_name='weather'")
        ).fetchall()
        session.close()
        stored_vars = {r[0] for r in rows}
        # Both variables from the NC file must have their own DB rows.
        assert "2m_temperature" in stored_vars
        assert "total_precipitation" in stored_vars


# ---------------------------------------------------------------------------
# SaverWeather — content-derived destination naming (BUG-07)
# ---------------------------------------------------------------------------


class TestSaverWeatherDestNaming:
    """Tests for the content-derived destination filename logic in SaverWeather.

    Verifies that :meth:`~datavia.weather.saver_weather.SaverWeather.save`
    copies temp files to descriptive names derived from file content rather
    than from the random ``tmp*`` stem (BUG-07 fix, naming responsibility
    moved from downloader to saver).

    All tests use a mocked :func:`~datavia.library.formats.extract_netcdf_layer_metadata`
    so no real NetCDF files are required.
    """

    def test_single_variable_nc_uses_source_variable_year(
        self, sqlite_db: None, tmp_path
    ) -> None:
        """A single-variable NC file is copied as ``{source}_{nc_var}_{year}.nc``.

        Parameters
        ----------
        sqlite_db : None
            pytest fixture; initialises the SQLite test database.
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.weather.saver_weather import SaverWeather

        nc_file = tmp_path / "tmpXXXXXX.nc"
        nc_file.write_bytes(b"FAKE_NC")

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "HYRAS"
        saver.data_dir = str(tmp_path)

        with patch(
            "datavia.weather.saver_weather.extract_netcdf_layer_metadata",
            return_value={
                "valid_from": "2024-01-01T00:00:00",
                "valid_until": "2024-12-31T23:59:59",
                "variables": ["tas"],
                "bbox": None,
                "crs": "EPSG:4326",
            },
        ):
            saver.save(str(nc_file))

        # The temp file must NOT appear in the data directory.
        assert not (tmp_path / "HYRAS_tmpXXXXXX.nc").exists()
        # The descriptive name must exist instead.
        assert (tmp_path / "HYRAS_tas_2024.nc").exists()

    def test_multi_variable_nc_omits_variable_from_stem(
        self, sqlite_db: None, tmp_path
    ) -> None:
        """A multi-variable NC file is copied as ``{source}_{year}.nc``.

        When more than one data variable is present in the file, encoding all
        of them in the filename would be impractically long.

        Parameters
        ----------
        sqlite_db : None
            pytest fixture; initialises the SQLite test database.
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.weather.saver_weather import SaverWeather

        nc_file = tmp_path / "tmpMULTI.nc"
        nc_file.write_bytes(b"FAKE_NC")

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "ERA5_land"
        saver.data_dir = str(tmp_path)

        with patch(
            "datavia.weather.saver_weather.extract_netcdf_layer_metadata",
            return_value={
                "valid_from": "2024-06-01T00:00:00",
                "valid_until": "2024-06-30T23:59:59",
                "variables": ["2m_temperature", "total_precipitation"],
                "bbox": None,
                "crs": "EPSG:4326",
            },
        ):
            saver.save(str(nc_file))

        assert (tmp_path / "ERA5_land_2024.nc").exists()

    def test_register_only_uses_file_stem_as_layer_name(
        self, sqlite_db: None, tmp_path
    ) -> None:
        """register_only=True uses the file stem directly, no double-prefix.

        Files already in the data directory carry the source_name as part of
        their name (e.g. ``HYRAS_tas_2024.nc``).  Prepending source_name
        again would produce ``HYRAS_HYRAS_tas_2024``.  The layer name must
        match what save() would produce for the same file.

        Parameters
        ----------
        sqlite_db : None
            pytest fixture; initialises the SQLite test database.
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from sqlalchemy import text

        from datavia.library.database.connection import session_local
        from datavia.weather.saver_weather import SaverWeather

        nc_file = tmp_path / "HYRAS_tas_2024.nc"
        nc_file.write_bytes(b"FAKE_NC")

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "HYRAS"
        saver.data_dir = str(tmp_path)

        with patch(
            "datavia.weather.saver_weather.extract_netcdf_layer_metadata",
            return_value={
                "valid_from": "2024-01-01T00:00:00",
                "valid_until": "2024-12-31T23:59:59",
                "variables": ["tas"],
                "bbox": None,
                "crs": "EPSG:4326",
            },
        ):
            saver.save(str(nc_file), register_only=True)

        session = session_local()
        row = session.execute(
            text("SELECT layer_name FROM weather_layers WHERE source_name='HYRAS'")
        ).fetchone()
        session.close()
        assert row is not None
        assert row[0] == "HYRAS_tas_2024", (
            f"Expected layer_name='HYRAS_tas_2024', got '{row[0]}'"
        )

    def test_parquet_uses_datetime_year_not_temp_stem(
        self, sqlite_db: None, tmp_path
    ) -> None:
        """A Parquet station file is copied as ``{source}_{year}.parquet``.

        The year is derived from the ``datetime`` column minimum, so the
        destination name is deterministic and human-readable rather than
        inheriting the random ``tmp*`` temp stem.

        Parameters
        ----------
        sqlite_db : None
            pytest fixture; initialises the SQLite test database.
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        import pandas as pd

        from datavia.weather.saver_weather import SaverWeather

        parquet_file = tmp_path / "tmpABCDEF.parquet"
        df = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2023-01-01", "2023-06-15", "2023-12-31"]),
                "station_id": [1, 1, 1],
                "temperature": [0.5, 20.3, 3.1],
            }
        )
        df.to_parquet(str(parquet_file))

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "DWD"
        saver.data_dir = str(tmp_path)

        saver.save(str(parquet_file))

        assert not (tmp_path / "DWD_tmpABCDEF.parquet").exists()
        assert (tmp_path / "DWD_2023.parquet").exists()


# ---------------------------------------------------------------------------
# extract_netcdf_layer_metadata — year-end timestamp boundary (BUG-06)
# ---------------------------------------------------------------------------


class TestExtractNetcdfLayerMetadata:
    """Tests for :func:`~datavia.library.formats.extract_netcdf_layer_metadata`.

    Verifies that ``valid_until`` is always rounded up to ``23:59:59`` of the
    last calendar date, regardless of the raw time value present in the file.
    """

    def test_valid_until_rounded_to_end_of_day(self, tmp_path) -> None:
        """valid_until is clamped to 23:59:59 regardless of the raw last time step.

        HYRAS precipitation stores its last annual step at 06:00 UTC on
        31 December.  Before the fix, annual boundary queries failed because
        the stored ``valid_until`` was ``2025-12-31T06:00:00`` instead of
        ``2025-12-31T23:59:59``.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        import numpy as np
        import xarray as xr

        from datavia.library.formats import extract_netcdf_layer_metadata

        times = np.array(
            ["2025-01-01T00:00:00", "2025-12-31T06:00:00"],
            dtype="datetime64[ns]",
        )
        ds = xr.Dataset(
            {"pr": (["time"], [0.0, 1.0])},
            coords={"time": times},
        )
        nc_file = tmp_path / "hyras_pr_2025.nc"
        ds.to_netcdf(str(nc_file))

        metadata = extract_netcdf_layer_metadata(str(nc_file))

        assert metadata["valid_until"] == "2025-12-31T23:59:59"

    def test_valid_from_unchanged(self, tmp_path) -> None:
        """valid_from is not altered — only the upper bound is rounded.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        import numpy as np
        import xarray as xr

        from datavia.library.formats import extract_netcdf_layer_metadata

        times = np.array(
            ["2025-01-01T06:00:00", "2025-12-31T06:00:00"],
            dtype="datetime64[ns]",
        )
        ds = xr.Dataset(
            {"tas": (["time"], [280.0, 275.0])},
            coords={"time": times},
        )
        nc_file = tmp_path / "hyras_tas_2025.nc"
        ds.to_netcdf(str(nc_file))

        metadata = extract_netcdf_layer_metadata(str(nc_file))

        # Lower bound must not be modified.
        assert metadata["valid_from"] == "2025-01-01T06:00:00.000000000"
        # Upper bound must still be end-of-day.
        assert metadata["valid_until"] == "2025-12-31T23:59:59"

    def test_midnight_last_step_unchanged(self, tmp_path) -> None:
        """A last step already at midnight produces the same 23:59:59 result.

        ERA5 files typically end at 00:00 UTC; this test confirms that the
        rounding does not shift the date forward.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        import numpy as np
        import xarray as xr

        from datavia.library.formats import extract_netcdf_layer_metadata

        times = np.array(
            ["2024-01-01T00:00:00", "2024-12-31T00:00:00"],
            dtype="datetime64[ns]",
        )
        ds = xr.Dataset(
            {"t2m": (["time"], [280.0, 275.0])},
            coords={"time": times},
        )
        nc_file = tmp_path / "era5_t2m_2024.nc"
        ds.to_netcdf(str(nc_file))

        metadata = extract_netcdf_layer_metadata(str(nc_file))

        assert metadata["valid_until"] == "2024-12-31T23:59:59"


# ---------------------------------------------------------------------------
# interpolate_netcdf — batch coordinates (BUG-05) + NaN pre-fill (BUG-08)
# ---------------------------------------------------------------------------


class TestInterpolateNetcdf:
    """Tests for :func:`~datavia.library.interpolation.interpolate_netcdf`.

    All tests use synthetic in-memory NetCDF datasets written to ``tmp_path``
    to avoid any network or real-file dependency.

    Covers:

    - Scalar lat/lon still returns a scalar (backward compatibility).
    - Array lat/lon returns an ``(N,)`` array (BUG-05 batch support).
    - A single ``open_dataset`` call is used regardless of N (performance).
    - Nodata cells filled before interpolation — edge points no longer NaN
      (BUG-08 pre-fill).
    - Geographic coordinates (ERA5-style latitude/longitude) are handled.
    """

    @staticmethod
    def _make_geographic_nc(tmp_path, *, with_nodata: bool = False) -> str:
        """Write a tiny ERA5-style geographic NetCDF to *tmp_path*.

        Creates a 5x5 degree grid centred on Germany with a single time step.

        Parameters
        ----------
        tmp_path : pathlib.Path
            Directory for the file.
        with_nodata : bool
            When ``True``, set the corner cells to the ``_FillValue``
            so that a query point near the edge triggers BUG-08.

        Returns
        -------
        str
            Absolute path to the written file.
        """
        import xarray as xr

        lats = np.array([47.0, 48.0, 49.0, 50.0, 51.0], dtype=float)
        lons = np.array([9.0, 10.0, 11.0, 12.0, 13.0], dtype=float)
        times = np.array(["2024-06-15T12:00:00"], dtype="datetime64[ns]")

        data = np.ones((1, len(lats), len(lons)), dtype=float) * 20.0
        fill_val = -9999.0

        if with_nodata:
            # Set the entire top row to fill_value to force BUG-08 scenario
            # at any point near lat=51.
            data[0, -1, :] = fill_val

        da = xr.DataArray(
            data,
            dims=["time", "latitude", "longitude"],
            coords={"time": times, "latitude": lats, "longitude": lons},
            attrs={"_FillValue": fill_val},
        )
        ds = xr.Dataset({"t2m": da})
        path = str(tmp_path / "era5_geo.nc")
        ds.to_netcdf(path)
        return path

    def test_scalar_input_returns_scalar(self, tmp_path) -> None:
        """A single lat/lon pair returns a Python float (backward compatible).

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.library.interpolation import interpolate_netcdf

        nc = self._make_geographic_nc(tmp_path)
        result = interpolate_netcdf(nc, 49.0, 11.0, "t2m", "2024-06-15T12:00:00")

        assert isinstance(result, float)
        assert result == pytest.approx(20.0)

    def test_array_input_returns_array(self, tmp_path) -> None:
        """An array of N coordinates returns an (N,) ndarray (BUG-05).

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.library.interpolation import interpolate_netcdf

        nc = self._make_geographic_nc(tmp_path)
        lats = np.array([48.0, 49.0, 50.0])
        lons = np.array([10.0, 11.0, 12.0])

        result = interpolate_netcdf(nc, lats, lons, "t2m", "2024-06-15T12:00:00")

        assert isinstance(result, np.ndarray)
        assert result.shape == (3,)
        np.testing.assert_allclose(result, 20.0, atol=1e-6)

    def test_single_file_open_for_batch(self, tmp_path) -> None:
        """xr.open_dataset is called exactly once regardless of N (BUG-05 perf).

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from unittest.mock import patch

        import xarray as xr

        from datavia.library.interpolation import interpolate_netcdf

        nc = self._make_geographic_nc(tmp_path)
        lats = np.array([48.0, 49.0, 50.0])
        lons = np.array([10.0, 11.0, 12.0])

        with patch("xarray.open_dataset", wraps=xr.open_dataset) as mock_open:
            interpolate_netcdf(nc, lats, lons, "t2m", "2024-06-15T12:00:00")

        assert mock_open.call_count == 1, (
            f"Expected 1 open_dataset call for {len(lats)} points, "
            f"got {mock_open.call_count}"
        )

    def test_nodata_prefill_prevents_nan_at_edge(self, tmp_path) -> None:
        """Edge points adjacent to nodata cells return a value, not NaN (BUG-08).

        The top row of the synthetic grid is set to fill_value.  A query near
        that edge (lat=50.5) would previously return NaN because the bilinear
        stencil contained a fill cell.  After the pre-fill fix the stencil is
        filled with the nearest valid value and the result is finite.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.library.interpolation import interpolate_netcdf

        nc = self._make_geographic_nc(tmp_path, with_nodata=True)
        # lat=50.5 sits between the valid row (50.0 = 20.0) and the nodata
        # row (51.0 = fill_value).  Without pre-fill this returns NaN.
        result = interpolate_netcdf(nc, 50.5, 11.0, "t2m", "2024-06-15T12:00:00")

        assert np.isfinite(result), (
            "Expected a finite value near a nodata boundary after pre-fill; "
            f"got {result}"
        )

    def test_unknown_variable_raises_key_error(self, tmp_path) -> None:
        """A missing variable name raises KeyError with a descriptive message.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.library.interpolation import interpolate_netcdf

        nc = self._make_geographic_nc(tmp_path)
        with pytest.raises(KeyError, match="no_such_var"):
            interpolate_netcdf(nc, 49.0, 11.0, "no_such_var", "2024-06-15T12:00:00")
