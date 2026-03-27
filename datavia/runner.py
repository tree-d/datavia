"""Docker container management for Datavia.

Provides utilities for starting, stopping, and checking the status of
the PostGIS database container used by Datavia for metadata storage.

Each project directory is automatically assigned a unique Docker Compose
project name and host port derived from a hash of the working directory
path.  This means two projects in different directories can run their
containers simultaneously without clashing, with no user configuration
required.  To override, set ``[project] name`` and ``[database] port``
in the project-local ``datavia.conf``.

The container is managed via docker-compose.yml located in the same
directory as this module.

Functions
---------
start_container : Start the PostGIS database container
stop_container : Stop and clean up the database container
get_container_status : Check if container is running
"""

import configparser
import hashlib
import logging
import os
import subprocess  # nosec B404 - subprocess required for docker-compose management
import time
from pathlib import Path

_POSTGRES_READY_TIMEOUT_S = 30
_POSTGRES_POLL_INTERVAL_S = 1
_PORT_RANGE_MIN = 49152
_PORT_RANGE_MAX = 65535

logger = logging.getLogger(__name__)
# Compose directory is always the package directory itself, where docker-compose.yml
# is bundled. Using __file__ ensures this works both in development and when the
# package is installed as a wheel into site-packages.
compose_dir = Path(__file__).parent


def _env_file_args() -> list[str]:
    """Return --env-file arguments for the first .env file found.

    Search order:

    1. ``<cwd>/.env`` — project-local secrets (highest priority, allows
       per-project database password overrides).
    2. ``~/.datavia/.env`` — user-global secrets (the recommended place for
       a single shared ``POSTGRES_PASSWORD``).

    Only the first file found is passed to docker compose.  Using explicit
    path lookups instead of calling ``get_config()`` avoids initialising the
    config singleton as a side-effect of container management calls.

    Returns:
        A list ``["--env-file", "<path>"]`` when a .env file is found,
        otherwise an empty list so the caller can splat it unconditionally.
    """
    candidates = [
        Path.cwd() / ".env",
        Path.home() / ".datavia" / ".env",
    ]
    for env_file in candidates:
        if env_file.is_file():
            logger.debug("Using .env file: %s", env_file)
            return ["--env-file", str(env_file)]
    return []


def _cwd_hash() -> str:
    """Return the MD5 hex digest of the current working directory path.

    Returns
    -------
    str
        MD5 hexdigest of ``str(Path.cwd())``.
    """
    return hashlib.md5(str(Path.cwd()).encode()).hexdigest()  # nosec B324


def _project_name() -> str:
    """Return the Docker Compose project name for the current project.

    Reads ``[project] name`` from the first ``datavia.conf`` found (CWD,
    then ``~/.datavia/``).  Falls back to a hash-derived slug so each project
    directory owns its own isolated container without requiring any user
    configuration.

    Returns
    -------
    str
        Project name such as ``"datavia-a1b2c3d4"``.
    """
    for candidate in [
        Path.cwd() / "datavia.conf",
        Path.home() / ".datavia" / "datavia.conf",
    ]:
        if candidate.is_file():
            cfg = configparser.ConfigParser()
            cfg.read(candidate)
            name = cfg.get("project", "name", fallback=None)
            if name:
                return name
    digest = _cwd_hash()
    return "datavia-" + digest[:8]


def _project_port() -> int:
    """Return the host port bound by the Docker Compose database service.

    Reads ``[database] port`` from the first ``datavia.conf`` found.
    Falls back to a hash-derived port in the unprivileged range
    49152-65535 so that parallel projects on the same machine cannot
    collide on the default port.

    Returns
    -------
    int
        Host port for the PostGIS container.
    """
    for candidate in [
        Path.cwd() / "datavia.conf",
        Path.home() / ".datavia" / "datavia.conf",
    ]:
        if candidate.is_file():
            cfg = configparser.ConfigParser()
            cfg.read(candidate)
            port = cfg.get("database", "port", fallback=None)
            if port and port.isdigit():
                return int(port)
    digest = _cwd_hash()
    return _PORT_RANGE_MIN + int(digest[:4], 16) % (_PORT_RANGE_MAX - _PORT_RANGE_MIN)


def _project_args() -> list[str]:
    """Return ``--project-name`` arguments for docker compose.

    Ensures each project directory maps to a distinct Docker Compose
    project, preventing container name and network collisions between
    projects that use datavia simultaneously.

    Returns
    -------
    list[str]
        ``["--project-name", "<name>"]``
    """
    return ["--project-name", _project_name()]


def _compose_env() -> dict[str, str]:
    """Return the environment dict for docker compose subprocess calls.

    Injects ``DATAVIA_PORT`` so the ``docker-compose.yml`` port mapping
    uses the project-specific host port instead of the hard-wired 5432.

    Returns
    -------
    dict[str, str]
        Copy of the current process environment with ``DATAVIA_PORT`` set.
    """
    return {**os.environ, "DATAVIA_PORT": str(_project_port())}


def start_container() -> None:
    """Start the datavia container and wait until PostgreSQL is ready.

    Runs ``docker compose up -d``, then polls ``pg_isready`` inside the
    container until the database accepts connections.

    Raises
    ------
    subprocess.CalledProcessError
        If ``docker compose up`` fails.
    TimeoutError
        If PostgreSQL does not become ready within
        ``_POSTGRES_READY_TIMEOUT_S`` seconds.
    """
    try:
        subprocess.run(
            ["docker", "compose", *_env_file_args(), *_project_args(), "up", "-d"],
            cwd=str(compose_dir),
            check=True,  # nosec B603 B607
            env=_compose_env(),
        )
        _wait_for_postgres()
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to start containers: {e}")
        raise


def _wait_for_postgres(
    timeout: int = _POSTGRES_READY_TIMEOUT_S,
    poll_interval: float = _POSTGRES_POLL_INTERVAL_S,
) -> None:
    """Poll the database container until PostgreSQL accepts connections.

    Runs ``pg_isready`` inside the container via ``docker compose exec``.
    Returns as soon as the exit code is 0 (PostgreSQL ready).

    Parameters
    ----------
    timeout:
        Maximum number of seconds to wait before raising ``TimeoutError``.
        Defaults to ``_POSTGRES_READY_TIMEOUT_S``.
    poll_interval:
        Seconds to sleep between readiness probes.
        Defaults to ``_POSTGRES_POLL_INTERVAL_S``.

    Raises
    ------
    TimeoutError
        If PostgreSQL is not ready within *timeout* seconds.
    """
    logger.info("Waiting for PostgreSQL to be ready (timeout=%ds)…", timeout)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(  # nosec B603 B607
            [
                "docker",
                "compose",
                *_env_file_args(),
                *_project_args(),
                "exec",
                "db",
                "pg_isready",
                "-U",
                "gis",
            ],
            cwd=str(compose_dir),
            capture_output=True,
            env=_compose_env(),
            check=False,
        )
        if result.returncode == 0:
            logger.info("PostgreSQL is ready.")
            return
        time.sleep(poll_interval)

    raise TimeoutError(
        f"PostgreSQL did not become ready within {timeout} seconds. "
        f"Check container logs: docker compose --project-name {_project_name()} logs db"
    )


def stop_container() -> None:
    """Stop the datavia container with proper cleanup."""
    try:
        # Stop containers gracefully with timeout
        subprocess.run(
            [
                "docker",
                "compose",
                *_env_file_args(),
                *_project_args(),
                "stop",
                "-t",
                "10",
            ],
            cwd=str(compose_dir),
            check=True,  # nosec B603 B607
            env=_compose_env(),
        )

        # Wait for containers to fully stop
        time.sleep(2)

        # Remove containers and networks
        subprocess.run(
            ["docker", "compose", *_env_file_args(), *_project_args(), "down"],
            cwd=str(compose_dir),
            check=True,  # nosec B603 B607
            env=_compose_env(),
        )

        # Additional wait to ensure cleanup is complete
        time.sleep(1)

        logger.info("Containers stopped and cleaned up successfully")
    except subprocess.CalledProcessError as e:
        logger.error(f"Error during container shutdown: {e}")
        # Force cleanup even if graceful stop failed
        subprocess.run(
            [
                "docker",
                "compose",
                *_env_file_args(),
                *_project_args(),
                "down",
                "--remove-orphans",
            ],
            cwd=str(compose_dir),
            check=False,  # nosec B603 B607
            env=_compose_env(),
        )
        raise


def get_container_status() -> bool:
    """Check if containers are running."""
    try:
        result = subprocess.run(
            ["docker", "compose", *_env_file_args(), *_project_args(), "ps", "-q"],
            cwd=str(compose_dir),
            capture_output=True,
            text=True,
            check=True,  # nosec B603 B607
            env=_compose_env(),
        )
        return bool(result.stdout.strip())
    except subprocess.CalledProcessError:
        return False
