"""
Configuration management for Datavia.

Provides centralized configuration for database connections, file paths,
API settings, and other system parameters using config file approach.
"""

import configparser
import hashlib
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

    @staticmethod
    def _user_dir() -> Path:
        """Return the datavia user home directory (``~/.datavia/``).

        This is the single canonical location for all user-specific datavia
        state: data files, logs, the optional ``datavia.conf``, and the
        optional ``.env`` for secrets.  It is created on demand by
        :meth:`ensure_directories` — callers must not assume it exists yet.

        Returns
        -------
        Path
            ``Path.home() / ".datavia"``
        """
        return Path.home() / ".datavia"

    def _find_config_file(self) -> str | None:
        """Find the datavia configuration file.

        Search order:

        1. Explicit path passed to the constructor (``config_file`` argument).
        2. ``~/.datavia/datavia.conf`` — the primary user configuration.
        3. ``<cwd>/datavia.conf`` — a project-local override (useful when
           running datavia from a project directory with custom settings).
        4. ``/etc/datavia/datavia.conf`` — system-wide default.

        Before searching, ``~/.datavia/.env`` is loaded via *python-dotenv*
        so that environment variables (e.g. ``POSTGRES_PASSWORD``) are
        available for ``${VAR:-default}`` expansion inside the config file.

        Returns
        -------
        str | None
            Absolute path to the first found configuration file, or ``None``
            when none of the locations exist (defaults are used instead).
        """
        # Always load .env so that env-var expansion inside datavia.conf works
        # and the database password is available to _set_defaults().
        # Search order mirrors runner._env_file_args():
        #   1. <cwd>/.env   — project-local secrets (highest priority)
        #   2. ~/.datavia/.env — user-global secrets
        for candidate in [Path.cwd() / ".env", self._user_dir() / ".env"]:
            if candidate.exists():
                dotenv.load_dotenv(dotenv_path=candidate)
                logger.info("Loaded environment variables from %s", candidate)
                break

        # 1. Explicit path from constructor
        if self._config_file and os.path.exists(self._config_file):
            logger.info(f"Found config file: {self._config_file}")
            return self._config_file

        # 2. User home
        user_config = self._user_dir() / "datavia.conf"
        if user_config.exists():
            logger.info(f"Found config file: {user_config}")
            return str(user_config)

        # 3. Project-local override (cwd)
        cwd_config = Path.cwd() / "datavia.conf"
        if cwd_config.exists():
            logger.info(f"Found project-local config file: {cwd_config}")
            return str(cwd_config)

        # 4. System-wide
        system_config = Path("/etc/datavia/datavia.conf")
        if system_config.exists():
            logger.info(f"Found system config file: {system_config}")
            return str(system_config)

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

    @staticmethod
    def _derive_project_defaults(cwd: Path) -> str:
        """Derive a stable project name from the working directory.

        Uses an MD5 digest of the absolute CWD path so that every project
        directory gets a unique, reproducible identity without any persistent
        state or user configuration.

        Parameters
        ----------
        cwd : Path
            The working directory whose path is hashed.

        Returns
        -------
        str
            Project name such as ``"datavia-a1b2c3d4"``.
        """
        digest = hashlib.md5(str(cwd).encode()).hexdigest()  # nosec B324
        return "datavia-" + digest[:8]

    def _set_defaults(self) -> None:
        """Set default configuration values."""
        project_name = self._derive_project_defaults(Path.cwd())

        # Project identity.
        # The value is derived from the CWD hash so each project directory gets
        # a stable, unique name without any configuration.  Override with
        # ``[project] name = my_project`` in datavia.conf.
        self.config["project"] = {
            "name": project_name,
        }

        # Database configuration.
        # Only connection_pooling is set here as a default.  The backend URL is
        # derived lazily in database_url:  SQLite (default) unless the user
        # configures ``[database] url`` or host/user keys in datavia.conf.
        self.config["database"] = {
            "connection_pooling": "true",
        }

        # File path configuration.
        # base_directory is intentionally left empty here so that the
        # base_directory property derives it from the storage setting at
        # runtime.  Users who want a fully custom path can set
        # base_directory explicitly in datavia.conf.
        self.config["paths"] = {
            "base_directory": "",
            # storage = project (default) →  <cwd>/.datavia/  (per-project, self-contained)
            # storage = global            →  ~/.datavia/      (shared across all projects)
            "storage": "project",
            "data_directory": "data/",
            "log_directory": "logs/",
            "path_validation": "false",  # directory is created on first use
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

    # Project properties
    @property
    def project_name(self) -> str:
        """Return the stable per-project identifier derived from the working directory.

        The value is a short hash of the absolute CWD path, giving each project
        directory a unique, reproducible name without any persistent state or user
        configuration.  Override with ``[project] name = my_project`` in
        ``datavia.conf``.

        Returns
        -------
        str
            Project name such as ``"datavia-a1b2c3d4"``.
        """
        return self.config["project"]["name"]

    # Database properties
    @property
    def database_url(self) -> str:
        """Return the database connection URL.

        Resolution order:

        1. ``[database] url`` key in ``datavia.conf`` — used verbatim (any
           SQLAlchemy-compatible backend).
        2. ``[database] host`` key present — a PostgreSQL URL is built from
           the ``host``, ``port``, ``user``, ``password``, and ``database``
           keys.
        3. Fallback — a SQLite database file located in the configured data
           directory (``<data_directory>/datavia.db``).

        Returns
        -------
        str
            A SQLAlchemy-compatible database URL string.
        """
        db = self.config["database"]
        # 1. Explicit URL takes precedence (any backend).
        explicit_url = db.get("url", "").strip()
        if explicit_url:
            return explicit_url
        # 2. PostgreSQL credentials configured inline.
        host = db.get("host", "").strip()
        if host:
            user = db.get("user", "gis")
            password = db.get("password", "datavia_dev")
            port = db.get("port", "5432")
            database = db.get("database", "gis")
            return f"postgresql://{user}:{password}@{host}:{port}/{database}"
        # 3. Default: SQLite file in the data directory.
        return f"sqlite:///{self.data_directory / 'datavia.db'}"

    @property
    def database_config(self) -> dict[str, Any]:
        """Get database configuration dictionary."""
        return dict(self.config["database"])

    # Path properties
    @property
    def base_directory(self) -> Path:
        """Return the datavia base directory.

        Resolution order:

        1. ``base_directory`` key in ``datavia.conf`` if non-empty — full
           explicit override for power users.
        2. ``storage = project`` **(default)** → ``<cwd>/.datavia/`` — per-project,
           self-contained; set this in a project-local ``datavia.conf``.
        3. ``storage = global`` → ``~/.datavia/`` — shared across
           all projects; avoids re-downloading large data files.

        Returns
        -------
        Path
            Resolved base directory; the directory may not exist yet
            (``ensure_directories`` creates it on first use).
        """
        explicit = self.config["paths"].get("base_directory", "").strip()
        if explicit:
            return Path(explicit)
        storage = self.config["paths"].get("storage", "global")
        if storage == "project":
            return Path.cwd() / ".datavia"
        return self._user_dir()

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

    _GITIGNORE_CONTENT = (
        "# datavia managed directories — large files, not tracked by git\n"
        "data/\n"
        "logs/\n"
    )

    _ROOT_GITIGNORE_ENTRY = ".datavia/\n"

    def _update_root_gitignore(self) -> None:
        """Append ``.datavia/`` to the project root ``.gitignore`` if not already present.

        Walks up from the current working directory to locate the git
        repository root (identified by a ``.git`` entry).  Falls back to the
        current working directory when no ``.git`` is found.  The file is
        only modified when no existing line already contains ``.datavia``, so
        manual customisations and pre-existing entries are always preserved.

        Returns
        -------
        None

        Raises
        ------
        No exceptions are raised; errors are logged at the ERROR level.
        """
        # Walk up from cwd to find the git repository root.
        git_root = Path.cwd()
        for candidate in [Path.cwd(), *Path.cwd().parents]:
            if (candidate / ".git").exists():
                git_root = candidate
                break

        gitignore_path = git_root / ".gitignore"

        # Skip if .datavia is already covered by any active (non-comment) line.
        if gitignore_path.exists():
            active_lines = [
                line
                for line in gitignore_path.read_text().splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            if any(".datavia" in line for line in active_lines):
                logger.debug(
                    "Root .gitignore already covers .datavia — no update needed."
                )
                return

        # Append the entry, ensuring it starts on its own line.
        try:
            needs_newline = (
                gitignore_path.exists()
                and gitignore_path.stat().st_size > 0
                and not gitignore_path.read_text().endswith("\n")
            )
            with gitignore_path.open("a") as fh:
                if needs_newline:
                    fh.write("\n")
                fh.write(
                    "\n# Datavia runtime directory — created automatically on first use\n"
                    + self._ROOT_GITIGNORE_ENTRY
                )
            logger.debug("Added .datavia/ to root .gitignore at %s", gitignore_path)
        except Exception as e:
            logger.error(
                "Failed to update root .gitignore at %s: %s", gitignore_path, e
            )

    def ensure_directories(self) -> None:
        """Create data and log directories and write a .gitignore into the base directory.

        The ``.gitignore`` is written once when the base directory is first
        created, preventing accidental commits of large GeoTIFF files and logs.
        An existing ``.gitignore`` is never overwritten so users can customise
        it freely after the first run.
        """
        if self.config["paths"].getboolean("create_missing"):
            directories = [self.data_directory, self.log_directory]

            for directory in directories:
                try:
                    directory.mkdir(parents=True, exist_ok=True)
                    logger.debug(f"Ensured directory exists: {directory}")
                except Exception as e:
                    logger.error(f"Failed to create directory {directory}: {e}")

            gitignore_path = self.base_directory / ".gitignore"
            if not gitignore_path.exists():
                try:
                    gitignore_path.write_text(self._GITIGNORE_CONTENT)
                    logger.debug(f"Wrote .gitignore to {gitignore_path}")
                except Exception as e:
                    logger.error(f"Failed to write .gitignore: {e}")

            self._update_root_gitignore()

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
