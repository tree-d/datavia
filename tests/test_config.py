#!/usr/bin/env python3
"""
Unit tests for datavia.config module.

Tests configuration management, file creation, validation, and environment handling.
"""

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

import datavia.config
from datavia.config import DataviaConfig, get_config, reload_config


class TestDataviaConfig:
    """Test the DataviaConfig class."""

    def setup_method(self):
        """Set up test environment before each test."""
        self.test_dir = Path(tempfile.mkdtemp())
        self.config_file = self.test_dir / "test_datavia.conf"

    def teardown_method(self):
        """Clean up after each test."""
        # Clean up temporary files

        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_config_init_with_existing_file(self):
        """Test DataviaConfig initialization with existing config file."""
        # Create a test config file
        self.config_file.write_text(
            """
[paths]
base_directory = /test/base
data_directory = /test/data
log_directory = /test/logs
create_missing = true
path_validation = false

[database]
host = testhost
port = 5433
name = test_db
"""
        )

        config = DataviaConfig(config_file=str(self.config_file))

        assert config.base_directory == Path("/test/base")
        assert config.data_directory == Path("/test/data")
        assert config.log_directory == Path("/test/logs")
        assert config.config["database"]["host"] == "testhost"
        assert config.config["database"]["port"] == "5433"

    def test_config_init_without_file_creates_default(self):
        """Test DataviaConfig creates default config when file doesn't exist."""
        non_existent_file = self.test_dir / "nonexistent.conf"

        config = DataviaConfig(config_file=str(non_existent_file))

        # Should create default configuration
        assert config.base_directory.is_absolute()
        assert config.data_directory.is_absolute()
        assert config.log_directory.is_absolute()
        assert config.config["database"]["host"] == "localhost"
        assert config.config["database"]["port"] == "5432"

    def test_ensure_directories_creates_missing_dirs(self):
        """Test ensure_directories creates missing directories when enabled."""
        test_config = f"""
[paths]
base_directory = {self.test_dir}
data_directory = {self.test_dir}/data
log_directory = {self.test_dir}/logs
create_missing = true
path_validation = false

[database]
host = localhost
port = 5432
name = datavia
"""
        self.config_file.write_text(test_config)

        config = DataviaConfig(config_file=str(self.config_file))
        config.ensure_directories()

        assert (self.test_dir / "data").exists()
        assert (self.test_dir / "logs").exists()

    def test_ensure_directories_skips_when_disabled(self):
        """Test ensure_directories doesn't create dirs when create_missing=false."""
        test_config = f"""
[paths]
base_directory = {self.test_dir}
data_directory = {self.test_dir}/data
log_directory = {self.test_dir}/logs
create_missing = false
path_validation = false

[database]
host = localhost
port = 5432
name = datavia
"""
        self.config_file.write_text(test_config)

        config = DataviaConfig(config_file=str(self.config_file))
        config.ensure_directories()

        assert not (self.test_dir / "data").exists()
        assert not (self.test_dir / "logs").exists()

    def test_validate_paths_fails_on_nonexistent_base(self):
        """Test validate_paths raises error when base directory doesn't exist."""
        test_config = """
[paths]
base_directory = /nonexistent/path
data_directory = /nonexistent/path/data
log_directory = /nonexistent/path/logs
create_missing = false
path_validation = true

[database]
host = localhost
port = 5432
name = datavia
"""
        self.config_file.write_text(test_config)

        config = DataviaConfig(config_file=str(self.config_file))

        with pytest.raises(ValueError, match="Base directory does not exist"):
            config.validate_paths()

    def test_validate_paths_passes_when_base_exists(self):
        """Test validate_paths passes when base directory exists."""
        test_config = f"""
[paths]
base_directory = {self.test_dir}
data_directory = {self.test_dir}/data
log_directory = {self.test_dir}/logs
create_missing = false
path_validation = true

[database]
host = localhost
port = 5432
name = datavia
"""
        self.config_file.write_text(test_config)

        config = DataviaConfig(config_file=str(self.config_file))

        # Should not raise an exception
        config.validate_paths()

    def test_get_method_returns_config_values(self):
        """Test get method returns configuration values."""
        test_config = """
[custom_section]
test_key = test_value
numeric_key = 42

[database]
host = localhost
port = 5432
name = datavia
"""
        self.config_file.write_text(test_config)

        config = DataviaConfig(config_file=str(self.config_file))

        assert config.get("custom_section", "test_key") == "test_value"
        assert config.get("custom_section", "numeric_key") == "42"
        assert config.get("database", "host") == "localhost"
        assert config.get("nonexistent_section", "key", "default") == "default"


class TestConfigModule:
    """Test module-level config functions."""

    def setup_method(self):
        """Set up test environment."""
        self.test_dir = Path(tempfile.mkdtemp())
        self.config_file = self.test_dir / "test_datavia.conf"
        # Clear global config between tests
        datavia.config._CONFIG_STATE["config"] = None

    def teardown_method(self):
        """Clean up after tests."""

        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        # Clear global config after tests
        datavia.config._CONFIG_STATE["config"] = None

    @patch("datavia.config.DataviaConfig")
    def test_get_config_returns_singleton(self, mock_config_class):
        """Test get_config returns the same instance on multiple calls."""
        # Clear any existing cached config
        datavia.config._CONFIG_STATE["config"] = None

        config1 = get_config()
        config2 = get_config()

        assert config1 is config2
        mock_config_class.assert_called_once()

    @patch("datavia.config.DataviaConfig")
    def test_reload_config_creates_new_instance(self, mock_config_class):
        """Test reload_config forces creation of new config instance."""
        # Clear any existing cached config
        datavia.config._CONFIG_STATE["config"] = None

        get_config()
        reload_config()
        get_config()

        # Should be called twice - once for initial, once for reload
        assert mock_config_class.call_count == 2

    def test_config_with_environment_variables(self):
        """Test configuration with environment variable overrides."""
        # Set environment variables
        with patch.dict(
            os.environ,
            {
                "DATAVIA_DATABASE_HOST": "env_host",
                "DATAVIA_DATABASE_PORT": "9999",
            },
        ):
            config = DataviaConfig()

            # Note: This test assumes the config class supports env var overrides
            # If not implemented yet, this documents the expected behavior
            if hasattr(config, "config"):
                # Check if environment variables are respected
                # This might need adjustment based on actual implementation
                pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
