Basic Usage
===========

This guide covers the core functionality and usage patterns of Datavia namespace packages.

Namespace Package Architecture
===============================

Datavia uses namespace packages for modular installation:

.. doctest::

    >>> from datavia.core.datavia import Datavia
    >>> from datavia.elevation import ElevationPipeline
    >>> 
    >>> # Create and initialize pipeline
    >>> elevation_pipeline = ElevationPipeline()
    >>> dv = Datavia(pipelines=[elevation_pipeline])
    >>> 
    >>> # Initialize system (downloads data if needed)
    >>> _ = dv() # doctest: +SKIP


Available Pipelines
-------------------

**Elevation Pipeline** (Fully working)

.. doctest::

    >>> from datavia.elevation import ElevationPipeline
    >>> 
    >>> # German elevation data from BKG DGM200 (200m resolution)
    >>> elevation_pipeline = ElevationPipeline()

**Soil Pipeline**

.. doctest::

    >>> from datavia.soil import SoilPipeline
    >>> 
    >>> # Soil properties from SoilGrids and HiHydroSoil
    >>> soil_pipeline = SoilPipeline()

Coordinate Systems
------------------

All pipelines accept coordinates in **EPSG:4326** (longitude, latitude) format:

.. doctest::

    >>> import numpy as np
    >>> 
    >>> # Single coordinate pair: [longitude, latitude]
    >>> berlin = np.array([[13.4050, 52.5200]])
    >>> 
    >>> # Multiple points
    >>> german_cities = np.array([
    ...     [13.4050, 52.5200],  # Berlin
    ...     [11.5820, 48.1351],  # Munich  
    ...     [6.9603, 50.9375],   # Cologne
    ...     [10.0014, 53.5511],  # Hamburg
    ... ])
    >>> len(german_cities)
    4

**Important Notes:**
- Use **longitude first**, then latitude: ``[lon, lat]``
- Coordinates are automatically transformed to appropriate CRS internally
- Best results within German boundaries

Getting Soil Data
-----------------

The soil pipeline integrates **SoilGrids** (WCS API) and **HiHydroSoil** (HTTP GeoTIFF
catalogue) into a single interface. ``get_data()`` returns a
``dict[str, np.ndarray]`` keyed by **coverage ID** — which encodes the property,
depth layer, and statistic, e.g. ``"clay_0-5cm_mean"``.
For single-coverage requests it returns a plain ``np.ndarray`` instead.

SoilGrids values are integer-scaled (clay in g/kg, pH×10), so divide as needed.

You can request a subset of properties with the ``properties`` keyword:

.. doctest::

    >>> from datavia.soil import SoilPipeline
    >>> import numpy as np
    >>>
    >>> # Initialize pipeline (default properties: clay, sand, silt, ph, carbon,
    >>> # field_capacity, wilting_point, porosity, hydraulic_conductivity)
    >>> pipeline = SoilPipeline()
    >>> _ = pipeline()           # Initialize components # doctest: +SKIP
    >>> _ = pipeline.update_data()  # Download data if not yet available # doctest: +SKIP
    >>>
    >>> # Define coordinates
    >>> coords = np.array([[13.4050, 52.5200]])  # Berlin
    >>>
    >>> # Get soil data - returns dict[str, np.ndarray] keyed by coverage ID
    >>> soil_data = pipeline.get_data(coords=coords, crs_coords="EPSG:4326")  # doctest: +SKIP
    >>>
    >>> # Access SoilGrids properties — keys are coverage IDs, e.g. "clay_0-5cm_mean"
    >>> _ = print(f"Clay:  {soil_data['clay_0-5cm_mean'][0] / 10:.1f} %")   # doctest: +SKIP
    Clay:  29.4 %
    >>> _ = print(f"Sand:  {soil_data['sand_0-5cm_mean'][0] / 10:.1f} %")   # doctest: +SKIP
    Sand:  56.3 %
    >>> _ = print(f"Silt:  {soil_data['silt_0-5cm_mean'][0] / 10:.1f} %")   # doctest: +SKIP
    Silt:  14.4 %
    >>> _ = print(f"pH:    {soil_data['ph_0-5cm_mean'][0] / 10:.2f}")        # doctest: +SKIP
    pH:    5.35
    >>> _ = print(f"SOC:   {soil_data['carbon_0-5cm_mean'][0]:.1f} ‰")       # doctest: +SKIP
    SOC:   781.6 ‰
    >>>
    >>> # HiHydroSoil hydraulic properties are stored as integers ×10 000;
    >>> # multiply by 0.0001 to get physical units (cm³/cm³ or cm/day for Ksat).
    >>> _ = print(f"Field capacity: {soil_data['field_capacity_0-5cm_mean'][0] * 0.0001:.4f} cm³/cm³")  # doctest: +SKIP
    >>> _ = print(f"Wilting point:  {soil_data['wilting_point_0-5cm_mean'][0] * 0.0001:.4f} cm³/cm³")   # doctest: +SKIP
    >>> _ = print(f"Porosity:       {soil_data['porosity_0-5cm_mean'][0] * 0.0001:.4f} cm³/cm³")        # doctest: +SKIP
    >>>
    >>> # Single-property request — returns np.ndarray directly (not a dict)
    >>> soil_ph = pipeline.get_data(  # doctest: +SKIP
    ...     coords=coords,
    ...     properties=["ph"],
    ...     depths=["0-5cm"],
    ...     crs_coords="EPSG:4326",
    ... )
    >>> _ = print(f"pH only: {soil_ph[0] / 10:.2f}")  # doctest: +SKIP
    pH only: 5.35
    >>>
    >>> # Verify the return type without a live pipeline
    >>> isinstance({"clay_0-5cm_mean": np.array([294.0])}, dict)
    True

Getting Elevation Data
----------------------
The elevation pipeline provides German elevation data at 200m resolution:

.. code-block:: python

    from datavia.elevation import ElevationPipeline
    import numpy as np
    
    # Initialize pipeline
    pipeline = ElevationPipeline()
    pipeline()  # Initialize
    pipeline.update_data() # Ensure data is available, if necessary download it
    
    # Define coordinates
    coords = np.array([[13.4050, 52.5200]])  # Berlin

    # Get elevation data
    elevations = pipeline.get_data(coords=coords, crs_coords="EPSG:4326")
    
    # Results are in meters above sea level
    print(f"Elevation: {elevations[0]:.1f}m")

Batch Processing
----------------

Efficiently process multiple coordinates at once:

.. doctest::

    >>> from datavia.elevation import ElevationPipeline
    >>> import numpy as np
    >>> 
    >>> # Initialize pipeline
    >>> pipeline = ElevationPipeline()
    >>> 
    >>> # Process many points efficiently
    >>> coords = np.array([
    ...     [13.4050, 52.5200],  # Berlin
    ...     [11.5820, 48.1351],  # Munich
    ...     [6.9603, 50.9375],   # Cologne
    ...     [9.9937, 53.5511],   # Hamburg
    ...     [8.6821, 50.1109],   # Frankfurt
    ... ])
    >>> 
    >>> # Single API call for all points
    >>> _ = pipeline() # Initialize pipeline # doctest: +SKIP
    >>> _ = pipeline.update_data() # Ensure data is available, if necessary download it # doctest: +SKIP
    >>> elevations = pipeline.get_data(coords=coords, crs_coords="EPSG:4326") # doctest: +SKIP
    >>> 
    >>> # Process results - check we have 5 coordinates
    >>> len(coords)
    5
    >>> 
    >>> # Example of processing results
    >>> for i in range(len(coords)): # doctest: +SKIP +ELLIPSIS
    ...     print(f"Point {i+1}: {elevations[i]:.1f}m") # doctest: +SKIP +ELLIPSIS
    Point 1: 35.7m
    Point 2: 513.1m
    Point 3: 47.4m
    Point 4: 7.8m
    Point 5: 95.6m

Error Handling
--------------

Handle potential errors gracefully:

.. doctest::

    >>> import numpy as np
    >>> from datavia.elevation import ElevationPipeline
    >>> 
    >>> def safe_get_elevation(coords):  # doctest: +SKIP
    ...     """Safely get elevation data with error handling."""
    ...     try:
    ...         pipeline = ElevationPipeline()
    ...         _ = pipeline()  # Initialize
    ...         _ = pipeline.update_data()  # Ensure data is available
    ...         elevations = pipeline.get_data(coords=coords, crs_coords="EPSG:4326")
    ...         return elevations
    ...     except Exception as e:
    ...         print(f"Error getting elevation data: {e}")
    ...         return None
    >>> 
    >>> # Example usage
    >>> coords = np.array([[13.4050, 52.5200]])
    >>> elevations = safe_get_elevation(coords) # doctest: +SKIP
    >>> 
    >>> # Test the function exists
    >>> callable(safe_get_elevation) # doctest: +SKIP
    True

Data Validation
---------------

Validate your coordinate data:

.. doctest::

    >>> def validate_coordinates(coords):
    ...     """Validate coordinate array for German bounds."""
    ...     if coords.shape[1] != 2:
    ...         raise ValueError("Coordinates must have shape (N, 2)")
    ...     
    ...     # Rough bounds for Germany
    ...     lat_min, lat_max = 47.0, 55.5
    ...     lon_min, lon_max = 5.5, 15.5
    ...     
    ...     lons, lats = coords[:, 0], coords[:, 1]
    ...     
    ...     if not np.all((lat_min <= lats) & (lats <= lat_max)):
    ...         print("Warning: Some latitudes outside German bounds")
    ...     
    ...     if not np.all((lon_min <= lons) & (lons <= lon_max)):
    ...         print("Warning: Some longitudes outside German bounds")
    ...     
    ...     return True
    >>> 
    >>> # Example
    >>> coords = np.array([[13.4050, 52.5200]])
    >>> validate_coordinates(coords)
    True

Performance Tips
----------------

For best performance:

1. **Batch coordinates**: Process multiple points in a single call rather than individual calls
2. **Validate inputs**: Check coordinate bounds before processing
3. **Handle errors**: Use try-catch blocks for robust applications
4. **Cache results**: Store elevation data if you'll need it again

.. doctest::

    >>> # Example: Batch processing is more efficient
    >>> many_coords = [[1, 2], [3, 4], [5, 6]]
    >>> len(many_coords) > 1  # Batch is better than individual
    True
    >>> # This approach is preferred over individual coordinate calls

Working with Results
--------------------

The results are returned as numpy arrays:

.. doctest::

    >>> # Mock elevation data for testing
    >>> elevations = np.array([35.5, 112.0, 47.9, 5.8])  # Example elevations
    >>> coords = np.array([[13.4050, 52.5200], [11.5820, 48.1351], [6.9603, 50.9375], [9.9937, 53.5511]])
    >>> 
    >>> # Basic statistics
    >>> f"Min elevation: {np.min(elevations):.1f}m"
    'Min elevation: 5.8m'
    >>> f"Max elevation: {np.max(elevations):.1f}m"
    'Max elevation: 112.0m'
    >>> f"Mean elevation: {np.mean(elevations):.1f}m"
    'Mean elevation: 50.3m'
    >>> 
    >>> # Find highest point
    >>> highest_idx = np.argmax(elevations)
    >>> f"Highest point: {elevations[highest_idx]:.1f}m"
    'Highest point: 112.0m'

Integration with Other Libraries
--------------------------------

Datavia works well with other geospatial libraries:

.. doctest::

    >>> import pandas as pd # doctest: +SKIP
    >>> import matplotlib.pyplot as plt # doctest: +SKIP
    >>> 
    >>> # Create a DataFrame with coordinates and elevations
    >>> df = pd.DataFrame({ # doctest: +SKIP
    ...     'lon': coords[:, 0],
    ...     'lat': coords[:, 1],
    ...     'elevation': elevations
    ... })
    >>> 
    >>> # Simple elevation plot
    >>> _ = plt.scatter(df['lon'], df['lat'], c=df['elevation'], cmap='terrain') # doctest: +SKIP
    >>> _ = plt.colorbar(label='Elevation (m)') # doctest: +SKIP
    >>> _ = plt.xlabel('Longitude') # doctest: +SKIP
    >>> _ = plt.ylabel('Latitude') # doctest: +SKIP
    >>> _ = plt.title('Elevation Map') # doctest: +SKIP
    >>> _ = plt.show() # doctest: +SKIP
    >>> 
    >>> # Test pandas is importable
    >>> import pandas
    >>> hasattr(pandas, 'DataFrame')
    True

Spatial Grid Visualization
---------------------------

Create and visualize elevation data over a geographic grid:

.. doctest::

    >>> import numpy as np
    >>> from datavia import Datavia
    >>> from datavia.elevation import ElevationPipeline
    >>> from matplotlib import pyplot as plt
    >>>
    >>> # Initialize system with elevation pipeline
    >>> dv = Datavia(pipelines=[ElevationPipeline()])
    >>> _ = dv() # doctest: +SKIP
    >>> _ = dv.elevation.update_data() # doctest: +SKIP
    >>>
    >>> # Define bounding box coordinates (Leipzig region, Germany)
    >>> # Format: [latitude, longitude]
    >>> top_left = [51.42181290311133, 12.241528509584548]
    >>> bottom_right = [51.26145352651316, 12.52475323962364]
    >>>
    >>> # Create regular grid of coordinates across the region
    >>> X, Y = np.meshgrid(
    ...     np.linspace(top_left[1], bottom_right[1], 500),  # Longitude
    ...     np.linspace(bottom_right[0], top_left[0], 500),  # Latitude
    ... )
    >>>
    >>> # Flatten grid into coordinate pairs [lon, lat]
    >>> coords = np.c_[X.ravel(), Y.ravel()]
    >>>
    >>> # Fetch elevation data for all grid points in a single batch call
    >>> elevation_data = dv.pipelines[0].get_data(coords, crs_coords="EPSG:4326") # doctest: +SKIP
    >>>
    >>> # Reshape elevation data back to grid for visualization
    >>> elevation_grid = elevation_data.reshape(X.shape) # doctest: +SKIP
    >>>
    >>> # Create heatmap visualization
    >>> _ = plt.figure(figsize=(12, 8)) # doctest: +SKIP
    >>> _ = plt.pcolormesh(X, Y, elevation_grid, cmap='terrain', shading='auto') # doctest: +SKIP
    >>> _ = plt.colorbar(label='Elevation (m)') # doctest: +SKIP
    >>> _ = plt.xlabel('Longitude') # doctest: +SKIP
    >>> _ = plt.ylabel('Latitude') # doctest: +SKIP
    >>> _ = plt.title('Elevation Map - Leipzig Region') # doctest: +SKIP
    >>> _ = plt.tight_layout() # doctest: +SKIP
    >>> _ = plt.show() # doctest: +SKIP

This approach demonstrates:

* **Grid creation**: Using `np.meshgrid()` to generate regular coordinate grids
* **Batch processing**: Fetching elevation for 500×500=250,000 points efficiently
* **Data reshaping**: Converting flat arrays back to 2D grids for visualization
* **Heatmap visualization**: Using `pcolormesh()` for continuous spatial data

Next Steps
----------

* Explore :doc:`examples` for more complex use cases
* Read the API documentation for complete interface references

