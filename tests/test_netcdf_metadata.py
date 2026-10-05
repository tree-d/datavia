"""Unit tests for :func:`~datavia.library.formats.extract_netcdf_layer_metadata`.

All tests use real in-memory NetCDF files created with xarray/numpy.
"""

import numpy as np

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
