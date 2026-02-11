Datavia Documentation
=====================

**Datavia** is a modular, pipeline-based system for integrating geospatial data sources. It uses **namespace packages** for truly modular installation - install only the data sources you need.

.. note::
   Datavia uses a multi-package architecture. The core system (``datavia``) contains NO pipeline code. Pipelines are separate packages (``datavia-elevation``, ``datavia-soil``) installed on demand.



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
    
    # Or use pixi for development
    pixi install

Basic usage with elevation data:

.. doctest::

    >>> from datavia.core.datavia import Datavia
    >>> from datavia.elevation import ElevationPipeline
    >>> import numpy as np
    >>> 
    >>> # Initialize pipeline and system
    >>> elevation_pipeline = ElevationPipeline()
    >>> dv = Datavia(pipelines=[elevation_pipeline])
    >>> 
    >>> # Initialize system (downloads data if needed)
    >>> dv() # doctest: +SKIP
    >>> 
    >>> # Get elevation data for coordinates (longitude, latitude)
    >>> coords = np.array([[13.4050, 52.5200]])  # Berlin
    >>> elevations = dv.elevation.get_data(coords=coords, crs_coords="EPSG:4326") # doctest: +SKIP
    >>> 
    >>> print(f"Berlin elevation: {elevations[0]:.1f}m") # doctest: +SKIP
    Berlin elevation: 35.5m

Database management:

.. code-block:: bash

    datavia start            # Start PostGIS container
    datavia stop             # Stop container
    datavia config           # View configuration

Architecture
------------

Datavia's pipeline-based architecture consists of three core interfaces:

Core Interfaces
~~~~~~~~~~~~~~~

.. doctest::

    >>> from datavia.core.interfaces import Downloader, Saver, Getter, Pipeline # doctest: +SKIP
    >>> 
    >>> # Each pipeline implements these three components:
    >>> class MyPipeline: # Simplified example
    ...     def __init__(self):
    ...         # Initialize pipeline with required components
    ...         pass
    >>> 
    >>> # Verify the class exists
    >>> callable(MyPipeline)
    True

**Pipeline Components:**

* **Downloader**: Fetches data from external sources (URLs, APIs)
* **Saver**: Stores data locally and metadata in PostGIS database
* **Getter**: Provides coordinate-based data access with spatial interpolation

Current Data Sources
~~~~~~~~~~~~~~~~~~~~

**✅ Elevation Data (Working)**

* **Source**: BKG DGM200 German elevation model (200m resolution)
* **Pipeline**: ``ElevationPipeline`` in ``datavia.elevation`` namespace package
* **Usage**: Fully functional, tested with Berlin (~35.5m), Munich (~511.9m)

**🔄 Soil Data (In Development)**

* **Source**: SoilGrids API (properties like clay%, pH, organic carbon)
* **Pipeline**: ``SoilPipeline`` in ``datavia.soil`` namespace package
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
    
    # Install with pixi (recommended for development)
    pixi install
    
    # Run setup script for local development
    pixi run ./scripts/setup-local-dev.sh
    
    # Or install with pip (requires manual dependency management)
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