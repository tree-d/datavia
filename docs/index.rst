Datavia Documentation
=====================

**Datavia** is a modular, pipeline-based system for integrating geospatial data sources. It uses **namespace packages** for truly modular installation - install only the data sources you need.

.. note::
   Datavia uses a multi-package architecture. The core system (``datavia``) contains NO pipeline code. Pipelines are separate packages (``datavia-elevation``, ``datavia-soil``) installed on demand.



Overview
--------

Datavia uses a modular architecture where **pipelines** combine three core components:

* **Downloader**: Fetches data from external sources (URLs, APIs)
* **Saver**: Stores data locally and metadata in the SQLite metadata database
* **Getter**: Provides coordinate-based data access with spatial interpolation

Available pipeline packages:

* **Elevation Pipeline** (``datavia[elevation]``): German BKG DGM200 (200 m resolution)
* **Soil Pipeline** (``datavia[soil]``): SoilGrids (WCS) + HiHydroSoil (HTTP GeoTIFF) — incremental download per coverage
* **Weather Pipeline** (``datavia[weather]``): Planned — DWD weather data
* **Radiation Pipeline**: Planned — CAMS radiation data

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

    >>> from datavia import Datavia
    >>> from datavia.elevation import ElevationPipeline
    >>> import numpy as np
    >>> elevation_pipeline = ElevationPipeline()
    >>> dv = Datavia(pipelines=[elevation_pipeline])
    >>> coords = np.array([[13.4050, 52.5200]])  # Berlin
    >>> _ = dv() # doctest: +SKIP
    >>> _ = dv.elevation.update_data() # doctest: +SKIP
    >>> elevations = dv.elevation.get_data(coords=coords, crs_coords="EPSG:4326") # doctest: +SKIP
    >>> print(f"Berlin elevation: {elevations[0]:.1f}m") # doctest: +SKIP
    Berlin elevation: 35.7m

Database management:

.. code-block:: bash

    datavia config           # View configuration

Architecture
------------

Datavia's pipeline-based architecture consists of three core interfaces:

Core Interfaces
~~~~~~~~~~~~~~~

.. doctest::

    >>> from datavia.core.interfaces import Downloader, Saver, Getter, Pipeline
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
* **Saver**: Stores data locally and metadata in the SQLite metadata database
* **Getter**: Provides coordinate-based data access with spatial interpolation

Current Data Sources
~~~~~~~~~~~~~~~~~~~~

**✅ Elevation Data**

* **Source**: BKG DGM200 German elevation model (200 m resolution)
* **Pipeline**: ``ElevationPipeline`` in ``datavia.elevation`` namespace package
* **Usage**: Fully functional, tested with Berlin (~35.5 m), Munich (~511.9 m)

**✅ Soil Data**

* **Sources**: SoilGrids API (clay, sand, silt, pH, carbon, …) and HiHydroSoil catalogue
  (field capacity, wilting point, porosity, hydraulic conductivity)
* **Pipeline**: ``SoilPipeline`` in ``datavia.soil`` namespace package
* **Download**: Incremental — one single-band GeoTIFF per coverage ID

System Management
~~~~~~~~~~~~~~~~~

.. code-block:: bash

    # Database operations
    datavia config           # View configuration

    # Data updates
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
    
    # Or install with pip (requires manual dependency management)
    pip install -e .[dev]
    
    # Run tests
    pytest

Data Licensing & Attribution
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Data downloaded by Datavia's pipelines is subject to the following third-party
licences. **Any application or publication that uses data obtained through
Datavia must comply with these requirements.**

**Elevation — BKG DGM200**
   Licence: Datenlizenz Deutschland – Namensnennung – Version 2.0
   (`dl-de/by-2-0 <https://www.govdata.de/dl-de/by-2-0>`_)

   Required attribution notice (Quellenvermerk):

   .. code-block:: text

      © GeoBasis-DE / BKG (year of last data access) dl-de/by-2-0 (Daten verändert)

**Soil — SoilGrids (ISRIC)**
   Licence: `Creative Commons Attribution 4.0 (CC-BY 4.0)
   <https://creativecommons.org/licenses/by/4.0/>`_

   Required citation:

      Poggio, L., de Sousa, L. M., Batjes, N. H., Heuvelink, G. B. M., Kempen, B.,
      Ribeiro, E., and Rossiter, D.: SoilGrids 2.0: producing soil information for the
      globe with quantified spatial uncertainty,
      *SOIL*, 7, 217–240, 2021.
      https://doi.org/10.5194/soil-7-217-2021

**Soil — HiHydroSoil v2.0 (FutureWater)**
   Licence: Free use with attribution
   (see `License_HHSv2.txt
   <http://opendap.biodt.eu/grasslands-pdt/soilMapsHiHydroSoil/License_HHSv2.txt>`_)

   Required citation:

      Simons, G.W.H., R. Koster, P. Droogers. 2020.
      HiHydroSoil v2.0 – A high resolution soil map of global hydraulic properties.
      FutureWater Report 213.

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