"""
Configuration management for Datavia.

Provides centralized configuration for database connections, file paths,
API settings, and other system parameters using config file approach.
"""

import configparser
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class DataviaConfig:
    """Centralized configuration manager for Datavia system."""

    def __init__(self, config_file: Optional[str] = None):
        """
        Initialize configuration manager.

        Parameters
        ----------
        config_file : str, optional
            Path to configuration file. If None, uses default locations.
        """
        self.config = configparser.ConfigParser()
        self._config_file = config_file
        self._load_config()

    def _find_config_file(self) -> Optional[str]:
        """Find configuration file in standard locations."""
        # If a specific config file is provided, try that first
        if self._config_file and os.path.exists(self._config_file):
            logger.info(f"Found config file: {self._config_file}")
            return self._config_file

        # Auto-detect project root and look for datavia.conf there
        project_root = self._auto_detect_base_directory()
        project_config = os.path.join(project_root, "datavia.conf")
        if os.path.exists(project_config):
            logger.info(f"Found config file at project root: {project_config}")
            return project_config

        # Fallback to other locations
        possible_locations = [
            "datavia.conf",  # Current directory
            os.path.expanduser("~/.datavia/config.conf"),  # User home
            "/etc/datavia/config.conf",  # System-wide
        ]

        for location in possible_locations:
            if location and os.path.exists(location):
                logger.info(f"Found config file: {location}")
                return location

        logger.warning("No config file found, using defaults")
        return None

    def _load_config(self):
        """Load configuration from file or set defaults."""
        config_file = self._find_config_file()

        # Set defaults first
        self._set_defaults()

        # Load from file if available
        if config_file:
            try:
                self.config.read(config_file)
                logger.info(f"Loaded configuration from {config_file}")
            except Exception as e:
                logger.error(f"Failed to load config file {config_file}: {e}")

    def _set_defaults(self):
        """Set default configuration values."""
        # Database configuration
        self.config["database"] = {
            "host": "localhost",
            "port": "5432",
            "database": "gis",
            "user": "gis",
            "password": "wieso",
            "connection_pooling": "true",
            "ssl_mode": "prefer",
        }

        # File path configuration - auto-detect base directory
        base_dir = self._auto_detect_base_directory()
        self.config["paths"] = {
            "base_directory": base_dir,
            "data_directory": "data/",
            "log_directory": "logs/",
            "path_validation": "true",
            "create_missing": "true",
        }

        # API configuration
        self.config["api"] = {
            "timeout_policy": "30",
            "retry_strategy": "exponential",
            "fallback_urls": "true",
            "url_validation": "true",
            "max_retries": "3",
        }

        # CRS and coordinate settings
        self.config["spatial"] = {
            "default_crs": "EPSG:25832",
            "coordinate_order": "xy",
            "error_handling": "strict",
        }

        # Logging configuration
        self.config["logging"] = {
            "level": "INFO",
            "error_policy": "user_notification",
            "error_notifications": "raise",
        }

    def _auto_detect_base_directory(self) -> str:
        """Auto-detect the base directory for the project."""
        # Start from current file location and work up
        current_path = Path(__file__).parent

        # Look for project markers
        project_markers = ["pixi.toml", "pyproject.toml", ".git", "datavia"]

        for parent in [current_path] + list(current_path.parents):
            for marker in project_markers:
                if (parent / marker).exists():
                    logger.info(f"Auto-detected base directory: {parent}")
                    return str(parent)

        # Fallback to current directory
        logger.warning("Could not auto-detect base directory, using current directory")
        return str(Path.cwd())

    # Database properties
    @property
    def database_url(self) -> str:
        """Get database connection URL."""
        db = self.config["database"]
        return f"postgresql://{db['user']}:{db['password']}@{db['host']}:{db['port']}/{db['database']}"

    @property
    def database_config(self) -> Dict[str, Any]:
        """Get database configuration dictionary."""
        return dict(self.config["database"])

    # Path properties
    @property
    def base_directory(self) -> Path:
        """Get base directory as Path object."""
        return Path(self.config["paths"]["base_directory"])

    @property
    def data_directory(self) -> Path:
        """Get data directory path."""
        base = self.base_directory
        data_path = self.config["paths"]["data_directory"]
        if os.path.isabs(data_path):
            return Path(data_path)
        return base / data_path

    @property
    def log_directory(self) -> Path:
        """Get log directory path."""
        base = self.base_directory
        log_path = self.config["paths"]["log_directory"]
        if os.path.isabs(log_path):
            return Path(log_path)
        return base / log_path

    # Spatial properties
    @property
    def default_crs(self) -> str:
        """Get default coordinate reference system."""
        return self.config["spatial"]["default_crs"]

    @property
    def coordinate_order(self) -> str:
        """Get coordinate order (xy or yx)."""
        return self.config["spatial"]["coordinate_order"]

    # API properties
    @property
    def api_timeout(self) -> int:
        """Get API timeout in seconds."""
        return int(self.config["api"]["timeout_policy"])

    @property
    def max_retries(self) -> int:
        """Get maximum number of API retries."""
        return int(self.config["api"]["max_retries"])

    def ensure_directories(self):
        """Create directories if they don't exist."""
        if self.config["paths"].getboolean("create_missing"):
            directories = [self.data_directory, self.log_directory]

            for directory in directories:
                try:
                    directory.mkdir(parents=True, exist_ok=True)
                    logger.debug(f"Ensured directory exists: {directory}")
                except Exception as e:
                    logger.error(f"Failed to create directory {directory}: {e}")

    def validate_paths(self):
        """Validate that required paths exist."""
        if self.config["paths"].getboolean("path_validation"):
            # Only validate base directory - others can be created
            if not self.base_directory.exists():
                raise ValueError(
                    f"Base directory does not exist: {self.base_directory}"
                )

    def get(self, section: str, key: str, fallback: Any = None) -> Any:
        """Get configuration value with fallback."""
        try:
            return self.config[section][key]
        except KeyError:
            if fallback is not None:
                return fallback
            raise KeyError(f"Configuration key not found: [{section}] {key}")


# Global configuration instance
_config = None


def get_config() -> DataviaConfig:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = DataviaConfig()
        _config.validate_paths()
        _config.ensure_directories()
    return _config


def reload_config(config_file: Optional[str] = None):
    """Reload configuration from file."""
    global _config
    _config = DataviaConfig(config_file)
    _config.validate_paths()
    _config.ensure_directories()
    return _config
