#!/usr/bin/env python
"""
MAIN CLI MODULE

This module defines the main command-line interface for Datavia using Click.
It includes commands for managing the Datavia environment, configuration,
and pipeline updates.
The CLI is designed to be user-friendly and provides clear feedback on operations.
"""

import logging
import sys

import click

from .cli_config import create_config_file
from .cli_utils import (
    get_available_pipelines,
    get_datavia_instance,
    get_pipeline_status,
    update_pipeline,
    update_weather_pipelines,
    validate_config_file,
)

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Configure logging for CLI execution."""
    logging.basicConfig(level=logging.INFO)


@click.group()
def main() -> None:
    """Datavia - Modular pipeline architecture for geospatial data integration."""
    _configure_logging()
    return None


# Configuration commands
@main.group()
def config() -> None:
    """Manage Datavia configuration and selective installation."""
    return None


@config.command()
@click.option("--elevation", is_flag=True, help="Include elevation pipeline")
@click.option("--soil", is_flag=True, help="Include soil pipeline")
@click.option("--weather", is_flag=True, help="Include weather pipeline")
@click.option("--radiation", is_flag=True, help="Include radiation pipeline")
@click.option(
    "--config-file", default="datavia_config.py", help="Configuration file path"
)
def init(
    elevation: bool, soil: bool, weather: bool, radiation: bool, config_file: str
) -> None:
    """Initialize Datavia configuration with selected pipelines."""
    selected_pipelines = []

    if elevation:
        selected_pipelines.append("elevation")
    if soil:
        selected_pipelines.append("soil")
    if weather:
        selected_pipelines.append("weather")
    if radiation:
        selected_pipelines.append("radiation")

    if not selected_pipelines:
        logger.error(
            "No pipelines selected. Use --elevation, --soil, --weather, or --radiation"
        )
        return

    create_config_file(selected_pipelines, config_file)
    logger.info(f"Configuration created: {config_file}")
    logger.info(f"Selected pipelines: {', '.join(selected_pipelines)}")


@config.command()
@click.option(
    "--config-file", default="datavia_config.py", help="Configuration file path"
)
def validate(config_file: str) -> None:
    """Validate Datavia configuration file."""
    is_valid, message = validate_config_file(config_file)

    if is_valid:
        logger.info("Configuration is valid ✅")
        logger.info(message)
        # Get detailed pipeline info
        datavia_instance = get_datavia_instance(config_file)
        if datavia_instance:
            for pipeline in datavia_instance.pipelines:
                logger.info(f"  - {pipeline.name}")
    else:
        logger.error(f"Configuration validation failed: {message}")


@config.command()
@click.option(
    "--config-file", default="datavia_config.py", help="Configuration file path"
)
def status(config_file: str) -> None:
    """Show current Datavia installation and configuration status."""
    logger.info("=== Datavia Installation Status ===")

    status_info = get_pipeline_status(config_file)

    if status_info["config_loaded"]:
        logger.info(f"Configuration loaded: {config_file}")
        logger.info(f"Active pipelines: {len(status_info['pipelines'])}")
        for pipeline_info in status_info["pipelines"]:
            logger.info(f"  - {pipeline_info['name']}: Ready ✅")
    else:
        for error in status_info["errors"]:
            logger.warning(error)
        return

    logger.info("Database: SQLite (auto-initialised) ✅")


# Dynamic pipeline update commands
@main.group()
def update() -> None:
    """Update data for installed pipelines."""
    return None


@update.command()
@click.option(
    "--config-file", default="datavia_config.py", help="Configuration file path"
)
def elevation(config_file: str) -> None:
    """Update elevation data."""
    if update_pipeline("elevation", config_file):
        logger.info("✅ Elevation update completed")
    else:
        logger.error("❌ Elevation update failed")
        sys.exit(1)


@update.command()
@click.option(
    "--config-file", default="datavia_config.py", help="Configuration file path"
)
def soil(config_file: str) -> None:
    """Update soil data."""
    if update_pipeline("soil", config_file):
        logger.info("✅ Soil update completed")
    else:
        logger.error("❌ Soil update failed")
        sys.exit(1)


@update.command()
@click.option(
    "--config-file", default="datavia_config.py", help="Configuration file path"
)
@click.option(
    "--source",
    default=None,
    help=(
        "Update only the weather pipeline with this source name "
        "(e.g. ERA5_land, HYRAS, DWD_stations). "
        "Omit to update all configured weather pipelines."
    ),
)
def weather(config_file: str, source: str | None) -> None:
    """Update weather data for one or all configured weather sources."""
    if update_weather_pipelines(source, config_file):
        logger.info("✅ Weather update completed")
    else:
        logger.error("❌ Weather update failed")
        sys.exit(1)


@update.command()
@click.option(
    "--config-file", default="datavia_config.py", help="Configuration file path"
)
def radiation(config_file: str) -> None:
    """Update radiation data."""
    if update_pipeline("radiation", config_file):
        logger.info("✅ Radiation update completed")
    else:
        logger.error("❌ Radiation update failed")
        sys.exit(1)


@update.command()
@click.option(
    "--config-file", default="datavia_config.py", help="Configuration file path"
)
def all(config_file: str) -> None:
    """Update all configured pipelines."""
    pipeline_names = get_available_pipelines(config_file)
    if not pipeline_names:
        logger.error(
            f"No configuration found at {config_file}. Run 'datavia config init' first."
        )
        return

    logger.info(f"Updating all configured pipelines: {', '.join(pipeline_names)}")

    success_count = 0
    for pipeline_name in pipeline_names:
        if update_pipeline(pipeline_name, config_file):
            success_count += 1

    logger.info(f"Update complete: {success_count}/{len(pipeline_names)} successful")


if __name__ == "__main__":
    main()
