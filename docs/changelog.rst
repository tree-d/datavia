Changelog
=========

All notable changes to Datavia will be documented in this file.

The format is based on `Keep a Changelog <https://keepachangelog.com/en/1.0.0/>`_,
and this project adheres to `Semantic Versioning <https://semver.org/spec/v2.0.0.html>`_.

[1.0.0] - 2025-01-15
---------------------

Added
~~~~~
- Complete three-component architecture (Fetcher, Processor, Getter)
- Working elevation data extraction from BKG DGM200 German topography
- Coordinate system transformations (EPSG:4326 ↔ EPSG:25832)
- PostGIS database integration for raster metadata storage
- NumPy-based coordinate processing interface
- Comprehensive end-to-end testing with real German city coordinates
- Complete Sphinx documentation with user guides and API reference
- Basic workflow examples and usage patterns

Fixed
~~~~~
- Critical coordinate order standardization for EPSG:4326 (lon,lat) order throughout system
- Database schema queries to use correct column names
- Import path issues in test scripts

Changed
~~~~~~~
- Migrated from topography-specific scripts to generic ingestor architecture
- Condensed masterplan from 815 lines to focused ~200 line roadmap
- Updated README files to reflect current working architecture

Technical Details
~~~~~~~~~~~~~~~~~
- **Core Components**: Fetcher (UpdateManager + StaticDataIngestor), Processor (spatial transformations), Getter (get_data API)
- **Database**: PostGIS with raster_layers table for TIFF metadata
- **Coordinate Handling**: Consistent EPSG:4326 (lon,lat) and EPSG:25832 (x,y) coordinate order
- **Data Sources**: Working TOPOGRAPHY (BKG DGM200), planned SOIL and WEATHER
- **Testing**: End-to-end validation with real coordinates (Berlin: 35.5m, Munich: 511.9m)
- **Documentation**: Complete Sphinx docs with alabaster theme

[0.1.0] - Development Phases
-----------------------------

Phase 1 (Legacy Analysis)
~~~~~~~~~~~~~~~~~~~~~~~~~
- Analyzed legacy implementations (datagrator, terraflow, datavia-old)
- Identified working components and architecture patterns
- Established development strategy and priorities

Phase 2 (Core Implementation)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
- Implemented three-component architecture
- Fixed critical coordinate handling bugs
- Achieved working elevation data extraction
- Validated architecture with real German data

Future Releases
---------------

[1.1.0] - Planned
~~~~~~~~~~~~~~~~~
- Additional data source integrations (soil, weather)
- Performance optimizations and caching
- Extended testing coverage
- User experience improvements

[1.2.0] - Planned
~~~~~~~~~~~~~~~~~
- Real-time data source support
- Advanced analytics capabilities
- Optional visualization dashboard
- Extended geographic coverage
