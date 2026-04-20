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
