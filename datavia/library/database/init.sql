-- Datavia schema — compatible with SQLite (default) and PostgreSQL.
-- For SQLite, bbox is stored as WKT text — for PostgreSQL it is also text
-- (no PostGIS geometry column is used so no extension is required).

CREATE TABLE IF NOT EXISTS raster_layers (
    id INTEGER PRIMARY KEY,
    layer_name TEXT NOT NULL,
    source_name TEXT,
    bbox TEXT,
    resolution_x DOUBLE PRECISION,
    resolution_y DOUBLE PRECISION,
    crs TEXT,
    uri TEXT,
    acquisition_time TEXT,
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS raster_band_metadata (
    id INTEGER PRIMARY KEY,
    layer_name TEXT NOT NULL,
    source_name TEXT,
    band_index INTEGER,
    description TEXT
);

-- Regular indexes (GIST / PostGIS indexes are not supported in SQLite)
CREATE INDEX IF NOT EXISTS idx_raster_source ON raster_layers (source_name);
CREATE INDEX IF NOT EXISTS idx_band_source ON raster_band_metadata (source_name);

-- Weather layers table: tracks downloaded NetCDF and Parquet weather files.
-- valid_from / valid_until use ISO-8601 datetime strings so the table works
-- with both SQLite (text comparison) and PostgreSQL (text or TIMESTAMPTZ).
CREATE TABLE IF NOT EXISTS weather_layers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    layer_name TEXT NOT NULL,
    source_name TEXT,
    variable TEXT,
    file_format TEXT,
    valid_from TEXT,
    valid_until TEXT,
    uri TEXT,
    acquisition_time TEXT,
    bbox TEXT,
    crs TEXT,
    metadata TEXT
);

-- Composite index used by GetterWeather to find relevant time windows quickly.
CREATE INDEX IF NOT EXISTS idx_weather_source_variable_time
    ON weather_layers (source_name, variable, valid_from, valid_until);

-- Index for per-variable Zarr store lookups in CoverageManager and GetterWeather.
-- Allows fast filtering by file_format so NetCDF and Zarr rows are separated
-- without scanning the full table.
CREATE INDEX IF NOT EXISTS idx_weather_source_variable_format
    ON weather_layers (source_name, variable, file_format);
