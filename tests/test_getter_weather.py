"""Unit tests for :class:`~datavia.weather.getter_weather.GetterWeather`.

Covers get_existing_layers(), get_registered_uris(), get_data(),
get_weather_data(), temporal resolution, and unit conversion.
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


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

    def test_get_data_raises_on_nan_single_timestamp(self, sqlite_db: None) -> None:
        """MissingWeatherDataError is raised when a queried point is NaN.

        A NaN gridded result at a single timestamp means the point/time was
        never downloaded (spatial nodata gaps are filled once, at write
        time), so get_data() must raise instead of returning NaN silently.
        """
        from datavia.weather.getter_weather import (
            GetterWeather,
            MissingWeatherDataError,
        )

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
                return_value=float("nan"),
            ),
            patch(
                "datavia.weather.getter_weather.interpolate_station_parquet",
                return_value=float("nan"),
            ),
        ):
            with pytest.raises(MissingWeatherDataError, match="temperature_2m"):
                getter.get_data(
                    coords,
                    variable="temperature_2m",
                    datetime_utc="2024-01-15T12:00:00",
                )

    def test_get_data_raises_on_nan_multi_timestamp(self, sqlite_db: None) -> None:
        """MissingWeatherDataError is raised for a NaN in a multi-timestamp batch."""
        from datavia.weather.getter_weather import (
            GetterWeather,
            MissingWeatherDataError,
        )

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

        with patch(
            "datavia.weather.getter_weather.interpolate_netcdf",
            return_value=np.array([20.0, float("nan")]),
        ):
            with pytest.raises(MissingWeatherDataError, match="temperature_2m"):
                getter.get_data(
                    coords,
                    variable="temperature_2m",
                    datetime_utc=["2024-01-15T12:00:00", "2024-01-16T12:00:00"],
                )

    @pytest.mark.parametrize("container", [list, tuple, np.array, pd.Series])
    def test_get_data_sequence_datetime_utc_treated_as_multi_time(
        self, sqlite_db: None, container
    ) -> None:
        """Any sequence type of timestamps takes the multi-time path (Bug 8).

        Before the fix, ``isinstance(datetime_utc, (list, tuple))`` excluded
        numpy arrays, pandas Series, and other array-likes, so e.g. a
        ``np.array([...])`` of timestamps was silently treated as a single
        scalar timestamp instead of a batch. This verifies
        ``interpolate_netcdf`` is called with both normalised timestamps
        (multi-time) regardless of whether ``datetime_utc`` is passed as a
        list, tuple, numpy array, or pandas Series.
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
        datetime_utc = container(["2024-01-15T12:00:00", "2024-01-16T12:00:00"])

        with patch(
            "datavia.weather.getter_weather.interpolate_netcdf",
            return_value=np.array([20.0, 21.0]),
        ) as mock_nc:
            result = getter.get_data(
                coords,
                variable="temperature_2m",
                datetime_utc=datetime_utc,
            )

        assert mock_nc.called
        called_datetime_utc = mock_nc.call_args.args[4]
        assert isinstance(called_datetime_utc, list) and len(called_datetime_utc) == 2, (
            "Expected the numpy array to be treated as a multi-time batch "
            f"(list of 2), got {called_datetime_utc!r}"
        )
        assert np.asarray(result).ravel().tolist() == pytest.approx([20.0, 21.0])

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
                "datavia.weather.getter_weather._try_open_zarr",
                return_value=None,
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

    def test_zarr_fast_path_uses_interpolate_dataset(self, sqlite_db: None) -> None:
        """When a Zarr store is available, get_data() calls interpolate_dataset()
        with no gap-fill kwargs, since ZarrStoreManager.write_dataset() fills
        nodata gaps at ingestion time and interpolate_dataset() assumes the
        data it receives is already filled."""
        from datavia.weather.getter_weather import GetterWeather

        getter = GetterWeather("ERA5_land")
        coords = np.array([[11.0, 49.0]])
        fake_zarr_ds = MagicMock()

        with (
            patch(
                "datavia.weather.getter_weather._try_open_zarr",
                return_value=fake_zarr_ds,
            ),
            patch(
                "datavia.weather.getter_weather.interpolate_dataset",
                return_value=np.array([20.0]),
            ) as mock_interp,
            patch(
                "datavia.weather.getter_weather.interpolate_station_parquet",
                return_value=float("nan"),
            ),
        ):
            getter.get_data(
                coords,
                variable="2m_temperature",
                datetime_utc="2024-06-15",
            )

        assert mock_interp.called
        assert "fill_gaps" not in mock_interp.call_args.kwargs, (
            "interpolate_dataset() does not accept a fill_gaps parameter"
        )

    def test_non_zarr_source_uses_interpolate_netcdf(self, sqlite_db: None) -> None:
        """Non-Zarr sources call interpolate_netcdf() with no gap-fill kwargs,
        since SaverWeather.save() fills nodata gaps via prepare_netcdf() at
        save time and interpolate_netcdf() assumes the data it receives is
        already filled."""
        from datavia.weather.getter_weather import GetterWeather

        _insert_weather_layer(
            source_name="weather",
            layer_name="weather_temperature_2m",
            variable="temperature_2m",
            file_format="netcdf",
            valid_from="2024-01-01T00:00:00",
            valid_until="2024-01-31T23:00:00",
            uri="/data/weather_temperature_2m.nc",
        )

        getter = GetterWeather("weather")
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
            patch(
                "datavia.weather.getter_weather.apply_conversion",
                side_effect=lambda s, v, val, u: val,
            ),
        ):
            getter.get_data(
                coords,
                variable="temperature_2m",
                datetime_utc="2024-01-15T12:00:00",
            )

        assert mock_nc.called
        assert "fill_gaps" not in mock_nc.call_args.kwargs, (
            "interpolate_netcdf() does not accept a fill_gaps parameter"
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


# ---------------------------------------------------------------------------


class TestGetterWeatherGetRegisteredUris:
    """Tests for :meth:`GetterWeather.get_registered_uris`.

    Verifies that the method returns only distinct URIs for ``self.source_name``
    and correctly deduplicates a single file registered for multiple variables.
    """

    def test_empty_db_returns_empty_set(self, sqlite_db: None) -> None:
        """Returns an empty set when no layers are registered.

        Parameters
        ----------
        sqlite_db : None
            In-memory SQLite fixture.
        """
        from datavia.weather.getter_weather import GetterWeather

        getter = GetterWeather("ERA5_land")
        assert getter.get_registered_uris() == set()

    def test_registered_uris_returned(self, sqlite_db: None) -> None:
        """All URIs registered for this source are present in the result.

        Parameters
        ----------
        sqlite_db : None
            In-memory SQLite fixture.
        """
        from datavia.weather.getter_weather import GetterWeather

        uri_a = "/data/ERA5_land_2m_temperature_202401.nc"
        uri_b = "/data/ERA5_land_2m_temperature_202402.nc"
        for uri, month in ((uri_a, "01"), (uri_b, "02")):
            _insert_weather_layer(
                source_name="ERA5_land",
                layer_name=f"ERA5_land_2m_temperature_2024{month}",
                variable="2m_temperature",
                file_format="netcdf",
                valid_from=f"2024-{month}-01T00:00:00",
                valid_until=f"2024-{month}-28T23:00:00",
                uri=uri,
            )

        getter = GetterWeather("ERA5_land")
        uris = getter.get_registered_uris()
        assert uri_a in uris
        assert uri_b in uris

    def test_is_source_scoped(self, sqlite_db: None) -> None:
        """Only URIs for self.source_name are included; other sources are excluded.

        Parameters
        ----------
        sqlite_db : None
            In-memory SQLite fixture.
        """
        from datavia.weather.getter_weather import GetterWeather

        era5_uri = "/data/ERA5_land_temperature.nc"
        hyras_uri = "/data/HYRAS_temperature.nc"
        _insert_weather_layer(
            source_name="ERA5_land",
            layer_name="ERA5_land_temperature_2m",
            variable="2m_temperature",
            file_format="netcdf",
            valid_from="2024-01-01T00:00:00",
            valid_until="2024-01-31T23:00:00",
            uri=era5_uri,
        )
        _insert_weather_layer(
            source_name="HYRAS",
            layer_name="HYRAS_temperature_2m",
            variable="2m_temperature",
            file_format="netcdf",
            valid_from="2024-01-01T00:00:00",
            valid_until="2024-01-31T23:00:00",
            uri=hyras_uri,
        )

        era5_getter = GetterWeather("ERA5_land")
        uris = era5_getter.get_registered_uris()
        assert era5_uri in uris
        assert hyras_uri not in uris, (
            "URIs for other sources must not appear in the result."
        )

    def test_multi_variable_file_deduplicated(self, sqlite_db: None) -> None:
        """A single file registered for two variables appears only once in the set.

        When one NetCDF file covers two variables (two DB rows), the returned
        set must contain the URI exactly once because Python sets deduplicate.

        Parameters
        ----------
        sqlite_db : None
            In-memory SQLite fixture.
        """
        from datavia.weather.getter_weather import GetterWeather

        shared_uri = "/data/ERA5_land_multi_202401.nc"
        for variable in ("2m_temperature", "total_precipitation"):
            _insert_weather_layer(
                source_name="ERA5_land",
                layer_name=f"ERA5_land_multi_202401_{variable}",
                variable=variable,
                file_format="netcdf",
                valid_from="2024-01-01T00:00:00",
                valid_until="2024-01-31T23:00:00",
                uri=shared_uri,
            )

        getter = GetterWeather("ERA5_land")
        uris = getter.get_registered_uris()
        assert shared_uri in uris
        # A set never contains duplicates; count by converting back to list.
        assert len([u for u in uris if u == shared_uri]) == 1


# ---------------------------------------------------------------------------
# SaverWeather — save(register_only=True)

# ---------------------------------------------------------------------------


class TestGetterWeatherEra5KelvinToCelsiusConversion:
    """Tests that the K→°C conversion from ``apply_conversion``
    is applied in ``get_data()``.

    The existing tests use ``variable="temperature_2m"`` (a HYRAS-only variable
    name) for the ERA5_land source, which means ``apply_conversion`` finds no
    conversion rule and returns the raw value unchanged.  These tests use the
    correct ERA5_land variable name ``"2m_temperature"`` to verify that the
    K→°C subtraction is actually applied end-to-end.
    """

    def test_raw_kelvin_converted_to_celsius(self, sqlite_db: None) -> None:
        """Raw 300.15 K from the mocked interpolator becomes 27.0 °C.

        ``apply_conversion("ERA5_land", "2m_temperature", 300.15, None)``
        must subtract 273.15, yielding 27.0.  ``apply_conversion`` is NOT
        mocked so the real conversion logic executes.

        Parameters
        ----------
        sqlite_db : None
            In-memory SQLite fixture.
        """
        from datavia.weather.getter_weather import GetterWeather

        _insert_weather_layer(
            source_name="ERA5_land",
            layer_name="ERA5_land_2m_temperature_202401",
            variable="2m_temperature",
            file_format="netcdf",
            valid_from="2024-01-01T00:00:00",
            valid_until="2024-01-31T23:00:00",
            uri="/data/era5_2m_temperature.nc",
        )

        getter = GetterWeather("ERA5_land")
        coords = np.array([[13.4, 52.5]])

        with (
            patch(
                "datavia.weather.getter_weather.interpolate_netcdf",
                return_value=300.15,
            ),
            patch(
                "datavia.weather.getter_weather._try_open_zarr",
                return_value=None,
            ),
            patch(
                "datavia.weather.getter_weather.interpolate_station_parquet",
                return_value=float("nan"),
            ),
        ):
            result = getter.get_data(
                coords,
                variable="2m_temperature",
                datetime_utc="2024-01-15T12:00:00",
            )

        # 300.15 K - 273.15 = 27.0 °C.
        assert result[0] == pytest.approx(27.0), (
            f"Expected 27.0 °C after K→°C conversion, got {result[0]}."
        )


# ---------------------------------------------------------------------------
# GetterWeather — unit_overrides forwarded to apply_conversion

# ---------------------------------------------------------------------------


class TestGetterWeatherUnitOverrides:
    """Tests that ``unit_overrides`` from :meth:`GetterWeather.__init__` are
    forwarded as the fourth argument to ``apply_conversion`` in each query.
    """

    def test_unit_overrides_forwarded_to_apply_conversion(
        self, sqlite_db: None
    ) -> None:
        """apply_conversion receives the unit_overrides dict passed at construction.

        ``apply_conversion`` is patched to record its arguments.  The fourth
        positional argument must be the same object that was supplied to
        :class:`GetterWeather` at construction time.

        Parameters
        ----------
        sqlite_db : None
            In-memory SQLite fixture.
        """
        from datavia.weather.getter_weather import GetterWeather

        overrides = {"2m_temperature": {"from": "K", "to": "degC"}}
        _insert_weather_layer(
            source_name="ERA5_land",
            layer_name="ERA5_land_2m_temperature_202401",
            variable="2m_temperature",
            file_format="netcdf",
            valid_from="2024-01-01T00:00:00",
            valid_until="2024-01-31T23:00:00",
            uri="/data/era5_2m_temperature.nc",
        )

        getter = GetterWeather("ERA5_land", unit_overrides=overrides)
        coords = np.array([[13.4, 52.5]])

        with (
            patch(
                "datavia.weather.getter_weather.interpolate_netcdf",
                return_value=280.0,
            ),
            patch(
                "datavia.weather.getter_weather._try_open_zarr",
                return_value=None,
            ),
            patch(
                "datavia.weather.getter_weather.interpolate_station_parquet",
                return_value=float("nan"),
            ),
            patch(
                "datavia.weather.getter_weather.apply_conversion",
                wraps=lambda s, v, val, u: val,
            ) as mock_convert,
        ):
            getter.get_data(
                coords,
                variable="2m_temperature",
                datetime_utc="2024-01-15T12:00:00",
            )

        mock_convert.assert_called_once()
        call_args = mock_convert.call_args
        assert call_args.args[0] == "ERA5_land", (
            f"Expected source_name='ERA5_land', got {call_args.args[0]!r}."
        )
        assert call_args.args[1] == "2m_temperature", (
            f"Expected variable='2m_temperature', got {call_args.args[1]!r}."
        )
        assert call_args.args[3] is overrides, (
            "unit_overrides must be forwarded by reference to apply_conversion."
        )
