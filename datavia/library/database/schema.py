"""
Datavia database schema, defined with SQLAlchemy Core.

Defining the tables here (instead of raw SQL) lets SQLAlchemy emit the correct
DDL for each backend, in particular auto-generated integer primary keys
(SQLite rowid alias vs. PostgreSQL SERIAL/identity).

bbox is stored as WKT text on every backend, so no PostGIS geometry column or
extension is required.  valid_from / valid_until use ISO-8601 datetime strings
so the weather table works with both SQLite (text comparison) and PostgreSQL.
"""

from sqlalchemy import Column, Double, Index, Integer, MetaData, Table, Text

metadata = MetaData()

raster_layers = Table(
    "raster_layers",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("layer_name", Text, nullable=False),
    Column("source_name", Text),
    Column("bbox", Text),
    Column("resolution_x", Double),
    Column("resolution_y", Double),
    Column("crs", Text),
    Column("uri", Text),
    Column("acquisition_time", Text),
    Column("metadata", Text),
)

raster_band_metadata = Table(
    "raster_band_metadata",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("layer_name", Text, nullable=False),
    Column("source_name", Text),
    Column("band_index", Integer),
    Column("description", Text),
)

# Weather layers table: tracks downloaded NetCDF and Parquet weather files.
weather_layers = Table(
    "weather_layers",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("layer_name", Text, nullable=False),
    Column("source_name", Text),
    Column("variable", Text),
    Column("file_format", Text),
    Column("valid_from", Text),
    Column("valid_until", Text),
    Column("uri", Text),
    Column("acquisition_time", Text),
    Column("bbox", Text),
    Column("crs", Text),
    Column("metadata", Text),
)

# Regular indexes (no GIST / PostGIS indexes, since bbox is plain text).
Index("idx_raster_source", raster_layers.c.source_name)
Index("idx_band_source", raster_band_metadata.c.source_name)

# Composite index used by GetterWeather to find relevant time windows quickly.
Index(
    "idx_weather_source_variable_time",
    weather_layers.c.source_name,
    weather_layers.c.variable,
    weather_layers.c.valid_from,
    weather_layers.c.valid_until,
)

# Index for per-variable Zarr store lookups in CoverageManager and
# GetterWeather.  Allows fast filtering by file_format so NetCDF and Zarr rows
# are separated without scanning the full table.
Index(
    "idx_weather_source_variable_format",
    weather_layers.c.source_name,
    weather_layers.c.variable,
    weather_layers.c.file_format,
)
