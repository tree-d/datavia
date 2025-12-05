CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS raster_layers (
    id SERIAL PRIMARY KEY,
    layer_name TEXT NOT NULL,
    source_name TEXT,  -- Added for pipeline isolation
    bbox geometry(POLYGON, 4326),
    resolution_x DOUBLE PRECISION,
    resolution_y DOUBLE PRECISION,
    crs TEXT,
    uri TEXT,
    acquisition_time timestamptz,
    metadata TEXT  -- Added for storing additional metadata as string
);

CREATE TABLE IF NOT EXISTS raster_band_metadata (
    id SERIAL PRIMARY KEY,
    layer_name TEXT NOT NULL,
    source_name TEXT,  -- Added for pipeline isolation 
    band_index INTEGER,  -- Changed from band_number to match code usage
    description TEXT
);

CREATE INDEX IF NOT EXISTS idx_raster_bbox ON raster_layers USING GIST (bbox);
CREATE INDEX IF NOT EXISTS idx_raster_source ON raster_layers (source_name);  -- Added for performance
CREATE INDEX IF NOT EXISTS idx_band_source ON raster_band_metadata (source_name);  -- Added for performance
