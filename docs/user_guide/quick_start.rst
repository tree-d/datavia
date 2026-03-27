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

Start/Stop the PostGIS database:

.. code-block:: bash

    datavia start/stop

Basic Usage
-----------

**Step 1: Import and initialize**

.. doctest::

    >>> from datavia import Datavia
    >>> from datavia.elevation import ElevationPipeline
    >>> import numpy as np
    >>> 
    >>> # Create pipeline and controller
    >>> elevation_pipeline = ElevationPipeline()
    >>> dv = Datavia(pipelines=[elevation_pipeline])

**Step 2: Initialize system and download data**

.. doctest::

    >>> # dv() connects to the PostGIS database and instantiates each pipeline's
    >>> # Downloader / Saver / Getter components. It does NOT download data.
    >>> _ = dv() # doctest: +SKIP
    >>>
    >>> # update_data() fetches data from the external source and caches it locally.
    >>> # Only needs to run once; subsequent calls skip already-downloaded data.
    >>> # First download: ~500 MB for the elevation pipeline.
    >>> # Returns True on success, False if the download failed.
    >>> success = dv.elevation.update_data() # doctest: +SKIP

**Step 3: Get data**

.. doctest::

    >>> coords = np.array([[13.4050, 52.5200]])  # Berlin
    >>> elevations = dv.elevation.get_data(coords=coords, crs_coords="EPSG:4326") # doctest: +SKIP
    >>> print(f"Berlin elevation: {elevations[0]:.1f}m") # doctest: +SKIP
    Berlin elevation: 35.7m

Complete Example
----------------

Here's a complete example that gets elevation data for German cities:

.. code-block:: python

    #!/usr/bin/env python3
    import numpy as np
    from datavia.core.datavia import Datavia
    from datavia.elevation import ElevationPipeline

    def main():
        # Initialize pipeline
        elevation_pipeline = ElevationPipeline()
        dv = Datavia(pipelines=[elevation_pipeline])
        dv()
        
        # Define coordinates for German cities
        cities = {
            "Hamburg": [9.9937, 53.5511],
            "Berlin": [13.4050, 52.5200],
            "Cologne": [6.9603, 50.9375],
            "Frankfurt": [8.6821, 50.1109],
        }
        
        # Convert to numpy array
        coords = np.array(list(cities.values()))
        
        # Get elevation data
        elevations = dv.elevation.get_data(coords=coords, crs_coords="EPSG:4326")
        
        # Display results
        for city, elevation in zip(cities.keys(), elevations):
            print(f"{city}: {elevation:.1f}m above sea level")

    if __name__ == "__main__":
        main()

Expected Output:

.. code-block:: text

    Hamburg: 5.8m above sea level
    Berlin: 35.5m above sea level
    Cologne: 47.9m above sea level
    Frankfurt: 112.0m above sea level

What's Next?
------------

* Learn about :doc:`basic_usage` for more detailed examples

* Check out more :doc:`examples`

