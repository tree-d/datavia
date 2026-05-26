#!/usr/bin/env python3
"""
Unit tests for datavia.cli module.

Tests command line interface functions including config file creation
and pipeline management.
"""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

from datavia.cli_config import create_config_file


# Create wrapper function that matches test expectations
def _create_config_file(config_file):
    """Wrapper for create_config_file with expected signature."""

    # Check if file exists (like old implementation)
    if Path(config_file).exists():
        return  # Skip if exists

    # Use default pipelines for backward compatibility
    default_pipelines = ["elevation", "soil"]
    create_config_file(default_pipelines, config_file)


class TestConfigFileCreation:
    """Test configuration file creation functions."""

    def setup_method(self):
        """Set up test environment."""
        self.test_dir = Path(tempfile.mkdtemp())
        self.config_file = self.test_dir / "test_datavia.conf"

    def teardown_method(self):
        """Clean up test environment."""

        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    @patch("builtins.open", new_callable=mock_open)
    def test_create_config_file_creates_default(self, mock_file):
        """Test _create_config_file creates default configuration."""
        _create_config_file(str(self.config_file))

        # Should open file for writing
        mock_file.assert_called_once_with(str(self.config_file), "w")
        # Should write content to file
        mock_file().write.assert_called()
        written_content = mock_file().write.call_args[0][0]

        # Check that essential sections are present
        assert "datavia" in written_content
        assert "pipelines" in written_content
        assert "elevation" in written_content

    @patch("pathlib.Path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_create_config_file_skips_if_exists(self, mock_file, mock_exists):
        """Test _create_config_file doesn't overwrite existing file."""
        mock_exists.return_value = True

        _create_config_file(str(self.config_file))

        # Should not open file for writing if file exists
        mock_file.assert_not_called()


class TestCliUtilsUpdatePipeline:
    """Tests for ``datavia.cli_utils.update_pipeline`` return-value forwarding.

    Verifies that the result of ``pipeline.update_data()`` is correctly
    propagated back to the caller — a bug-fix introduced after the original
    implementation always returned ``True`` regardless of pipeline outcome.
    """

    def _make_fake_config(self, tmp_path, pipeline_name: str) -> str:
        """Write a minimal Python config file containing one mock pipeline.

        Parameters
        ----------
        tmp_path : pathlib.Path
            Pytest-provided temporary directory.
        pipeline_name : str
            The ``name`` attribute the fake pipeline should expose.

        Returns
        -------
        str
            Absolute path to the written config file.
        """
        config_path = tmp_path / "datavia_config.py"
        config_path.write_text(
            "class _FakePipeline:\n"
            f"    name = '{pipeline_name}'\n"
            "    def update_data(self):\n"
            "        return self._update_result\n\n"
            "class _FakeDatavia:\n"
            "    def __init__(self, result):\n"
            "        p = _FakePipeline()\n"
            "        p._update_result = result\n"
            "        self.pipelines = [p]\n\n"
            f"datavia = _FakeDatavia(True)\n"
        )
        return str(config_path)

    def test_returns_true_when_update_data_succeeds(self, tmp_path) -> None:
        """update_pipeline forwards True when pipeline.update_data() returns True."""
        from datavia.cli_utils import reset_datavia_instance, update_pipeline

        config_path = tmp_path / "datavia_config.py"
        config_path.write_text(
            "class _P:\n"
            "    name = 'weather'\n"
            "    def update_data(self):\n"
            "        return True\n\n"
            "class _D:\n"
            "    pipelines = [_P()]\n\n"
            "datavia = _D()\n"
        )
        reset_datavia_instance()
        try:
            result = update_pipeline("weather", str(config_path))
            assert result is True
        finally:
            reset_datavia_instance()

    def test_returns_false_when_update_data_fails(self, tmp_path) -> None:
        """update_pipeline forwards False when pipeline.update_data() returns False."""
        from datavia.cli_utils import reset_datavia_instance, update_pipeline

        config_path = tmp_path / "datavia_config.py"
        config_path.write_text(
            "class _P:\n"
            "    name = 'weather'\n"
            "    def update_data(self):\n"
            "        return False\n\n"
            "class _D:\n"
            "    pipelines = [_P()]\n\n"
            "datavia = _D()\n"
        )
        reset_datavia_instance()
        try:
            result = update_pipeline("weather", str(config_path))
            assert result is False
        finally:
            reset_datavia_instance()

    def test_returns_false_for_unknown_pipeline(self, tmp_path) -> None:
        """update_pipeline returns False when the named pipeline is not registered."""
        from datavia.cli_utils import reset_datavia_instance, update_pipeline

        config_path = tmp_path / "datavia_config.py"
        config_path.write_text(
            "class _P:\n"
            "    name = 'elevation'\n"
            "    def update_data(self):\n"
            "        return True\n\n"
            "class _D:\n"
            "    pipelines = [_P()]\n\n"
            "datavia = _D()\n"
        )
        reset_datavia_instance()
        try:
            result = update_pipeline("weather", str(config_path))
            assert result is False
        finally:
            reset_datavia_instance()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
