CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS raster_layers (
    id SERIAL PRIMARY KEY,
    layer_name TEXT NOT NULL,
    source_name TEXT, 
    bbox geometry(POLYGON, 4326),
    resolution_x DOUBLE PRECISION,
    resolution_y DOUBLE PRECISION,
    crs TEXT,
    uri TEXT,
    acquisition_time timestamptz,
    metadata TEXT 
);

CREATE TABLE IF NOT EXISTS raster_band_metadata (
    id SERIAL PRIMARY KEY,
    layer_name TEXT NOT NULL,
    source_name TEXT, 
    band_index INTEGER,
    description TEXT
);

CREATE INDEX IF NOT EXISTS idx_raster_bbox ON raster_layers USING GIST (bbox);
CREATE INDEX IF NOT EXISTS idx_raster_source ON raster_layers (source_name);
CREATE INDEX IF NOT EXISTS idx_band_source ON raster_band_metadata (source_name);
