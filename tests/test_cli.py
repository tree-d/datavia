#!/usr/bin/env python3
"""
Unit tests for datavia.cli module.

Tests command line interface functions including config file creation,
pipeline management, and container operations.
"""

import pytest
import tempfile
import os
import json
from pathlib import Path
from unittest.mock import patch, mock_open, MagicMock, call

from datavia.cli import (
    _create_config_file,
    _generate_selective_imports,
    _get_pipeline_dependencies,
    _install_pipeline_dependencies,
    _start,
    _stop,
    _update_pipeline,
    load_config_if_exists,
)


class TestConfigFileCreation:
    """Test configuration file creation functions."""

    def setup_method(self):
        """Set up test environment."""
        self.test_dir = Path(tempfile.mkdtemp())
        self.config_file = self.test_dir / "test_datavia.conf"

    def teardown_method(self):
        """Clean up test environment."""
        import shutil

        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    @patch("datavia.cli.Path.write_text")
    def test_create_config_file_creates_default(self, mock_write):
        """Test _create_config_file creates default configuration."""
        _create_config_file(str(self.config_file))

        # Should write configuration content
        mock_write.assert_called_once()
        written_content = mock_write.call_args[0][0]

        # Check that essential sections are present
        assert "[paths]" in written_content
        assert "[database]" in written_content
        assert "base_directory" in written_content
        assert "host = localhost" in written_content

    @patch("datavia.cli.Path.exists")
    @patch("datavia.cli.Path.write_text")
    def test_create_config_file_skips_if_exists(self, mock_write, mock_exists):
        """Test _create_config_file doesn't overwrite existing file."""
        mock_exists.return_value = True

        _create_config_file(str(self.config_file))

        # Should not write if file exists
        mock_write.assert_not_called()

    def test_load_config_if_exists_loads_existing(self):
        """Test load_config_if_exists loads existing configuration."""
        # Create a test config file
        test_config = {
            "pipelines": ["elevation", "soil"],
            "custom_setting": "test_value",
        }

        with open(self.config_file, "w") as f:
            json.dump(test_config, f)

        result = load_config_if_exists(str(self.config_file))

        assert result == test_config

    def test_load_config_if_exists_returns_empty_for_nonexistent(self):
        """Test load_config_if_exists returns empty dict for nonexistent file."""
        result = load_config_if_exists(str(self.test_dir / "nonexistent.json"))

        assert result == {}

    @patch("builtins.open", mock_open(read_data="invalid json"))
    def test_load_config_if_exists_handles_invalid_json(self):
        """Test load_config_if_exists handles invalid JSON gracefully."""
        result = load_config_if_exists("some_file.json")

        assert result == {}


class TestPipelineDependencies:
    """Test pipeline dependency management functions."""

    def test_get_pipeline_dependencies_elevation(self):
        """Test _get_pipeline_dependencies for elevation pipeline."""
        deps = _get_pipeline_dependencies("elevation")

        assert isinstance(deps, list)
        # Should include geospatial dependencies
        expected_deps = ["rasterio", "numpy", "pyproj"]
        for dep in expected_deps:
            assert any(dep in d for d in deps)

    def test_get_pipeline_dependencies_soil(self):
        """Test _get_pipeline_dependencies for soil pipeline."""
        deps = _get_pipeline_dependencies("soil")

        assert isinstance(deps, list)
        # Should include HTTP and data processing dependencies
        expected_deps = ["requests", "numpy"]
        for dep in expected_deps:
            assert any(dep in d for d in deps)

    def test_get_pipeline_dependencies_unknown(self):
        """Test _get_pipeline_dependencies for unknown pipeline."""
        deps = _get_pipeline_dependencies("unknown_pipeline")

        assert deps == []

    @patch("subprocess.run")
    def test_install_pipeline_dependencies_success(self, mock_run):
        """Test _install_pipeline_dependencies successful installation."""
        mock_run.return_value.returncode = 0

        result = _install_pipeline_dependencies(["numpy", "rasterio"])

        assert result is True
        mock_run.assert_called_once()
        # Should use pip install
        args = mock_run.call_args[0][0]
        assert "pip" in args
        assert "install" in args
        assert "numpy" in args
        assert "rasterio" in args

    @patch("subprocess.run")
    def test_install_pipeline_dependencies_failure(self, mock_run):
        """Test _install_pipeline_dependencies handles installation failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "pip")

        result = _install_pipeline_dependencies(["nonexistent_package"])

        assert result is False

    @patch("subprocess.run")
    def test_install_pipeline_dependencies_empty_list(self, mock_run):
        """Test _install_pipeline_dependencies with empty dependency list."""
        result = _install_pipeline_dependencies([])

        assert result is True
        # Should not call pip install
        mock_run.assert_not_called()


class TestSelectiveImports:
    """Test selective import generation."""

    def test_generate_selective_imports_single_pipeline(self):
        """Test _generate_selective_imports for single pipeline."""
        import_code = _generate_selective_imports(["elevation"])

        assert "from datavia.elevation import ElevationPipeline" in import_code
        assert "except ImportError" in import_code
        assert "elevation = ElevationPipeline()" in import_code

    def test_generate_selective_imports_multiple_pipelines(self):
        """Test _generate_selective_imports for multiple pipelines."""
        import_code = _generate_selective_imports(["elevation", "soil"])

        assert "from datavia.elevation import ElevationPipeline" in import_code
        assert "from datavia.soil import SoilPipeline" in import_code
        assert "elevation = ElevationPipeline()" in import_code
        assert "soil = SoilPipeline()" in import_code

    def test_generate_selective_imports_empty_list(self):
        """Test _generate_selective_imports with empty pipeline list."""
        import_code = _generate_selective_imports([])

        # Should still have basic structure
        assert "pipelines = []" in import_code
        assert "from datavia import Datavia" in import_code

    def test_generate_selective_imports_unknown_pipeline(self):
        """Test _generate_selective_imports with unknown pipeline."""
        import_code = _generate_selective_imports(["unknown_pipeline"])

        # Should handle gracefully, possibly with generic import pattern
        assert isinstance(import_code, str)
        assert len(import_code) > 0


class TestContainerCommands:
    """Test container management CLI functions."""

    @patch("datavia.cli.start_container")
    @patch("datavia.cli.get_container_status")
    def test_start_when_container_stopped(self, mock_status, mock_start):
        """Test _start function when container is stopped."""
        mock_status.return_value = False
        mock_start.return_value = True

        result = _start()

        assert result is True
        mock_start.assert_called_once()

    @patch("datavia.cli.start_container")
    @patch("datavia.cli.get_container_status")
    def test_start_when_container_running(self, mock_status, mock_start):
        """Test _start function when container is already running."""
        mock_status.return_value = True

        result = _start()

        assert result is True
        # Should not try to start if already running
        mock_start.assert_not_called()

    @patch("datavia.cli.stop_container")
    @patch("datavia.cli.get_container_status")
    def test_stop_when_container_running(self, mock_status, mock_stop):
        """Test _stop function when container is running."""
        mock_status.return_value = True
        mock_stop.return_value = True

        result = _stop()

        assert result is True
        mock_stop.assert_called_once()

    @patch("datavia.cli.stop_container")
    @patch("datavia.cli.get_container_status")
    def test_stop_when_container_stopped(self, mock_status, mock_stop):
        """Test _stop function when container is already stopped."""
        mock_status.return_value = False

        result = _stop()

        assert result is True
        # Should not try to stop if already stopped
        mock_stop.assert_not_called()


class TestPipelineUpdate:
    """Test pipeline update functionality."""

    @patch("datavia.cli._install_pipeline_dependencies")
    @patch("datavia.cli._get_pipeline_dependencies")
    def test_update_pipeline_installs_dependencies(self, mock_get_deps, mock_install):
        """Test _update_pipeline installs pipeline dependencies."""
        mock_get_deps.return_value = ["numpy", "rasterio"]
        mock_install.return_value = True

        result = _update_pipeline("elevation")

        assert result is True
        mock_get_deps.assert_called_once_with("elevation")
        mock_install.assert_called_once_with(["numpy", "rasterio"])

    @patch("datavia.cli._install_pipeline_dependencies")
    @patch("datavia.cli._get_pipeline_dependencies")
    def test_update_pipeline_handles_install_failure(self, mock_get_deps, mock_install):
        """Test _update_pipeline handles dependency installation failure."""
        mock_get_deps.return_value = ["numpy", "rasterio"]
        mock_install.return_value = False

        result = _update_pipeline("elevation")

        assert result is False

    @patch("datavia.cli._install_pipeline_dependencies")
    @patch("datavia.cli._get_pipeline_dependencies")
    def test_update_pipeline_no_dependencies(self, mock_get_deps, mock_install):
        """Test _update_pipeline with pipeline that has no dependencies."""
        mock_get_deps.return_value = []

        result = _update_pipeline("unknown_pipeline")

        # Should still succeed if no dependencies
        assert result is True
        mock_install.assert_called_once_with([])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
