"""Tests for spatial operations library functions."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from datavia.library.spatial_ops import (
    extract_values_at_coords,
    get_geotiff_bounds,
    process_multiband_tiff,
    raster_sample,
    read_geotiff_metadata,
    validate_coordinates_in_bounds,
)


class TestValueExtraction:
    """Test coordinate value extraction functions."""

    @patch("datavia.library.spatial_ops.rasterio")
    def test_extract_values_at_coords_basic(self, mock_rasterio):
        """Test basic value extraction from TIFF."""
        # Mock rasterio dataset
        mock_src = MagicMock()
        mock_src.crs.to_string.return_value = "EPSG:4326"
        mock_src.nodata = None
        mock_src.sample.return_value = [np.array([10.5]), np.array([20.3])]
        mock_rasterio.open.return_value.__enter__.return_value = mock_src

        coords = np.array([[1.0, 2.0], [3.0, 4.0]])
        result = extract_values_at_coords("test.tiff", coords)

        assert len(result) == 2
        assert result[0] == 10.5
        assert result[1] == 20.3
        mock_src.sample.assert_called_once_with(coords, indexes=1)

    @patch("datavia.library.spatial_ops.rasterio")
    def test_extract_values_at_coords_with_crs_transform(self, mock_rasterio):
        """Test value extraction with coordinate transformation."""
        # Mock rasterio dataset
        mock_src = MagicMock()
        mock_src.crs.to_string.return_value = "EPSG:3857"
        mock_src.nodata = None  # No nodata value

        # Mock sample method to return an iterator of arrays
        mock_src.sample.return_value = iter([np.array([15.0])])
        mock_rasterio.open.return_value.__enter__.return_value = mock_src

        coords = np.array([[1.0, 2.0]])

        with patch(
            "datavia.library.spatial_ops.transform_coordinates"
        ) as mock_transform:
            mock_transform.return_value = np.array([[111319.5, 222684.2]])
            result = extract_values_at_coords(
                "test.tiff", coords, source_crs="EPSG:4326"
            )

            # Verify the transform was called
            mock_transform.assert_called_once_with(coords, "EPSG:4326", "EPSG:3857")
            # Verify sample was called (without checking exact array values)
            mock_src.sample.assert_called_once()
            # Check that we get a result
            assert len(result) == 1
            assert result[0] == 15.0

    @patch("datavia.library.spatial_ops.rasterio")
    def test_extract_values_at_coords_with_nodata(self, mock_rasterio):
        """Test value extraction handling nodata values."""
        mock_src = MagicMock()
        mock_src.crs.to_string.return_value = "EPSG:4326"
        mock_src.nodata = -9999
        mock_src.sample.return_value = [np.array([10.0]), np.array([-9999])]
        mock_rasterio.open.return_value.__enter__.return_value = mock_src

        coords = np.array([[1.0, 2.0], [3.0, 4.0]])
        result = extract_values_at_coords("test.tiff", coords)

        assert result[0] == 10.0
        assert np.isnan(result[1])  # nodata converted to NaN

    @patch("datavia.library.spatial_ops.rasterio")
    def test_extract_values_at_coords_band_selection(self, mock_rasterio):
        """Test extraction from specific band."""
        mock_src = MagicMock()
        mock_src.crs.to_string.return_value = "EPSG:4326"
        mock_src.sample.return_value = [np.array([100.0])]
        mock_rasterio.open.return_value.__enter__.return_value = mock_src

        coords = np.array([[1.0, 2.0]])
        extract_values_at_coords("test.tiff", coords, band=3)

        mock_src.sample.assert_called_once_with(coords, indexes=3)

    @patch("datavia.library.spatial_ops.RASTERIO_AVAILABLE", False)
    def test_extract_values_no_rasterio(self):
        """Test graceful handling when rasterio not available."""
        coords = np.array([[1.0, 2.0], [3.0, 4.0]])
        result = extract_values_at_coords("test.tiff", coords)

        assert len(result) == 2
        assert all(np.isnan(result))

    @patch("datavia.library.spatial_ops.rasterio")
    def test_extract_values_file_error(self, mock_rasterio):
        """Test error handling for file read errors."""
        mock_rasterio.open.side_effect = FileNotFoundError("File not found")

        coords = np.array([[1.0, 2.0]])
        result = extract_values_at_coords("nonexistent.tiff", coords)

        assert len(result) == 1
        assert np.isnan(result[0])


class TestRasterSampling:
    """Test raster sampling and interpolation functions."""

    @patch("datavia.library.spatial_ops.RASTERIO_AVAILABLE", False)
    def test_raster_sample_no_rasterio(self):
        """Test raster sampling when rasterio not available."""
        coords = np.array([[1.0, 2.0]])
        result = raster_sample("test.tiff", coords)

        assert len(result) == 1
        assert np.isnan(result[0])

    @patch("datavia.library.spatial_ops.rasterio")
    def test_raster_sample_nearest(self, mock_rasterio):
        """Test raster sampling with nearest neighbor method."""
        # This would test the nearest neighbor implementation
        # For now, let's just test that it calls the right function
        mock_src = MagicMock()
        mock_rasterio.open.return_value.__enter__.return_value = mock_src

        coords = np.array([[1.0, 2.0]])
        with patch(
            "datavia.library.spatial_ops.extract_values_at_coords"
        ) as mock_extract:
            mock_extract.return_value = np.array([10.0])
            raster_sample("test.tiff", coords, method="nearest")

            # Should call the extract function for nearest method
            mock_extract.assert_called_once()


class TestMultibandProcessing:
    """Test multiband TIFF processing functions."""

    @patch("datavia.library.spatial_ops.rasterio")
    def test_process_multiband_tiff_basic(self, mock_rasterio):
        """Test basic multiband TIFF processing."""
        # Mock rasterio dataset
        mock_src = MagicMock()
        mock_src.count = 3
        mock_src.height = 100
        mock_src.width = 200
        mock_src.crs.to_string.return_value = "EPSG:4326"
        mock_src.bounds = (0, 0, 10, 10)
        mock_src.dtypes = ["uint8", "uint8", "uint8"]
        mock_src.nodatavals = [None, None, None]
        mock_src.get_band_description.return_value = "Test Band"
        mock_rasterio.open.return_value.__enter__.return_value = mock_src

        result = process_multiband_tiff("test.tiff")

        assert isinstance(result, dict)
        assert result["band_count"] == 3
        assert result["shape"] == (100, 200)
        assert "bands" in result
        assert len(result["bands"]) == 3

    @patch("datavia.library.spatial_ops.RASTERIO_AVAILABLE", False)
    def test_process_multiband_tiff_no_rasterio(self):
        """Test multiband processing when rasterio not available."""
        coords = np.array([[1.0, 2.0]])
        result = process_multiband_tiff("test.tiff", coords)

        assert result == {}

    @patch("datavia.library.spatial_ops.rasterio")
    def test_process_multiband_tiff_file_error(self, mock_rasterio):
        """Test error handling in multiband processing."""
        mock_rasterio.open.side_effect = Exception("Read error")

        coords = np.array([[1.0, 2.0]])
        result = process_multiband_tiff("test.tiff", coords)

        assert result == {}


class TestMetadataOperations:
    """Test TIFF metadata reading functions."""

    @patch("datavia.library.spatial_ops.rasterio")
    def test_read_geotiff_metadata_basic(self, mock_rasterio):
        """Test basic GeoTIFF metadata reading."""
        mock_src = MagicMock()
        mock_src.crs.to_string.return_value = "EPSG:4326"
        mock_src.transform = [1.0, 0.0, -180.0, 0.0, -1.0, 90.0]
        mock_src.count = 2
        mock_src.width = 360
        mock_src.height = 180
        mock_src.nodata = -9999
        mock_src.bounds = (-180, -90, 180, 90)
        mock_src.dtypes = ["float32", "float32"]
        mock_rasterio.open.return_value.__enter__.return_value = mock_src

        result = read_geotiff_metadata("test.tiff")

        assert isinstance(result, dict)
        assert result["crs"] == "EPSG:4326"
        assert result["count"] == 2
        assert result["shape"] == (180, 360)  # (height, width)
        assert "bounds" in result
        assert "dtype" in result

    @patch("datavia.library.spatial_ops.RASTERIO_AVAILABLE", False)
    def test_read_geotiff_metadata_no_rasterio(self):
        """Test metadata reading when rasterio not available."""
        result = read_geotiff_metadata("test.tiff")

        assert result == {}

    @patch("datavia.library.spatial_ops.rasterio")
    def test_read_geotiff_metadata_file_error(self, mock_rasterio):
        """Test error handling in metadata reading."""
        mock_rasterio.open.side_effect = FileNotFoundError("File not found")

        result = read_geotiff_metadata("nonexistent.tiff")

        assert result == {}


class TestBoundsOperations:
    """Test spatial bounds operations."""

    @patch("datavia.library.spatial_ops.rasterio")
    def test_get_geotiff_bounds_basic(self, mock_rasterio):
        """Test basic GeoTIFF bounds extraction."""
        mock_src = MagicMock()
        mock_src.bounds = (0.0, 10.0, 20.0, 30.0)
        mock_rasterio.open.return_value.__enter__.return_value = mock_src

        result = get_geotiff_bounds("test.tiff")

        assert result == (0.0, 10.0, 20.0, 30.0)

    @patch("datavia.library.spatial_ops.RASTERIO_AVAILABLE", False)
    def test_get_geotiff_bounds_no_rasterio(self):
        """Test bounds extraction when rasterio not available."""
        result = get_geotiff_bounds("test.tiff")

        assert result is None

    @patch("datavia.library.spatial_ops.rasterio")
    def test_get_geotiff_bounds_file_error(self, mock_rasterio):
        """Test error handling in bounds extraction."""
        mock_rasterio.open.side_effect = Exception("Read error")

        result = get_geotiff_bounds("nonexistent.tiff")

        assert result is None


class TestCoordinateValidation:
    """Test coordinate bounds validation."""

    def test_validate_coordinates_in_bounds_inside(self):
        """Test coordinate validation for points inside bounds."""
        bounds = (0.0, 0.0, 10.0, 10.0)  # (min_x, min_y, max_x, max_y)
        coords = np.array([[5.0, 5.0], [2.0, 8.0]])

        result = validate_coordinates_in_bounds(coords, bounds)

        assert np.array_equal(result, [True, True])

    def test_validate_coordinates_in_bounds_outside(self):
        """Test coordinate validation for points outside bounds."""
        bounds = (0.0, 0.0, 10.0, 10.0)
        coords = np.array([[15.0, 5.0], [5.0, 15.0], [-1.0, 5.0]])

        result = validate_coordinates_in_bounds(coords, bounds)

        assert np.array_equal(result, [False, False, False])

    def test_validate_coordinates_in_bounds_mixed(self):
        """Test coordinate validation for mixed inside/outside points."""
        bounds = (0.0, 0.0, 10.0, 10.0)
        coords = np.array([[5.0, 5.0], [15.0, 5.0], [8.0, 2.0]])

        result = validate_coordinates_in_bounds(coords, bounds)

        assert np.array_equal(result, [True, False, True])

    def test_validate_coordinates_in_bounds_edge_cases(self):
        """Test coordinate validation for edge cases."""
        bounds = (0.0, 0.0, 10.0, 10.0)
        coords = np.array([[0.0, 0.0], [10.0, 10.0], [0.0, 10.0]])

        result = validate_coordinates_in_bounds(coords, bounds)

        # Should include boundary points
        assert np.array_equal(result, [True, True, True])


class TestIntegration:
    """Test integrated spatial operations workflows."""

    @patch("datavia.library.spatial_ops.rasterio")
    def test_extract_and_validate_workflow(self, mock_rasterio):
        """Test combined coordinate validation and value extraction."""
        # Mock rasterio dataset for both bounds and value extraction calls
        mock_src = MagicMock()
        mock_src.bounds = (0.0, 0.0, 10.0, 10.0)
        mock_src.crs.to_string.return_value = "EPSG:4326"
        mock_src.nodata = None
        mock_src.sample.return_value = [np.array([100.0])]

        # Set up context manager to return the same mock for all calls
        mock_rasterio.open.return_value.__enter__.return_value = mock_src

        coords = np.array([[5.0, 5.0], [15.0, 15.0]])  # one inside, one outside

        # First validate coordinates
        bounds = get_geotiff_bounds("test.tiff")
        assert bounds == (0.0, 0.0, 10.0, 10.0)

        valid_mask = validate_coordinates_in_bounds(coords, bounds)
        assert valid_mask[0]  # first point is valid
        assert not valid_mask[1]  # second point is invalid

        # Extract values only for valid coordinates
        valid_coords = coords[valid_mask]
        assert len(valid_coords) == 1  # Only one coordinate should be valid

        # Reset the mock to track new calls
        mock_src.reset_mock()
        values = extract_values_at_coords("test.tiff", valid_coords)

        assert len(values) == 1
        assert values[0] == 100.0
