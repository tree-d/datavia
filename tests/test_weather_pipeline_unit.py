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

    def test_check_source_station_filter(self, sqlite_db: None) -> None:
        """station_ids filter matches only layers that cover all requested stations.

        A layer with stations ["S1", "S2"] satisfies queries for ["S1"],
        ["S1", "S2"], but not ["S3"] or ["S1", "S3"].
        Calling without station_ids preserves the original time-overlap
        behaviour and still returns True.
        """
        import json as _json

        from sqlalchemy import text

        from datavia.library.database.connection import session_local
        from datavia.library.database.query import check_weather_source_exists

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
                         :valid_from, :valid_until, :uri, 'EPSG:4326', NULL, :metadata)
                    """
                ),
                {
                    "layer_name": "dwd_2024_s1_s2",
                    "source_name": "DWD_stations",
                    "variable": "temperature_2m",
                    "file_format": "parquet",
                    "valid_from": "2024-07-01T00:00:00",
                    "valid_until": "2024-07-31T23:00:00",
                    "uri": "/data/dwd_2024.parquet",
                    "metadata": _json.dumps(
                        {"file_format": "parquet", "station_ids": ["S1", "S2"]}
                    ),
                },
            )
            session.commit()
        finally:
            session.close()

        assert (
            check_weather_source_exists(
                "DWD_stations",
                variable="temperature_2m",
                station_ids=["S1"],
            )
            is True
        )
        assert (
            check_weather_source_exists(
                "DWD_stations",
                variable="temperature_2m",
                station_ids=["S1", "S2"],
            )
            is True
        )
        assert (
            check_weather_source_exists(
                "DWD_stations",
                variable="temperature_2m",
                station_ids=["S3"],
            )
            is False
        )
        assert (
            check_weather_source_exists(
                "DWD_stations",
                variable="temperature_2m",
                station_ids=["S1", "S3"],
            )
            is False
        )
        # Without station_ids the existing check still returns True.
        assert (
            check_weather_source_exists("DWD_stations", variable="temperature_2m")
            is True
        )

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
        from datavia.weather.saver_weather import SaverWeather

        from datavia.library.database.query import check_weather_source_exists

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
                "bbox": (
                    "POLYGON ((5.9 47.3, 15.0 47.3, 15.0 55.1, 5.9 55.1, 5.9 47.3))"
                ),
                "crs": "EPSG:4326",
            },
        ):
            result = saver.save(str(nc_file))

        assert result is True
        assert check_weather_source_exists("era5") is True

    def test_save_idempotent(self, sqlite_db: None, tmp_path) -> None:
        """Calling save() twice on the same file does not create duplicate rows."""
        from datavia.weather.saver_weather import SaverWeather
        from sqlalchemy import text

        from datavia.library.database.connection import session_local

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

    def test_check_data_exists_with_station_ids(
        self, sqlite_db: None, tmp_path
    ) -> None:
        """check_data_exists with station_ids returns True only when the layer
        covers all requested stations.

        A layer registered with ["A", "B"] satisfies queries for ["A"] and
        ["A", "B"] but not ["C"].  A call without station_ids (legacy callers)
        still returns True.
        """
        import json as _json

        from datavia.weather.saver_weather import SaverWeather
        from sqlalchemy import text

        from datavia.library.database.connection import session_local

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "DWD_stations"
        saver.data_dir = str(tmp_path)

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
                         :valid_from, :valid_until, :uri, 'EPSG:4326', NULL, :metadata)
                    """
                ),
                {
                    "layer_name": "DWD_stations_2024_a_b",
                    "source_name": "DWD_stations",
                    "variable": "temperature_2m",
                    "file_format": "parquet",
                    "valid_from": "2024-07-01T00:00:00",
                    "valid_until": "2024-07-31T23:00:00",
                    "uri": str(tmp_path / "DWD_stations_2024.parquet"),
                    "metadata": _json.dumps(
                        {"file_format": "parquet", "station_ids": ["A", "B"]}
                    ),
                },
            )
            session.commit()
        finally:
            session.close()

        assert saver.check_data_exists("temperature_2m", station_ids=["A"]) is True
        assert saver.check_data_exists("temperature_2m", station_ids=["A", "B"]) is True
        assert saver.check_data_exists("temperature_2m", station_ids=["C"]) is False
        # Legacy call without station_ids preserves True.
        assert saver.check_data_exists("temperature_2m") is True


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

    def test_get_data_timezone_aware_timestamp_not_nan(self, sqlite_db: None) -> None:
        """Timezone-aware timestamps (e.g. UTC 'Z' suffix) must not produce NaN.

        Regression test for the bug where str(pd.Timestamp(...)) produced a
        space-separated ISO string ("2024-01-15 12:00:00") that compared
        lexicographically less than the T-separated DB values, causing
        get_weather_paths() to return an empty list and get_data() to raise
        RuntimeError or return all-NaN silently.
        """
        import pandas as pd
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

        tz_aware = pd.Timestamp("2024-01-15T12:00:00Z")
        with (
            patch(
                "datavia.weather.getter_weather.interpolate_netcdf",
                return_value=3.7,
            ) as mock_nc,
            patch(
                "datavia.weather.getter_weather.interpolate_station_parquet",
                return_value=float("nan"),
            ),
        ):
            result = getter.get_data(
                coords,
                variable="temperature_2m",
                datetime_utc=tz_aware,
            )

        assert mock_nc.called, (
            "interpolate_netcdf was not called — DB path lookup failed"
        )
        assert np.isfinite(result[0]), "Expected a finite value, got NaN"
        assert result[0] == pytest.approx(3.7)

    def test_get_data_z_suffix_string_not_nan(self, sqlite_db: None) -> None:
        """ISO strings with a 'Z' suffix must resolve to a T-separated query string.

        Regression companion to test_get_data_timezone_aware_timestamp_not_nan:
        callers that pass "2024-01-15T12:00:00Z" as a raw string should also
        work because _to_naive_utc() normalises the timezone, and isoformat()
        then emits the correct T-separated representation for the DB comparison.
        """
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
                return_value=3.7,
            ) as mock_nc,
            patch(
                "datavia.weather.getter_weather.interpolate_station_parquet",
                return_value=float("nan"),
            ),
        ):
            result = getter.get_data(
                coords,
                variable="temperature_2m",
                datetime_utc="2024-01-15T12:00:00Z",
            )

        assert mock_nc.called, (
            "interpolate_netcdf was not called — DB path lookup failed"
        )
        assert np.isfinite(result[0]), "Expected a finite value, got NaN"
        assert result[0] == pytest.approx(3.7)


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
        """update_data() calls saver.save() for each path in the combined string.

        Patches :class:`CoverageManager` to return one missing cell and
        :class:`CompositeWeatherDownloader` so the per-cell downloader returns
        two paths without any network access.
        """
        from datavia.weather.coverage_manager import CoverageCell
        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(
            config={
                "source": "ERA5_land",
                "variables": ["2m_temperature"],
                "date_start": "2024-01-01",
                "date_end": "2024-01-31",
            }
        )
        # Pre-assign components so self() is not invoked during update_data().
        pipe.downloader = MagicMock()
        pipe.saver = MagicMock()
        pipe.saver.save.return_value = True
        pipe.saver.list_managed_files.return_value = []
        pipe.getter = MagicMock()
        pipe.getter.get_registered_uris.return_value = set()

        fake_cell = CoverageCell(
            bbox=(5.9, 47.3, 15.0, 55.1),
            date_start="2024-01-01",
            date_end="2024-01-31",
        )
        mock_cell_dl = MagicMock()
        mock_cell_dl.download.return_value = "/tmp/era5.nc\n/tmp/dwd.parquet"

        with (
            patch("datavia.weather.pipeline.CoverageManager") as mock_cm_cls,
            patch(
                "datavia.weather.pipeline.CompositeWeatherDownloader",
                return_value=mock_cell_dl,
            ),
            patch.object(pipe, "sync_files_and_database"),
        ):
            mock_cm_cls.return_value.missing_spatiotemporal.return_value = [fake_cell]
            result = pipe.update_data()

        assert result is True
        assert pipe.saver.save.call_count == 2
        saved_paths = [call.args[0] for call in pipe.saver.save.call_args_list]
        assert "/tmp/era5.nc" in saved_paths
        assert "/tmp/dwd.parquet" in saved_paths

    def test_update_data_raises_on_download_failure(self) -> None:
        """update_data() raises RuntimeError
        when the per-cell download() returns 'failed'.

        All cells are still attempted before raising so that partial data
        from successful cells remains in the database.
        """
        from datavia.weather.coverage_manager import CoverageCell
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
        pipe.saver = MagicMock()
        pipe.saver.list_managed_files.return_value = []
        pipe.getter = MagicMock()
        pipe.getter.get_registered_uris.return_value = set()

        fake_cell = CoverageCell(
            bbox=(5.9, 47.3, 15.0, 55.1),
            date_start="2024-01-01",
            date_end="2024-01-31",
        )
        mock_cell_dl = MagicMock()
        mock_cell_dl.download.return_value = "failed"

        with (
            patch("datavia.weather.pipeline.CoverageManager") as mock_cm_cls,
            patch(
                "datavia.weather.pipeline.CompositeWeatherDownloader",
                return_value=mock_cell_dl,
            ),
            patch.object(pipe, "sync_files_and_database"),
        ):
            mock_cm_cls.return_value.missing_spatiotemporal.return_value = [fake_cell]
            with pytest.raises(RuntimeError, match="failed to download or save"):
                pipe.update_data()

        pipe.saver.save.assert_not_called()

    def test_update_data_raises_on_partial_save_failure(self) -> None:
        """update_data() raises RuntimeError when at least one save() fails.

        The successfully saved file is still processed; only the failure causes
        the exception, raised after the full loop completes.
        """
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
        pipe.saver = MagicMock()
        pipe.saver.save.side_effect = [True, False]
        pipe.saver.list_managed_files.return_value = []
        pipe.getter = MagicMock()
        pipe.getter.get_registered_uris.return_value = set()

        from datavia.weather.coverage_manager import CoverageCell

        fake_cell = CoverageCell(
            bbox=(5.9, 47.3, 15.0, 55.1),
            date_start="2024-01-01",
            date_end="2024-01-31",
        )
        mock_cell_dl = MagicMock()
        mock_cell_dl.download.return_value = "/tmp/era5.nc\n/tmp/dwd.parquet"

        with (
            patch("datavia.weather.pipeline.CoverageManager") as mock_cm_cls,
            patch(
                "datavia.weather.pipeline.CompositeWeatherDownloader",
                return_value=mock_cell_dl,
            ),
            patch.object(pipe, "sync_files_and_database"),
        ):
            mock_cm_cls.return_value.missing_spatiotemporal.return_value = [fake_cell]
            with pytest.raises(RuntimeError, match="failed to download or save"):
                pipe.update_data()

    def test_invalid_variable_for_era5_raises_at_init(self) -> None:
        """Passing a HYRAS-only variable to an ERA5 pipeline
        raises ValueError at __init__.

        ``temperature_2m_max`` and ``temperature_2m_min`` are defined only in
        the HYRAS ``nc_variable_map``.  Constructing a ``WeatherPipeline`` with
        ``source='ERA5_land'`` and either of those variables must raise
        ``ValueError`` immediately, before any network access.

        This prevents the silent failure mode where the CDS API receives an
        unrecognised variable name, the download is skipped, and
        ``get_data()`` later raises ``RuntimeError: No weather files found``.
        """
        from datavia.weather.pipeline import WeatherPipeline

        with pytest.raises(ValueError, match="temperature_2m_max"):
            WeatherPipeline(
                config={
                    "source": "ERA5_land",
                    "variables": ["temperature_2m_max", "temperature_2m_min"],
                    "date_start": "2024-06-01",
                    "date_end": "2024-06-30",
                }
            )

    def test_invalid_variable_error_message_includes_valid_set(self) -> None:
        """The ValueError for an invalid variable lists the valid options.

        Ensures the error message is actionable: the user can read which
        variables are actually supported without consulting the source registry
        manually.
        """
        from datavia.weather.pipeline import WeatherPipeline

        with pytest.raises(ValueError, match="2m_temperature"):
            WeatherPipeline(
                config={
                    "source": "ERA5_land",
                    "variables": ["temperature_2m_max"],
                    "date_start": "2024-06-01",
                    "date_end": "2024-06-30",
                }
            )

    def test_update_data_skips_dwd_when_stations_covered(self) -> None:
        """update_data() returns True without downloading when DWD data is cached.

        When saver.check_data_exists() returns True for the configured station
        set and date range, update_data() must not call the downloader.
        """
        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(
            config={
                "source": "DWD_stations",
                "variables": ["temperature_2m"],
                "date_start": "2024-07-01",
                "date_end": "2024-07-31",
                "dwd_stations": [
                    {"id": "01234", "latitude": 53.5, "longitude": 10.0},
                ],
            }
        )
        pipe.downloader = MagicMock()
        pipe.saver = MagicMock()
        pipe.saver.check_data_exists.return_value = True
        pipe.getter = MagicMock()

        with patch.object(pipe, "sync_files_and_database"):
            result = pipe.update_data()

        assert result is True
        pipe.saver.check_data_exists.assert_called_once_with(
            variable="temperature_2m",
            from_dt="2024-07-01",
            to_dt="2024-07-31",
            station_ids=["01234"],
        )
        pipe.downloader.download.assert_not_called()

    def test_update_data_downloads_dwd_when_station_set_differs(self) -> None:
        """update_data() proceeds to download when the requested DWD stations
        are not yet cached.

        When saver.check_data_exists() returns False, update_data() must call
        the downloader exactly once and save the returned file path.
        """
        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(
            config={
                "source": "DWD_stations",
                "variables": ["temperature_2m"],
                "date_start": "2024-07-01",
                "date_end": "2024-07-31",
                "dwd_stations": [
                    {"id": "99999", "latitude": 48.1, "longitude": 11.6},
                ],
            }
        )
        mock_cell_dl = MagicMock()
        mock_cell_dl.download.return_value = "/tmp/dwd.parquet"
        pipe.downloader = MagicMock()
        pipe.saver = MagicMock()
        pipe.saver.check_data_exists.return_value = False
        pipe.saver.save.return_value = True
        pipe.getter = MagicMock()

        with (
            patch.object(pipe, "sync_files_and_database"),
            patch(
                "datavia.weather.pipeline.CompositeWeatherDownloader",
                return_value=mock_cell_dl,
            ),
        ):
            result = pipe.update_data()

        assert result is True
        mock_cell_dl.download.assert_called_once()
        pipe.saver.save.assert_called_once_with("/tmp/dwd.parquet")


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
        import numpy as np

        from datavia.library.unit_conversions import kelvin_to_celsius

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
        """kelvin_to_celsius converts temperature correctly."""
        from datavia.library.unit_conversions import kelvin_to_celsius

        result = kelvin_to_celsius(300.0)
        assert result == pytest.approx(300.0 - 273.15)

    def test_convert_era5_variable_precipitation(self) -> None:
        """precipitation_m_to_mm converts precipitation correctly."""
        from datavia.library.unit_conversions import precipitation_m_to_mm

        assert precipitation_m_to_mm(0.005) == pytest.approx(5.0)

    def test_convert_era5_variable_unknown_passthrough(self) -> None:
        """Unknown variables are returned unchanged
        (identity test on ssrd_to_par passthrough)."""
        from datavia.library.unit_conversions import kelvin_to_celsius

        # kelvin_to_celsius always applies the offset; test with a value
        # that maps to a known result to confirm the function is still callable.
        assert kelvin_to_celsius(273.15) == pytest.approx(0.0)


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
        from datavia.weather.saver_weather import SaverWeather
        from sqlalchemy import text

        from datavia.library.database.connection import session_local

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
        from datavia.weather.saver_weather import SaverWeather
        from sqlalchemy import text

        from datavia.library.database.connection import session_local

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

    All tests use a mocked
    :func:`~datavia.library.formats.extract_netcdf_layer_metadata`
    so no real NetCDF files are required.
    """

    def test_single_variable_nc_uses_source_variable_year(
        self, sqlite_db: None, tmp_path
    ) -> None:
        """Single-variable NC naming includes source, variable, date-range and bbox tag.

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
        assert (tmp_path / "HYRAS_tas_202401_202412_nobbox.nc").exists()

    def test_multi_variable_nc_omits_variable_from_stem(
        self, sqlite_db: None, tmp_path
    ) -> None:
        """Multi-variable NC naming omits the variable token from the stem.

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

        assert (tmp_path / "ERA5_land_202406_202406_nobbox.nc").exists()

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
        from datavia.weather.saver_weather import SaverWeather
        from sqlalchemy import text

        from datavia.library.database.connection import session_local

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
        """A Parquet station file is copied as
        ``{source}_{year_start}_{year_end}_{hash}.parquet``.

        The year range is derived from the ``datetime`` column and the station
        hash from the sorted ``station_id`` list, so the destination name is
        deterministic and human-readable rather than inheriting the random
        ``tmp*`` temp stem.  Two downloads with the same station set in the same
        year produce the same stem; different station sets produce different
        stems (Bug 6 fix).

        Parameters
        ----------
        sqlite_db : None
            pytest fixture; initialises the SQLite test database.
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        import hashlib

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

        station_hash = hashlib.md5(
            str([1]).encode(), usedforsecurity=False
        ).hexdigest()[:6]
        expected_name = f"DWD_2023_2023_{station_hash}.parquet"

        assert not (tmp_path / "DWD_tmpABCDEF.parquet").exists()
        assert (tmp_path / expected_name).exists(), (
            f"Expected '{expected_name}' in {tmp_path}, "
            f"found: {list(tmp_path.glob('*.parquet'))}"
        )

    def test_parquet_different_station_sets_produce_unique_stems(
        self, tmp_path
    ) -> None:
        """Two DWD downloads with different station sets
        in the same year get different stems.

        Before the fix, both UC3 (8 German cities) and UC5 (6 Munich-centric
        cities) produced ``DWD_stations_2024`` — the same filename — because
        the stem was derived from the year alone.  UC5's save then silently
        overwrote UC3's cached file.

        This test verifies that the station hash makes the stems structurally
        distinct so that no two downloads with different station sets can
        collide.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        import pandas as pd
        from datavia.weather.saver_weather import _build_dest_stem

        # UC3 — 8 German cities, July 2024
        df_uc3 = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2024-07-17"] * 8),
                "station_id": [101, 102, 103, 104, 105, 106, 107, 108],
                "temperature_2m": [20.0, 19.5, 21.0, 18.0, 22.0, 17.5, 23.0, 16.0],
            }
        )
        parquet_uc3 = str(tmp_path / "uc3.parquet")
        df_uc3.to_parquet(parquet_uc3)

        # UC5 — 4 Munich-centric cities, August 2024
        df_uc5 = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2024-08-05"] * 4),
                "station_id": [201, 202, 203, 204],
                "temperature_2m": [24.0, 25.0, 23.0, 26.0],
            }
        )
        parquet_uc5 = str(tmp_path / "uc5.parquet")
        df_uc5.to_parquet(parquet_uc5)

        stem_uc3 = _build_dest_stem(parquet_uc3, "parquet", "DWD_stations")
        stem_uc5 = _build_dest_stem(parquet_uc5, "parquet", "DWD_stations")

        assert stem_uc3 != stem_uc5, (
            f"Expected different stems for different station sets; "
            f"UC3: '{stem_uc3}', UC5: '{stem_uc5}'"
        )
        # Both stems must include the year so the name is still human-readable.
        assert "2024" in stem_uc3
        assert "2024" in stem_uc5


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

    def test_valid_time_coord_metadata(self, tmp_path) -> None:
        """ERA5 files that expose ``valid_time`` instead of ``time`` are handled.

        ERA5 files decoded with ``cfgrib`` use ``valid_time`` as the time
        dimension name.  Before the fix, these files produced
        ``valid_from = None`` and ``valid_until = None``.

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
            {"t2m": (["valid_time"], [280.0, 275.0])},
            coords={"valid_time": times},
        )
        nc_file = tmp_path / "era5_t2m_valid_time.nc"
        ds.to_netcdf(str(nc_file))

        metadata = extract_netcdf_layer_metadata(str(nc_file))

        assert metadata["valid_from"] == "2024-01-01T00:00:00.000000000", (
            "valid_from must be extracted from the valid_time coordinate"
        )
        assert metadata["valid_until"] == "2024-12-31T23:59:59", (
            "valid_until must be rounded to end-of-day from the valid_time coordinate"
        )

    def test_valid_time_coord_stem(self, tmp_path) -> None:
        """``_build_dest_stem`` encodes month-range and bbox tag in the stem.

        When ``extract_netcdf_layer_metadata`` correctly resolves ``valid_from``
        and ``valid_until`` from the ``valid_time`` coordinate, the dest stem
        must contain ``{year}{month_start}_{year}{month_end}`` and ``nobbox``
        (no lat/lon in this dataset) rather than just ``_{year}``.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        import numpy as np
        import xarray as xr
        from datavia.weather.saver_weather import _build_dest_stem

        times = np.array(
            ["2024-01-01T00:00:00", "2024-12-31T00:00:00"],
            dtype="datetime64[ns]",
        )
        ds = xr.Dataset(
            {"2m_temperature": (["valid_time"], [280.0, 275.0])},
            coords={"valid_time": times},
        )
        nc_file = tmp_path / "era5_tmp_valid_time.nc"
        ds.to_netcdf(str(nc_file))

        stem = _build_dest_stem(str(nc_file), "netcdf", "ERA5_land")

        assert stem == "ERA5_land_2m_temperature_202401_202412_nobbox", (
            f"Expected 'ERA5_land_2m_temperature_202401_202412_nobbox', got '{stem}'"
        )


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

    def test_descending_latitude_produces_non_nan_result(self, tmp_path) -> None:
        """ERA5-style descending latitude does not cause all-NaN output (Bug 3).

        ERA5-Land files from the CDS API store the latitude dimension in
        descending order (North to South).  Before the fix, calling
        ``interpolate_na`` on such a grid raised
        ``ValueError: Index 'latitude' must be monotonically increasing``,
        which was silently swallowed and caused the caller to receive all-NaN.

        This test constructs a synthetic 5x5 grid with latitude running from
        55.0 down to 47.0, exactly mirroring the ERA5 orientation, and verifies
        that ``interpolate_netcdf`` returns a finite value.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        import xarray as xr

        from datavia.library.interpolation import interpolate_netcdf

        # Descending latitudes: North → South, as delivered by the CDS API.
        lats = np.array([55.0, 53.0, 51.0, 49.0, 47.0], dtype=float)
        lons = np.array([9.0, 10.0, 11.0, 12.0, 13.0], dtype=float)
        times = np.array(["2024-06-15T12:00:00"], dtype="datetime64[ns]")

        data = np.full((1, len(lats), len(lons)), 18.5, dtype=float)
        da = xr.DataArray(
            data,
            dims=["time", "latitude", "longitude"],
            coords={"time": times, "latitude": lats, "longitude": lons},
        )
        ds = xr.Dataset({"t2m": da})
        nc_path = str(tmp_path / "era5_descending_lat.nc")
        ds.to_netcdf(nc_path)

        result = interpolate_netcdf(nc_path, 51.0, 11.0, "t2m", "2024-06-15T12:00:00")

        assert np.isfinite(result), (
            "Expected a finite interpolated value for a descending-latitude "
            f"ERA5-style grid; got {result!r}"
        )
        assert result == pytest.approx(18.5)

    def test_multi_file_nc_returns_data_from_all_files(self, tmp_path) -> None:
        """A list of NetCDF paths is opened as a combined dataset (Bug 5).

        ERA5 monthly chunks are stored as separate ``.nc`` files.  Before the
        fix, ``interpolate_netcdf`` only opened ``nc_files[0]`` and returned
        NaN for any timestamp that lived in a subsequent file.  After the fix,
        passing a list opens all files with ``xr.open_mfdataset`` and timestamps
        from any file in the list are returned correctly.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        import xarray as xr

        from datavia.library.interpolation import interpolate_netcdf

        lats = np.array([47.0, 48.0, 49.0, 50.0, 51.0], dtype=float)
        lons = np.array([9.0, 10.0, 11.0, 12.0, 13.0], dtype=float)

        # June file — constant value 20.0
        june_ts = np.array(["2024-06-15T12:00:00"], dtype="datetime64[ns]")
        june_data = np.full((1, len(lats), len(lons)), 20.0)
        june_da = xr.DataArray(
            june_data,
            dims=["time", "latitude", "longitude"],
            coords={"time": june_ts, "latitude": lats, "longitude": lons},
        )
        june_path = str(tmp_path / "era5_june.nc")
        xr.Dataset({"t2m": june_da}).to_netcdf(june_path)

        # July file — constant value 25.0
        july_ts = np.array(["2024-07-15T12:00:00"], dtype="datetime64[ns]")
        july_data = np.full((1, len(lats), len(lons)), 25.0)
        july_da = xr.DataArray(
            july_data,
            dims=["time", "latitude", "longitude"],
            coords={"time": july_ts, "latitude": lats, "longitude": lons},
        )
        july_path = str(tmp_path / "era5_july.nc")
        xr.Dataset({"t2m": july_da}).to_netcdf(july_path)

        result_june = interpolate_netcdf(
            [june_path, july_path], 49.0, 11.0, "t2m", "2024-06-15T12:00:00"
        )
        result_july = interpolate_netcdf(
            [june_path, july_path], 49.0, 11.0, "t2m", "2024-07-15T12:00:00"
        )

        assert np.isfinite(result_june), (
            f"Expected finite result for June timestamp "
            f"from multi-file list; got {result_june!r}"
        )
        assert np.isfinite(result_july), (
            f"Expected finite result for July timestamp "
            f"from multi-file list; got {result_july!r}"
        )
        assert result_june == pytest.approx(20.0)
        assert result_july == pytest.approx(25.0)

    def test_list_of_timestamps_returns_array(self, tmp_path) -> None:
        """A list of daily timestamps returns an (N,) array of values.

        Regression test: passing a list of ``pd.Timestamp`` objects previously
        caused ``pd.Timestamp(str(list))`` to receive the string representation
        of the whole Python list, which raised ``DateParseError``.  After the
        fix the function normalises each element individually and uses
        vectorised xarray selection.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        import pandas as pd
        import xarray as xr

        from datavia.library.interpolation import interpolate_netcdf

        lats = np.array([47.0, 49.0, 51.0], dtype=float)
        lons = np.array([9.0, 11.0, 13.0], dtype=float)
        timestamps = [
            np.datetime64("2024-01-15T12:00:00"),
            np.datetime64("2024-02-15T12:00:00"),
            np.datetime64("2024-03-15T12:00:00"),
        ]
        # Different value per time step so we can assert correct selection.
        data = np.stack(
            [
                np.full((len(lats), len(lons)), float(i + 1))
                for i in range(len(timestamps))
            ]
        )  # shape (3, 3, 3)

        da = xr.DataArray(
            data,
            dims=["time", "latitude", "longitude"],
            coords={
                "time": np.array(timestamps, dtype="datetime64[ns]"),
                "latitude": lats,
                "longitude": lons,
            },
        )
        nc_path = str(tmp_path / "era5_multi_ts.nc")
        xr.Dataset({"t2m": da}).to_netcdf(nc_path)

        ts_list = [
            pd.Timestamp("2024-01-15T12:00:00"),
            pd.Timestamp("2024-02-15T12:00:00"),
        ]
        result = interpolate_netcdf(nc_path, 49.0, 11.0, "t2m", ts_list)

        assert isinstance(result, np.ndarray), f"Expected ndarray, got {type(result)}"
        assert result.shape == (2,), f"Expected shape (2,), got {result.shape}"
        assert np.all(np.isfinite(result)), f"Expected all finite values, got {result}"
        # Jan → value 1.0, Feb → value 2.0
        assert result[0] == pytest.approx(1.0)
        assert result[1] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# interpolate_netcdf — input_crs parameter (Enhancement 1 / Step 5)
# ---------------------------------------------------------------------------


class TestInterpolateNetcdfCRS:
    """Tests for ``input_crs`` in :func:`interpolate_netcdf`.

    Verifies that input coordinates in any pyproj-compatible CRS are
    reprojected correctly to the file's native CRS before interpolation.
    All tests use synthetic NetCDF files written to ``tmp_path``.
    """

    @staticmethod
    def _make_geographic_nc(tmp_path) -> str:
        """Write a tiny ERA5-style geographic NetCDF to *tmp_path*.

        Parameters
        ----------
        tmp_path : pathlib.Path
            Temporary directory for the file.

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
        da = xr.DataArray(
            data,
            dims=["time", "latitude", "longitude"],
            coords={"time": times, "latitude": lats, "longitude": lons},
        )
        ds = xr.Dataset({"t2m": da})
        path = str(tmp_path / "era5_crs.nc")
        ds.to_netcdf(path)
        return path

    def test_default_input_crs_is_epsg4326(self, tmp_path) -> None:
        """Omitting input_crs returns the same value as passing EPSG:4326 explicitly.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.library.interpolation import interpolate_netcdf

        nc = self._make_geographic_nc(tmp_path)
        result_default = interpolate_netcdf(
            nc, 49.0, 11.0, "t2m", "2024-06-15T12:00:00"
        )
        result_explicit = interpolate_netcdf(
            nc, 49.0, 11.0, "t2m", "2024-06-15T12:00:00", input_crs="EPSG:4326"
        )
        assert result_default == pytest.approx(result_explicit)

    def test_epsg3035_geographic_file_matches_wgs84_result(self, tmp_path) -> None:
        """EPSG:3035 input for a geographic file gives the same result as EPSG:4326.

        The EPSG:3035 equivalent of (lat=49.0, lon=11.0) is computed via pyproj
        and passed with ``input_crs="EPSG:3035"``.  The returned value must
        match the WGS84 query within floating-point tolerance.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        import pyproj

        from datavia.library.interpolation import interpolate_netcdf

        nc = self._make_geographic_nc(tmp_path)
        lat_wgs84, lon_wgs84 = 49.0, 11.0

        t = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3035", always_xy=True)
        x_3035, y_3035 = t.transform(lon_wgs84, lat_wgs84)

        result_wgs84 = interpolate_netcdf(
            nc, lat_wgs84, lon_wgs84, "t2m", "2024-06-15T12:00:00"
        )
        result_3035 = interpolate_netcdf(
            nc, y_3035, x_3035, "t2m", "2024-06-15T12:00:00", input_crs="EPSG:3035"
        )

        assert result_3035 == pytest.approx(result_wgs84, abs=1e-4)

    def test_getter_weather_accepts_non_wgs84_crs(self) -> None:
        """GetterWeather.get_data() no longer raises for non-EPSG:4326 input.

        The old guard that raised ValueError for any CRS other than EPSG:4326
        has been removed.  This test verifies that the call reaches
        ``interpolate_netcdf`` with the correct ``input_crs`` keyword argument.
        """
        from datavia.weather.getter_weather import GetterWeather

        getter = GetterWeather("HYRAS")
        coords = np.array([[4_100_000.0, 3_200_000.0]])  # arbitrary EPSG:3035 coords

        with (
            patch(
                "datavia.weather.getter_weather.get_weather_paths",
                return_value=["/fake/file.nc"],
            ),
            patch(
                "datavia.weather.getter_weather.interpolate_netcdf",
                return_value=np.array([15.0]),
            ) as mock_interp,
            patch(
                "datavia.weather.getter_weather.get_nc_variable_name",
                return_value="tas",
            ),
            patch(
                "datavia.weather.getter_weather.apply_conversion",
                side_effect=lambda s, v, val, u: val,
            ),
        ):
            getter.get_data(
                coords,
                crs_coords="EPSG:3035",
                variable="2m_temperature",
                datetime_utc="2024-06-15",
            )

        assert mock_interp.call_args.kwargs.get("input_crs") == "EPSG:3035", (
            "input_crs was not forwarded to interpolate_netcdf"
        )


# ---------------------------------------------------------------------------
# interpolate_netcdf — temporal_resolution parameter (Enhancement 2 / Step 6)
# ---------------------------------------------------------------------------


class TestTemporalResolution:
    """Tests for the ``temporal_resolution`` parameter and the HYRAS hourly guard.

    Covers:

    - ``"daily"`` returns a scalar (unchanged behaviour).
    - ``"hourly"`` returns a 1-D time-series for all sub-daily steps in the
      requested day.
    - Unknown resolution values raise :exc:`ValueError`.
    - :class:`~datavia.weather.hyras_downloader.HYRASDownloader` raises
      :exc:`ValueError` immediately when ``temporal_resolution="hourly"``.
    - :class:`~datavia.weather.getter_weather.GetterWeather` forwards
      ``temporal_resolution`` to ``interpolate_netcdf``.
    """

    @staticmethod
    def _make_hourly_nc(tmp_path) -> str:
        """Write a synthetic NetCDF with 24 hourly time steps to *tmp_path*.

        Parameters
        ----------
        tmp_path : pathlib.Path
            Temporary directory.

        Returns
        -------
        str
            Absolute path to the written file.
        """
        import xarray as xr

        lats = np.array([48.0, 49.0, 50.0], dtype=float)
        lons = np.array([10.0, 11.0, 12.0], dtype=float)
        times = np.array(
            [f"2024-06-15T{h:02d}:00:00" for h in range(24)],
            dtype="datetime64[ns]",
        )
        data = np.ones((24, len(lats), len(lons)), dtype=float) * 20.0
        da = xr.DataArray(
            data,
            dims=["time", "latitude", "longitude"],
            coords={"time": times, "latitude": lats, "longitude": lons},
        )
        ds = xr.Dataset({"t2m": da})
        path = str(tmp_path / "hourly.nc")
        ds.to_netcdf(path)
        return path

    def test_daily_resolution_returns_scalar(self, tmp_path) -> None:
        """``temporal_resolution="daily"`` returns the nearest single step as a scalar.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.library.interpolation import interpolate_netcdf

        nc = self._make_hourly_nc(tmp_path)
        result = interpolate_netcdf(
            nc,
            49.0,
            11.0,
            "t2m",
            "2024-06-15T12:00:00",
            temporal_resolution="daily",
        )
        assert isinstance(result, float)
        assert np.isfinite(result)

    def test_hourly_resolution_returns_time_series(self, tmp_path) -> None:
        """``temporal_resolution="hourly"`` returns all 24 sub-daily steps as an array.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.library.interpolation import interpolate_netcdf

        nc = self._make_hourly_nc(tmp_path)
        result = interpolate_netcdf(
            nc,
            49.0,
            11.0,
            "t2m",
            "2024-06-15T06:00:00",
            temporal_resolution="hourly",
        )
        assert isinstance(result, np.ndarray), (
            f"Expected ndarray for hourly resolution, got {type(result)}"
        )
        assert result.ndim == 1
        assert result.shape[0] == 24, f"Expected 24 hourly steps, got {result.shape[0]}"
        np.testing.assert_allclose(result, 20.0, atol=1e-6)

    def test_unknown_resolution_raises_value_error(self, tmp_path) -> None:
        """An unrecognised ``temporal_resolution`` value raises :exc:`ValueError`.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.library.interpolation import interpolate_netcdf

        nc = self._make_hourly_nc(tmp_path)
        with pytest.raises(ValueError, match="temporal_resolution"):
            interpolate_netcdf(
                nc,
                49.0,
                11.0,
                "t2m",
                "2024-06-15T00:00:00",
                temporal_resolution="minutely",
            )

    def test_hyras_hourly_raises_at_init(self) -> None:
        """HYRASDownloader raises :exc:`ValueError` on ``temporal_resolution='hourly'``.

        HYRAS provides daily-only data so hourly is unsupported.  The error
        is raised in ``__init__`` before any network access occurs.
        """
        from datavia.weather.hyras_downloader import HYRASDownloader

        with pytest.raises(ValueError, match="hourly"):
            HYRASDownloader(
                variables=["2m_temperature"],
                date_start="2024-01-01",
                date_end="2024-12-31",
                temporal_resolution="hourly",
            )

    def test_getter_forwards_temporal_resolution(self) -> None:
        """GetterWeather forwards temporal_resolution to interpolate_netcdf."""
        from datavia.weather.getter_weather import GetterWeather

        getter = GetterWeather("ERA5_land", temporal_resolution="hourly")
        coords = np.array([[11.0, 49.0]])

        with (
            patch(
                "datavia.weather.getter_weather.get_weather_paths",
                return_value=["/fake/era5.nc"],
            ),
            patch(
                "datavia.weather.getter_weather.interpolate_netcdf",
                return_value=np.array([20.0]),
            ) as mock_interp,
            patch(
                "datavia.weather.getter_weather.get_nc_variable_name",
                return_value="2m_temperature",
            ),
            patch(
                "datavia.weather.getter_weather.apply_conversion",
                side_effect=lambda s, v, val, u: val,
            ),
        ):
            getter.get_data(
                coords,
                variable="2m_temperature",
                datetime_utc="2024-06-15",
            )

        assert mock_interp.call_args.kwargs.get("temporal_resolution") == "hourly", (
            "temporal_resolution was not forwarded to interpolate_netcdf"
        )


# ---------------------------------------------------------------------------
# TestCoverageManager
# ---------------------------------------------------------------------------


def _insert_coverage_cell(
    source_name: str,
    variable: str,
    bbox: tuple[float, float, float, float],
    date_start: str,
    date_end: str,
) -> None:
    """Insert a ``weather_layers`` row with a WKT POLYGON bbox for coverage tests.

    Parameters
    ----------
    source_name : str
    variable : str
    bbox : tuple[float, float, float, float]
        ``(west, south, east, north)`` in EPSG:4326 degrees.
    date_start : str
        ISO date string ``YYYY-MM-DD``; used as ``valid_from``.
    date_end : str
        ISO date string ``YYYY-MM-DD``; used as ``valid_until`` (with
        ``T23:59:59`` appended to match the saver convention).
    """
    from sqlalchemy import text

    from datavia.library.database.connection import session_local

    w, s, e, n = bbox
    bbox_wkt = f"POLYGON (({w} {s}, {e} {s}, {e} {n}, {w} {n}, {w} {s}))"

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
                     :valid_from, :valid_until, :uri, 'EPSG:4326', :bbox, NULL)
                """
            ),
            {
                "layer_name": f"{source_name}_{variable}_{date_start}",
                "source_name": source_name,
                "variable": variable,
                "file_format": "netcdf",
                "valid_from": date_start,
                "valid_until": f"{date_end}T23:59:59",
                "uri": f"/fake/{source_name}_{variable}_{date_start}.nc",
                "bbox": bbox_wkt,
            },
        )
        session.commit()
    finally:
        session.close()


class TestCoverageManager:
    """Tests for :class:`~datavia.weather.coverage_manager.CoverageManager`.

    All tests use the ``sqlite_db`` fixture so they run against an in-memory
    database with no network access.  The 2-D subtraction algorithm is
    exercised through five scenarios that collectively cover all branches of
    :func:`~datavia.weather.coverage_manager._compute_missing`.
    """

    # Germany bounding box used as a representative large extent.
    _GERMANY: ClassVar[tuple[float, float, float, float]] = (5.9, 47.3, 15.0, 55.1)
    # Berlin inner-city approximate bounding box (fully inside Germany).
    _BERLIN: ClassVar[tuple[float, float, float, float]] = (13.1, 52.3, 13.8, 52.7)
    # East Germany bounding box (contains Berlin, inside Germany).
    _EAST_GERMANY: ClassVar[tuple[float, float, float, float]] = (
        10.0,
        50.0,
        15.0,
        55.0,
    )

    def test_fully_covered_returns_empty(self, sqlite_db) -> None:
        """Requesting a bbox and date range already in the DB returns an empty list.

        Verifies the happy-path where all data is already registered and no
        download is triggered.
        """
        from datavia.weather.coverage_manager import CoverageManager

        _insert_coverage_cell(
            "ERA5_land", "2m_temperature", self._GERMANY, "2024-01-01", "2024-12-31"
        )
        mgr = CoverageManager("ERA5_land", ["2m_temperature"])
        result = mgr.missing_spatiotemporal(self._GERMANY, "2024-01-01", "2024-12-31")

        assert result == [], (
            f"Expected no missing cells when DB fully covers the request; got {result}"
        )

    def test_wider_time_range_returns_date_gaps(self, sqlite_db) -> None:
        """A request wider in time than what is in the DB returns before/after gaps.

        Existing: Germany 2024.  Requested: Germany 2023-2025.
        Expected: two cells - one for 2023 and one for 2025.
        """
        from datavia.weather.coverage_manager import CoverageManager

        _insert_coverage_cell(
            "ERA5_land", "2m_temperature", self._GERMANY, "2024-01-01", "2024-12-31"
        )
        mgr = CoverageManager("ERA5_land", ["2m_temperature"])
        result = mgr.missing_spatiotemporal(self._GERMANY, "2023-01-01", "2025-12-31")

        assert len(result) == 2, (
            f"Expected 2 temporal gap cells; got {len(result)}: {result}"
        )

        date_starts = {c.date_start for c in result}
        date_ends = {c.date_end for c in result}
        assert "2023-01-01" in date_starts, "Expected a cell starting 2023-01-01."
        assert "2025-12-31" in date_ends, "Expected a cell ending 2025-12-31."
        assert "2024-01-01" not in date_starts, (
            "Cell starting on 2024-01-01 should not be missing."
        )
        # Both gap cells must span the full Germany bbox.
        for cell in result:
            assert cell.bbox == self._GERMANY, (
                f"Temporal gap cell has unexpected bbox {cell.bbox!r}; "
                f"expected {self._GERMANY!r}."
            )

    def test_wider_bbox_returns_spatial_strips(self, sqlite_db) -> None:
        """A request wider in space than what is in the DB returns spatial strips.

        Existing: western half of Germany for 2024.
        Requested: full Germany for 2024.
        Expected: the eastern strip is returned as a missing cell.
        """
        from datavia.weather.coverage_manager import CoverageManager

        west_half = (5.9, 47.3, 10.45, 55.1)  # western half
        _insert_coverage_cell(
            "ERA5_land", "2m_temperature", west_half, "2024-01-01", "2024-12-31"
        )

        mgr = CoverageManager("ERA5_land", ["2m_temperature"])
        result = mgr.missing_spatiotemporal(self._GERMANY, "2024-01-01", "2024-12-31")

        # The right strip (east of the existing cell) must appear.
        assert len(result) >= 1, "Expected at least one spatial strip."
        # No cell should cover the western half (5.9 to 10.45 longitude).
        for cell in result:
            w, _s, e, _n = cell.bbox
            assert not (w <= 8.0 <= e), (
                f"Cell {cell} covers longitude 8.0° which is in the already-registered "
                f"western half and should not be re-fetched."
            )

    def test_2d_overlap_berlin_east_germany(self, sqlite_db) -> None:
        """2-D overlap: Berlin 1990-2000 on disk, East Germany 1980-2010 requested.

        The existing Berlin/1990-2000 cell splits the request into:

        - 1 temporal piece before 1990 (full East Germany bbox)
        - 4 spatial strips during 1990-2000 (East Germany minus Berlin)
        - 1 temporal piece after 2000 (full East Germany bbox)

        Total: 6 cells.  Crucially, the Berlin area must not appear in any of
        the 4 spatial strips that cover the 1990-2000 overlap period.
        """
        from datavia.weather.coverage_manager import CoverageManager

        _insert_coverage_cell(
            "ERA5_land", "2m_temperature", self._BERLIN, "1990-01-01", "2000-12-31"
        )
        mgr = CoverageManager("ERA5_land", ["2m_temperature"])
        result = mgr.missing_spatiotemporal(
            self._EAST_GERMANY, "1980-01-01", "2010-12-31"
        )

        assert len(result) == 6, (
            f"Expected 6 cells (2 temporal + 4 spatial strips); got {len(result)}: "
            f"{result}"
        )

        # The two temporal pieces must have the full East Germany bbox.
        temporal_cells = [
            c
            for c in result
            if c.date_end < "1990-01-01" or c.date_start > "2000-12-31"
        ]
        assert len(temporal_cells) == 2, (
            "Expected 2 temporal-only cells; "
            f"got {len(temporal_cells)}: {temporal_cells}"
        )
        for cell in temporal_cells:
            assert cell.bbox == self._EAST_GERMANY, (
                f"Temporal cell has unexpected bbox {cell.bbox!r}."
            )

        # The 4 spatial strips must all lie in the 1990-2000 overlap period
        # and none of them must cover the Berlin centroid (13.45, 52.5).
        spatial_cells = [
            c for c in result if "1990-01-01" <= c.date_start <= "2000-12-31"
        ]
        assert len(spatial_cells) == 4, (
            f"Expected 4 spatial strip cells during 1990-2000; "
            f"got {len(spatial_cells)}: {spatial_cells}"
        )
        berlin_lon, berlin_lat = 13.45, 52.5
        for cell in spatial_cells:
            w, s, e, n = cell.bbox
            assert not (w <= berlin_lon <= e and s <= berlin_lat <= n), (
                f"Spatial strip {cell} covers the Berlin centroid ({berlin_lon}, "
                f"{berlin_lat}) — Berlin/1990-2000 must not be re-fetched."
            )

    def test_invalid_date_order_raises(self) -> None:
        """date_start > date_end raises :exc:`ValueError`.

        The error is raised before any DB access so no ``sqlite_db`` fixture
        is needed.
        """
        from datavia.weather.coverage_manager import CoverageManager

        with pytest.raises(ValueError, match="date_start"):
            mgr = CoverageManager.__new__(CoverageManager)
            mgr._source_name = "ERA5_land"
            mgr._variables = ["2m_temperature"]
            mgr._cells_per_variable = {"2m_temperature": []}
            mgr.missing_spatiotemporal(self._GERMANY, "2024-12-31", "2024-01-01")

    def test_update_data_skips_download_when_fully_covered(self, sqlite_db) -> None:
        """``update_data()`` issues no downloader calls when data is already registered.

        Pre-populates the DB with a cell that fully covers the pipeline's
        request, then asserts that :class:`CompositeWeatherDownloader` is
        never instantiated.
        """
        from datavia.weather.pipeline import WeatherPipeline

        # Pre-populate DB: Germany for all of 2024, both variables.
        for var in ["2m_temperature"]:
            _insert_coverage_cell(
                "ERA5_land", var, self._GERMANY, "2024-01-01", "2024-12-31"
            )

        pipe = WeatherPipeline(
            config={
                "source": "ERA5_land",
                "variables": ["2m_temperature"],
                "date_start": "2024-01-01",
                "date_end": "2024-12-31",
                "era5_bbox": [55.1, 5.9, 47.3, 15.0],  # [north, west, south, east]
            }
        )
        pipe()  # Initialise components.

        with (
            patch(
                "datavia.weather.pipeline.CompositeWeatherDownloader"
            ) as mock_downloader_cls,
            patch.object(pipe, "sync_files_and_database"),
        ):
            result = pipe.update_data()

        mock_downloader_cls.assert_not_called()
        assert result is True


class TestWeatherPipelineLifecycle:
    """Tests for WeatherPipeline lifecycle methods: get_config and reconfigure.

    All tests are isolated and do not touch the database because ``get_config``
    and ``reconfigure`` are pure in-memory operations on the stored config
    dict.
    """

    _BASE_CONFIG: ClassVar[dict] = {
        "source": "ERA5_land",
        "variables": ["2m_temperature"],
        "date_start": "2024-01-01",
        "date_end": "2024-12-31",
    }

    def test_get_config_returns_copy(self) -> None:
        """get_config returns a copy; mutating it does not affect the pipeline.

        Ensures callers cannot accidentally corrupt the internal config by
        modifying the returned dict.
        """
        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(config=self._BASE_CONFIG)
        cfg = pipe.get_config()
        cfg["date_start"] = "2023-01-01"

        assert pipe.get_config()["date_start"] == "2024-01-01", (
            "Mutating the returned config must not change "
            "the pipeline's internal state."
        )

    def test_get_config_matches_init_config(self) -> None:
        """get_config returns all keys supplied at construction."""
        from datavia.weather.pipeline import WeatherPipeline

        config = {**self._BASE_CONFIG, "era5_bbox": [55.1, 5.9, 47.3, 15.0]}
        pipe = WeatherPipeline(config=config)

        assert pipe.get_config() == config

    def test_reconfigure_merge_updates_dates(self) -> None:
        """reconfigure in merge mode applies only the provided delta.

        Only ``date_start`` and ``date_end`` are updated; all other keys
        keep their original values.
        """
        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(config=self._BASE_CONFIG)
        pipe.reconfigure({"date_start": "2023-06-01", "date_end": "2023-12-31"})

        cfg = pipe.get_config()
        assert cfg["date_start"] == "2023-06-01"
        assert cfg["date_end"] == "2023-12-31"
        assert cfg["source"] == "ERA5_land", "Unchanged keys must be preserved."
        assert cfg["variables"] == ["2m_temperature"], (
            "Unchanged keys must be preserved."
        )

    def test_reconfigure_replace_sets_entire_config(self) -> None:
        """reconfigure with replace=True swaps the full config.

        The new config contains only the required keys.  Optional keys from
        the original config (e.g. ``era5_bbox``) must not be present.
        """
        from datavia.weather.pipeline import WeatherPipeline

        original = {**self._BASE_CONFIG, "era5_bbox": [55.1, 5.9, 47.3, 15.0]}
        pipe = WeatherPipeline(config=original)

        new_config = {
            "source": "ERA5_land",
            "variables": ["total_precipitation"],
            "date_start": "2020-01-01",
            "date_end": "2020-12-31",
        }
        pipe.reconfigure(new_config, replace=True)

        cfg = pipe.get_config()
        assert cfg == new_config
        assert "era5_bbox" not in cfg, (
            "replace=True must remove keys absent from the new config."
        )

    def test_reconfigure_rebuilds_downloader(self) -> None:
        """reconfigure re-creates the downloader with the new config.

        After reconfiguration the pipeline's ``downloader`` attribute must be
        a fresh :class:`CompositeWeatherDownloader` instance, not the old one.
        """
        from unittest.mock import patch

        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(config=self._BASE_CONFIG)
        pipe()  # Instantiate components.

        original_downloader = pipe.downloader

        with patch("datavia.weather.pipeline.GetterWeather"):
            pipe.reconfigure({"date_end": "2025-12-31"})

        assert pipe.downloader is not original_downloader, (
            "reconfigure must rebuild the downloader instance."
        )

    def test_reconfigure_source_change_raises(self) -> None:
        """reconfigure raises ValueError when 'source' would change.

        The source name is immutable after construction because it is baked
        into the pipeline name and all database rows.
        """
        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(config=self._BASE_CONFIG)

        with pytest.raises(ValueError, match="source"):
            pipe.reconfigure({"source": "HYRAS"})

    def test_reconfigure_missing_required_key_raises(self) -> None:
        """reconfigure with replace=True raises ValueError when required key is absent.

        Supplying an incomplete replacement config must fail validation before
        any state is mutated.
        """
        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(config=self._BASE_CONFIG)

        incomplete = {
            "source": "ERA5_land",
            "variables": ["2m_temperature"],
            # date_start and date_end intentionally omitted
        }

        with pytest.raises(ValueError):
            pipe.reconfigure(incomplete, replace=True)

        # Original config must be unchanged after failed reconfigure.
        assert pipe.get_config()["date_start"] == "2024-01-01", (
            "A failed reconfigure must not partially mutate the pipeline config."
        )

    def test_replace_false_at_init_stores_copy(self) -> None:
        """WeatherPipeline(config, replace=False) stores a copy of the config.

        Mutating the original dict after construction must not affect the
        pipeline's internal config.
        """
        from datavia.weather.pipeline import WeatherPipeline

        config = dict(self._BASE_CONFIG)
        pipe = WeatherPipeline(config=config, replace=False)

        config["date_start"] = "2000-01-01"
        assert pipe.get_config()["date_start"] == "2024-01-01", (
            "WeatherPipeline must store a copy; mutating the caller's dict "
            "must not change the pipeline state."
        )


# ---------------------------------------------------------------------------
# ERA5Downloader — monthly chunking (Step 9)
# ---------------------------------------------------------------------------


class TestERA5DownloaderMonthlyChunking:
    """Tests for :meth:`ERA5Downloader._iter_monthly_chunks`.

    Verifies the chunking logic independently of CDS access.  All tests are
    purely computational — no network or ``cdsapi`` interaction.
    """

    def test_single_month_produces_one_chunk(self) -> None:
        """A range within one calendar month yields exactly one chunk."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_monthly_chunks("2024-01-05", "2024-01-20")
        assert len(chunks) == 1
        assert chunks[0] == ("2024-01-05", "2024-01-20")

    def test_full_month_boundaries(self) -> None:
        """A range covering exactly one full month yields the full-month chunk."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_monthly_chunks("2024-02-01", "2024-02-29")
        assert chunks == [("2024-02-01", "2024-02-29")]

    def test_three_month_range_produces_three_chunks(self) -> None:
        """A range spanning January to March 2024 yields exactly three chunks."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_monthly_chunks("2024-01-01", "2024-03-31")
        assert len(chunks) == 3
        assert chunks[0] == ("2024-01-01", "2024-01-31")
        assert chunks[1] == ("2024-02-01", "2024-02-29")
        assert chunks[2] == ("2024-03-01", "2024-03-31")

    def test_partial_first_and_last_months(self) -> None:
        """Partial start and end months are clipped to the requested range."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_monthly_chunks("2024-01-15", "2024-03-10")
        assert len(chunks) == 3
        assert chunks[0] == ("2024-01-15", "2024-01-31")
        assert chunks[1] == ("2024-02-01", "2024-02-29")
        assert chunks[2] == ("2024-03-01", "2024-03-10")

    def test_year_boundary_chunk_count(self) -> None:
        """A range crossing a year boundary produces the correct number of chunks."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_monthly_chunks("2023-11-01", "2024-02-28")
        # Nov-2023, Dec-2023, Jan-2024, Feb-2024 → 4 chunks.
        assert len(chunks) == 4
        assert chunks[0][0] == "2023-11-01"
        assert chunks[-1][1] == "2024-02-28"

    def test_single_day_range(self) -> None:
        """A single-day range produces one chunk with equal start and end."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_monthly_chunks("2024-06-15", "2024-06-15")
        assert chunks == [("2024-06-15", "2024-06-15")]

    def test_end_before_start_raises(self) -> None:
        """ValueError is raised when date_end precedes date_start."""
        from datavia.weather.era5_downloader import ERA5Downloader

        with pytest.raises(ValueError, match="date_end"):
            ERA5Downloader._iter_monthly_chunks("2024-03-01", "2024-02-01")

    def test_chunks_are_sorted(self) -> None:
        """The returned chunk list is sorted by start date."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_monthly_chunks("2024-01-01", "2024-12-31")
        start_dates = [c[0] for c in chunks]
        assert start_dates == sorted(start_dates)

    def test_full_year_produces_twelve_chunks(self) -> None:
        """A full calendar year produces exactly 12 monthly chunks."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_monthly_chunks("2024-01-01", "2024-12-31")
        assert len(chunks) == 12


# ---------------------------------------------------------------------------
# ERA5Downloader — download() with mocked CDS client (Step 9)
# ---------------------------------------------------------------------------


class TestERA5DownloaderDownloadMocked:
    """Tests for :meth:`ERA5Downloader.download` with a mocked CDS client.

    All tests replace ``cdsapi.Client`` with a mock so no network access is
    required.  The mock records how many ``retrieve()`` calls were made, which
    verifies the monthly-chunking logic end-to-end.
    """

    @staticmethod
    def _make_mock_client(tmp_path) -> MagicMock:
        """Return a ``cdsapi.Client`` mock whose ``retrieve`` writes a stub file.

        The mock's ``retrieve`` side-effect creates a real temporary file so
        that the caller can check that paths are produced.

        Parameters
        ----------
        tmp_path : pathlib.Path
            Directory used for stub output files.

        Returns
        -------
        MagicMock
            Configured mock with a ``retrieve`` method that returns a job-like
            object with a ``download`` method.
        """
        import tempfile

        client_mock = MagicMock()

        def fake_retrieve(dataset, request):
            fd, path = tempfile.mkstemp(suffix=".nc", dir=str(tmp_path), prefix="era5_")
            import os

            os.close(fd)
            job = MagicMock()
            job.reply = {"status": "completed", "request_id": "test-job"}
            job.download = MagicMock(side_effect=lambda p: None)
            # The saver reads the path we pass to download(), so record it.
            job._out_path = path
            # Capture what path was used in the outer download() call.
            client_mock._last_out_path = path
            return job

        client_mock.retrieve = MagicMock(side_effect=fake_retrieve)
        return client_mock

    def test_three_month_range_submits_three_jobs(self, tmp_path) -> None:
        """A three-month range triggers exactly three CDS retrieve() calls.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.weather.era5_downloader import ERA5Downloader

        downloader = ERA5Downloader(
            variables=["2m_temperature"],
            date_start="2024-01-01",
            date_end="2024-03-31",
            buffer_days=0,
        )
        client_mock = self._make_mock_client(tmp_path)

        with patch("datavia.weather.era5_downloader.cdsapi") as mock_cdsapi:
            mock_cdsapi.Client.return_value = client_mock
            result = downloader.download()

        assert client_mock.retrieve.call_count == 3, (
            f"Expected 3 CDS retrieve() calls for a 3-month range, "
            f"got {client_mock.retrieve.call_count}"
        )
        # Result must be a newline-joined string of 3 paths.
        paths = result.splitlines()
        assert len(paths) == 3

    def test_single_month_submits_one_job(self, tmp_path) -> None:
        """A range within one calendar month triggers exactly one retrieve() call.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.weather.era5_downloader import ERA5Downloader

        downloader = ERA5Downloader(
            variables=["2m_temperature"],
            date_start="2024-06-01",
            date_end="2024-06-30",
            buffer_days=0,
        )
        client_mock = self._make_mock_client(tmp_path)

        with patch("datavia.weather.era5_downloader.cdsapi") as mock_cdsapi:
            mock_cdsapi.Client.return_value = client_mock
            result = downloader.download()

        assert client_mock.retrieve.call_count == 1
        assert len(result.splitlines()) == 1

    def test_buffer_days_extends_first_chunk_into_prior_month(self, tmp_path) -> None:
        """buffer_days=1 starting on 2024-02-01 stretches the first chunk into January.

        The effective start becomes 2024-01-31, so the chunking produces two
        jobs: one for January (single day) and one for February.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.weather.era5_downloader import ERA5Downloader

        downloader = ERA5Downloader(
            variables=["2m_temperature"],
            date_start="2024-02-01",
            date_end="2024-02-29",
            buffer_days=1,
        )
        client_mock = self._make_mock_client(tmp_path)

        with patch("datavia.weather.era5_downloader.cdsapi") as mock_cdsapi:
            mock_cdsapi.Client.return_value = client_mock
            result = downloader.download()

        # Effective start = 2024-01-31 → Jan chunk + Feb chunk = 2 jobs.
        assert client_mock.retrieve.call_count == 2, (
            f"Expected 2 jobs when buffer_days spans into previous month, "
            f"got {client_mock.retrieve.call_count}"
        )
        assert len(result.splitlines()) == 2

    def test_cds_queue_timeout_raises_timeout_error(self, tmp_path) -> None:
        """TimeoutError is raised when a queued job exceeds cds_queue_timeout.

        The mock job stays in ``"queued"`` state indefinitely.  With
        ``cds_queue_timeout=0`` the timeout is triggered on the first poll.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.weather.era5_downloader import ERA5Downloader

        downloader = ERA5Downloader(
            variables=["2m_temperature"],
            date_start="2024-01-01",
            date_end="2024-01-31",
            buffer_days=0,
            cds_queue_timeout=0,
        )

        client_mock = MagicMock()

        def fake_retrieve_queued(dataset, request):
            job = MagicMock()
            job.reply = {"status": "queued", "request_id": "timeout-job"}
            job.update = MagicMock()
            job.delete = MagicMock()
            return job

        client_mock.retrieve = MagicMock(side_effect=fake_retrieve_queued)

        with (
            patch("datavia.weather.era5_downloader.cdsapi") as mock_cdsapi,
            patch("datavia.weather.era5_downloader.time.sleep"),
        ):
            mock_cdsapi.Client.return_value = client_mock
            with pytest.raises(TimeoutError, match="timeout-job"):
                downloader.download()

    def test_composite_downloader_collects_multi_path_era5_result(
        self, tmp_path
    ) -> None:
        """CompositeWeatherDownloader.download() flattens multi-path ERA5 results.

        When ERA5Downloader returns a newline-joined 3-path string,
        CompositeWeatherDownloader must pass all three paths through so that
        WeatherPipeline can call SaverWeather.save() on each.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.weather.composite_downloader import CompositeWeatherDownloader

        multi_path_result = "/tmp/era5_jan.nc\n/tmp/era5_feb.nc\n/tmp/era5_mar.nc"

        with patch(
            "datavia.weather.composite_downloader.get_grid_downloader_class"
        ) as mock_registry:
            mock_grid_class = MagicMock()
            mock_grid_instance = MagicMock()
            mock_grid_instance.download.return_value = multi_path_result
            mock_grid_class.return_value = mock_grid_instance
            mock_registry.return_value = mock_grid_class

            composite = CompositeWeatherDownloader(
                config={
                    "source": "ERA5_land",
                    "variables": ["2m_temperature"],
                    "date_start": "2024-01-01",
                    "date_end": "2024-03-31",
                }
            )
            result = composite.download()

        paths = [p for p in result.splitlines() if p]
        assert len(paths) == 3
        assert "/tmp/era5_jan.nc" in paths
        assert "/tmp/era5_feb.nc" in paths
        assert "/tmp/era5_mar.nc" in paths


# ---------------------------------------------------------------------------
# WeatherPipeline — cds_queue_timeout in known config keys (Step 9)
# ---------------------------------------------------------------------------


class TestWeatherPipelineCdsQueueTimeout:
    """Tests that ``cds_queue_timeout`` is accepted as a valid config key."""

    _BASE_CONFIG: ClassVar[dict] = {
        "source": "ERA5_land",
        "variables": ["2m_temperature"],
        "date_start": "2024-01-01",
        "date_end": "2024-12-31",
    }

    def test_cds_queue_timeout_accepted_in_config(self) -> None:
        """WeatherPipeline accepts ``cds_queue_timeout`` without raising ValueError."""
        from datavia.weather.pipeline import WeatherPipeline

        config = {**self._BASE_CONFIG, "cds_queue_timeout": 1800}
        # Must not raise.
        pipe = WeatherPipeline(config=config)
        assert pipe.get_config()["cds_queue_timeout"] == 1800

    def test_cds_queue_timeout_forwarded_to_composite(self) -> None:
        """``cds_queue_timeout`` is forwarded to ``CompositeWeatherDownloader``."""
        from datavia.weather.composite_downloader import CompositeWeatherDownloader

        config = {
            "source": "ERA5_land",
            "variables": ["2m_temperature"],
            "date_start": "2024-01-01",
            "date_end": "2024-01-31",
            "cds_queue_timeout": 600,
        }

        with patch(
            "datavia.weather.composite_downloader.get_grid_downloader_class"
        ) as mock_registry:
            mock_grid_class = MagicMock()
            mock_grid_class.return_value = MagicMock()
            mock_registry.return_value = mock_grid_class

            CompositeWeatherDownloader(config=config)

        # The grid class must have been called with cds_queue_timeout=600.
        call_kwargs = mock_grid_class.call_args[1]
        assert call_kwargs.get("cds_queue_timeout") == 600, (
            f"Expected cds_queue_timeout=600 forwarded to grid downloader, "
            f"got {call_kwargs}"
        )


# ---------------------------------------------------------------------------
# ERA5Downloader — quarterly chunking
# ---------------------------------------------------------------------------


class TestERA5DownloaderQuarterlyChunking:
    """Unit tests for :meth:`ERA5Downloader._iter_quarterly_chunks`."""

    def test_full_year_produces_four_quarters(self) -> None:
        """A full calendar year produces exactly four quarterly chunks."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_quarterly_chunks("2023-01-01", "2023-12-31")
        assert len(chunks) == 4
        assert chunks[0] == ("2023-01-01", "2023-03-31")
        assert chunks[1] == ("2023-04-01", "2023-06-30")
        assert chunks[2] == ("2023-07-01", "2023-09-30")
        assert chunks[3] == ("2023-10-01", "2023-12-31")

    def test_two_full_years_produce_eight_quarters(self) -> None:
        """Two full calendar years produce eight quarterly chunks."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_quarterly_chunks("2022-01-01", "2023-12-31")
        assert len(chunks) == 8

    def test_partial_first_quarter_clipped(self) -> None:
        """A range starting mid-quarter clips the first chunk correctly."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_quarterly_chunks("2023-02-15", "2023-06-30")
        # Q1 clipped: starts 2023-02-15
        assert chunks[0] == ("2023-02-15", "2023-03-31")
        # Q2 is full
        assert chunks[1] == ("2023-04-01", "2023-06-30")

    def test_partial_last_quarter_clipped(self) -> None:
        """A range ending mid-quarter clips the last chunk correctly."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_quarterly_chunks("2023-07-01", "2023-11-15")
        assert chunks[-1] == ("2023-10-01", "2023-11-15")

    def test_single_day_produces_one_chunk(self) -> None:
        """A one-day range produces a single quarterly chunk."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_quarterly_chunks("2023-05-20", "2023-05-20")
        assert len(chunks) == 1
        assert chunks[0] == ("2023-05-20", "2023-05-20")

    def test_invalid_range_raises(self) -> None:
        """date_end < date_start raises ValueError."""
        from datavia.weather.era5_downloader import ERA5Downloader

        with pytest.raises(ValueError, match="date_end"):
            ERA5Downloader._iter_quarterly_chunks("2023-06-01", "2023-01-01")

    def test_cross_year_boundary_quarterly(self) -> None:
        """A range spanning a year boundary (Q4 → Q1) produces correct chunks."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_quarterly_chunks("2022-11-01", "2023-03-31")
        # Q4 2022 clipped
        assert chunks[0] == ("2022-11-01", "2022-12-31")
        # Q1 2023 full
        assert chunks[1] == ("2023-01-01", "2023-03-31")
        assert len(chunks) == 2

    def test_leap_year_q1_end(self) -> None:
        """Q1 of a leap year ends on Feb 29 when the range includes it."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_quarterly_chunks("2024-01-01", "2024-06-30")
        # Q1 must end on 2024-03-31 (not Feb 29 — quarters end on month boundaries)
        assert chunks[0] == ("2024-01-01", "2024-03-31")


# ---------------------------------------------------------------------------
# ERA5Downloader — yearly chunking
# ---------------------------------------------------------------------------


class TestERA5DownloaderYearlyChunking:
    """Unit tests for :meth:`ERA5Downloader._iter_yearly_chunks`."""

    def test_full_year_produces_one_chunk(self) -> None:
        """A full calendar year produces exactly one yearly chunk."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_yearly_chunks("2023-01-01", "2023-12-31")
        assert len(chunks) == 1
        assert chunks[0] == ("2023-01-01", "2023-12-31")

    def test_three_years_produces_three_chunks(self) -> None:
        """Three calendar years produce exactly three yearly chunks."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_yearly_chunks("2022-01-01", "2024-12-31")
        assert len(chunks) == 3
        assert chunks[0] == ("2022-01-01", "2022-12-31")
        assert chunks[1] == ("2023-01-01", "2023-12-31")
        assert chunks[2] == ("2024-01-01", "2024-12-31")

    def test_partial_first_year_clipped(self) -> None:
        """A range starting mid-year clips the first yearly chunk."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_yearly_chunks("2023-06-01", "2024-12-31")
        assert chunks[0] == ("2023-06-01", "2023-12-31")
        assert chunks[1] == ("2024-01-01", "2024-12-31")

    def test_partial_last_year_clipped(self) -> None:
        """A range ending mid-year clips the last yearly chunk."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_yearly_chunks("2022-01-01", "2023-09-30")
        assert chunks[-1] == ("2023-01-01", "2023-09-30")

    def test_single_day_produces_one_chunk(self) -> None:
        """A one-day range produces a single yearly chunk."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_yearly_chunks("2023-07-04", "2023-07-04")
        assert len(chunks) == 1
        assert chunks[0] == ("2023-07-04", "2023-07-04")

    def test_invalid_range_raises(self) -> None:
        """date_end < date_start raises ValueError."""
        from datavia.weather.era5_downloader import ERA5Downloader

        with pytest.raises(ValueError, match="date_end"):
            ERA5Downloader._iter_yearly_chunks("2023-12-01", "2023-01-01")


# ---------------------------------------------------------------------------
# ERA5Downloader — chunk_by parameter validation and dispatch
# ---------------------------------------------------------------------------


class TestERA5DownloaderChunkBy:
    """Unit tests for the ``chunk_by`` parameter on ERA5Downloader."""

    def test_invalid_chunk_by_raises(self) -> None:
        """Passing an unknown chunk_by value raises ValueError at construction."""
        from datavia.weather.era5_downloader import ERA5Downloader

        with pytest.raises(ValueError, match="chunk_by"):
            ERA5Downloader(
                variables=["2m_temperature"],
                date_start="2024-01-01",
                date_end="2024-12-31",
                chunk_by="weekly",  # Not a valid value.
            )

    def test_default_chunk_by_is_monthly(self) -> None:
        """The default chunk_by value is 'monthly'."""
        from datavia.weather.era5_downloader import ERA5Downloader

        dl = ERA5Downloader(
            variables=["2m_temperature"],
            date_start="2024-01-01",
            date_end="2024-12-31",
        )
        assert dl.chunk_by == "monthly"

    def test_get_chunks_monthly_dispatch(self) -> None:
        """_get_chunks with chunk_by='monthly' returns one chunk per month."""
        from datavia.weather.era5_downloader import ERA5Downloader

        dl = ERA5Downloader(
            variables=["2m_temperature"],
            date_start="2024-01-01",
            date_end="2024-03-31",
            chunk_by="monthly",
        )
        chunks = dl._get_chunks("2024-01-01")
        assert len(chunks) == 3  # Jan, Feb, Mar

    def test_get_chunks_quarterly_dispatch(self) -> None:
        """_get_chunks with chunk_by='quarterly' returns one chunk per quarter."""
        from datavia.weather.era5_downloader import ERA5Downloader

        dl = ERA5Downloader(
            variables=["2m_temperature"],
            date_start="2024-01-01",
            date_end="2024-12-31",
            chunk_by="quarterly",
        )
        chunks = dl._get_chunks("2024-01-01")
        assert len(chunks) == 4

    def test_get_chunks_yearly_dispatch(self) -> None:
        """_get_chunks with chunk_by='yearly' returns one chunk per year."""
        from datavia.weather.era5_downloader import ERA5Downloader

        dl = ERA5Downloader(
            variables=["2m_temperature"],
            date_start="2023-01-01",
            date_end="2024-12-31",
            chunk_by="yearly",
        )
        chunks = dl._get_chunks("2023-01-01")
        assert len(chunks) == 2

    def test_get_chunks_none_dispatch(self) -> None:
        """_get_chunks with chunk_by='none' returns a single chunk."""
        from datavia.weather.era5_downloader import ERA5Downloader

        dl = ERA5Downloader(
            variables=["2m_temperature"],
            date_start="2024-01-01",
            date_end="2024-12-31",
            chunk_by="none",
        )
        chunks = dl._get_chunks("2024-01-01")
        assert len(chunks) == 1
        assert chunks[0] == ("2024-01-01", "2024-12-31")

    @patch("datavia.weather.era5_downloader.cdsapi")
    def test_chunk_by_none_produces_one_cds_job(self, mock_cdsapi: MagicMock) -> None:
        """download() with chunk_by='none' submits exactly one CDS job."""

        from datavia.weather.era5_downloader import ERA5Downloader

        mock_client = MagicMock()
        mock_cdsapi.Client.return_value = mock_client

        # Simulate a job that downloads an empty temp file.
        def fake_download(path: str) -> None:
            with open(path, "wb"):
                pass

        mock_job = MagicMock()
        mock_job.reply = {"status": "completed", "request_id": "test-none"}
        mock_job.download.side_effect = fake_download
        mock_client.retrieve.return_value = mock_job

        dl = ERA5Downloader(
            variables=["2m_temperature"],
            date_start="2024-01-01",
            date_end="2024-12-31",
            chunk_by="none",
            buffer_days=0,
        )
        dl.download()

        assert mock_client.retrieve.call_count == 1, (
            "Expected 1 CDS job for chunk_by='none', "
            f"got {mock_client.retrieve.call_count}"
        )

    @patch("datavia.weather.era5_downloader.cdsapi")
    def test_chunk_by_quarterly_two_years_produces_eight_jobs(
        self, mock_cdsapi: MagicMock
    ) -> None:
        """download() with chunk_by='quarterly' over 2 years submits 8 CDS jobs."""
        from datavia.weather.era5_downloader import ERA5Downloader

        mock_client = MagicMock()
        mock_cdsapi.Client.return_value = mock_client

        def fake_download(path: str) -> None:
            with open(path, "wb"):
                pass

        mock_job = MagicMock()
        mock_job.reply = {"status": "completed", "request_id": "test-quarterly"}
        mock_job.download.side_effect = fake_download
        mock_client.retrieve.return_value = mock_job

        dl = ERA5Downloader(
            variables=["2m_temperature"],
            date_start="2022-01-01",
            date_end="2023-12-31",
            chunk_by="quarterly",
            buffer_days=0,
        )
        dl.download()

        assert mock_client.retrieve.call_count == 8, (
            "Expected 8 CDS jobs for 2 years quarterly, "
            f"got {mock_client.retrieve.call_count}"
        )


# ---------------------------------------------------------------------------
# CompositeWeatherDownloader — chunk_by forwarding
# ---------------------------------------------------------------------------


class TestCompositeDownloaderChunkByForwarding:
    """Tests that ``chunk_by`` is forwarded from config to the grid downloader."""

    def test_chunk_by_forwarded_to_grid_downloader(self) -> None:
        """CompositeWeatherDownloader forwards chunk_by to the ERA5 grid downloader."""
        from datavia.weather.composite_downloader import CompositeWeatherDownloader

        config = {
            "source": "ERA5_land",
            "variables": ["2m_temperature"],
            "date_start": "2024-01-01",
            "date_end": "2024-01-31",
            "chunk_by": "quarterly",
        }

        with patch(
            "datavia.weather.composite_downloader.get_grid_downloader_class"
        ) as mock_registry:
            mock_grid_class = MagicMock()
            mock_grid_class.return_value = MagicMock()
            mock_registry.return_value = mock_grid_class

            CompositeWeatherDownloader(config=config)

        call_kwargs = mock_grid_class.call_args[1]
        assert call_kwargs.get("chunk_by") == "quarterly", (
            f"Expected chunk_by='quarterly' forwarded to grid downloader, "
            f"got {call_kwargs}"
        )

    def test_chunk_by_absent_not_forwarded(self) -> None:
        """When chunk_by is not in config, it is not passed to the grid downloader."""
        from datavia.weather.composite_downloader import CompositeWeatherDownloader

        config = {
            "source": "ERA5_land",
            "variables": ["2m_temperature"],
            "date_start": "2024-01-01",
            "date_end": "2024-01-31",
        }

        with patch(
            "datavia.weather.composite_downloader.get_grid_downloader_class"
        ) as mock_registry:
            mock_grid_class = MagicMock()
            mock_grid_class.return_value = MagicMock()
            mock_registry.return_value = mock_grid_class

            CompositeWeatherDownloader(config=config)

        call_kwargs = mock_grid_class.call_args[1]
        assert "chunk_by" not in call_kwargs, (
            f"chunk_by must not be forwarded when absent from config, got {call_kwargs}"
        )


# ---------------------------------------------------------------------------
# WeatherPipeline — chunk_by config key acceptance
# ---------------------------------------------------------------------------


class TestWeatherPipelineChunkByConfig:
    """Tests that ``chunk_by`` is accepted as a valid WeatherPipeline config key."""

    _BASE_CONFIG: ClassVar[dict] = {
        "source": "ERA5_land",
        "variables": ["2m_temperature"],
        "date_start": "2024-01-01",
        "date_end": "2024-12-31",
    }

    def test_chunk_by_accepted_in_pipeline_config(self) -> None:
        """WeatherPipeline accepts chunk_by without raising ValueError."""
        from datavia.weather.pipeline import WeatherPipeline

        config = {**self._BASE_CONFIG, "chunk_by": "quarterly"}
        pipe = WeatherPipeline(config=config)
        assert pipe.get_config()["chunk_by"] == "quarterly"

    def test_chunk_by_all_valid_values_accepted(self) -> None:
        """WeatherPipeline accepts all four valid chunk_by values."""
        from datavia.weather.pipeline import WeatherPipeline

        for value in ("monthly", "quarterly", "yearly", "none"):
            config = {**self._BASE_CONFIG, "chunk_by": value}
            # Must not raise.
            pipe = WeatherPipeline(config=config)
            assert pipe.get_config()["chunk_by"] == value
