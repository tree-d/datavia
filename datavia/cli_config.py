#!/usr/bin/env python3
"""
CLI Configuration Generation Module

Handles generation of Python configuration files for Datavia pipelines.
Separated from main CLI for better maintainability.
"""

import logging

from .config import get_config

logger = logging.getLogger(__name__)

ELEVATION_EXAMPLE = """
    # Example 1: Get elevation data
    try:
        elevations = datavia.elevation.get_data(coords=berlin_coords, crs_coords="EPSG:4326")
        print(f"Berlin elevation: {elevations[0]:.1f}m")

        elevations = datavia.elevation.get_data(coords=munich_coords, crs_coords="EPSG:4326")
        print(f"Munich elevation: {elevations[0]:.1f}m")
    except Exception as e:
        print(f"Elevation example failed: {e}")
        print("Note: Make sure to run 'datavia update elevation' first!")"""

SOIL_EXAMPLE = """
    # Example 2: Get soil data
    # Note: SoilPipeline.get_data() returns dict[str, np.ndarray] - one array per property.
    try:
        soil_data = datavia.soil.get_data(coords=berlin_coords, crs_coords="EPSG:4326")
        print("Berlin soil properties:")
        for prop, values in soil_data.items():
            print(f"  {prop}: {values[0]:.2f}")

        soil_data = datavia.soil.get_data(coords=munich_coords, crs_coords="EPSG:4326")
        print("Munich soil properties:")
        for prop, values in soil_data.items():
            print(f"  {prop}: {values[0]:.2f}")
    except Exception as e:
        print(f"Soil example failed: {e}")
        print("Note: Make sure to run 'datavia update soil' first!")"""

WEATHER_EXAMPLE = """
    # Example 3: Get weather data
    try:
        weather_data = datavia.weather.get_data(coords=berlin_coords, crs_coords="EPSG:4326")
        print(f"Berlin weather: {weather_data}")
    except Exception as e:
        print(f"Weather example failed: {e}")
        print("Note: Make sure to run 'datavia update weather' first!")"""

RADIATION_EXAMPLE = """
    # Example 4: Get radiation data
    try:
        radiation_data = datavia.radiation.get_data(coords=berlin_coords, crs_coords="EPSG:4326")
        print(f"Berlin solar radiation: {radiation_data}")
    except Exception as e:
        print(f"Radiation example failed: {e}")
        print("Note: Make sure to run 'datavia update radiation' first!")"""

GENERAL_EXAMPLE = """
    # Example: General approach for any pipeline
    for pipeline in datavia.pipelines:
        try:
            print(f"Testing {pipeline.name} pipeline...")
            data = pipeline.get_data(coords=berlin_coords, crs_coords="EPSG:4326")
            # SoilPipeline returns dict[str, np.ndarray]; scalar pipelines return np.ndarray
            if isinstance(data, dict):
                for prop, values in data.items():
                    print(f"  {pipeline.name}.{prop}: {values[0]:.2f}")
            else:
                print(f"  {pipeline.name}: {data[0]:.2f}")
        except Exception as e:
            print(f"{pipeline.name} failed: {e}")"""


def _generate_usage_examples(selected_pipelines: list[str]) -> str:
    """Generate usage examples based on selected pipelines."""
    examples = []

    if "elevation" in selected_pipelines:
        examples.append(ELEVATION_EXAMPLE)

    if "soil" in selected_pipelines:
        examples.append(SOIL_EXAMPLE)

    if "weather" in selected_pipelines:
        examples.append(WEATHER_EXAMPLE)

    if "radiation" in selected_pipelines:
        examples.append(RADIATION_EXAMPLE)

    # Add a general example for any pipeline
    examples.append(GENERAL_EXAMPLE)

    return "\n".join(examples)


def create_config_file(selected_pipelines: list[str], config_file: str) -> None:
    """Create Python configuration file with selected pipelines."""

    # Generate imports based on selected pipelines
    imports = ["from datavia import Datavia", "from datavia.config import get_config"]
    pipeline_instances = []

    if "elevation" in selected_pipelines:
        imports.append("from datavia.elevation import ElevationPipeline")
        pipeline_instances.append(
            """elevation = ElevationPipeline(
    url="https://sgx.geodatenzentrum.de/wcs_dgm200_inspire?VERSION=2.0.1&SERVICE=WCS&REQUEST=GetCoverage&COVERAGEID=dgm200_inspire__EL.GridCoverage&format=image/tiff&crs=EPSG:25832&bbox=280000,5235000,921000,6101000"
)"""
        )

    if "soil" in selected_pipelines:
        imports.append("from datavia.soil import SoilPipeline")
        pipeline_instances.append(
            """soil = SoilPipeline(
    properties=["clay", "sand", "silt", "ph", "carbon"]
)"""
        )

    if "weather" in selected_pipelines:
        imports.append("from datavia.weather import WeatherPipeline")
        pipeline_instances.append(
            """weather = WeatherPipeline(
    data_source="DWD",
    variables=["temperature", "precipitation", "humidity"]
)"""
        )

    if "radiation" in selected_pipelines:
        imports.append("from datavia.radiation import RadiationPipeline")
        pipeline_instances.append(
            """radiation = RadiationPipeline(
    data_source="CAMS",
    variables=["global_irradiance", "direct_irradiance", "diffuse_irradiance"]
)"""
        )

    # Generate the configuration file content
    config_content = f'''#!/usr/bin/env python3
"""
Datavia Configuration File

This file defines your Datavia instance with selected pipelines.
You can customize pipeline parameters as needed.

Generated pipelines: {", ".join(selected_pipelines)}
"""

{chr(10).join(imports)}

# Initialize pipelines with custom parameters
{chr(10).join(pipeline_instances)}

# Create the Datavia instance with all configured pipelines
pipelines = []
'''

    # Add pipeline variables to the list
    for pipeline in selected_pipelines:
        config_content += f"pipelines.append({pipeline})\n"

    config_content += f'''\n# Choose your initialization approach:

# Option 1: Pre-initialized (recommended for most users)
# Get uninitialized instance first
datavia_raw = Datavia(pipelines=pipelines)

# Then initialize it - this happens on import and initializes the database
datavia = datavia_raw()

# Option 2: Manual initialization (avoid import overhead)
# Comment the line above and use this approach instead:
# 1. Only keep: datavia_raw = Datavia(pipelines=pipelines)
# 2. In your scripts: datavia = datavia_raw() when you're ready to initialize

# Note: The CLI automatically detects which approach you're using!

# Database connection URL for this project.
# Port is derived from the project directory; re-run `datavia init` if you move
# the project to a different directory.
DATABASE_URL = get_config().database_url
DATA_DIRECTORY = "/var/lib/datavia/data"
DEFAULT_CRS = "EPSG:25832"

# =============================================================================
# USAGE EXAMPLE - Copy this to your own script
# =============================================================================

if __name__ == "__main__":
    # Example usage - copy this to your own Python script
    import numpy as np

    print("=== Datavia Usage Example ===")
    print(f"Loaded pipelines: {{[p.name for p in datavia.pipelines]}}")

    # Define some coordinates (longitude, latitude)
    berlin_coords = np.array([[13.4050, 52.5200]])  # Berlin
    munich_coords = np.array([[11.5820, 48.1351]])  # Munich

    {_generate_usage_examples(selected_pipelines)}

    print("\\n=== Example completed successfully! ===")
    print("Copy the example code above to your own script to use Datavia.")

# =============================================================================
# STANDALONE SCRIPT TEMPLATE - Copy this entire block to a new .py file
# =============================================================================

"""
#!/usr/bin/env python3
# Save this as: my_datavia_script.py

# Import the configured datavia instance
from datavia_config import datavia
import numpy as np

def main() -> None:
    print("=== My Datavia Script ===")

    # Define coordinates for analysis
    locations = {{
        "Berlin": [13.4050, 52.5200],
        "Munich": [11.5820, 48.1351],
        "Hamburg": [9.9937, 53.5511]
    }}

    for city, coords in locations.items():
        print(f"\\n--- {{city}} ---")
        coord_array = np.array([coords])

        # Use each available pipeline
        for pipeline in datavia.pipelines:
            try:
                data = pipeline.get_data(coords=coord_array, crs_coords="EPSG:4326")
                # SoilPipeline returns dict[str, np.ndarray]; others return np.ndarray
                if isinstance(data, dict):
                    summary = {{k: float(v[0]) for k, v in data.items()}}
                    print(f"{{pipeline.name}}: {{summary}}")
                else:
                    print(f"{{pipeline.name}}: {{data[0] if len(data) > 0 else 'No data'}}")
            except Exception as e:
                print(f"{{pipeline.name}}: Error - {{e}}")

    print("\\n=== Script completed! ===")

if __name__ == "__main__":
    main()
"""
'''

    # Write the Python configuration file
    with open(config_file, "w") as f:
        f.write(config_content)

    logger.info(f"Generated Python configuration: {config_file}")
    logger.info(
        "You can now customize pipeline parameters by editing this file directly."
    )
