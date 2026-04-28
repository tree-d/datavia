Changelog
=========

All notable changes to Datavia will be documented in this file.

The format is based on `Keep a Changelog <https://keepachangelog.com/en/1.0.0/>`_,
and this project adheres to `Semantic Versioning <https://semver.org/spec/v2.0.0.html>`_.

[Unreleased] — 1.0.3
----------------------

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

[1.0.2] - 2026-03-23
----------------------

The soil pipeline release. Adds the first soil data source integration
(HiHydroSoil and SoilGrids) on top of the existing elevation pipeline.

.. note::
   Multiband TIFF support was explored during this cycle (git tag ``1.0.2-dev``,
   commit ``78bcae6``) but ultimately abandoned. That tag marks the last known
   working state of the multiband approach for future reference.

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

[1.0.1] - 2026-02-24
----------------------

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

[1.0.0] - 2025-01-15
---------------------

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

[0.1.0] - Development Phases
-----------------------------

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

[1.0.4] - Planned
~~~~~~~~~~~~~~~~~~
- Weather pipeline (branch ``weather`` in progress).
- Real-time weather data source integration.
- Extended geographic coverage
