Changelog
=========

All notable changes to Datavia will be documented in this file.

The format is based on `Keep a Changelog <https://keepachangelog.com/en/1.0.0/>`_,
and this project adheres to `Semantic Versioning <https://semver.org/spec/v2.0.0.html>`_.

`[Unreleased] — 1.0.4 <https://github.com/tree-d/datavia/compare/1.0.3...HEAD>`_
-----------------------------------------------------------------------------------

The weather pipeline release. Adds real-time and reanalysis weather data source
integrations on top of the existing elevation and soil pipelines.

Added
~~~~~
- ``datavia.weather`` sub-package with a full pipeline architecture.
- ``WeatherPipeline`` orchestrator for multi-source weather data retrieval.
- ``SourceRegistry`` for managing and selecting weather data sources.
- ``HyrasDownloader`` for DWD HYRAS gridded observation data (precipitation,
  temperature, humidity, radiation).
- ``Era5Downloader`` for ECMWF ERA5 reanalysis data with chunked download
  support to handle large time ranges efficiently.
- ``DwdDownloader`` for DWD station and gridded observational data.
- ``CompositeDownloader`` for transparent multi-source queries with fallback.
- ``CoverageManager`` for tracking spatial and temporal coverage of
  already-downloaded data to avoid redundant downloads.
- ``GetterWeather`` for querying weather values at arbitrary coordinates.
- ``SaverWeather`` for persisting weather data to disk.
- CRS and temporal resolution fields in weather pipeline configuration.
- Progress indicators (``tqdm``) in all downloaders.
- Unique filename generation for downloaded weather files.
- Batch coordinate processing in the weather query interface.
- ``reconfigure()`` method on the pipeline for runtime reconfiguration.

Fixed
~~~~~
- Valid-time alignment bug in ERA5 data retrieval.
- Edge NaN values when filling spatial coverage gaps.
- File naming collisions for concurrent downloads.

Changed
~~~~~~~
- Unit conversion utilities extended to cover weather variables.


`[1.0.3] — 2026-04-28 <https://github.com/tree-d/datavia/compare/1.0.2...1.0.3>`_
----------------------------------------------------------------------------------

The SQLite migration release. Removes the Docker/PostgreSQL requirement and
makes the database zero-setup.

Changed
~~~~~~~
- **Breaking** — replaced PostgreSQL/PostGIS metadata backend with SQLite.
  The database is now created automatically at ``<data_directory>/datavia.db``
  (no Docker, no credentials, no setup required).
- ``bbox`` column changed from PostGIS ``geometry`` type to plain ``TEXT``
  (WKT string in EPSG:4326).  :func:`~datavia.library.database.query.get_raster_metadata`
  now returns ``bbox_wkt`` as a plain WKT string.
- ``acquisition_time`` column changed from ``timestamptz`` to ``TEXT``
  (ISO-8601 string).
- :func:`~datavia.library.database.start.initialize_database` uses
  :func:`sqlalchemy.inspect` for cross-backend table detection instead of
  ``to_regclass``.
- :func:`~datavia.library.database.connection._DatabaseManager._build_engine`
  skips connection-pool kwargs for SQLite URLs and adds
  ``check_same_thread=False`` instead.
- ``config.database_url`` now defaults to a SQLite URL derived from
  ``data_directory``; PostgreSQL is opt-in via ``[database] url = ...``.
- Docker setup: dynamic port assignment and isolated database per instance.

Removed
~~~~~~~
- ``runner.py`` (Docker container lifecycle management) — no longer needed.
- ``datavia start`` and ``datavia stop`` CLI commands.
- ``psycopg2-binary`` dependency.


`[1.0.2] — 2026-03-23 <https://github.com/tree-d/datavia/compare/1.0.1...1.0.2>`_
----------------------------------------------------------------------------------

The soil pipeline release. Adds the first soil data source integration
(HiHydroSoil and SoilGrids) on top of the existing elevation pipeline.

.. note::
   Multiband TIFF support was explored during this cycle (git tag ``1.0.2-dev``,
   commit ``78bcae6``) but ultimately abandoned. That tag marks the last known
   working state of the multiband approach for future reference.

Initial stable release. Working elevation data extraction from BKG DGM200
German topography with a PostGIS backend (Docker-based).

Added
~~~~~
- HiHydroSoil data source integration.
- SoilGrids downloader.
- Soil depth and value fields in raster metadata.
- Multiple single-layer TIFF support.
- Sub-packages (``elevation``, ``soil``) versioned individually alongside
  the main package.
- Docstrings across pipeline components; ``available`` property check on
  data sources.

Fixed
~~~~~
- Pixel/resolution calculation bug in ``SoilGridsDownloader``.
- Soil pipeline end-to-end test failures.
- Remote availability check; removed unreliable HiHydroSoil depth variants.

Changed
~~~~~~~
- Cleaner split of responsibilities between ``TiffGetter`` and ``TiffSaver``.
- Improved exception handling and formatting in ``TiffSaver``.
- ``update_data()`` return value refactored for consistency.
- Error handling improved across pipeline class.
- Switched linter from ``black``/``isort`` to ``ruff`` only.
- Removed ``rioxxarray`` dependency.
- Updated unit definitions for soil output values.


`[1.0.1] — 2026-02-24 <https://github.com/tree-d/datavia/compare/dev-1.0.0-dev.20260224...1.0.1>`_
--------------------------------------------------------------------------------------------------

The elevation pipeline release. Focuses on packaging, CI/CD infrastructure,
and code quality on top of the working elevation data extraction from 1.0.0.

Added
~~~~~
- Full CI/CD pipeline (GitLab) with pull-request and main-branch workflows.
- Dev environment via ``pixi``.
- ``fiona`` as an explicit dependency.
- Build system using ``pixi`` for PyPI and conda packaging.
- Automated versioning script.

Changed
~~~~~~~
- Repo moved from GitLab to new namespace; package layout restructured.
- Renamed ``GetterTiff`` to follow naming convention.
- Refactored function signatures for clarity.
- Improved array conversion logic.
- Enhanced Sphinx documentation; fixed doc build issues.
- License updated across sub-packages.


`[1.0.0] — 2025-01-15 <https://github.com/tree-d/datavia/releases/tag/dev-1.0.0-dev.20260224>`_
------------------------------------------------------------------------------------------------

Initial stable release. Working elevation data extraction from BKG DGM200
German topography with a PostGIS backend (Docker-based).

Added
~~~~~
- Complete three-component architecture (Fetcher, Processor, Getter).
- Elevation data extraction from BKG DGM200 German topography.
- Coordinate system transformations (EPSG:4326 ↔ EPSG:25832).
- PostGIS database integration for raster metadata storage.
- NumPy-based coordinate processing interface.
- End-to-end tests with real German city coordinates
  (Berlin: 35.5 m, Munich: 511.9 m).
- Sphinx documentation with user guides and API reference.

Fixed
~~~~~
- Coordinate order standardization for EPSG:4326 (lon, lat) throughout.
- Database schema queries to use correct column names.
- Import path issues in test scripts.

Changed
~~~~~~~
- Migrated from topography-specific scripts to generic ingestor architecture.
- Updated README files to reflect current working architecture.


`[0.1.0] — Development Phases <https://github.com/tree-d/datavia>`_
---------------------------------------------------------------------

Phase 1 (Legacy Analysis)
~~~~~~~~~~~~~~~~~~~~~~~~~
- Analyzed legacy implementations (datagrator, terraflow, datavia-old).
- Identified working components and architecture patterns.
- Established development strategy and priorities.

Phase 2 (Core Implementation)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
- Implemented three-component architecture.
- Fixed critical coordinate handling bugs.
- Achieved working elevation data extraction.
- Validated architecture with real German data.

Future Releases
---------------

[1.0.5] — Planned
------------------

The Zarr store migration release. Replaces the per-download ``.nc`` file model
with a consolidated Zarr store per ``(source, variable, year)`` triple,
eliminating file-proliferation and making point time-series queries
significantly faster.

Added
~~~~~
- ``ZarrStoreManager`` — new class encapsulating all Zarr I/O: store
  creation, region writes, lazy reads, multi-year concatenation, and
  NetCDF-to-Zarr migration.
- ``zarr_grid`` entries in ``SOURCE_REGISTRY`` for every gridded source
  (ERA5-Land and HYRAS), defining the fixed Germany-extent coordinate grid,
  chunk layout, and Zstd/Blosc2 codec configuration used by all stores.
- ``CoverageManager.rebuild_from_store(variable)`` — repopulates
  ``weather_layers`` DB rows from Zarr store contents by scanning each year
  store month by month.  Required so the coverage cache can be rebuilt
  after DB loss or a fresh checkout without re-downloading from CDS.
- Auto-rebuild in ``CoverageManager.__init__``: if a Zarr store exists on
  disk for a ``(source, variable)`` pair that has no DB rows, rebuild is
  triggered automatically before the first coverage query.
- ``data_dir`` parameter on ``CoverageManager.__init__`` to allow injection
  of the data directory independently of ``get_config()``.
- ``ZarrStoreManager.migrate_nc_file(nc_path, variable)`` for importing
  existing ``.nc`` files into the Zarr store.
- ``interpolate_dataset`` overload in the interpolation library, allowing
  ``GetterWeather`` to pass an already-open ``xr.Dataset`` without
  re-opening from disk.
- ``file_format="zarr"`` value accepted in the ``weather_layers`` DB table.
- New database index ``idx_weather_source_variable_format`` on
  ``(source_name, variable, file_format)`` to speed up per-variable store
  lookups in ``CoverageManager``.
- ``zarr>=3.0,<4``, ``numcodecs>=0.12,<1``, and ``fasteners>=0.19`` added
  as direct dependencies of ``datavia-weather``.

Changed
~~~~~~~
- ``GetterWeather.get_data()`` now tries to open a Zarr store first via
  ``ZarrStoreManager.open_multi_year()``; falls back to the ``interpolate_netcdf``
  path for legacy ``.nc`` rows, preserving full backward compatibility.
- ``GetterWeather.get_existing_layers()`` scans Zarr store directories via
  ``ZarrStoreManager.list_available_variables()`` instead of querying the DB.
- ``CoverageManager`` no longer requires filename uniqueness for the
  ``uri`` column — multiple DB rows may share the same Zarr store path.

Fixed
~~~~~
- ``.write_in_progress`` sentinel left by an interrupted write is now
  removed automatically by ``rebuild_from_store``, recovering partially
  written stores without data loss.
