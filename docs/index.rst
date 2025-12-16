Datavia Documentation
=====================

**Datavia** is a modular, pipeline-based system for integrating geospatial data sources. It uses **namespace packages** for truly modular installation - install only the data sources you need.

.. note::
   Datavia uses a multi-package architecture. The core system (``datavia``) contains NO pipeline code. Pipelines are separate packages (``datavia-elevation``, ``datavia-soil``) installed on demand.

.. warning::
   This documentation is being updated to reflect the new namespace package architecture. Some examples may be outdated.

Overview
--------

Datavia uses a modular architecture where **pipelines** combine three core components:

* **Downloader**: Fetches data from external sources (URLs, APIs)
* **Saver**: Stores data locally and metadata in PostGIS database
* **Getter**: Provides coordinate-based data access with spatial interpolation

Available pipeline packages:

* **Elevation Pipeline** (``datavia[elevation]``): German BKG DGM200 (200m resolution)
* **Soil Pipeline** (``datavia[soil]``): SoilGrids API with selective download (280MB strategy)
* **Weather Pipeline** (``datavia[weather]``): Planned - DWD weather data
* **Radiation Pipeline**: Planned - CAMS radiation data

Quick Start
-----------

Installation (Modular):

.. code-block:: bash

    # Core system only (NO pipeline code)
    pip install datavia
    
    # Core + specific pipelines
    pip install datavia[elevation]     # BKG elevation data
    pip install datavia[soil]          # SoilGrids soil data
    pip install datavia[elevation,soil] # Multiple pipelines
    
    # Everything
    pip install datavia[all]
    pip install datavia
    
    # Or use pixi for development
    pixi install

Basic usage with elevation data:

.. code-block:: python

    from datavia.core.datavia import Datavia
    from datavia_pipelines.pipelines.elevation import ElevationPipeline
    import numpy as np

    # Initialize pipeline and system
    elevation_pipeline = ElevationPipeline()
    dv = Datavia(pipelines=[elevation_pipeline])
    
    # Initialize system (downloads data if needed)
    dv()

    # Get elevation data for coordinates (longitude, latitude)
    coords = np.array([[13.4050, 52.5200]])  # Berlin
    elevations = dv.elevation.get_data(coords=coords, crs_coords="EPSG:4326")
    
    print(f"Berlin elevation: {elevations[0]:.1f}m")  # Output: ~35.5m

Database management:

.. code-block:: bash

    datavia db start         # Start PostGIS container
    datavia db stop          # Stop container
    datavia db status        # Check status
Architecture
------------

Datavia's pipeline-based architecture consists of three core interfaces:

Core Interfaces
~~~~~~~~~~~~~~~

.. code-block:: python

    from datavia.core.interfaces import Downloader, Saver, Getter, Pipeline
    
    # Each pipeline implements these three components:
    class MyPipeline(Pipeline):
        def __init__(self):
            super().__init__(
                name="my_data",
                downloader=MyDownloader,
                saver=MySaver,  
                getter=MyGetter,
                url="https://api.example.com/data"
            )

**Pipeline Components:**

* **Downloader**: Fetches data from external sources (URLs, APIs)
* **Saver**: Stores data locally and metadata in PostGIS database
* **Getter**: Provides coordinate-based data access with spatial interpolation

Current Data Sources
~~~~~~~~~~~~~~~~~~~~

**✅ Elevation Data (Working)**

* **Source**: BKG DGM200 German elevation model (200m resolution)
* **Pipeline**: ``ElevationPipeline`` in ``datavia-pipelines``
* **Usage**: Fully functional, tested with Berlin (~35.5m), Munich (~511.9m)

**🔄 Soil Data (In Development)**

* **Source**: SoilGrids API (properties like clay%, pH, organic carbon)
* **Pipeline**: ``SoilPipeline`` in ``datavia-pipelines``
* **Status**: API integration in progress

System Management
~~~~~~~~~~~~~~~~~

.. code-block:: bash

    # Database operations
    datavia db start         # Start PostGIS container
    datavia db stop          # Stop container  
    datavia db status        # Check status
    
    # Data updates (planned)
    datavia update elevation
    datavia update soil

Development Setup
~~~~~~~~~~~~~~~~~

.. code-block:: bash

    # Clone repository
    git clone https://github.com/tree-d/datavia.git
    cd datavia
    
    # Install with pixi (recommended)
    pixi install
    
    # Or install with pip
    pip install -e .[dev]
    
    # Run tests
    pytest

Documentation
=============

.. toctree::
   :maxdepth: 2
   :caption: User Guide:

   user_guide/quick_start
   user_guide/basic_usage
   user_guide/examples

.. toctree::
   :maxdepth: 2
   :caption: API Reference:

   api/index
   
.. toctree::
   :maxdepth: 1
   :caption: Reference:

   changelog

Indices and Tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`