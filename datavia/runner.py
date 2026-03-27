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
    """Start the datavia container with proper error handling."""
    try:
        subprocess.run(
            ["docker", "compose", *_env_file_args(), "up", "-d"],
            cwd=str(compose_dir),
            check=True,  # nosec B603 B607
        )
        # Wait a moment for containers to fully initialize
        time.sleep(2)
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to start containers: {e}")
        raise


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
