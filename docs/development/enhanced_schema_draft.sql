-- DESIGN DRAFT — NOT USED IN PRODUCTION
-- ======================================
-- This file is a design reference for a future schema migration intended to
-- support temporal pipelines (weather, radiation). It is NOT executed by Docker
-- and MUST NOT be placed in datavia/library/database/ (docker-entrypoint-initdb.d).
--
-- Key differences from the active schema (init.sql):
--   - Uses 'data_source' instead of 'source_name' (incompatible with all Python queries)
--   - Adds temporal columns: temporal_type, valid_from, valid_until
--   - Adds audit columns: file_size_mb, checksum, created_at, updated_at
--   - Missing: raster_band_metadata table (required by the soil pipeline)
--
-- Before implementing, this draft must be converted into a proper migration script
-- that renames 'data_source' -> 'source_name' and adds raster_band_metadata.
-- See datavia/library/database/init.sql for the active production schema.
--
-- Enhanced database schema for multiple TIFF strategy
-- Supports both static and temporal data sources

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS raster_layers (
    id SERIAL PRIMARY KEY,
    layer_name TEXT NOT NULL,                    -- e.g., 'TOPOGRAPHY', 'SOIL_MOISTURE'
    data_source TEXT NOT NULL,                   -- e.g., 'TOPOGRAPHY', 'SOIL', 'WEATHER' 
    temporal_type TEXT DEFAULT 'static',         -- 'static', 'daily', 'hourly', 'monthly'
    bbox geometry(POLYGON, 4326),
    resolution_x DOUBLE PRECISION,
    resolution_y DOUBLE PRECISION,
    crs TEXT,
    uri TEXT,                                    -- File path to TIFF
    acquisition_time timestamptz,
    valid_from timestamptz,                      -- Data validity start
    valid_until timestamptz,                     -- Data validity end (NULL for static)
    file_size_mb DOUBLE PRECISION,
    checksum TEXT,                               -- For integrity verification
    created_at timestamptz DEFAULT NOW(),
    updated_at timestamptz DEFAULT NOW()
);

-- Spatial index for efficient bbox queries
CREATE INDEX IF NOT EXISTS idx_raster_bbox ON raster_layers USING GIST (bbox);

-- Index for efficient data source queries
CREATE INDEX IF NOT EXISTS idx_raster_data_source ON raster_layers (data_source);

-- Index for temporal queries (finding latest data)
CREATE INDEX IF NOT EXISTS idx_raster_temporal ON raster_layers (data_source, acquisition_time DESC);

-- Index for validity period queries
CREATE INDEX IF NOT EXISTS idx_raster_validity ON raster_layers (data_source, valid_from, valid_until);

-- Example data
INSERT INTO raster_layers (layer_name, data_source, temporal_type, crs, uri, acquisition_time, valid_from) VALUES
('germany_topography_dgm200', 'TOPOGRAPHY', 'static', 'EPSG:25832', 'data/germany_topography_dgm200.tif', '2024-01-01', '2024-01-01'),
('germany_soil_base', 'SOIL', 'static', 'EPSG:4326', 'data/germany_soil_base.tif', '2024-01-01', '2024-01-01');
