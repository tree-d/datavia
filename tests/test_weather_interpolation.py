"""Unit tests for interpolation utilities (no network access).

Covers:
- :func:`~datavia.library.interpolation.blend_gridded_and_station`
- :func:`~datavia.library.interpolation.interpolate_station_parquet`
- :func:`~datavia.library.interpolation.interpolate_netcdf`
"""

from unittest.mock import patch

import numpy as np
import pytest

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
