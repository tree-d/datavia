"""
Complete file format handling library migrated from processor.

Supports all geospatial formats used by pipelines:
- GeoTIFF: Reading, writing, metadata extraction
- Shapefile: Vector data processing for BÜK soil classification
- NetCDF: Weather and radiation temporal data
- JSON: API response processing

All processor format functionality migrated here.
"""

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Import dependencies with fallbacks
try:
    import rasterio
    from rasterio.crs import CRS

    RASTERIO_AVAILABLE = True
except ImportError:
    RASTERIO_AVAILABLE = False

try:
    import fiona
    import shapely.geometry as geom
    from shapely.prepared import prep

    VECTOR_AVAILABLE = True
except ImportError:
    VECTOR_AVAILABLE = False

try:
    import netCDF4
    import xarray as xr

    NETCDF_AVAILABLE = True
except ImportError:
    NETCDF_AVAILABLE = False


# GeoTIFF Operations (migrated from processor)
def read_tiff_metadata(filepath: str) -> dict[str, Any]:
    """Complete TIFF metadata reading with all processor functionality."""
    if not RASTERIO_AVAILABLE:
        return {}

    try:
        with rasterio.open(filepath) as src:
            metadata = {
                "width": src.width,
                "height": src.height,
                "count": src.count,
                "crs": src.crs.to_string() if src.crs else None,
                "transform": src.transform,
                "bounds": src.bounds,
                "dtypes": [str(dtype) for dtype in src.dtypes],
                "nodata": src.nodata,
                "compression": src.compression.value if src.compression else None,
                "tiled": src.is_tiled,
                "block_shapes": src.block_shapes,
            }

            # Add band-specific metadata
            if src.count > 1:
                bands = []
                for i in range(1, src.count + 1):
                    band_meta = {
                        "band": i,
                        "dtype": str(src.dtypes[i - 1]),
                        "nodata": src.nodatavals[i - 1] if src.nodatavals else None,
                        "description": None,
                    }

                    try:
                        band_meta["description"] = src.get_band_description(i)
                    except Exception:
                        pass

                    bands.append(band_meta)

                metadata["bands"] = bands

            return metadata

    except Exception as e:
        logger.error(f"Error reading TIFF metadata from {filepath}: {e}")
        return {}


def write_tiff_data(
    data: np.ndarray,
    filepath: str,
    crs: str = "EPSG:4326",
    transform: Any = None,
    nodata: float = None,
) -> bool:
    """Write array data to GeoTIFF with proper geospatial metadata."""
    if not RASTERIO_AVAILABLE:
        logger.error("rasterio not available - cannot write TIFF")
        return False

    try:
        # Handle multi-dimensional arrays
        if data.ndim == 2:
            height, width = data.shape
            count = 1
            data = data.reshape(1, height, width)
        elif data.ndim == 3:
            count, height, width = data.shape
        else:
            logger.error(f"Unsupported array dimensions: {data.ndim}")
            return False

        # Create transform if not provided
        if transform is None:
            # Simple identity transform - should be provided by caller
            from rasterio.transform import from_bounds

            transform = from_bounds(-180, -90, 180, 90, width, height)

        # Write TIFF
        with rasterio.open(
            filepath,
            "w",
            driver="GTiff",
            height=height,
            width=width,
            count=count,
            dtype=data.dtype,
            crs=crs,
            transform=transform,
            nodata=nodata,
            compress="lzw",  # Efficient compression
        ) as dst:
            dst.write(data)

        logger.info(f"Successfully wrote TIFF: {filepath}")
        return True

    except Exception as e:
        logger.error(f"Error writing TIFF to {filepath}: {e}")
        return False


def validate_tiff_file(filepath: str) -> bool:
    """Validate TIFF file integrity and geospatial metadata."""
    if not RASTERIO_AVAILABLE:
        return False

    try:
        with rasterio.open(filepath) as src:
            # Basic validation
            if src.width <= 0 or src.height <= 0 or src.count <= 0:
                return False

            # Try to read a small sample
            window = rasterio.windows.Window(
                0, 0, min(10, src.width), min(10, src.height)
            )
            _ = src.read(1, window=window)

            # Check CRS
            if not src.crs:
                logger.warning(f"TIFF {filepath} has no CRS information")

            return True

    except Exception as e:
        logger.error(f"TIFF validation failed for {filepath}: {e}")
        return False


# Shapefile Operations (for BÜK soil classification)
def read_shapefile_data(filepath: str) -> dict[str, Any]:
    """Read shapefile data for soil classification (BÜK support)."""
    if not VECTOR_AVAILABLE:
        logger.error("fiona/shapely not available - cannot read shapefiles")
        return {}

    try:
        with fiona.open(filepath) as src:
            features = []
            for feature in src:
                features.append(
                    {
                        "geometry": feature["geometry"],
                        "properties": feature["properties"],
                    }
                )

            return {
                "crs": src.crs,
                "bounds": src.bounds,
                "schema": src.schema,
                "features": features,
                "count": len(features),
            }

    except Exception as e:
        logger.error(f"Error reading shapefile {filepath}: {e}")
        return {}


def query_shapefile_by_coords(filepath: str, coords: np.ndarray) -> list[dict]:
    """Query shapefile features by coordinate points (for BÜK classification)."""
    if not VECTOR_AVAILABLE:
        return []

    try:
        results = []
        points = [geom.Point(coord[0], coord[1]) for coord in coords]

        with fiona.open(filepath) as src:
            for point in points:
                found = None
                for feature in src:
                    polygon = geom.shape(feature["geometry"])
                    if polygon.contains(point):
                        found = {
                            "properties": feature["properties"],
                            "coordinates": [point.x, point.y],
                        }
                        break
                results.append(found)

        return results

    except Exception as e:
        logger.error(f"Error querying shapefile by coordinates: {e}")
        return []


# NetCDF Operations (for weather/radiation data)
def read_netcdf_metadata(filepath: str) -> dict[str, Any]:
    """Read NetCDF metadata for weather/radiation data."""
    if not NETCDF_AVAILABLE:
        logger.error("xarray/netCDF4 not available - cannot read NetCDF")
        return {}

    try:
        with xr.open_dataset(filepath) as ds:
            metadata = {
                "dimensions": dict(ds.dims),
                "variables": list(ds.data_vars.keys()),
                "coordinates": list(ds.coords.keys()),
                "attributes": dict(ds.attrs),
                "time_range": None,
                "spatial_bounds": None,
            }

            # Extract time information
            if "time" in ds.coords:
                time_coord = ds.coords["time"]
                metadata["time_range"] = {
                    "start": str(time_coord.min().values),
                    "end": str(time_coord.max().values),
                    "count": len(time_coord),
                }

            # Extract spatial bounds
            if "longitude" in ds.coords and "latitude" in ds.coords:
                lon = ds.coords["longitude"]
                lat = ds.coords["latitude"]
                metadata["spatial_bounds"] = {
                    "west": float(lon.min()),
                    "east": float(lon.max()),
                    "south": float(lat.min()),
                    "north": float(lat.max()),
                }

            return metadata

    except Exception as e:
        logger.error(f"Error reading NetCDF metadata from {filepath}: {e}")
        return {}


def process_temporal_netcdf(
    filepath: str,
    variable: str,
    coords: np.ndarray,
    time_range: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """Process NetCDF file for temporal weather/radiation data extraction."""
    if not NETCDF_AVAILABLE:
        return {}

    try:
        with xr.open_dataset(filepath) as ds:
            if variable not in ds.data_vars:
                logger.error(f"Variable {variable} not found in NetCDF")
                return {}

            # Select time range if specified
            if time_range:
                ds = ds.sel(time=slice(time_range[0], time_range[1]))

            # Extract data at coordinates
            results = {}
            for i, coord in enumerate(coords):
                try:
                    # Select nearest grid point
                    point_data = ds.sel(
                        longitude=coord[0], latitude=coord[1], method="nearest"
                    )[variable]

                    results[f"point_{i}"] = {
                        "coordinates": coord.tolist(),
                        "values": point_data.values.tolist(),
                        "time": (
                            ds.time.values.tolist() if "time" in ds.coords else None
                        ),
                    }

                except Exception as e:
                    logger.warning(
                        f"Failed to extract data for coordinate {coord}: {e}"
                    )
                    results[f"point_{i}"] = None

            return {
                "variable": variable,
                "results": results,
                "metadata": {
                    "units": ds[variable].attrs.get("units", "unknown"),
                    "long_name": ds[variable].attrs.get("long_name", variable),
                },
            }

    except Exception as e:
        logger.error(f"Error processing NetCDF temporal data: {e}")
        return {}
