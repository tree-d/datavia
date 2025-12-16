import click
import yaml
import os
from pathlib import Path
from .runner import start_container, stop_container, get_container_status
import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_config_if_exists(config_file="datavia_config.yaml"):
    """Load configuration file if it exists, return None otherwise."""
    if os.path.exists(config_file):
        with open(config_file, "r") as f:
            return yaml.safe_load(f)
    return None


@click.group()
def main():
    """Datavia - Modular pipeline architecture for geospatial data integration."""
    pass


# Infrastructure commands (unchanged)
@main.command()
def start():
    """Start the PostGIS database container."""
    _start()


@main.command()
def stop():
    """Stop the PostGIS database container."""
    _stop()


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
    "--config-file", default="datavia_config.yaml", help="Configuration file path"
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

    _create_config_file(selected_pipelines, config_file)
    logger.info(f"Configuration created: {config_file}")
    logger.info(f"Selected pipelines: {', '.join(selected_pipelines)}")


@config.command()
@click.option(
    "--config-file", default="datavia_config.yaml", help="Configuration file path"
)
def validate(config_file):
    """Validate Datavia configuration file."""
    if not os.path.exists(config_file):
        logger.error(f"Configuration file not found: {config_file}")
        return

    try:
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        # Validate required sections
        required_sections = ["pipelines", "shared_components"]
        for section in required_sections:
            if section not in config:
                logger.error(f"Missing required section: {section}")
                return

        # Validate pipeline configuration
        if "install" not in config["pipelines"]:
            logger.error("Missing 'install' in pipelines configuration")
            return

        logger.info("Configuration is valid ✅")
        logger.info(
            f"Pipelines to install: {', '.join(config['pipelines']['install'])}"
        )

    except yaml.YAMLError as e:
        logger.error(f"Invalid YAML syntax: {e}")
    except Exception as e:
        logger.error(f"Configuration validation failed: {e}")


@config.command()
@click.option(
    "--config-file", default="datavia_config.yaml", help="Configuration file path"
)
def status(config_file):
    """Show current Datavia installation and configuration status."""
    logger.info("=== Datavia Installation Status ===")

    # Check configuration
    if os.path.exists(config_file):
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)
        configured_pipelines = config.get("pipelines", {}).get("install", [])
        logger.info(f"Configured pipelines: {', '.join(configured_pipelines)}")
    else:
        logger.warning(f"No configuration file found: {config_file}")
        return

    # Check database container
    db_status = "Running ✅" if get_container_status() else "Stopped ❌"
    logger.info(f"Database container: {db_status}")

    # Check installed pipelines (simplified - will be enhanced in Phase B)
    logger.info("Pipeline status:")
    for pipeline in configured_pipelines:
        # For now, show based on configuration - will be runtime detection in Phase B
        logger.info(f"  {pipeline}: Configured ✅")


# Dynamic pipeline update commands
@main.group()
def update():
    """Update data for installed pipelines."""
    pass


@update.command()
def elevation():
    """Update elevation data."""
    _update_pipeline("elevation")


@update.command()
def soil():
    """Update soil data."""
    _update_pipeline("soil")


@update.command()
def weather():
    """Update weather data."""
    _update_pipeline("weather")


@update.command()
def radiation():
    """Update radiation data."""
    _update_pipeline("radiation")


@update.command()
def all():
    """Update all configured pipelines."""
    config = load_config_if_exists()
    if not config:
        logger.error("No configuration found. Run 'datavia config init' first.")
        return

    installed_pipelines = config.get("pipelines", {}).get("install", [])
    logger.info(f"Updating all configured pipelines: {', '.join(installed_pipelines)}")

    success_count = 0
    for pipeline_name in installed_pipelines:
        if _update_pipeline(pipeline_name):
            success_count += 1

    logger.info(
        f"Update complete: {success_count}/{len(installed_pipelines)} successful"
    )


def _update_pipeline(pipeline_name: str) -> bool:
    """Update data for a specific pipeline using new architecture."""
    logger.info(f"Updating {pipeline_name} data...")

    try:
        from .core.datavia import Datavia

        # Use new Datavia controller
        datavia = Datavia([])
        success = datavia.update_pipeline(pipeline_name)

        if success:
            logger.info(f"Successfully updated {pipeline_name}")
        else:
            logger.error(f"Failed to update {pipeline_name}")

        return success

    except ImportError as e:
        logger.error(f"Datavia core not available: {e}")
        return False
    except Exception as e:
        logger.error(f"Error updating {pipeline_name}: {e}")
        return False


# NEW: Install command for selective installation
@main.command()
@click.option(
    "--config-file", default="datavia_config.yaml", help="Configuration file path"
)
@click.option(
    "--dry-run", is_flag=True, help="Show what would be installed without installing"
)
def install(config_file, dry_run):
    """Install only the pipelines specified in configuration."""
    if not os.path.exists(config_file):
        logger.error(f"Configuration file not found: {config_file}")
        logger.info("Run 'datavia config init' to create a configuration first.")
        return

    with open(config_file, "r") as f:
        config = yaml.safe_load(f)

    pipelines_to_install = config.get("pipelines", {}).get("install", [])

    if dry_run:
        logger.info("=== DRY RUN - No actual installation ===")
        logger.info(f"Would install pipelines: {', '.join(pipelines_to_install)}")
        for pipeline in pipelines_to_install:
            dependencies = _get_pipeline_dependencies(pipeline)
            logger.info(f"  {pipeline}: {', '.join(dependencies)}")
        return

    logger.info(f"Installing pipelines: {', '.join(pipelines_to_install)}")

    # Generate selective imports
    _generate_selective_imports(pipelines_to_install)

    # Install pipeline-specific dependencies
    for pipeline in pipelines_to_install:
        _install_pipeline_dependencies(pipeline)

    logger.info("Installation complete!")
    logger.info("Available update commands:")
    for pipeline in pipelines_to_install:
        logger.info(f"  datavia update {pipeline}")


def _get_pipeline_dependencies(pipeline_name: str) -> list:
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


def _install_pipeline_dependencies(pipeline_name: str):
    """Install dependencies for a specific pipeline."""
    dependencies = _get_pipeline_dependencies(pipeline_name)
    if dependencies:
        logger.info(
            f"Installing dependencies for {pipeline_name}: {', '.join(dependencies)}"
        )
        # In Phase B, this would actually install the dependencies
        # For now, just log what would be installed
        logger.info(f"Dependencies for {pipeline_name} would be installed here")
    else:
        logger.info(f"No additional dependencies needed for {pipeline_name}")


def _generate_selective_imports(installed_pipelines: list):
    """Generate __init__.py with only installed pipeline imports."""
    init_file_path = Path(__file__).parent / "__init__.py"

    # Read existing __init__.py to preserve non-pipeline imports
    existing_content = []
    if init_file_path.exists():
        with open(init_file_path, "r") as f:
            lines = f.readlines()

        # Keep everything up to the pipeline imports section
        in_pipeline_section = False
        for line in lines:
            if "# Pipeline imports - auto-generated" in line:
                in_pipeline_section = True
                break
            if not in_pipeline_section:
                existing_content.append(line)

    # Generate new content with selective pipeline imports
    new_content = existing_content + [
        "\n# Pipeline imports - auto-generated based on installation\n"
    ]

    # Add imports only for installed pipelines
    pipeline_imports = []
    if "topography" in installed_pipelines:
        new_content.append("# Topography pipeline available\n")
        pipeline_imports.append("TopographyPipeline")

    if "soil" in installed_pipelines:
        new_content.append("# Soil pipeline available\n")
        pipeline_imports.append("SoilPipeline")

    if "weather" in installed_pipelines:
        new_content.append("# Weather pipeline available\n")
        pipeline_imports.append("WeatherPipeline")

    if "radiation" in installed_pipelines:
        new_content.append("# Radiation pipeline available\n")
        pipeline_imports.append("RadiationPipeline")

    # Update __all__ list
    if pipeline_imports:
        new_content.append("\n# Update __all__with installed pipelines\n")
        new_content.append(f"__all__.extend({pipeline_imports})\n")

    # Write updated __init__.py
    with open(init_file_path, "w") as f:
        f.writelines(new_content)

    logger.info(
        f"Updated __init__.py with imports for: {', '.join(installed_pipelines)}"
    )


# Helper functions
def _create_config_file(selected_pipelines: list, config_file: str):
    """Create configuration file with selected pipelines - updated for generic TIFF handling."""
    config = {
        "pipelines": {
            "install": selected_pipelines,
            "exclude": [
                p
                for p in ["elevation", "soil", "weather", "radiation"]
                if p not in selected_pipelines
            ],
        },
        "shared_components": {
            "database_url": "postgresql://datavia:datavia@localhost:5432/datavia",
            "data_directory": "/var/lib/datavia/data",
            "default_crs": "EPSG:25832",
            "processor_parallelism": 4,
        },
    }

    # Add pipeline-specific configuration with generic TIFF handling
    if "elevation" in selected_pipelines:
        config["elevation"] = {
            "source_name": "BKG_DGM200",
            "source": "https://sgx.geodatenzentrum.de/wcs_dgm200_inspire",  # <- THIS IS THE URL
            "params": {
                "VERSION": "2.0.1",
                "SERVICE": "WCS",
                "REQUEST": "GetCoverage",
                "COVERAGEID": "dgm200_inspire__EL.GridCoverage",
                "FORMAT": "image/tiff",
                "CRS": "EPSG:25832",
            },
            "timeout": 300,
        }

    if "soil" in selected_pipelines:
        config["soil"] = {
            "source_name": "SoilGrids",
            "properties": [
                "clay",
                "sand",
                "silt",
                "ph",
                "carbon",
            ],  # Selective approach
            "depths": ["0-5cm", "5-15cm"],  # Priority depths from masterplan
            "resolution": 250,
            "api_url": "https://rest.soilgrids.org/soilgrids/v2.0/properties/query",
        }

    if "weather" in selected_pipelines:
        config["weather"] = {
            "data_source": "DWD",
            "variables": ["temperature", "precipitation", "humidity"],
            "default_frequency": "daily",
            "interpolation_methods": ["linear", "nearest", "cubic"],
            "temporal_resolution": "hourly",
        }

    if "radiation" in selected_pipelines:
        config["radiation"] = {
            "data_source": "CAMS",
            "variables": [
                "global_irradiance",
                "direct_irradiance",
                "diffuse_irradiance",
            ],
        }

    # Write configuration file
    with open(config_file, "w") as f:
        yaml.dump(config, f, default_flow_style=False, indent=2)


def _start():
    """Build (if needed) and start the backend container."""
    # Check if already running
    if get_container_status():
        logger.warning("Containers are already running. Stopping first...")
        _stop()
        time.sleep(2)  # Wait before restart

    logger.info("Starting the container...")
    start_container()

    # Verify containers started successfully
    if get_container_status():
        logger.info("Container started successfully.")
    else:
        logger.error("Container failed to start properly.")
        return


def _stop():
    """Stop and remove the backend container."""
    if not get_container_status():
        logger.info("No containers are currently running.")
        return

    logger.info("Stopping the container...")
    try:
        stop_container()

        # Verify containers stopped
        if not get_container_status():
            logger.info("Container stopped successfully.")
        else:
            logger.warning("Container may not have stopped completely.")
    except Exception as e:
        logger.error(f"Error stopping container: {e}")


if __name__ == "__main__":
    main()
