#!/usr/bin/env python3
"""
CLI Utilities Module

Handles Datavia instance management, pipeline operations, and container management.
Separated from main CLI for better maintainability and reusability.
"""

import importlib.util
import logging
import os
import time
from typing import Any

from .runner import get_container_status, start_container, stop_container

logger = logging.getLogger(__name__)

# Module-level instance tracking without global statement usage
# Rationale: Implements singleton pattern for CLI commands to avoid
# repeatedly loading/initializing the Datavia instance between commands.
# This is standard practice for CLI tools where the same instance should
# be reused across multiple command invocations in a session.
# Thread-safety not required as CLI commands run sequentially.
_INSTANCE_STATE = {"datavia_instance": None}


def _get_cached_instance() -> Any | None:
    """Get the cached Datavia instance if available.

    Returns
    -------
    Any | None
        The cached Datavia instance or None if not yet initialized.
    """
    return _INSTANCE_STATE["datavia_instance"]


def _set_cached_instance(instance: Any | None) -> None:
    """Cache the Datavia instance for reuse across CLI commands.

    Parameters
    ----------
    instance : Any | None
        The Datavia instance to cache, or None to clear the cache.
    """
    _INSTANCE_STATE["datavia_instance"] = instance


def get_datavia_instance(config_file: str = "datavia_config.py") -> Any | None:
    """Get the Datavia instance from Python config, ensuring singleton behavior."""
    cached_instance = _get_cached_instance()
    if cached_instance is not None:
        logger.info("Using existing Datavia instance")
        return cached_instance

    if not os.path.exists(config_file):
        logger.error(f"Configuration file not found: {config_file}")
        logger.info("Run 'datavia config init' to create a configuration first.")
        return None

    try:
        # Load Python config dynamically
        spec = importlib.util.spec_from_file_location("datavia_config", config_file)
        if spec is None or spec.loader is None:
            logger.error(f"Could not create module spec for {config_file}")
            return None
        config_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config_module)

        # Try to get initialized instance first
        if hasattr(config_module, "datavia"):
            _set_cached_instance(config_module.datavia)
            logger.info("Loaded initialized Datavia instance from configuration")
            return _get_cached_instance()

        # Fallback: try to get raw instance and initialize it
        elif hasattr(config_module, "datavia_raw"):
            logger.info("Found raw Datavia instance, initializing...")
            _set_cached_instance(config_module.datavia_raw())
            logger.info("Successfully initialized raw Datavia instance")
            return _get_cached_instance()

        else:
            logger.error(
                "No 'datavia' or 'datavia_raw' instance found in configuration file"
            )
            logger.info(
                "Expected: 'datavia = Datavia(pipelines=pipelines)()' or 'datavia_raw = Datavia(pipelines=pipelines)'"
            )
            return None

    except Exception as e:
        logger.error(f"Error loading configuration: {e}")
        return None


def reset_datavia_instance() -> None:
    """Reset the global Datavia instance (useful for testing)."""
    _set_cached_instance(None)


def update_pipeline(pipeline_name: str, config_file: str = "datavia_config.py") -> bool:
    """Update data for a specific pipeline using actual pipeline instances."""
    logger.info(f"Updating {pipeline_name} data...")

    try:
        datavia_instance = get_datavia_instance(config_file)
        if not datavia_instance:
            logger.error(f"Could not load Datavia instance from {config_file}")
            return False

        # Find the specific pipeline by name
        pipeline = None
        for p in datavia_instance.pipelines:
            if p.name == pipeline_name:
                pipeline = p
                break

        if pipeline is None:
            logger.error(f"Pipeline '{pipeline_name}' not found in configuration")
            logger.info(
                f"Available pipelines: {[p.name for p in datavia_instance.pipelines]}"
            )
            return False

        # Call the actual update_data method on the pipeline
        logger.info(f"Calling {pipeline_name}.update_data()...")
        pipeline.update_data()

        logger.info(f"Successfully updated {pipeline_name}")
        return True

    except Exception as e:
        logger.error(f"Error updating {pipeline_name}: {e}")
        return False


def get_pipeline_dependencies(pipeline_name: str) -> list[str]:
    """Get dependencies for a specific pipeline."""
    dependencies_map = {
        "elevation": [
            "rasterio>=1.3.0",
            "gdal>=3.4.0",
            "requests>=2.28.0",
        ],  # Uses TiffDownloader
        "soil": [
            "soilgrids>=0.1.0",
            "rasterio>=1.3.0",
        ],  # Uses SoilGridsDownloader + TiffSaver only
        "weather": ["xarray>=2023.1.0", "netcdf4>=1.6.0"],
        "radiation": ["pvlib>=0.9.0", "pyproj>=3.3.0"],
    }
    return dependencies_map.get(pipeline_name, [])


def install_pipeline_dependencies(pipeline_name: str) -> bool:
    """Show installation instructions for pipeline dependencies."""
    dependencies = get_pipeline_dependencies(pipeline_name)
    if dependencies:
        logger.info(f"📦 Dependencies needed for {pipeline_name} pipeline:")
        for dep in dependencies:
            logger.info(f"  • {dep}")

        logger.info("\n💡 To install these dependencies, run:")
        logger.info(f"   pip install {' '.join(dependencies)}")
        logger.info("\n   Or install them individually:")
        for dep in dependencies:
            logger.info(f"   pip install {dep}")

        logger.info(
            f"\n🔄 After installation, run 'datavia update {pipeline_name}' to fetch data"
        )
        return False  # Return False to indicate manual installation needed
    else:
        logger.info(f"✅ No additional dependencies needed for {pipeline_name}")
        return True


def start_datavia_environment() -> bool:
    """Build (if needed) and start the backend container."""
    # Check if already running
    if get_container_status():
        logger.warning("Containers are already running. Stopping first...")
        if not stop_datavia_environment():
            return False
        time.sleep(2)  # Wait before restart

    logger.info("Starting the container...")
    start_container()

    # Verify containers started successfully
    if get_container_status():
        logger.info("Container started successfully.")
        return True
    else:
        logger.error("Container failed to start properly.")
        return False


def stop_datavia_environment() -> bool:
    """Stop and remove the backend container."""
    if not get_container_status():
        logger.info("No containers are currently running.")
        return True

    logger.info("Stopping the container...")
    try:
        stop_container()

        # Verify containers stopped
        if not get_container_status():
            logger.info("Container stopped successfully.")
            return True
        else:
            logger.warning("Container may not have stopped completely.")
            return False
    except Exception as e:
        logger.error(f"Error stopping container: {e}")
        return False


def validate_config_file(config_file: str) -> tuple[bool, str]:
    """Validate Python config file and return (is_valid, error_message)."""
    if not os.path.exists(config_file):
        return False, f"Configuration file not found: {config_file}"

    try:
        # Try to load and validate Python config
        datavia_instance = get_datavia_instance(config_file)
        if datavia_instance is not None:
            return (
                True,
                f"Valid configuration with {len(datavia_instance.pipelines)} pipeline(s)",
            )
        else:
            return False, "Configuration validation failed"
    except Exception as e:
        return False, f"Configuration validation failed: {e}"


def get_available_pipelines(config_file: str = "datavia_config.py") -> list[str]:
    """Get list of available pipeline names from config."""
    datavia_instance = get_datavia_instance(config_file)
    if datavia_instance:
        return [pipeline.name for pipeline in datavia_instance.pipelines]
    return []


def get_pipeline_status(config_file: str = "datavia_config.py") -> dict[str, Any]:
    """Get detailed status info for pipelines and infrastructure."""
    status: dict[str, Any] = {
        "config_loaded": False,
        "database_running": False,
        "pipelines": [],
        "errors": [],
    }

    # Check configuration
    if os.path.exists(config_file):
        datavia_instance = get_datavia_instance(config_file)
        if datavia_instance:
            status["config_loaded"] = True
            status["pipelines"] = [
                {"name": p.name, "status": "ready"} for p in datavia_instance.pipelines
            ]
        else:
            status["errors"].append("Configuration file exists but could not be loaded")
    else:
        status["errors"].append(f"No configuration file found: {config_file}")

    # Check database container
    status["database_running"] = get_container_status()

    return status
