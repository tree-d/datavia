import logging
import sys

import click

from .cli_config import create_config_file
from .cli_utils import (
    get_available_pipelines,
    get_datavia_instance,
    get_pipeline_dependencies,
    get_pipeline_status,
    install_pipeline_dependencies,
    start_datavia_environment,
    stop_datavia_environment,
    update_pipeline,
    validate_config_file,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@click.group()
def main():
    """Datavia - Modular pipeline architecture for geospatial data integration."""
    pass


# Infrastructure commands (unchanged)
@main.command()
def start():
    """Start the PostGIS database container."""
    if start_datavia_environment():
        logger.info("✅ Container startup completed successfully")
    else:
        logger.error("❌ Container startup failed")
        sys.exit(1)


@main.command()
def stop():
    """Stop the PostGIS database container."""
    if stop_datavia_environment():
        logger.info("✅ Container shutdown completed successfully")
    else:
        logger.error("❌ Container shutdown failed")
        sys.exit(1)


# Configuration commands
@main.group()
def config():
    """Manage Datavia configuration and selective installation."""
    pass


@config.command()
@click.option("--elevation", is_flag=True, help="Include elevation pipeline")
@click.option("--soil", is_flag=True, help="Include soil pipeline")
@click.option("--weather", is_flag=True, help="Include weather pipeline")
@click.option("--radiation", is_flag=True, help="Include radiation pipeline")
@click.option(
    "--config-file", default="datavia_config.py", help="Configuration file path"
)
def init(elevation, soil, weather, radiation, config_file):
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
def validate(config_file):
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
def status(config_file):
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

    # Check database container
    db_status = "Running ✅" if status_info["database_running"] else "Stopped ❌"
    logger.info(f"Database container: {db_status}")


# Dynamic pipeline update commands
@main.group()
def update():
    """Update data for installed pipelines."""
    pass


@update.command()
@click.option(
    "--config-file", default="datavia_config.py", help="Configuration file path"
)
def elevation(config_file):
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
def soil(config_file):
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
def weather(config_file):
    """Update weather data."""
    if update_pipeline("weather", config_file):
        logger.info("✅ Weather update completed")
    else:
        logger.error("❌ Weather update failed")
        sys.exit(1)


@update.command()
@click.option(
    "--config-file", default="datavia_config.py", help="Configuration file path"
)
def radiation(config_file):
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
def all(config_file):
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


# NEW: Install command for selective installation
@main.command()
@click.option(
    "--config-file", default="datavia_config.py", help="Configuration file path"
)
@click.option(
    "--dry-run", is_flag=True, help="Show what would be installed without installing"
)
def install(config_file, dry_run):
    """Install dependencies for pipelines specified in configuration."""
    pipeline_names = get_available_pipelines(config_file)
    if not pipeline_names:
        logger.error(f"Configuration file not found: {config_file}")
        logger.info("Run 'datavia config init' to create a configuration first.")
        return

    if dry_run:
        logger.info("=== DRY RUN - No actual installation ===")
        logger.info(f"Would install dependencies for: {', '.join(pipeline_names)}")
        for pipeline_name in pipeline_names:
            dependencies = get_pipeline_dependencies(pipeline_name)
            logger.info(f"  {pipeline_name}: {', '.join(dependencies)}")
        return

    logger.info("📋 Checking dependencies for your configured pipelines...")
    logger.info(f"Configured pipelines: {', '.join(pipeline_names)}\n")

    # Check dependencies for each pipeline
    needs_installation = []
    for pipeline_name in pipeline_names:
        if not install_pipeline_dependencies(pipeline_name):
            needs_installation.append(pipeline_name)
        logger.info("")  # Add spacing between pipelines

    if needs_installation:
        logger.info("🎯 SUMMARY:")
        logger.info(f"Dependencies needed for: {', '.join(needs_installation)}")
        logger.info("\n✨ Once dependencies are installed, you can use:")
        for pipeline_name in pipeline_names:
            logger.info(f"   datavia update {pipeline_name}")
    else:
        logger.info("🎉 All pipelines ready! You can now run update commands:")
        for pipeline_name in pipeline_names:
            logger.info(f"   datavia update {pipeline_name}")


if __name__ == "__main__":
    main()
