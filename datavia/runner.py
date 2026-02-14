import logging
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)
compose_dir = Path(__file__).parent


def start_container() -> None:
    """Start the datavia container with proper error handling."""
    try:
        subprocess.run(
            ["docker", "compose", "up", "-d"], cwd=str(compose_dir), check=True
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
            ["docker", "compose", "stop", "-t", "10"], cwd=str(compose_dir), check=True
        )

        # Wait for containers to fully stop
        time.sleep(2)

        # Remove containers and networks
        subprocess.run(["docker", "compose", "down"], cwd=str(compose_dir), check=True)

        # Additional wait to ensure cleanup is complete
        time.sleep(1)

        logger.info("Containers stopped and cleaned up successfully")
    except subprocess.CalledProcessError as e:
        logger.error(f"Error during container shutdown: {e}")
        # Force cleanup even if graceful stop failed
        subprocess.run(
            ["docker", "compose", "down", "--remove-orphans"],
            cwd=str(compose_dir),
            check=False,
        )
        raise


def get_container_status() -> bool:
    """Check if containers are running."""
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "-q"],
            cwd=str(compose_dir),
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(result.stdout.strip())
    except subprocess.CalledProcessError:
        return False
