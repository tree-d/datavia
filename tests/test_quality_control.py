"""Tests for data quality control and validation functions."""

import numpy as np
import pytest

from datavia.library.quality_control import (
    _detect_outliers_iqr,
    _detect_outliers_modified_zscore,
    _detect_outliers_zscore,
    apply_quality_filters,
    check_data_quality,
    detect_outliers,
    validate_coordinate_bounds,
)


class TestCoordinateValidation:
    """Test coordinate bounds validation functions."""

    def test_validate_coordinate_bounds_valid_wgs84(self):
        """Test coordinate validation for valid WGS84 coordinates."""
        coords = np.array([[10.0, 50.0], [-120.0, 30.0], [0.0, 0.0]])
        bounds = (-180.0, -90.0, 180.0, 90.0)

        result = validate_coordinate_bounds(coords, bounds, crs="EPSG:4326")

        assert result["valid"] is True
        assert len(result["valid_indices"]) == 3
        assert result["error"] is None

    def test_validate_coordinate_bounds_invalid_wgs84(self):
        """Test coordinate validation for invalid WGS84 coordinates."""
        coords = np.array([[200.0, 50.0], [10.0, 100.0], [-190.0, 30.0]])
        bounds = (-180.0, -90.0, 180.0, 90.0)

        result = validate_coordinate_bounds(coords, bounds, crs="EPSG:4326")

        assert result["valid"] is False
        assert len(result["valid_indices"]) == 0
        assert "geographic" in result["error"]

    def test_validate_coordinate_bounds_mixed_validity(self):
        """Test coordinate validation with mixed valid/invalid coordinates."""
        coords = np.array([[10.0, 50.0], [200.0, 30.0], [-120.0, 40.0]])
        bounds = (-180.0, -90.0, 180.0, 90.0)

        result = validate_coordinate_bounds(coords, bounds, crs="EPSG:4326")

        assert result["valid"] is False
        assert len(result["valid_indices"]) == 2  # first and third coordinates
        assert 0 in result["valid_indices"]
        assert 2 in result["valid_indices"]

    def test_validate_coordinate_bounds_custom_bounds(self):
        """Test coordinate validation with custom bounds."""
        coords = np.array([[5.0, 15.0], [25.0, 35.0], [15.0, 25.0]])
        bounds = (10.0, 20.0, 20.0, 30.0)  # restricted bounds

        result = validate_coordinate_bounds(coords, bounds, crs="EPSG:4326")

        assert result["valid"] is False
        assert len(result["valid_indices"]) == 1  # only third coordinate in bounds
        assert 2 in result["valid_indices"]

    def test_validate_coordinate_bounds_invalid_format(self):
        """Test coordinate validation with invalid coordinate format."""
        coords = np.array([10.0, 50.0])  # 1D array instead of 2D
        bounds = (-180.0, -90.0, 180.0, 90.0)

        result = validate_coordinate_bounds(coords, bounds)

        assert result["valid"] is False
        assert "Coordinates must be 2D array" in result["error"]
        assert result["valid_indices"] == []

    def test_validate_coordinate_bounds_nan_values(self):
        """Test coordinate validation with NaN values."""
        coords = np.array([[10.0, 50.0], [np.nan, 30.0], [20.0, np.inf]])
        bounds = (-180.0, -90.0, 180.0, 90.0)

        result = validate_coordinate_bounds(coords, bounds)

        assert result["valid"] is False
        assert len(result["valid_indices"]) == 1  # only first coordinate is valid
        assert 0 in result["valid_indices"]

    def test_validate_coordinate_bounds_non_wgs84(self):
        """Test coordinate validation for non-WGS84 CRS."""
        coords = np.array([[500000.0, 6000000.0], [600000.0, 7000000.0]])
        bounds = (400000.0, 5000000.0, 700000.0, 8000000.0)

        result = validate_coordinate_bounds(coords, bounds, crs="EPSG:3857")

        assert result["valid"] is True
        assert len(result["valid_indices"]) == 2


class TestOutlierDetection:
    """Test outlier detection functions."""

    def test_detect_outliers_iqr_basic(self):
        """Test IQR outlier detection with basic dataset."""
        # Dataset with clear outliers
        data = np.array([1, 2, 3, 4, 5, 100, 200])  # 100, 200 are outliers

        result = _detect_outliers_iqr(data, threshold=1.5)

        assert "outlier_indices" in result
        assert "outlier_values" in result
        assert len(result["outlier_indices"]) >= 2  # should detect the outliers

    def test_detect_outliers_iqr_no_outliers(self):
        """Test IQR outlier detection with no outliers."""
        data = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])

        result = _detect_outliers_iqr(data, threshold=1.5)

        assert len(result["outlier_indices"]) == 0

    def test_detect_outliers_zscore_basic(self):
        """Test Z-score outlier detection."""
        # Normal distribution with outliers
        np.random.seed(42)
        normal_data = np.random.normal(0, 1, 100)
        outliers = np.array([10, -10])  # extreme outliers
        data = np.concatenate([normal_data, outliers])

        result = _detect_outliers_zscore(data, threshold=3.0)

        assert len(result["outlier_indices"]) >= 2

    def test_detect_outliers_modified_zscore_basic(self):
        """Test Modified Z-score outlier detection."""
        data = np.array([1, 2, 3, 4, 5, 100])  # 100 is outlier

        result = _detect_outliers_modified_zscore(data, threshold=3.5)

        assert len(result["outlier_indices"]) >= 1

    def test_detect_outliers_interface(self):
        """Test the main detect_outliers interface function."""
        data = np.array([1, 2, 3, 4, 5, 100, 200])

        # Test IQR method
        result_iqr = detect_outliers(data, method="iqr", threshold=1.5)
        assert "outlier_indices" in result_iqr

        # Test Z-score method
        result_zscore = detect_outliers(data, method="zscore", threshold=3.0)
        assert "outlier_indices" in result_zscore

        # Test Modified Z-score method
        result_mod_zscore = detect_outliers(
            data, method="modified_zscore", threshold=3.5
        )
        assert "outlier_indices" in result_mod_zscore

    def test_detect_outliers_invalid_method(self):
        """Test outlier detection with invalid method."""
        data = np.array([1, 2, 3, 4, 5])

        with pytest.raises(ValueError, match="Unknown outlier detection method"):
            detect_outliers(data, method="invalid_method")

    def test_detect_outliers_empty_data(self):
        """Test outlier detection with empty data."""
        data = np.array([])

        result = detect_outliers(data, method="iqr")
        assert len(result["outlier_indices"]) == 0

    def test_detect_outliers_single_value(self):
        """Test outlier detection with single value."""
        data = np.array([42])

        result = detect_outliers(data, method="iqr")
        assert len(result["outlier_indices"]) == 0


class TestDataQualityAssessment:
    """Test comprehensive data quality assessment functions."""

    def test_check_data_quality_good_data(self):
        """Test data quality check with good quality data."""
        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        coords = np.array(
            [[10.0, 50.0], [20.0, 60.0], [30.0, 70.0], [40.0, 50.0], [50.0, 60.0]]
        )

        result = check_data_quality(data, coords)

        assert isinstance(result, dict)
        assert "overall_quality" in result
        assert "data_coverage" in result
        assert "outlier_percentage" in result
        assert result["data_coverage"] > 0.9  # should have good coverage

    def test_check_data_quality_with_missing_data(self):
        """Test data quality check with missing data."""
        data = np.array([1.0, np.nan, 3.0, np.nan, 5.0])
        coords = np.array(
            [[10.0, 50.0], [20.0, 60.0], [30.0, 70.0], [40.0, 50.0], [50.0, 60.0]]
        )

        result = check_data_quality(data, coords)

        assert result["data_coverage"] == 0.6  # 3 out of 5 valid
        assert "missing_data_percentage" in result

    def test_check_data_quality_with_outliers(self):
        """Test data quality check with outliers."""
        data = np.array([1.0, 2.0, 100.0, 4.0, 5.0])  # 100 is outlier
        coords = np.array(
            [[10.0, 50.0], [20.0, 60.0], [30.0, 70.0], [40.0, 50.0], [50.0, 60.0]]
        )

        result = check_data_quality(data, coords)

        assert result["outlier_percentage"] > 0
        assert "outliers_detected" in result

    def test_check_data_quality_invalid_coordinates(self):
        """Test data quality check with invalid coordinates."""
        data = np.array([1.0, 2.0, 3.0])
        coords = np.array(
            [[200.0, 50.0], [20.0, 60.0], [30.0, 70.0]]
        )  # first coord invalid

        result = check_data_quality(data, coords)

        assert "coordinate_validity" in result
        assert result["coordinate_validity"] < 1.0

    def test_check_data_quality_mismatched_lengths(self):
        """Test data quality check with mismatched data and coordinate lengths."""
        data = np.array([1.0, 2.0, 3.0])
        coords = np.array([[10.0, 50.0], [20.0, 60.0]])  # different length

        with pytest.raises(
            ValueError, match="Data and coordinates must have same length"
        ):
            check_data_quality(data, coords)


class TestQualityFiltering:
    """Test quality-based data filtering functions."""

    def test_apply_quality_filters_basic(self):
        """Test basic quality filtering."""
        data = np.array([1.0, 2.0, np.nan, 100.0, 5.0])  # NaN and outlier
        coords = np.array(
            [[10.0, 50.0], [20.0, 60.0], [30.0, 70.0], [40.0, 50.0], [50.0, 60.0]]
        )

        result = apply_quality_filters(data, coords)

        assert isinstance(result, dict)
        assert "filtered_data" in result
        assert "filtered_coords" in result
        assert "filter_summary" in result

        # Should have fewer points than input
        assert len(result["filtered_data"]) <= len(data)
        assert len(result["filtered_coords"]) == len(result["filtered_data"])

    def test_apply_quality_filters_no_outlier_removal(self):
        """Test quality filtering without outlier removal."""
        data = np.array([1.0, 2.0, np.nan, 100.0, 5.0])
        coords = np.array(
            [[10.0, 50.0], [20.0, 60.0], [30.0, 70.0], [40.0, 50.0], [50.0, 60.0]]
        )

        result = apply_quality_filters(data, coords, remove_outliers=False)

        # Should only remove NaN values, not outliers
        assert len(result["filtered_data"]) == 4  # remove only NaN

    def test_apply_quality_filters_custom_bounds(self):
        """Test quality filtering with custom coordinate bounds."""
        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        coords = np.array(
            [[5.0, 15.0], [25.0, 35.0], [15.0, 25.0], [100.0, 200.0], [12.0, 22.0]]
        )

        bounds = (10.0, 20.0, 20.0, 30.0)  # restrictive bounds
        result = apply_quality_filters(data, coords, coordinate_bounds=bounds)

        # Should filter out coordinates outside bounds
        assert len(result["filtered_data"]) <= 2  # only coords within bounds

    def test_apply_quality_filters_all_invalid(self):
        """Test quality filtering when all data is invalid."""
        data = np.array([np.nan, np.nan, np.nan])
        coords = np.array([[10.0, 50.0], [20.0, 60.0], [30.0, 70.0]])

        result = apply_quality_filters(data, coords)

        assert len(result["filtered_data"]) == 0
        assert len(result["filtered_coords"]) == 0
        assert "removed_all_data" in result["filter_summary"]

    def test_apply_quality_filters_preserve_valid(self):
        """Test that quality filtering preserves good quality data."""
        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        coords = np.array(
            [[10.0, 50.0], [20.0, 60.0], [30.0, 70.0], [40.0, 50.0], [50.0, 60.0]]
        )

        result = apply_quality_filters(data, coords)

        # All data should be preserved if it's good quality
        assert len(result["filtered_data"]) == len(data)
        np.testing.assert_array_equal(result["filtered_data"], data)


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_outlier_detection_constant_data(self):
        """Test outlier detection with constant data."""
        data = np.array([5, 5, 5, 5, 5])

        result = detect_outliers(data, method="iqr")
        assert len(result["outlier_indices"]) == 0

    def test_quality_check_extreme_coordinates(self):
        """Test quality check with extreme coordinate values."""
        data = np.array([1.0, 2.0])
        coords = np.array([[-180.0, -90.0], [180.0, 90.0]])  # extreme valid coords

        result = check_data_quality(data, coords)
        assert result["coordinate_validity"] == 1.0

    def test_quality_filters_empty_input(self):
        """Test quality filtering with empty input."""
        data = np.array([])
        coords = np.array([]).reshape(0, 2)

        result = apply_quality_filters(data, coords)
        assert len(result["filtered_data"]) == 0
        assert len(result["filtered_coords"]) == 0

    def test_coordinate_validation_edge_bounds(self):
        """Test coordinate validation at exact boundary values."""
        coords = np.array([[-180.0, -90.0], [180.0, 90.0], [0.0, 0.0]])
        bounds = (-180.0, -90.0, 180.0, 90.0)

        result = validate_coordinate_bounds(coords, bounds, crs="EPSG:4326")
        assert result["valid"] is True
        assert len(result["valid_indices"]) == 3  # all should be valid
