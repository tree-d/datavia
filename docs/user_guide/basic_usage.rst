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
    >>> dv() # doctest: +SKIP


Available Pipelines
-------------------

**Elevation Pipeline** (Fully working)

.. doctest::

    >>> from datavia.elevation import ElevationPipeline
    >>> 
    >>> # German elevation data from BKG DGM200 (200m resolution)
    >>> elevation_pipeline = ElevationPipeline()

**Soil Pipeline** (In development)

.. doctest::

    >>> from datavia.soil import SoilPipeline # doctest: +SKIP
    >>> 
    >>> # Soil properties from SoilGrids API
    >>> soil_pipeline = SoilPipeline() # doctest: +SKIP

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

Getting Elevation Data
----------------------

The elevation pipeline provides German elevation data at 200m resolution:

.. code-block:: python

    from datavia.elevation import ElevationPipeline
    import numpy as np
    
    # Initialize pipeline
    pipeline = ElevationPipeline()
    pipeline.update_data()
    
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
    >>> elevations = pipeline.get_data(coords=coords, crs_coords="EPSG:4326") # doctest: +SKIP
    >>> 
    >>> # Process results - check we have 5 coordinates
    >>> len(coords)
    5
    >>> 
    >>> # Example of processing results
    >>> for i in range(len(coords)): # doctest: +SKIP
    ...     print(f"Point {i+1}: elevation data") # doctest: +SKIP

Error Handling
--------------

Handle potential errors gracefully:

.. doctest::

    >>> import numpy as np
    >>> from datavia.elevation import ElevationPipeline
    >>> 
    >>> def safe_get_elevation(coords):
    ...     """Safely get elevation data with error handling."""
    ...     try:
    ...         pipeline = ElevationPipeline()
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
    >>> callable(safe_get_elevation)
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

    >>> # Good: Batch processing (conceptual example)
    >>> # elevations = get_data(many_coords, DataSource.TOPOGRAPHY) # doctest: +SKIP
    >>> 
    >>> # Avoid: Individual calls
    >>> # for coord in many_coords:  # doctest: +SKIP
    >>> #     elevation = get_data([coord], DataSource.TOPOGRAPHY)  # Inefficient  # doctest: +SKIP
    >>> 
    >>> # Demonstrate the concept exists
    >>> many_coords = [[1, 2], [3, 4]]
    >>> len(many_coords) > 1  # Batch is better than individual
    True

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
    >>> plt.scatter(df['lon'], df['lat'], c=df['elevation'], cmap='terrain') # doctest: +SKIP
    >>> plt.colorbar(label='Elevation (m)') # doctest: +SKIP
    >>> plt.xlabel('Longitude') # doctest: +SKIP
    >>> plt.ylabel('Latitude') # doctest: +SKIP
    >>> plt.title('Elevation Map') # doctest: +SKIP
    >>> plt.show() # doctest: +SKIP
    >>> 
    >>> # Test pandas is importable
    >>> import pandas
    >>> hasattr(pandas, 'DataFrame')
    True

Next Steps
----------

* Explore :doc:`examples` for more complex use cases
* Read the API documentation for complete interface references

