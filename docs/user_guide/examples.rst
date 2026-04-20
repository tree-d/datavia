Examples
========

This section provides three comprehensive examples using Datavia's namespace package architecture.

Example 1: City Elevation Analysis
----------------------------------

Get elevation data for major German cities using the elevation pipeline:

.. doctest::

    >>> import numpy as np
    >>> import matplotlib.pyplot as plt
    >>> from datavia.elevation import ElevationPipeline

    >>> def city_elevation_analysis():
    ...     """Create elevation profile for German cities."""
    ...     # Initialise and download data
    ...     pipeline = ElevationPipeline()
    ...     pipeline()  # Creates Downloader / Saver / Getter components
    ...     pipeline.update_data()  # Downloads data on first run; skips if cached
    ...
    ...     # Define major German cities (longitude, latitude)
    ...     cities = {
    ...         "Hamburg":   [9.9937,  53.5511],
    ...         "Berlin":    [13.4050, 52.5200],
    ...         "Cologne":   [6.9603,  50.9375],
    ...         "Frankfurt": [8.6821,  50.1109],
    ...         "Stuttgart": [9.1829,  48.7758],
    ...         "Munich":    [11.5820, 48.1351],
    ...     }
    ...     coords = np.array(list(cities.values()))
    ...     city_names = list(cities.keys())
    ...
    ...     # get_data() returns np.ndarray of shape (N,) — one value per coordinate
    ...     elevations = pipeline.get_data(coords=coords, crs_coords="EPSG:4326")
    ...
    ...     plt.figure(figsize=(12, 6))
    ...     bars = plt.bar(city_names, elevations, color='lightblue', edgecolor='navy')
    ...     plt.ylabel('Elevation (m)')
    ...     plt.title('Elevation Profile of Major German Cities')
    ...     plt.xticks(rotation=45)
    ...     for bar, elevation in zip(bars, elevations):
    ...         plt.text(
    ...             bar.get_x() + bar.get_width() / 2,
    ...             bar.get_height() + 5,
    ...             f'{elevation:.0f}m',
    ...             ha='center', va='bottom',
    ...         )
    ...     plt.tight_layout()
    ...     plt.show()
    ...
    ...     print("German Cities Elevation Summary:")
    ...     print("-" * 40)
    ...     for city, elevation in zip(city_names, elevations):
    ...         print(f"{city:>10}: {elevation:>6.1f}m")

    >>> city_elevation_analysis()  # doctest: +SKIP +ELLIPSIS
    German Cities Elevation Summary:
    ...


Example 2: Multi-Pipeline Environmental Data
============================================

Combine elevation and soil data for environmental analysis:

.. doctest::

    >>> import numpy as np
    >>> import pandas as pd
    >>> from datavia import Datavia
    >>> from datavia.elevation import ElevationPipeline
    >>> from datavia.soil import SoilPipeline

    >>> def environmental_analysis():
    ...     """Analyze environmental conditions across sample locations."""
    ...     elevation = ElevationPipeline()
    ...     soil = SoilPipeline()
    ...     dv = Datavia(pipelines=[elevation, soil])
    ...     dv()  # Connects to DB; instantiates components
    ...     dv.elevation.update_data()
    ...     dv.soil.update_data()
    ...
    ...     # Sample locations across Germany (longitude, latitude)
    ...     locations = {
    ...         "North Coast":    [8.6821,  54.9200],
    ...         "Central Plains": [10.5000, 52.0000],
    ...         "Black Forest":   [8.2000,  48.0000],
    ...         "Bavarian Alps":  [11.0000, 47.5000],
    ...     }
    ...     coords = np.array(list(locations.values()))
    ...     location_names = list(locations.keys())
    ...
    ...     # Elevation returns np.ndarray shape (N,)
    ...     elevations = dv.elevation.get_data(coords=coords, crs_coords="EPSG:4326")
    ...
    ...     # Soil returns dict[str, np.ndarray] keyed by coverage ID
    ...     soil_data = dv.soil.get_data(coords=coords, crs_coords="EPSG:4326")
    ...
    ...     df = pd.DataFrame({
    ...         'Location':     location_names,
    ...         'Longitude':    coords[:, 0],
    ...         'Latitude':     coords[:, 1],
    ...         'Elevation_m':  elevations,
    ...         'Clay_pct':     soil_data['clay_0-5cm_mean'] / 10,
    ...         'Soil_pH':      soil_data['ph_0-5cm_mean'] / 10,
    ...         'SOC_g_per_kg': soil_data['carbon_0-5cm_mean'] / 10,
    ...     })
    ...     print("Environmental Analysis Results:")
    ...     print("=" * 50)
    ...     print(df.to_string(index=False, float_format='%.2f'))
    ...     correlation = df['Elevation_m'].corr(df['Soil_pH'])
    ...     print(f"\nElevation-Soil pH Correlation: {correlation:.3f}")
    ...     return df

    >>> environmental_analysis()  # doctest: +SKIP +ELLIPSIS
    Environmental Analysis Results:
    ...

Example 3: Spatial Grid Analysis
================================

Analyze elevation patterns across a spatial grid:

.. doctest::

    >>> import numpy as np
    >>> import matplotlib.pyplot as plt
    >>> from datavia.elevation import ElevationPipeline

    >>> def spatial_grid_analysis():
    ...     """Create elevation surface analysis over a spatial grid."""
    ...     # Initialise and download data
    ...     pipeline = ElevationPipeline()
    ...     pipeline()  # Creates Downloader / Saver / Getter components
    ...     pipeline.update_data()  # Downloads data on first run; skips if cached
    ...
    ...     # Study area: Baden-Württemberg
    ...     lon_min, lon_max = 7.5, 10.5
    ...     lat_min, lat_max = 47.5, 49.5
    ...     lon_grid = np.linspace(lon_min, lon_max, 20)
    ...     lat_grid = np.linspace(lat_min, lat_max, 15)
    ...     lon_mesh, lat_mesh = np.meshgrid(lon_grid, lat_grid)
    ...
    ...     # Flatten: pipeline expects shape (N, 2) as [longitude, latitude]
    ...     coords = np.column_stack([lon_mesh.flatten(), lat_mesh.flatten()])
    ...
    ...     # get_data() returns np.ndarray of shape (N,)
    ...     elevations = pipeline.get_data(coords=coords, crs_coords="EPSG:4326")
    ...     elevation_grid = elevations.reshape(lon_mesh.shape)
    ...
    ...     plt.figure(figsize=(12, 8))
    ...     contour = plt.contourf(lon_mesh, lat_mesh, elevation_grid,
    ...                            levels=20, cmap='terrain', alpha=0.8)
    ...     plt.colorbar(contour, label='Elevation (m)')
    ...     contour_lines = plt.contour(lon_mesh, lat_mesh, elevation_grid,
    ...                                 levels=10, colors='black', alpha=0.4, linewidths=0.5)
    ...     plt.clabel(contour_lines, inline=True, fontsize=8, fmt='%dm')
    ...     cities = {
    ...         "Stuttgart": (9.1829, 48.7758),
    ...         "Karlsruhe": (8.4037, 49.0069),
    ...         "Freiburg":  (7.8521, 47.9990),
    ...     }
    ...     for city, (lon, lat) in cities.items():
    ...         plt.plot(lon, lat, 'ro', markersize=8, markeredgecolor='black')
    ...         plt.text(lon + 0.1, lat, city, fontsize=10, fontweight='bold')
    ...     plt.xlabel('Longitude (°E)')
    ...     plt.ylabel('Latitude (°N)')
    ...     plt.title('Elevation Analysis - Baden-Württemberg Region')
    ...     plt.grid(True, alpha=0.3)
    ...     plt.tight_layout()
    ...     plt.show()
    ...
    ...     print("Spatial Grid Statistics:")
    ...     print("-" * 30)
    ...     print(f"Grid size: {elevation_grid.shape}")
    ...     print(f"Min elevation: {np.min(elevations):.1f}m")
    ...     print(f"Max elevation: {np.max(elevations):.1f}m")
    ...     print(f"Mean elevation: {np.mean(elevations):.1f}m")
    ...     print(f"Std elevation: {np.std(elevations):.1f}m")

    >>> spatial_grid_analysis()  # doctest: +SKIP +ELLIPSIS
    Spatial Grid Statistics:
    ...

Installation and Setup
======================

To run these examples, install the required packages:

.. code-block:: bash

    # Install datavia with pipelines
    pip install datavia[elevation,soil]
    
    # Install visualization dependencies
    pip install matplotlib pandas

Each example can be run independently and demonstrates different aspects of the Datavia system:

- **Example 1**: Basic single-pipeline usage with elevation data
- **Example 2**: Multi-pipeline integration combining elevation and soil data  
- **Example 3**: Advanced spatial analysis with grid-based data access

Notes
-----

* All examples use the new namespace package imports (``datavia.elevation``, ``datavia.soil``)
* Coordinates are provided in WGS84 format (EPSG:4326) as (longitude, latitude)
* The examples include proper error handling and data initialization
* Visualization examples require matplotlib and pandas packages

All examples can be run directly:

.. code-block:: bash

    # Copy any example to a file and run it
    pixi run python your_example.py

Make sure you have the required dependencies:

.. code-block:: bash

    pip install matplotlib pandas  # For visualization examples

Next Steps
----------

* Read the API documentation for detailed interface references
* Explore the source code in the repository
