"""Docker container management for Datavia.

Provides utilities for starting, stopping, and checking the status of
the PostGIS database container used by Datavia for metadata storage.

The container is managed via docker-compose.yml located in the same
directory as this module.

Functions
---------
start_container : Start the PostGIS database container
stop_container : Stop and clean up the database container
get_container_status : Check if container is running
"""

import logging
import subprocess  # nosec B404 - subprocess required for docker-compose management
import time
from pathlib import Path

_POSTGRES_READY_TIMEOUT_S = 30
_POSTGRES_POLL_INTERVAL_S = 1

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
            ["docker", "compose", *_env_file_args(), "up", "-d"],
            cwd=str(compose_dir),
            check=True,  # nosec B603 B607
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
            ["docker", "compose", *_env_file_args(), "exec", "db", "pg_isready", "-U", "gis"],
            cwd=str(compose_dir),
            capture_output=True,
        )
        if result.returncode == 0:
            logger.info("PostgreSQL is ready.")
            return
        time.sleep(poll_interval)

    raise TimeoutError(
        f"PostgreSQL did not become ready within {timeout} seconds. "
        "Check container logs: docker compose logs db"
    )


def stop_container() -> None:
    """Stop the datavia container with proper cleanup."""
    try:
        # Stop containers gracefully with timeout
        subprocess.run(
            ["docker", "compose", *_env_file_args(), "stop", "-t", "10"],
            cwd=str(compose_dir),
            check=True,  # nosec B603 B607
        )

        # Wait for containers to fully stop
        time.sleep(2)

        # Remove containers and networks
        subprocess.run(
            ["docker", "compose", *_env_file_args(), "down"],
            cwd=str(compose_dir),
            check=True,  # nosec B603 B607
        )

        # Additional wait to ensure cleanup is complete
        time.sleep(1)

        logger.info("Containers stopped and cleaned up successfully")
    except subprocess.CalledProcessError as e:
        logger.error(f"Error during container shutdown: {e}")
        # Force cleanup even if graceful stop failed
        subprocess.run(
            ["docker", "compose", *_env_file_args(), "down", "--remove-orphans"],
            cwd=str(compose_dir),
            check=False,  # nosec B603 B607
        )
        raise


def get_container_status() -> bool:
    """Check if containers are running."""
    try:
        result = subprocess.run(
            ["docker", "compose", *_env_file_args(), "ps", "-q"],
            cwd=str(compose_dir),
            capture_output=True,
            text=True,
            check=True,  # nosec B603 B607
        )
        return bool(result.stdout.strip())
    except subprocess.CalledProcessError:
        return False
