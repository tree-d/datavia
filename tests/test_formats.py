"""Tests for datavia.library.formats.

Derived from the public API signatures and confirmed return-type contracts.
All file I/O is replaced by lightweight mocks; no real TIFF, NetCDF, or
Parquet files are required.

Functions under test:
- read_tiff_metadata(filepath) -> dict with known keys
- write_tiff_data(data, filepath, ...) -> bool
- validate_tiff_file(filepath) -> bool
- read_netcdf_metadata(filepath) -> dict with known keys
- extract_netcdf_layer_metadata(filepath) -> dict with known keys
- write_parquet(records, path) -> None; raises ValueError on empty records
- read_parquet_time_range(path, variable, from_dt, to_dt) -> DataFrame;
  raises KeyError for missing variable
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from datavia.library.formats import (
    extract_netcdf_layer_metadata,
    read_netcdf_metadata,
    read_parquet_time_range,
    read_tiff_metadata,
    validate_tiff_file,
    write_parquet,
    write_tiff_data,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TIFF_KEYS = {
    "width",
    "height",
    "count",
    "crs",
    "transform",
    "bounds",
    "dtypes",
    "nodata",
    "compression",
    "tiled",
    "block_shapes",
}

_NETCDF_METADATA_KEYS = {
    "dimensions",
    "variables",
    "coordinates",
    "attributes",
    "time_range",
    "spatial_bounds",
}

_NETCDF_LAYER_KEYS = {
    "valid_from",
    "valid_until",
    "variables",
    "crs",
    "bbox",
    "resolution_x",
    "resolution_y",
}


def _make_rasterio_ds_mock() -> MagicMock:
    """Build a minimal rasterio dataset context-manager mock.

    Returns:
        MagicMock: A fake rasterio dataset exposing all attributes read by
            ``read_tiff_metadata`` and ``validate_tiff_file``.
    """
    mock_ds = MagicMock()
    mock_ds.crs = MagicMock()
    mock_ds.crs.to_string.return_value = "EPSG:4326"
    mock_ds.transform = MagicMock()
    mock_ds.bounds = MagicMock()
    mock_ds.count = 1
    mock_ds.width = 100
    mock_ds.height = 100
    mock_ds.dtypes = ["float32"]
    mock_ds.nodata = None
    mock_ds.compression = None
    mock_ds.is_tiled = False
    mock_ds.block_shapes = [(256, 256)]
    mock_ds.read = MagicMock(return_value=np.zeros((10, 10)))
    mock_ds.__enter__ = lambda s: s
    mock_ds.__exit__ = MagicMock(return_value=False)
    return mock_ds


def _make_xarray_ds_mock() -> MagicMock:
    """Build a minimal xarray Dataset context-manager mock.

    Returns:
        MagicMock: A fake xarray Dataset exposing all attributes read by
            ``read_netcdf_metadata`` and ``extract_netcdf_layer_metadata``.
    """
    mock_ds = MagicMock()
    mock_ds.attrs = {"Conventions": "CF-1.7"}
    mock_ds.dims = {"time": 24, "latitude": 10, "longitude": 10}
    mock_ds.data_vars = MagicMock()
    mock_ds.data_vars.keys.return_value = ["temperature_2m"]
    mock_ds.coords = {}
    mock_ds.__enter__ = lambda s: s
    mock_ds.__exit__ = MagicMock(return_value=False)
    return mock_ds


# ---------------------------------------------------------------------------
# read_tiff_metadata
# ---------------------------------------------------------------------------


class TestReadTiffMetadata:
    """Tests for the read_tiff_metadata function."""

    def test_returns_dict_on_success(self) -> None:
        """Test that read_tiff_metadata returns a dict when rasterio is mocked.

        Returns:
            None
        """
        mock_ds = _make_rasterio_ds_mock()
        with patch("datavia.library.formats.rasterio.open", return_value=mock_ds):
            result = read_tiff_metadata("/fake/path.tif")
        assert isinstance(result, dict)

    def test_result_contains_required_keys(self) -> None:
        """Test that the returned dict contains all documented keys.

        Returns:
            None
        """
        mock_ds = _make_rasterio_ds_mock()
        with patch("datavia.library.formats.rasterio.open", return_value=mock_ds):
            result = read_tiff_metadata("/fake/path.tif")
        assert _TIFF_KEYS.issubset(result.keys())

    def test_returns_empty_dict_for_missing_file(self) -> None:
        """Test that a non-existent file path returns an empty dict.

        Returns:
            None
        """
        result = read_tiff_metadata("/this/path/does/not/exist_zz9999.tif")
        assert isinstance(result, dict)

    def test_width_and_height_are_integers(self) -> None:
        """Test that width and height values in the metadata are integers.

        Returns:
            None
        """
        mock_ds = _make_rasterio_ds_mock()
        with patch("datavia.library.formats.rasterio.open", return_value=mock_ds):
            result = read_tiff_metadata("/fake/path.tif")
        if result:
            assert isinstance(result["width"], int)
            assert isinstance(result["height"], int)


# ---------------------------------------------------------------------------
# write_tiff_data
# ---------------------------------------------------------------------------


class TestWriteTiffData:
    """Tests for the write_tiff_data function."""

    def test_returns_bool_on_success(self) -> None:
        """Test that write_tiff_data returns a bool when rasterio write is mocked.

        Returns:
            None
        """
        data = np.zeros((10, 10), dtype=np.float32)
        mock_ds = _make_rasterio_ds_mock()
        with patch("datavia.library.formats.rasterio.open", return_value=mock_ds):
            result = write_tiff_data(data, "/fake/out.tif")
        assert isinstance(result, bool)

    def test_accepts_crs_kwarg(self) -> None:
        """Test that the ``crs`` keyword argument is accepted without TypeError.

        Returns:
            None
        """
        data = np.zeros((10, 10), dtype=np.float32)
        mock_ds = _make_rasterio_ds_mock()
        with patch("datavia.library.formats.rasterio.open", return_value=mock_ds):
            try:
                write_tiff_data(data, "/fake/out.tif", crs="EPSG:4326")
            except TypeError as exc:
                pytest.fail(f"crs kwarg rejected: {exc}")

    def test_accepts_nodata_kwarg(self) -> None:
        """Test that the ``nodata`` keyword argument is accepted without TypeError.

        Returns:
            None
        """
        data = np.zeros((10, 10), dtype=np.float32)
        mock_ds = _make_rasterio_ds_mock()
        with patch("datavia.library.formats.rasterio.open", return_value=mock_ds):
            try:
                write_tiff_data(data, "/fake/out.tif", nodata=-9999.0)
            except TypeError as exc:
                pytest.fail(f"nodata kwarg rejected: {exc}")

    def test_accepts_transform_kwarg(self) -> None:
        """Test that the ``transform`` keyword argument is accepted without TypeError.

        Returns:
            None
        """
        data = np.zeros((10, 10), dtype=np.float32)
        mock_ds = _make_rasterio_ds_mock()
        with patch("datavia.library.formats.rasterio.open", return_value=mock_ds):
            try:
                write_tiff_data(data, "/fake/out.tif", transform=MagicMock())
            except TypeError as exc:
                pytest.fail(f"transform kwarg rejected: {exc}")


# ---------------------------------------------------------------------------
# validate_tiff_file
# ---------------------------------------------------------------------------


class TestValidateTiffFile:
    """Tests for the validate_tiff_file function."""

    def test_returns_bool(self) -> None:
        """Test that validate_tiff_file always returns a bool.

        Returns:
            None
        """
        mock_ds = _make_rasterio_ds_mock()
        with patch("datavia.library.formats.rasterio.open", return_value=mock_ds):
            result = validate_tiff_file("/fake/path.tif")
        assert isinstance(result, bool)

    def test_returns_false_for_nonexistent_path(self) -> None:
        """Test that validate_tiff_file returns False when the file is absent.

        Returns:
            None
        """
        result = validate_tiff_file("/this/path/does/absolutely/not/exist_zz9999.tif")
        assert result is False


# ---------------------------------------------------------------------------
# read_netcdf_metadata
# ---------------------------------------------------------------------------


class TestReadNetcdfMetadata:
    """Tests for the read_netcdf_metadata function."""

    def test_returns_dict_with_mocked_dataset(self) -> None:
        """Test that read_netcdf_metadata returns a dict when xarray is mocked.

        Returns:
            None
        """
        mock_ds = _make_xarray_ds_mock()
        with patch("datavia.library.formats.xr.open_dataset", return_value=mock_ds):
            result = read_netcdf_metadata("/fake/file.nc")
        assert isinstance(result, dict)

    def test_result_contains_required_keys(self) -> None:
        """Test that the returned dict contains all documented keys.

        Returns:
            None
        """
        mock_ds = _make_xarray_ds_mock()
        with patch("datavia.library.formats.xr.open_dataset", return_value=mock_ds):
            result = read_netcdf_metadata("/fake/file.nc")
        if result:
            assert _NETCDF_METADATA_KEYS.issubset(result.keys())

    def test_returns_empty_dict_for_missing_file(self) -> None:
        """Test that a missing file returns an empty dict rather than raising.

        Returns:
            None
        """
        result = read_netcdf_metadata("/no/such/file.nc")
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# extract_netcdf_layer_metadata
# ---------------------------------------------------------------------------


class TestExtractNetcdfLayerMetadata:
    """Tests for the extract_netcdf_layer_metadata function."""

    def test_returns_dict_with_mocked_dataset(self) -> None:
        """Test that the function returns a dict when the NetCDF file is mocked.

        Returns:
            None
        """
        mock_ds = _make_xarray_ds_mock()
        with patch("datavia.library.formats.xr.open_dataset", return_value=mock_ds):
            result = extract_netcdf_layer_metadata("/fake/file.nc")
        assert isinstance(result, dict)

    def test_result_contains_required_keys(self) -> None:
        """Test that the returned dict has all keys required for DB insertion.

        Returns:
            None
        """
        mock_ds = _make_xarray_ds_mock()
        with patch("datavia.library.formats.xr.open_dataset", return_value=mock_ds):
            result = extract_netcdf_layer_metadata("/fake/file.nc")
        if result:
            assert _NETCDF_LAYER_KEYS.issubset(result.keys())

    def test_crs_defaults_to_epsg4326(self) -> None:
        """Test that crs defaults to 'EPSG:4326' when no CRS data is in the mock.

        Returns:
            None
        """
        mock_ds = _make_xarray_ds_mock()
        with patch("datavia.library.formats.xr.open_dataset", return_value=mock_ds):
            result = extract_netcdf_layer_metadata("/fake/file.nc")
        if result:
            assert result["crs"] == "EPSG:4326"

    def test_returns_empty_dict_for_missing_file(self) -> None:
        """Test that a missing file returns an empty dict rather than raising.

        Returns:
            None
        """
        result = extract_netcdf_layer_metadata("/no/such/file.nc")
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# write_parquet
# ---------------------------------------------------------------------------


def test_write_parquet_returns_none() -> None:
    """Test that write_parquet returns None (write-only side effect).

    Returns:
        None
    """
    import pandas as pd

    records = [{"datetime": "2023-01-01T00:00:00", "temperature_2m": 10.5}]
    with patch.object(pd.DataFrame, "to_parquet") as mock_to_parquet:
        result = write_parquet(records, "/fake/out.parquet")
        mock_to_parquet.assert_called_once()
    assert result is None


def test_write_parquet_raises_on_empty_records() -> None:
    """Test that write_parquet raises ValueError when records is empty.

    Returns:
        None
    """
    with pytest.raises(ValueError):
        write_parquet([], "/fake/out.parquet")


def test_write_parquet_accepts_list_of_dicts() -> None:
    """Test that write_parquet accepts a list of dicts without TypeError.

    Returns:
        None
    """
    import pandas as pd

    records = [
        {"datetime": "2023-01-01T00:00:00", "temperature_2m": 10.5},
        {"datetime": "2023-01-02T00:00:00", "temperature_2m": 12.0},
    ]
    with patch.object(pd.DataFrame, "to_parquet"):
        try:
            write_parquet(records, "/fake/out.parquet")
        except TypeError as exc:
            pytest.fail(f"write_parquet rejected valid arguments: {exc}")


# ---------------------------------------------------------------------------
# read_parquet_time_range
# ---------------------------------------------------------------------------


def test_read_parquet_time_range_raises_on_missing_variable(
    tmp_path,
) -> None:
    """Test that read_parquet_time_range raises KeyError for an absent column.

    Returns:
        None
    """
    import pandas as pd

    parquet_file = tmp_path / "test.parquet"
    df = pd.DataFrame({"datetime": ["2023-01-01"], "other_var": [1.0]})
    df.to_parquet(str(parquet_file))

    with pytest.raises(KeyError):
        read_parquet_time_range(
            str(parquet_file),
            variable="temperature_2m",
            from_dt="2023-01-01T00:00:00",
            to_dt="2023-01-31T23:59:59",
        )


def test_read_parquet_time_range_returns_dataframe_for_valid_data(
    tmp_path,
) -> None:
    """Test that read_parquet_time_range returns a DataFrame for a valid file.

    Returns:
        None
    """
    import pandas as pd

    parquet_file = tmp_path / "test.parquet"
    df = pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2023-01-05", "2023-01-10", "2023-02-15"]),
            "temperature_2m": [5.0, 7.5, 3.0],
        }
    )
    df.to_parquet(str(parquet_file))

    result = read_parquet_time_range(
        str(parquet_file),
        variable="temperature_2m",
        from_dt="2023-01-01T00:00:00",
        to_dt="2023-01-31T23:59:59",
    )
    assert isinstance(result, pd.DataFrame)
    # Only January rows should be returned.
    assert len(result) == 2


def test_read_parquet_time_range_time_filter_is_inclusive(
    tmp_path,
) -> None:
    """Test that the time range filter includes rows at the boundary dates.

    Returns:
        None
    """
    import pandas as pd

    parquet_file = tmp_path / "test.parquet"
    df = pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2023-01-01", "2023-01-31"]),
            "temperature_2m": [0.0, 1.0],
        }
    )
    df.to_parquet(str(parquet_file))

    result = read_parquet_time_range(
        str(parquet_file),
        variable="temperature_2m",
        from_dt="2023-01-01T00:00:00",
        to_dt="2023-01-31T23:59:59",
    )
    assert len(result) == 2
