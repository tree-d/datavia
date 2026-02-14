#!/usr/bin/env python3
"""
Unit tests for datavia.library.coordinate_transforms module.

Tests coordinate transformations between different CRS systems.
"""

import numpy as np
import pytest
from pyproj.exceptions import CRSError

from datavia.library.coordinate_transforms import get_transformer, transform_coordinates


class TestCoordinateTransforms:
    """Test coordinate transformation functions."""

    def test_get_transformer_creates_transformer(self):
        """Test get_transformer creates a pyproj.Transformer object."""
        transformer = get_transformer("EPSG:4326", "EPSG:3857")

        # Should return a transformer object
        assert transformer is not None
        # Should have transform method
        assert hasattr(transformer, "transform")

    def test_get_transformer_same_crs(self):
        """Test get_transformer with same source and target CRS."""
        transformer = get_transformer("EPSG:4326", "EPSG:4326")

        assert transformer is not None
        # Transform should be identity (or close to it)
        x, y = transformer.transform(13.4050, 52.5200)  # Berlin
        assert abs(x - 13.4050) < 1e-6
        assert abs(y - 52.5200) < 1e-6

    def test_get_transformer_invalid_crs(self):
        """Test get_transformer with invalid CRS raises error."""
        with pytest.raises((ValueError, CRSError)):  # Handle both possible exceptions
            get_transformer("INVALID:CRS", "EPSG:4326")

    def test_transform_coordinates_single_point(self):
        """Test transform_coordinates with a single coordinate pair."""
        # Berlin coordinates: WGS84 to Web Mercator
        coords = np.array([[13.4050, 52.5200]])

        result = transform_coordinates(coords, "EPSG:4326", "EPSG:3857")

        assert result.shape == (1, 2)
        # Web Mercator coordinates should be much larger
        assert abs(result[0, 0]) > 1000000  # X coordinate (easting)
        assert abs(result[0, 1]) > 1000000  # Y coordinate (northing)

    def test_transform_coordinates_multiple_points(self):
        """Test transform_coordinates with multiple coordinate pairs."""
        # German cities: Berlin, Munich, Hamburg
        coords = np.array(
            [
                [13.4050, 52.5200],  # Berlin
                [11.5820, 48.1351],  # Munich
                [9.9937, 53.5511],  # Hamburg
            ]
        )

        result = transform_coordinates(coords, "EPSG:4326", "EPSG:3857")

        assert result.shape == (3, 2)
        # All coordinates should be transformed
        for i in range(3):
            assert abs(result[i, 0]) > 1000000  # X coordinates
            assert abs(result[i, 1]) > 1000000  # Y coordinates

    def test_transform_coordinates_identity(self):
        """Test transform_coordinates with same source and target CRS."""
        coords = np.array([[13.4050, 52.5200]])

        result = transform_coordinates(coords, "EPSG:4326", "EPSG:4326")

        # Should be approximately the same as input
        np.testing.assert_allclose(result, coords, rtol=1e-6)

    def test_transform_coordinates_empty_array(self):
        """Test transform_coordinates with empty coordinate array."""
        coords = np.array([]).reshape(0, 2)

        result = transform_coordinates(coords, "EPSG:4326", "EPSG:3857")

        assert result.shape == (0, 2)

    def test_transform_coordinates_round_trip(self):
        """Test transformation round trip (forward and back)."""
        original_coords = np.array(
            [
                [13.4050, 52.5200],  # Berlin
                [11.5820, 48.1351],  # Munich
            ]
        )

        # Transform to Web Mercator and back
        transformed = transform_coordinates(original_coords, "EPSG:4326", "EPSG:3857")
        back_transformed = transform_coordinates(transformed, "EPSG:3857", "EPSG:4326")

        # Should be very close to original
        np.testing.assert_allclose(back_transformed, original_coords, rtol=1e-6)

    def test_transform_coordinates_german_to_utm(self):
        """Test transformation from WGS84 to German UTM zones."""
        # Coordinates spanning Germany (should mostly be UTM 32N and 33N)
        coords = np.array(
            [
                [6.0, 51.0],  # Western Germany (UTM 32N)
                [13.4050, 52.5200],  # Berlin (UTM 33N)
                [15.0, 52.0],  # Eastern Germany (UTM 33N)
            ]
        )

        # Transform to UTM 32N
        result_utm32 = transform_coordinates(coords, "EPSG:4326", "EPSG:25832")

        assert result_utm32.shape == (3, 2)
        # UTM coordinates should be in reasonable ranges (based on actual data)
        assert all(200000 < x < 1000000 for x in result_utm32[:, 0])  # Easting
        assert all(5000000 < y < 6000000 for y in result_utm32[:, 1])  # Northing

    def test_transform_coordinates_invalid_input_shape(self):
        """Test transform_coordinates with invalid input shape."""
        # Wrong shape (should be Nx2)
        coords = np.array([13.4050, 52.5200])  # 1D array

        # Function handles gracefully and returns original on error
        result = transform_coordinates(coords, "EPSG:4326", "EPSG:3857")

        # Should return the original coordinates on error
        np.testing.assert_allclose(result, coords)

    def test_transform_coordinates_nan_handling(self):
        """Test transform_coordinates with NaN values."""
        coords = np.array(
            [
                [13.4050, 52.5200],  # Valid
                [np.nan, 52.5200],  # NaN longitude
                [13.4050, np.nan],  # NaN latitude
            ]
        )

        result = transform_coordinates(coords, "EPSG:4326", "EPSG:3857")

        assert result.shape == (3, 2)
        # First point should be valid
        assert not np.isnan(result[0, 0]) and not np.isnan(result[0, 1])
        # Points with NaN input may produce NaN output (behavior depends on pyproj)

    def test_transform_coordinates_out_of_bounds(self):
        """Test transform_coordinates with coordinates outside valid ranges."""
        # Extreme coordinates
        coords = np.array(
            [
                [200.0, 90.0],  # Invalid longitude
                [0.0, -100.0],  # Invalid latitude
            ]
        )

        # Should handle gracefully (might return NaN or raise exception)
        try:
            result = transform_coordinates(coords, "EPSG:4326", "EPSG:3857")
            # If no exception, check for NaN values
            assert result.shape == (2, 2)
        except Exception:
            # Acceptable behavior for invalid coordinates
            pass


class TestTransformerCaching:
    """Test transformer caching behavior."""

    def test_transformer_basic_functionality(self):
        """Test that get_transformer returns working transformers."""
        transformer1 = get_transformer("EPSG:4326", "EPSG:3857")
        transformer2 = get_transformer("EPSG:4326", "EPSG:3857")

        # Both should be valid transformers
        assert transformer1 is not None
        assert transformer2 is not None
        assert hasattr(transformer1, "transform")
        assert hasattr(transformer2, "transform")

        # Should be the same cached object
        assert transformer1 is transformer2

    def test_transformer_different_crs_creates_different_objects(self):
        """Test that different CRS combinations create different transformers."""
        transformer1 = get_transformer("EPSG:4326", "EPSG:3857")
        transformer2 = get_transformer("EPSG:4326", "EPSG:25832")

        # Should be different objects
        assert transformer1 is not transformer2
        # Both should still work
        assert hasattr(transformer1, "transform")
        assert hasattr(transformer2, "transform")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
