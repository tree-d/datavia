"""End-to-end integration tests for Datavia pipeline workflows."""

import pytest
import numpy as np
import tempfile
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from datavia.config import DataviaConfig
from datavia.core.datavia import Datavia
from datavia.runner import start_container, stop_container, get_container_status


class TestDataviaE2EWorkflows:
    """End-to-end tests for complete Datavia workflows."""

    @pytest.fixture
    def temp_config_dir(self):
        """Create temporary config directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def sample_coordinates(self):
        """Sample coordinates for testing."""
        return np.array(
            [
                [10.0, 50.0],  # Germany
                [2.35, 48.85],  # Paris
                [-0.12, 51.5],  # London
                [12.5, 41.9],  # Rome
            ]
        )

    def test_elevation_pipeline_workflow(self, temp_config_dir, sample_coordinates):
        """Test complete elevation data pipeline workflow."""
        # Setup config
        config_file = temp_config_dir / "datavia.conf"

        with (
            patch("datavia.config.Path.exists", return_value=True),
            patch("datavia.config.Path.read_text") as mock_read,
        ):
            # Mock config content
            mock_config = {
                "data_dir": str(temp_config_dir / "data"),
                "log_level": "INFO",
                "cache_enabled": True,
            }
            mock_read.return_value = json.dumps(mock_config)

            config = DataviaConfig(str(config_file))

            # Initialize Datavia instance
            with patch("datavia.core.datavia.DataviaConfig") as mock_config_class:
                mock_config_class.return_value = config
                datavia = Datavia()

                # Mock pipeline components
                with patch("datavia.elevation.ElevationPipeline") as mock_pipeline:
                    # Mock elevation data response
                    mock_elevation_data = {
                        "elevation_values": np.array([500.0, 100.0, 50.0, 300.0]),
                        "coordinates": sample_coordinates,
                        "source": "SRTM",
                        "resolution": "30m",
                    }
                    mock_pipeline.return_value.process.return_value = (
                        mock_elevation_data
                    )

                    # Test pipeline execution
                    result = datavia.run_pipeline(
                        "elevation", coordinates=sample_coordinates
                    )

                    # Verify results
                    assert "elevation_values" in result
                    assert len(result["elevation_values"]) == len(sample_coordinates)
                    assert result["source"] == "SRTM"

                    # Verify pipeline was called correctly
                    mock_pipeline.assert_called_once()
                    mock_pipeline.return_value.process.assert_called_once()

    def test_soil_pipeline_workflow(self, temp_config_dir, sample_coordinates):
        """Test complete soil data pipeline workflow."""
        config_file = temp_config_dir / "datavia.conf"

        with (
            patch("datavia.config.Path.exists", return_value=True),
            patch("datavia.config.Path.read_text") as mock_read,
        ):
            mock_config = {
                "data_dir": str(temp_config_dir / "data"),
                "log_level": "INFO",
            }
            mock_read.return_value = json.dumps(mock_config)

            config = DataviaConfig(str(config_file))

            with patch("datavia.core.datavia.DataviaConfig") as mock_config_class:
                mock_config_class.return_value = config
                datavia = Datavia()

                with patch("datavia.soil.SoilPipeline") as mock_pipeline:
                    # Mock soil data response
                    mock_soil_data = {
                        "ph": np.array([6.5, 7.0, 6.8, 7.2]),
                        "organic_carbon": np.array([2.1, 1.8, 2.5, 1.9]),
                        "coordinates": sample_coordinates,
                        "source": "SoilGrids",
                        "depth": "0-5cm",
                    }
                    mock_pipeline.return_value.process.return_value = mock_soil_data

                    result = datavia.run_pipeline(
                        "soil", coordinates=sample_coordinates
                    )

                    assert "ph" in result
                    assert "organic_carbon" in result
                    assert len(result["ph"]) == len(sample_coordinates)
                    assert result["source"] == "SoilGrids"

    def test_multi_pipeline_workflow(self, temp_config_dir, sample_coordinates):
        """Test workflow combining multiple pipelines."""
        config_file = temp_config_dir / "datavia.conf"

        with (
            patch("datavia.config.Path.exists", return_value=True),
            patch("datavia.config.Path.read_text") as mock_read,
        ):
            mock_config = {
                "data_dir": str(temp_config_dir / "data"),
                "log_level": "INFO",
            }
            mock_read.return_value = json.dumps(mock_config)

            config = DataviaConfig(str(config_file))

            with patch("datavia.core.datavia.DataviaConfig") as mock_config_class:
                mock_config_class.return_value = config
                datavia = Datavia()

                # Mock both pipelines
                with (
                    patch("datavia.elevation.ElevationPipeline") as mock_elev,
                    patch("datavia.soil.SoilPipeline") as mock_soil,
                ):
                    mock_elev_data = {
                        "elevation_values": np.array([500.0, 100.0, 50.0, 300.0]),
                        "coordinates": sample_coordinates,
                    }
                    mock_soil_data = {
                        "ph": np.array([6.5, 7.0, 6.8, 7.2]),
                        "coordinates": sample_coordinates,
                    }

                    mock_elev.return_value.process.return_value = mock_elev_data
                    mock_soil.return_value.process.return_value = mock_soil_data

                    # Run both pipelines
                    elevation_result = datavia.run_pipeline(
                        "elevation", coordinates=sample_coordinates
                    )
                    soil_result = datavia.run_pipeline(
                        "soil", coordinates=sample_coordinates
                    )

                    # Verify both worked
                    assert "elevation_values" in elevation_result
                    assert "ph" in soil_result

                    # Verify coordinates match
                    np.testing.assert_array_equal(
                        elevation_result["coordinates"], soil_result["coordinates"]
                    )


class TestContainerIntegration:
    """Test container lifecycle in realistic scenarios."""

    @patch("subprocess.run")
    def test_container_startup_workflow(self, mock_run):
        """Test complete container startup workflow."""
        # Mock container status progression
        status_responses = [
            # Initial check: not running
            MagicMock(stdout="", returncode=0),
            # Start containers
            MagicMock(returncode=0),
            # Verify running
            MagicMock(stdout="container123", returncode=0),
        ]
        mock_run.side_effect = status_responses

        # Check initial status
        assert get_container_status() is False

        # Start containers
        with patch("time.sleep"):  # Speed up test
            start_container()

        # Verify now running
        assert get_container_status() is True

    @patch("subprocess.run")
    def test_container_cleanup_workflow(self, mock_run):
        """Test complete container cleanup workflow."""
        # Mock container running initially
        cleanup_responses = [
            # Initial check: running
            MagicMock(stdout="container123", returncode=0),
            # Stop containers
            MagicMock(returncode=0),
            # Down containers
            MagicMock(returncode=0),
            # Verify stopped
            MagicMock(stdout="", returncode=0),
        ]
        mock_run.side_effect = cleanup_responses

        # Verify initially running
        assert get_container_status() is True

        # Stop containers
        with patch("time.sleep"):
            stop_container()

        # Verify now stopped
        assert get_container_status() is False


class TestDataQualityWorkflows:
    """Test data quality assessment in realistic workflows."""

    def test_coordinate_validation_workflow(self):
        """Test complete coordinate validation workflow."""
        from datavia.library.quality_control import validate_coordinate_bounds

        # Mixed quality coordinates
        coords = np.array(
            [
                [10.0, 50.0],  # Valid
                [200.0, 30.0],  # Invalid longitude
                [-120.0, 40.0],  # Valid
                [50.0, 100.0],  # Invalid latitude
                [0.0, 0.0],  # Valid
            ]
        )
        bounds = (-180.0, -90.0, 180.0, 90.0)

        result = validate_coordinate_bounds(coords, bounds, crs="EPSG:4326")

        # Should identify valid coordinates
        assert len(result["valid_indices"]) == 3  # indices 0, 2, 4
        assert 0 in result["valid_indices"]
        assert 2 in result["valid_indices"]
        assert 4 in result["valid_indices"]

    def test_outlier_detection_workflow(self):
        """Test complete outlier detection workflow."""
        from datavia.library.quality_control import detect_outliers

        # Dataset with clear outliers
        elevation_data = np.array([100, 120, 110, 105, 115, 1000, 95, 108, 2000, 102])

        # Detect outliers using IQR method
        result = detect_outliers(elevation_data, method="iqr", threshold=1.5)

        # Should detect the extreme values (1000, 2000)
        assert len(result["indices"]) >= 2
        assert (
            1000 in elevation_data[result["indices"]]
            or 2000 in elevation_data[result["indices"]]
        )

    def test_spatial_bounds_validation_workflow(self):
        """Test spatial bounds validation in pipeline context."""
        from datavia.library.spatial_ops import validate_coordinates_in_bounds

        # Test coordinates around Europe
        coords = np.array(
            [
                [10.0, 50.0],  # Germany - inside
                [100.0, 30.0],  # Asia - outside
                [2.0, 48.0],  # France - inside
                [-50.0, 60.0],  # Atlantic - outside
            ]
        )

        # European bounds
        europe_bounds = (
            -10.0,
            35.0,
            30.0,
            70.0,
        )  # (min_lon, min_lat, max_lon, max_lat)

        valid_mask = validate_coordinates_in_bounds(coords, europe_bounds)

        # Should validate European coordinates
        assert valid_mask[0] == True  # Germany
        assert valid_mask[1] == False  # Asia
        assert valid_mask[2] == True  # France
        assert valid_mask[3] == False  # Atlantic


class TestSpatialOperationsWorkflow:
    """Test spatial operations in realistic scenarios."""

    @patch("datavia.library.spatial_ops.rasterio")
    def test_tiff_value_extraction_workflow(self, mock_rasterio):
        """Test complete TIFF value extraction workflow."""
        from datavia.library.spatial_ops import extract_values_at_coords

        # Mock TIFF data
        mock_src = MagicMock()
        mock_src.crs.to_string.return_value = "EPSG:4326"
        mock_src.nodata = -9999
        mock_src.sample.return_value = [
            np.array([500.0]),  # Elevation value 1
            np.array([100.0]),  # Elevation value 2
            np.array([-9999]),  # No data
            np.array([300.0]),  # Elevation value 3
        ]
        mock_rasterio.open.return_value.__enter__.return_value = mock_src

        coords = np.array(
            [
                [10.0, 50.0],
                [2.0, 48.0],
                [0.0, 0.0],  # No data location
                [12.0, 42.0],
            ]
        )

        values = extract_values_at_coords("elevation.tiff", coords)

        # Should extract values with nodata converted to NaN
        assert len(values) == 4
        assert values[0] == 500.0
        assert values[1] == 100.0
        assert np.isnan(values[2])  # nodata converted to NaN
        assert values[3] == 300.0

    @patch("datavia.library.spatial_ops.rasterio")
    def test_metadata_extraction_workflow(self, mock_rasterio):
        """Test complete metadata extraction workflow."""
        from datavia.library.spatial_ops import (
            read_geotiff_metadata,
            get_geotiff_bounds,
        )

        # Mock TIFF metadata
        mock_src = MagicMock()
        mock_src.crs.to_string.return_value = "EPSG:4326"
        mock_src.bounds = (-180.0, -90.0, 180.0, 90.0)
        mock_src.height = 1800
        mock_src.width = 3600
        mock_src.count = 1
        mock_src.nodata = -32768
        mock_src.dtypes = ["int16"]
        mock_src.transform = [0.1, 0.0, -180.0, 0.0, -0.1, 90.0]
        mock_rasterio.open.return_value.__enter__.return_value = mock_src

        # Extract metadata
        metadata = read_geotiff_metadata("global_dem.tiff")
        bounds = get_geotiff_bounds("global_dem.tiff")

        # Verify metadata extraction
        assert metadata["crs"] == "EPSG:4326"
        assert metadata["shape"] == (1800, 3600)
        assert metadata["count"] == 1
        assert metadata["nodata"] == -32768

        # Verify bounds extraction
        assert bounds == (-180.0, -90.0, 180.0, 90.0)


class TestConfigurationWorkflows:
    """Test configuration management in realistic scenarios."""

    def test_config_initialization_workflow(self, tmp_path):
        """Test complete configuration initialization workflow."""
        config_file = tmp_path / "datavia.conf"

        # Test config creation
        config = DataviaConfig(str(config_file))

        # Verify default values
        assert config.get("log_level") == "INFO"
        assert config.get("cache_enabled") == True
        assert "data" in config.get("data_dir")

        # Test config modification
        config.set("log_level", "DEBUG")
        config.set("custom_setting", "test_value")

        # Test config persistence
        config.save()

        # Reload and verify persistence
        new_config = DataviaConfig(str(config_file))
        assert new_config.get("log_level") == "DEBUG"
        assert new_config.get("custom_setting") == "test_value"

    def test_global_config_workflow(self):
        """Test global configuration instance workflow."""
        from datavia.config import get_config, reload_config

        # Test global instance
        config1 = get_config()
        config2 = get_config()

        # Should be singleton
        assert config1 is config2

        # Test configuration reload
        reload_config()
        config3 = get_config()

        # Should be new instance after reload
        assert config3 is not config1


class TestErrorHandlingWorkflows:
    """Test error handling in realistic failure scenarios."""

    def test_missing_file_workflow(self):
        """Test handling of missing TIFF files."""
        from datavia.library.spatial_ops import extract_values_at_coords

        coords = np.array([[10.0, 50.0]])

        # Should handle missing files gracefully
        values = extract_values_at_coords("nonexistent.tiff", coords)

        assert len(values) == 1
        assert np.isnan(values[0])

    def test_invalid_coordinates_workflow(self):
        """Test handling of invalid coordinate inputs."""
        from datavia.library.quality_control import validate_coordinate_bounds

        # Test with invalid coordinate format
        invalid_coords = np.array([10.0, 50.0])  # Should be 2D
        bounds = (-180.0, -90.0, 180.0, 90.0)

        result = validate_coordinate_bounds(invalid_coords, bounds)

        assert result["valid"] is False
        assert "Coordinates must be 2D array" in result["error"]

    @patch("subprocess.run")
    def test_container_failure_workflow(self, mock_run):
        """Test handling of container operation failures."""
        # Mock container start failure
        mock_run.side_effect = Exception("Docker not available")

        # Should handle docker failures gracefully
        with pytest.raises(Exception):
            start_container()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
