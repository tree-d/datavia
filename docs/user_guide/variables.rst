Pipelines and Variables
=======================

This page lists every pipeline and every variable that datavia can deliver.
The first section is a short guide to requesting data; the tables follow.

Using Pipelines
---------------

Every request follows the same four steps: create a pipeline, register it
with :class:`~datavia.core.datavia.Datavia`, download with ``update_data()``,
then query with ``get_data()``.

**1. Install the pipeline package.** The core package contains no pipeline
code; install the ones you need (see :doc:`../index` for the pixi commands):

.. code-block:: bash

    pip install datavia[elevation]
    pip install datavia[soil]
    pip install datavia[weather]
    pip install datavia-weather[kriging]   # only for source="DWD_stations"

**2. Create the pipelines.** Elevation and soil work without a config.
A weather pipeline needs a ``source``, the ``variables`` to download, and a
date range. The full list of weather config keys is in the
:class:`~datavia.weather.pipeline.WeatherPipeline` docstring.

.. doctest::

    >>> from datavia import Datavia
    >>> from datavia.elevation import ElevationPipeline
    >>> from datavia.soil import SoilPipeline
    >>> from datavia.weather import WeatherPipeline
    >>>
    >>> elevation = ElevationPipeline()
    >>> soil = SoilPipeline(config={
    ...     "source": "soil",
    ...     "properties": ["clay", "field_capacity"],
    ...     "depths": ["0-5cm"],
    ... })
    >>> hyras = WeatherPipeline(config={
    ...     "source": "HYRAS",
    ...     "variables": ["2m_temperature", "total_precipitation"],
    ...     "date_start": "2024-06-01",
    ...     "date_end": "2024-06-30",
    ... })

Creating a pipeline only checks its config: an unknown key, or a variable the
source does not provide, raises ``ValueError`` here, before anything is
downloaded.

**3. Register them with Datavia.** ``dv()`` creates the metadata database and
data directory if needed; it does not download anything. Each pipeline is then
reachable under its ``source`` name, e.g. ``dv.elevation``, ``dv.soil``,
``dv.HYRAS``.

.. doctest::

    >>> dv = Datavia(pipelines=[elevation, soil, hyras])
    >>> _ = dv()  # doctest: +SKIP

**4. Download, then query.** ``update_data()`` fetches only what is not
stored yet, so it is safe to call every time. How much it downloads depends on
the area, the number of variables or coverages, and (for weather) the length
of the date range. ``get_data()`` reads the local copy; coordinates are an
``(N, 2)`` array of ``[longitude, latitude]`` pairs.

.. doctest::

    >>> import numpy as np
    >>> coords = np.array([[13.405, 52.520], [11.582, 48.135]])  # Berlin, Munich
    >>>
    >>> _ = dv.elevation.update_data()  # doctest: +SKIP
    >>> _ = dv.soil.update_data()       # doctest: +SKIP
    >>> _ = dv.HYRAS.update_data()      # doctest: +SKIP
    >>>
    >>> # Elevation: no variable argument, metres above sea level, shape (N,)
    >>> heights = dv.elevation.get_data(coords, crs_coords="EPSG:4326")  # doctest: +SKIP
    >>>
    >>> # Soil: choose properties / depths / statistic ("value").
    >>> # Several coverages -> dict keyed by "{property}_{depth}_{statistic}".
    >>> soil_values = dv.soil.get_data(coords, properties=["clay", "field_capacity"])  # doctest: +SKIP
    >>> clay = soil_values["clay_0-5cm_mean"]  # doctest: +SKIP
    >>> # Exactly one coverage -> plain array of shape (N,)
    >>> clay = dv.soil.get_data(coords, properties="clay", depths="0-5cm")  # doctest: +SKIP
    >>>
    >>> # Weather: variable and datetime_utc are required.
    >>> t = dv.HYRAS.get_data(coords, variable="2m_temperature",
    ...                       datetime_utc="2024-06-15")  # doctest: +SKIP
    >>> # A list of T timestamps gives shape (N, T)
    >>> pr = dv.HYRAS.get_data(coords, variable="total_precipitation",
    ...                        datetime_utc=["2024-06-15", "2024-06-16"])  # doctest: +SKIP
    >>> # Single point shortcut
    >>> t_berlin = dv.HYRAS.get_weather_data(lat=52.52, lon=13.405,
    ...                                      variable="2m_temperature",
    ...                                      datetime_utc="2024-06-15")  # doctest: +SKIP

The same downloads are available from the command line, driven by a config
file (``datavia_config.py`` by default):

.. code-block:: bash

    datavia update elevation
    datavia update soil
    datavia update weather                # all configured weather pipelines
    datavia update weather --source HYRAS # one of them

**How each pipeline takes a request**

.. list-table::
   :header-rows: 1
   :widths: 14 36 22 28

   * - Pipeline
     - Selects the variable with
     - Returns
     - Notes
   * - Elevation
     - Nothing (one variable)
     - ``(N,)`` array
     - ``nan`` outside the raster.
   * - Soil
     - ``properties``, ``depths``, ``value`` (the statistic); default to the
       pipeline config
     - ``dict`` keyed by coverage ID, or ``(N,)`` array when exactly one
       coverage matches
     - Values are raw upstream integers; see the soil table for the
       scale factors.
   * - Weather
     - ``variable`` and ``datetime_utc`` (both required)
     - ``(N,)`` for one timestamp, ``(N, T)`` for a list of T
     - ``temporal_resolution`` in the config selects ``"daily"`` (default)
       or ``"hourly"`` values. ``radius_km`` / ``station_weight`` apply when
       ``dwd_stations`` is configured.

Pipelines
---------

.. list-table::
   :header-rows: 1
   :widths: 16 14 16 22 18 14

   * - Pipeline (``source`` / ``dv.<name>``)
     - Install
     - Data source
     - Coverage and grid
     - Time
     - Credentials
   * - ``ElevationPipeline`` (``elevation``)
     - ``datavia[elevation]``
     - BKG DGM200 digital elevation model
     - Germany, 200 m, EPSG:25832
     - Static
     - None
   * - ``SoilPipeline`` (``soil``)
     - ``datavia[soil]``
     - SoilGrids 2.0 (ISRIC, WCS) and HiHydroSoil v2.0 (FutureWater, GeoTIFF)
     - Germany window of the global 250 m grids
     - Static
     - None
   * - ``WeatherPipeline`` (``ERA5_land``)
     - ``datavia[weather]``
     - Copernicus ERA5-Land reanalysis (CDS API)
     - Germany by default (``era5_bbox``), 0.1°, EPSG:4326
     - Hourly; ``"daily"`` or ``"hourly"`` queries
     - CDS API key in ``~/.cdsapirc``
   * - ``WeatherPipeline`` (``HYRAS``)
     - ``datavia[weather]``
     - DWD HYRAS gridded observations (DWD OpenData)
     - Germany, 1 km (radiation 5 km), EPSG:3035
     - Daily only
     - None
   * - ``WeatherPipeline`` (``DWD_stations``)
     - ``datavia-weather[kriging]``
     - DWD Climate Data Center station observations, kriged onto the HYRAS
       grid (see :doc:`kriging`)
     - Germany, 1 km, EPSG:3035
     - Daily only
     - None

A weather pipeline handles one source. To combine sources, create one
pipeline per source and register them all with the same ``Datavia``.

Variables
---------

Weather
~~~~~~~

Pass the variable names in the ``variables`` config key and as ``variable=``
to ``get_data()``.

.. list-table::
   :header-rows: 1
   :widths: 26 8 8 8 14 22 14

   * - Variable
     - ERA5_land
     - HYRAS
     - DWD_stations
     - Unit
     - ``"daily"`` value
     - Native name (ERA5 / HYRAS)
   * - ``2m_temperature``
     - ✓
     - ✓
     - ✓
     - °C
     - Daily mean
     - ``t2m`` / ``tas``
   * - ``temperature_2m_max``
     - –
     - ✓
     - ✓
     - °C
     - Daily maximum
     - – / ``tasmax``
   * - ``temperature_2m_min``
     - –
     - ✓
     - ✓
     - °C
     - Daily minimum
     - – / ``tasmin``
   * - ``total_precipitation``
     - ✓
     - ✓
     - ✓
     - mm
     - Total over the 06–06 UTC precipitation day
     - ``tp`` / ``pr``
   * - ``surface_solar_radiation_downwards``
     - ✓
     - ✓
     - –
     - PAR, µmol m⁻² s⁻¹
     - Mean flux over the day
     - ``ssrd`` / ``rsds``
   * - ``relative_humidity_2m``
     - –
     - ✓
     - ✓
     - %
     - Daily mean
     - – / ``hurs``
   * - ``vapour_pressure_deficit``
     - ✓
     - ✓
     - ✓
     - kPa
     - Daily value (FAO-56); see below
     - derived, stored as ``vpd``

* Units are those after datavia's automatic conversion (K → °C, m → mm,
  shortwave → PAR). Override them per variable with the ``unit_conversions``
  config key.
* Radiation is returned as photosynthetically active radiation: 50 % of
  the shortwave flux, multiplied by 4.57 µmol J⁻¹.
* In ``"hourly"`` mode (ERA5_land only) each value is the one at that hour;
  precipitation is the amount that fell during the hour.
* No source publishes the vapour pressure deficit; datavia derives it when
  downloading, after FAO-56 (Allen et al. 1998), and stores it like any
  other variable. The method depends on what the source measures:

  - ``DWD_stations``: per station and day, saturation vapour pressure from
    the daily maximum and minimum temperature minus the measured daily mean
    vapour pressure (CDC ``VPM``); then kriged like the other variables.
  - ``HYRAS``: per grid cell and day, the same saturation vapour pressure
    times ``1 - hurs / 100`` (actual vapour pressure from the mean relative
    humidity). Requesting it downloads the ``tasmax``, ``tasmin`` and
    ``hurs`` files of each year.
  - ``ERA5_land``: per hour from 2 m temperature and dewpoint (an extra
    variable in each CDS request); the ``"daily"`` value is the mean of the
    24 hourly values.

  Negative values (measurement noise) are set to 0.

The variable lists come from the source registry:

.. doctest::

    >>> from datavia.weather.source_registry import get_valid_variables
    >>> sorted(get_valid_variables("ERA5_land"))  # doctest: +NORMALIZE_WHITESPACE
    ['2m_temperature', 'surface_solar_radiation_downwards', 'total_precipitation',
     'vapour_pressure_deficit']
    >>> sorted(get_valid_variables("HYRAS"))  # doctest: +NORMALIZE_WHITESPACE
    ['2m_temperature', 'relative_humidity_2m', 'surface_solar_radiation_downwards',
     'temperature_2m_max', 'temperature_2m_min', 'total_precipitation',
     'vapour_pressure_deficit']
    >>> sorted(get_valid_variables("DWD_stations"))  # doctest: +NORMALIZE_WHITESPACE
    ['2m_temperature', 'relative_humidity_2m', 'temperature_2m_max',
     'temperature_2m_min', 'total_precipitation', 'vapour_pressure_deficit']

Soil
~~~~

Pass property names in the ``properties`` config key or as ``properties=`` to
``update_data()`` / ``get_data()``. Values are returned **as stored upstream,
without rescaling**: divide by the factor in the table to get the
conventional unit.

.. list-table::
   :header-rows: 1
   :widths: 20 13 27 13 9 12 6

   * - Property
     - Source
     - Description
     - Raw unit
     - Divide by
     - Unit
     - Default
   * - ``clay``
     - SoilGrids
     - Clay content
     - g/kg
     - 10
     - %
     - ✓
   * - ``sand``
     - SoilGrids
     - Sand content
     - g/kg
     - 10
     - %
     - ✓
   * - ``silt``
     - SoilGrids
     - Silt content
     - g/kg
     - 10
     - %
     - ✓
   * - ``ph``
     - SoilGrids
     - pH in water (API name ``phh2o``)
     - pH × 10
     - 10
     - pH
     - ✓
   * - ``carbon``
     - SoilGrids
     - Soil organic carbon content (API name ``soc``)
     - dg/kg
     - 10
     - g/kg
     - ✓
   * - ``bdod``
     - SoilGrids
     - Bulk density of the fine earth fraction
     - cg/cm³
     - 100
     - kg/dm³
     -
   * - ``cec``
     - SoilGrids
     - Cation exchange capacity at pH 7
     - mmol(c)/kg
     - 10
     - cmol(c)/kg
     -
   * - ``cfvo``
     - SoilGrids
     - Volumetric fraction of coarse fragments (> 2 mm)
     - cm³/dm³
     - 10
     - vol %
     -
   * - ``nitrogen``
     - SoilGrids
     - Total nitrogen
     - cg/kg
     - 100
     - g/kg
     -
   * - ``ocd``
     - SoilGrids
     - Organic carbon density
     - hg/m³
     - 10
     - kg/m³
     -
   * - ``ocs``
     - SoilGrids
     - Organic carbon stock (depth ``0-30cm`` only)
     - t/ha
     - 10
     - kg/m²
     -
   * - ``wv0010``
     - SoilGrids
     - Volumetric water content at 10 kPa
     - 10⁻³ cm³/cm³
     - 10
     - vol %
     -
   * - ``wv0033``
     - SoilGrids
     - Volumetric water content at 33 kPa
     - 10⁻³ cm³/cm³
     - 10
     - vol %
     -
   * - ``wv1500``
     - SoilGrids
     - Volumetric water content at 1500 kPa
     - 10⁻³ cm³/cm³
     - 10
     - vol %
     -
   * - ``field_capacity``
     - HiHydroSoil
     - Water content at field capacity, pF 2 (file prefix ``WCpF2``)
     - cm³/cm³ × 10⁴
     - 10 000
     - cm³/cm³
     - ✓
   * - ``wilting_point``
     - HiHydroSoil
     - Water content at wilting point, pF 4.2 (file prefix ``WCpF4.2``)
     - cm³/cm³ × 10⁴
     - 10 000
     - cm³/cm³
     - ✓
   * - ``porosity``
     - HiHydroSoil
     - Saturated water content (file prefix ``WCsat``)
     - cm³/cm³ × 10⁴
     - 10 000
     - cm³/cm³
     - ✓
   * - ``hydraulic_conductivity``
     - HiHydroSoil
     - Saturated hydraulic conductivity (file prefix ``Ksat``)
     - cm/day × 10⁴
     - 10 000
     - cm/day
     - ✓

**Depths and statistics.** Every requested property is downloaded for every
requested depth and one statistic. Each combination is stored as its own
coverage, named ``{property}_{depth}_{statistic}`` (e.g.
``field_capacity_0-5cm_mean``).

.. list-table::
   :header-rows: 1
   :widths: 16 52 32

   * - Source
     - Depths (``depths``)
     - Statistics (config ``statistic``, call argument ``value``)
   * - SoilGrids
     - ``0-5cm``, ``5-15cm``, ``15-30cm``, ``30-60cm``, ``60-100cm``,
       ``100-200cm``; ``0-30cm`` for ``ocs`` only
     - ``mean``, ``Q0.05``, ``Q0.5``, ``Q0.95``, ``uncertainty``
   * - HiHydroSoil
     - ``0-5cm``, ``5-15cm``, ``15-30cm``, ``30-60cm``, ``60-100cm``,
       ``100-200cm``
     - ``mean`` only

The defaults are depths ``0-5cm`` and ``5-15cm``, statistic ``mean``, and the
properties marked ✓ above:

.. doctest::

    >>> from datavia.soil import SoilPipeline
    >>> default_soil = SoilPipeline()
    >>> default_soil.properties  # doctest: +NORMALIZE_WHITESPACE
    ['clay', 'sand', 'silt', 'ph', 'carbon', 'field_capacity', 'wilting_point',
     'porosity', 'hydraulic_conductivity']
    >>> default_soil.depths, default_soil.statistic
    (['0-5cm', '5-15cm'], 'mean')

To see what is already stored locally, call
``SoilPipeline.get_available_properties()``. To see what the upstream
services currently offer (this queries the network), call
``SoilPipeline.get_remote_available_properties()``.

Elevation
~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Variable
     - Unit
     - Notes
   * - Elevation
     - m above sea level
     - BKG DGM200 at 200 m. ``get_data()`` takes no variable argument.
       Points outside the raster (Germany) return ``nan``.

Licences
--------

Each data source has its own licence and attribution requirements; see
*Data Licensing & Attribution* on the :doc:`../index` page.
