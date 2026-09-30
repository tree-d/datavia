API Reference
=============

This section contains the API documentation for Datavia's core components and pipeline packages.

Core Interfaces
---------------

.. automodule:: datavia.core.interfaces
   :members:
   :undoc-members:
   :show-inheritance:

Pipeline Controller
-------------------

.. automodule:: datavia.core.datavia
   :members:
   :undoc-members:
   :show-inheritance:

Core Components
---------------

Downloaders
~~~~~~~~~~~

.. automodule:: datavia.core.downloader_url
   :members:
   :undoc-members:

Data Access
~~~~~~~~~~~

.. automodule:: datavia.core.getter_tiff
   :members:
   :undoc-members:

Data Storage
~~~~~~~~~~~~

.. automodule:: datavia.core.saver_tiff
   :members:
   :undoc-members:

Library Functions
-----------------

Coordinate Transformations
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: datavia.library.coordinate_transforms
   :members:
   :undoc-members:

Spatial Operations
~~~~~~~~~~~~~~~~~~

.. automodule:: datavia.library.spatial_ops
   :members:
   :undoc-members:

Interpolation
~~~~~~~~~~~~~

.. automodule:: datavia.library.interpolation
   :members:
   :undoc-members:

Database Integration
~~~~~~~~~~~~~~~~~~~~

.. automodule:: datavia.library.database.connection
   :members:
   :undoc-members:

.. automodule:: datavia.library.database.query
   :members:
   :undoc-members:

Weather Pipeline
----------------

.. automodule:: datavia.weather.pipeline
   :members:
   :undoc-members:
   :show-inheritance:

Weather Sources and Units
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: datavia.weather.source_registry
   :members:
   :undoc-members:

Weather Downloaders
~~~~~~~~~~~~~~~~~~~

.. automodule:: datavia.weather.composite_downloader
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: datavia.weather.era5_downloader
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: datavia.weather.hyras_downloader
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: datavia.weather.dwd_downloader
   :members:
   :undoc-members:
   :show-inheritance:

Weather Storage and Retrieval
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: datavia.weather.zarr_store_manager
   :members:
   :undoc-members:

.. automodule:: datavia.weather.coverage_manager
   :members:
   :undoc-members:

.. automodule:: datavia.weather.saver_weather
   :members:
   :undoc-members:

.. automodule:: datavia.weather.getter_weather
   :members:
   :undoc-members:

Pipeline Packages
-----------------

Elevation Pipeline
~~~~~~~~~~~~~~~~~~

.. automodule:: datavia.elevation.pipeline
   :members:
   :undoc-members:
   :show-inheritance:

Soil Pipeline
~~~~~~~~~~~~~

.. automodule:: datavia.soil.pipeline
   :members:
   :undoc-members:
   :show-inheritance:

SoilGrids Downloader
~~~~~~~~~~~~~~~~~~~~

.. automodule:: datavia.soil.soilgrids_downloader
   :members:
   :undoc-members:
   :show-inheritance:

HiHydroSoil Downloader
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: datavia.soil.hihydrosoil_downloader
   :members:
   :undoc-members:
   :show-inheritance:

Composite Downloader
~~~~~~~~~~~~~~~~~~~~

.. automodule:: datavia.soil.composite_downloader
   :members:
   :undoc-members:
   :show-inheritance:

Configuration
-------------

.. automodule:: datavia.config
   :members:
   :undoc-members:

Command Line Interface
----------------------

.. automodule:: datavia.cli
   :members:
   :undoc-members:

