Quick Start Guide
=================

Get started with Datavia geospatial data integration in minutes.

Prerequisites
-------------

* Python 3.12 or higher
* `pixi <https://pixi.sh/>`_ (recommended) or pip
* Docker (for PostGIS database)

Installation
------------

**Option 1: Using pixi (recommended for development)**

.. code-block:: bash

    # Clone repository
    git clone https://github.com/tree-d/datavia.git
    cd datavia
    
    # Install dependencies
    pixi install

**Option 2: Using pip**

.. code-block:: bash

    pip install datavia
    # Or for development
    pip install -e .[dev]

Setup Database
--------------

Start the PostGIS database:

.. code-block:: bash

    datavia db start

Basic Usage
-----------

**Step 1: Import and initialize**

.. code-block:: python

    from datavia.core.datavia import Datavia
    from datavia.elevation import ElevationPipeline
    import numpy as np
    
    # Create pipeline and controller
    elevation_pipeline = ElevationPipeline()
    dv = Datavia(pipelines=[elevation_pipeline, ])

**Step 2: Initialize system**

.. code-block:: python

    # This downloads data if needed (first run)
    dv()
    dv.elevation.update_data()

**Step 3: Get data**

.. code-block:: python

    # Define coordinates (longitude, latitude)
    coords = np.array([[13.4050, 52.5200]])  # Berlin
    
    # Get elevation data
    elevations = dv.elevation.get_data(coords=coords, crs_coords="EPSG:4326")
    print(f"Berlin elevation: {elevations[0]:.1f}m")  # ~35.5m

2. **Define your coordinates**:

   .. code-block:: python

       # Coordinates as (longitude, latitude) pairs
       coords = np.array([
           [13.4050, 52.5200],  # Berlin
           [11.5820, 48.1351],  # Munich
       ])

3. **Get elevation data**:

   .. code-block:: python

       elevations = get_data(coords, DataSource.TOPOGRAPHY)
       print(f"Berlin: {elevations[0]:.1f}m")
       print(f"Munich: {elevations[1]:.1f}m")

Complete Example
----------------

Here's a complete example that gets elevation data for German cities:

.. code-block:: python

    #!/usr/bin/env python3
    import numpy as np
    from datavia.getter import get_data, DataSource

    def main():
        # Define coordinates for German cities
        cities = {
            "Hamburg": [9.9937, 53.5511],   # Hamburg
            "Berlin": [13.4050, 52.5200],   # Berlin
            "Cologne": [6.9603, 50.9375],   # Cologne
            "Frankfurt": [8.6821, 50.1109], # Frankfurt
        }
        
        # Convert to numpy array
        coords = np.array(list(cities.values()))
        
        # Get elevation data
        elevations = get_data(coords, DataSource.TOPOGRAPHY)
        
        # Display results
        for i, (city, elevation) in enumerate(zip(cities.keys(), elevations)):
            print(f"{city}: {elevation:.1f}m above sea level")

    if __name__ == "__main__":
        main()

Expected Output:

.. code-block:: text

    Berlin: 35.5m above sea level
    Cologne: 47.9m above sea level
    Hamburg: 5.8m above sea level
    Frankfurt: 112.0m above sea level

What's Next?
------------

* Learn about :doc:`basic_usage` for more detailed examples

* Check out more :doc:`examples`

