"""
Configuration management for Datavia.

Provides centralized configuration for database connections, file paths,
API settings, and other system parameters using config file approach.
"""

import configparser
import logging
import os
import re
from pathlib import Path
from typing import Any, cast

import dotenv

logger = logging.getLogger(__name__)


class _ConfigManager:
    """Singleton-like manager for Datavia configuration."""

    def __init__(self) -> None:
        self._config_instance: DataviaConfig | None = None
        self._initialized = False

    def get_config(self, config_file: str | None = None) -> "DataviaConfig":
        """
        Get or create the DataviaConfig instance lazily.

        If a config_file is provided, it will force a reload of the configuration.
        """
        if self._config_instance is None or config_file:
            self._config_instance = DataviaConfig(config_file)
            self._initialized = False  # Mark as not initialized if reloaded
        return self._config_instance

    def reload(self, config_file: str | None = None) -> "DataviaConfig":
        """Force a reload of the configuration."""
        self._config_instance = DataviaConfig(config_file)
        self._initialized = False
        return self._config_instance

    def is_initialized(self) -> bool:
        """Check if the config has been validated and directories created."""
        return self._initialized

    def set_initialized(self) -> None:
        """Mark the config as initialized."""
        self._initialized = True


_config_manager = _ConfigManager()


def get_config(config_file: str | None = None) -> "DataviaConfig":
    """
    Get the application-wide configuration object.

    This function provides a singleton instance of the DataviaConfig,
    ensuring that configuration is loaded only once. It will also validate
    paths and ensure directories exist on first creation.

    Args:
        config_file: Optional path to a specific config file to load.
                     If provided, it may force a reload of the configuration.

    Returns:
        The singleton DataviaConfig instance.
    """
    # Get the config instance. If it's the first time, it will be created.
    config_instance = _config_manager.get_config(config_file)

    # Ensure validation and directory creation only happens once on creation
    if not _config_manager.is_initialized():
        config_instance.validate_paths()
        config_instance.ensure_directories()
        _config_manager.set_initialized()

    return config_instance


def reload_config(config_file: str | None = None) -> "DataviaConfig":
    """
    Force a reload of the configuration from a file and replace the
    global instance.
    """
    reloaded_config = _config_manager.reload(config_file=config_file)
    reloaded_config.validate_paths()
    reloaded_config.ensure_directories()
    _config_manager.set_initialized()
    return reloaded_config


class DataviaConfig:
    """Centralized configuration manager for Datavia system."""

    def __init__(self, config_file: str | None = None):
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

    def _find_config_file(self) -> str | None:
        """Find configuration file in standard locations."""
        # If a specific config file is provided, try that first
        if self._config_file and os.path.exists(self._config_file):
            logger.info(f"Found config file: {self._config_file}")
            return self._config_file

        # Auto-detect project root and look for datavia.conf there
        project_root = self._auto_detect_base_directory()

        # Load .env file from project root if it exists
        dotenv_path = Path(project_root) / ".env"
        if dotenv_path.exists():
            dotenv.load_dotenv(dotenv_path=dotenv_path)
            logger.info(f"Loaded environment variables from {dotenv_path}")

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

    def _load_config(self) -> None:
        """Load configuration from file or set defaults."""
        config_file = self._find_config_file()

        # Set defaults first
        self._set_defaults()

        # Load from file if available
        if config_file:
            try:
                self.config.read(config_file)
                self._expand_environment_variables()
                self._apply_environment_overrides()
                logger.info(f"Loaded configuration from {config_file}")
            except Exception as e:
                logger.error(f"Failed to load config file {config_file}: {e}")
        else:
            self._apply_environment_overrides()

    def _expand_environment_variables(self) -> None:
        """Expand environment variables in config values.

        Supports ${VAR} and ${VAR:-default} syntax.
        Example: ${POSTGRES_PASSWORD:-datavia_dev}
        """

        for section in self.config.sections():
            for key in self.config[section]:
                value = self.config[section][key]

                # Match ${VAR} or ${VAR:-default}
                pattern = r"\$\{([^}:]+)(?::-([^}]*))?\}"

                def replace_env(match: re.Match[str]) -> str:
                    var_name = match.group(1)
                    default_value = match.group(2) if match.group(2) is not None else ""
                    return cast(str, os.getenv(var_name, default_value))

                expanded_value = re.sub(pattern, replace_env, value)
                self.config[section][key] = expanded_value

    def _apply_environment_overrides(self) -> None:
        """Apply DATAVIA_* environment variable overrides to config.

        Uses the pattern DATAVIA_<SECTION>_<KEY>, e.g. DATAVIA_DATABASE_HOST.
        """
        for section in self.config.sections():
            for key in self.config[section]:
                env_key = f"DATAVIA_{section}_{key}".upper()
                env_value = os.getenv(env_key)
                if env_value is not None:
                    self.config[section][key] = env_value

    def _set_defaults(self) -> None:
        """Set default configuration values."""
        # Database configuration
        self.config["database"] = {
            "host": "localhost",
            "port": "5432",
            "database": "gis",
            "user": "gis",
            "password": os.getenv("POSTGRES_PASSWORD", "datavia_dev"),
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

        for parent in [current_path, *list(current_path.parents)]:
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
    def database_config(self) -> dict[str, Any]:
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

    def ensure_directories(self) -> None:
        """Create directories if they don't exist."""
        if self.config["paths"].getboolean("create_missing"):
            directories = [self.data_directory, self.log_directory]

            for directory in directories:
                try:
                    directory.mkdir(parents=True, exist_ok=True)
                    logger.debug(f"Ensured directory exists: {directory}")
                except Exception as e:
                    logger.error(f"Failed to create directory {directory}: {e}")

    def validate_paths(self) -> None:
        """Validate that required paths exist."""
        if (
            self.config["paths"].getboolean("path_validation")
            and not self.base_directory.exists()
        ):
            raise ValueError(f"Base directory does not exist: {self.base_directory}")

    def get(self, section: str, key: str, fallback: Any = None) -> Any:
        """Get configuration value with fallback."""
        try:
            return self.config[section][key]
        except KeyError:
            if fallback is not None:
                return fallback
            raise KeyError(f"Configuration key not found: [{section}] {key}") from None
