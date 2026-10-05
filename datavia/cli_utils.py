#!/usr/bin/env python3
"""
CLI Utilities Module

Handles Datavia instance management, pipeline operations, and container management.
Separated from main CLI for better maintainability and reusability.
"""

import importlib.util
import logging
import os
from typing import Any

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
                "Expected: 'datavia = Datavia(pipelines=pipelines)()' "
                "or 'datavia_raw = Datavia(pipelines=pipelines)'"
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

        # Call the actual update_data method on the pipeline and propagate
        # its success/failure signal back to the caller.
        logger.info(f"Calling {pipeline_name}.update_data()...")
        result = pipeline.update_data()

        if result:
            logger.info(f"Successfully updated {pipeline_name}")
        else:
            logger.error(f"{pipeline_name}.update_data() reported a failure")
        return bool(result)

    except Exception as e:
        logger.error(f"Error updating {pipeline_name}: {e}")
        return False


def _is_weather_pipeline(pipeline: Any) -> bool:
    """Return True when *pipeline* is a WeatherPipeline instance.

    Uses a class-name check so that ``cli_utils`` does not need to import
    ``WeatherPipeline`` directly (which would create a hard dependency on the
    optional ``datavia-weather`` package).

    Parameters
    ----------
    pipeline : Any
        A pipeline instance from the Datavia registry.

    Returns
    -------
    bool
        ``True`` when the MRO of *pipeline* contains a class named
        ``"WeatherPipeline"``.
    """
    return any(cls.__name__ == "WeatherPipeline" for cls in type(pipeline).__mro__)


def update_weather_pipelines(
    source: str | None,
    config_file: str = "datavia_config.py",
) -> bool:
    """Update one or all WeatherPipeline instances registered in *config_file*.

    When *source* is provided, only the pipeline whose ``name`` matches
    *source* (e.g. ``"ERA5_land"``, ``"HYRAS"``, ``"DWD_stations"``) is
    updated.  When *source* is ``None`` every ``WeatherPipeline`` found in the
    configuration is updated and the function returns ``True`` only if all
    of them succeed.

    Parameters
    ----------
    source : str or None
        ``source_name`` of the weather pipeline to update, e.g.
        ``"ERA5_land"``.  Pass ``None`` to update all weather pipelines.
    config_file : str, optional
        Path to the Python configuration file.  Defaults to
        ``"datavia_config.py"``.

    Returns
    -------
    bool
        ``True`` if every targeted pipeline reported success; ``False`` if any
        failed or if no matching pipeline was found.
    """
    datavia_instance = get_datavia_instance(config_file)
    if not datavia_instance:
        logger.error(f"Could not load Datavia instance from {config_file}")
        return False

    weather_pipelines = [
        p for p in datavia_instance.pipelines if _is_weather_pipeline(p)
    ]

    if not weather_pipelines:
        logger.error(
            "No WeatherPipeline instances found in configuration. "
            "Add a WeatherPipeline to your datavia_config.py."
        )
        return False

    if source is not None:
        matched = [p for p in weather_pipelines if p.name == source]
        if not matched:
            available = [p.name for p in weather_pipelines]
            logger.error(
                f"No weather pipeline named '{source}' found. "
                f"Available weather sources: {available}"
            )
            return False
        targets = matched
    else:
        targets = weather_pipelines

    all_succeeded = True
    for pipeline in targets:
        logger.info(f"Updating weather pipeline '{pipeline.name}'...")
        try:
            result = pipeline.update_data()
            if result:
                logger.info(f"✅ '{pipeline.name}' updated successfully")
            else:
                logger.error(f"❌ '{pipeline.name}'.update_data() reported a failure")
                all_succeeded = False
        except Exception as exc:
            logger.error(f"❌ Error updating '{pipeline.name}': {exc}")
            all_succeeded = False

    return all_succeeded


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
                "Valid configuration with "
                f"{len(datavia_instance.pipelines)} pipeline(s)",
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
    """Get detailed status info for pipelines.

    Parameters
    ----------
    config_file : str, optional
        Path to the Python configuration file.  Defaults to
        ``"datavia_config.py"``.

    Returns
    -------
    dict[str, Any]
        A status dictionary with keys:

        * ``config_loaded`` — whether the config was loaded successfully.
        * ``pipelines`` — list of ``{name, status}`` dicts.
        * ``errors`` — list of error message strings.
    """
    status: dict[str, Any] = {
        "config_loaded": False,
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

    return status
