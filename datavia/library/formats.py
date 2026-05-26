"""
Complete file format handling library migrated from processor.

Supports all geospatial formats used by pipelines:
- GeoTIFF: Reading, writing, metadata extraction
- Shapefile: Vector data processing for BÜK soil classification
- NetCDF: Weather and radiation temporal data
- JSON: API response processing

All processor format functionality migrated here.
"""

import contextlib
import logging
from typing import Any

import numpy as np
from rasterio.transform import from_bounds

logger = logging.getLogger(__name__)

# Import dependencies with fallbacks
try:
    import rasterio

    RASTERIO_AVAILABLE = True
except ImportError:
    RASTERIO_AVAILABLE = False

try:
    import fiona

    FIONA_AVAILABLE = True
except ImportError:
    FIONA_AVAILABLE = False

try:
    import shapely.geometry as geom

    SHAPELY_AVAILABLE = True
except ImportError:
    SHAPELY_AVAILABLE = False

VECTOR_AVAILABLE = FIONA_AVAILABLE and SHAPELY_AVAILABLE

try:
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

                    with contextlib.suppress(Exception):
                        band_meta["description"] = src.get_band_description(i)

                    bands.append(band_meta)

                metadata["bands"] = bands

            return metadata

    except Exception as e:
        logger.error("Error reading TIFF metadata from %s: %s", filepath, e)
        return {}


def write_tiff_data(
    data: np.ndarray,
    filepath: str,
    crs: str = "EPSG:4326",
    transform: Any = None,
    nodata: float | None = None,
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
            logger.error("Unsupported array dimensions: %s", data.ndim)
            return False

        # Create transform if not provided
        if transform is None:
            # Simple identity transform - should be provided by caller

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

        logger.info("Successfully wrote TIFF: %s", filepath)
        return True

    except Exception as e:
        logger.error("Error writing TIFF to %s: %s", filepath, e)
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
                logger.warning("TIFF %s has no CRS information", filepath)

            return True

    except Exception as e:
        logger.error("TIFF validation failed for %s: %s", filepath, e)
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
        logger.error("Error reading shapefile %s: %s", filepath, e)
        return {}


def query_shapefile_by_coords(
    filepath: str, coords: np.ndarray
) -> list[dict[str, Any]]:
    """Query shapefile features by coordinate points (for BÜK classification)."""
    if not VECTOR_AVAILABLE:
        return []

    try:
        results: list[dict[str, Any]] = []
        points = [geom.Point(coord[0], coord[1]) for coord in coords]

        with fiona.open(filepath) as src:
            for point in points:
                found: dict[str, Any] | None = None
                for feature in src:
                    polygon = geom.shape(feature["geometry"])
                    if polygon.contains(point):
                        found = {
                            "properties": feature["properties"],
                            "coordinates": [point.x, point.y],
                        }
                        break
                if found is not None:
                    results.append(found)

        return results

    except Exception as e:
        logger.error("Error querying shapefile by coordinates: %s", e)
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
        logger.error("Error reading NetCDF metadata from %s: %s", filepath, e)
        return {}


def extract_netcdf_layer_metadata(filepath: str) -> dict[str, Any]:
    """Extract the DB-insert fields from a CF-compliant NetCDF weather file.

    Reads just the coordinate metadata without loading full data arrays and
    returns the fields required to populate a ``weather_layers`` database row.

    Parameters
    ----------
    filepath : str
        Absolute path to the NetCDF file.

    Returns
    -------
    dict[str, Any]
        Dictionary with keys:

        - ``valid_from`` (str): ISO-8601 start of the time dimension, or
          ``None`` when the file has no time coordinate.
        - ``valid_until`` (str): ISO-8601 end of the time dimension.
        - ``variables`` (list[str]): Data variable names.
        - ``crs`` (str): CRS string; defaults to ``"EPSG:4326"`` (ERA5 standard).
        - ``bbox`` (str): WKT POLYGON bounding box, or ``None`` when spatial
          coordinates are absent.
        - ``resolution_x`` (float | None): Longitude step.
        - ``resolution_y`` (float | None): Latitude step.

        Returns an empty dict when the file cannot be read or xarray is absent.
    """
    if not NETCDF_AVAILABLE:
        logger.error("xarray/netCDF4 not available — cannot extract NetCDF metadata")
        return {}

    try:
        with xr.open_dataset(filepath) as ds:
            # --- Time bounds ---
            valid_from: str | None = None
            valid_until: str | None = None
            if "time" in ds.coords:
                time_vals = ds.coords["time"]
                valid_from = str(time_vals.min().values)
                valid_until = str(time_vals.max().values)

            # --- Spatial bounds & resolution ---
            lat_name = "latitude" if "latitude" in ds.coords else "lat"
            lon_name = "longitude" if "longitude" in ds.coords else "lon"
            bbox_wkt: str | None = None
            resolution_x: float | None = None
            resolution_y: float | None = None

            if lat_name in ds.coords and lon_name in ds.coords:
                lats = ds.coords[lat_name].values
                lons = ds.coords[lon_name].values
                west = float(lons.min())
                east = float(lons.max())
                south = float(lats.min())
                north = float(lats.max())
                bbox_wkt = (
                    f"POLYGON (({west} {south}, {east} {south}, "
                    f"{east} {north}, {west} {north}, {west} {south}))"
                )
                # Guard against 2-D auxiliary coordinate arrays (e.g. HYRAS
                # ETRS89-LAEA files store lat/lon as 2-D fields alongside the
                # projected x/y dimensions).  Resolution is only meaningful for
                # 1-D coordinate axes.
                if lons.ndim == 1 and len(lons) > 1:
                    resolution_x = float(abs(lons[1] - lons[0]))
                if lats.ndim == 1 and len(lats) > 1:
                    resolution_y = float(abs(lats[1] - lats[0]))

            # ERA5 geographic-coordinate files carry a CF convention string
            # (e.g. "latitude_longitude") in grid_mapping_name, not an EPSG
            # code.  Always default to EPSG:4326 for ERA5 data.
            return {
                "valid_from": valid_from,
                "valid_until": valid_until,
                "variables": list(ds.data_vars.keys()),
                "crs": "EPSG:4326",
                "bbox": bbox_wkt,
                "resolution_x": resolution_x,
                "resolution_y": resolution_y,
            }

    except Exception as e:
        logger.error("Error extracting NetCDF layer metadata from %s: %s", filepath, e)
        return {}


def write_parquet(records: list[dict[str, Any]], path: str) -> None:
    """Write a list of station observation dicts to a Parquet file.

    Uses ``pandas.DataFrame.to_parquet`` with snappy compression. If the
    target file already exists it is **overwritten**.

    Parameters
    ----------
    records : list[dict[str, Any]]
        List of observation dictionaries. All dicts must share the same
        keys (they will become data-frame columns).
    path : str
        Absolute destination path for the Parquet file.

    Raises
    ------
    ImportError
        If pandas or pyarrow is not installed.
    ValueError
        If *records* is empty.
    """
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError(
            "pandas is required for write_parquet. "
            "Install datavia-weather or run `pip install pandas pyarrow`."
        ) from exc

    if not records:
        raise ValueError("records must not be empty.")

    df = pd.DataFrame(records)
    df.to_parquet(path, engine="pyarrow", compression="snappy", index=False)
    logger.info("Wrote %d records to Parquet: %s", len(records), path)


def read_parquet_time_range(
    path: str,
    variable: str,
    from_dt: str,
    to_dt: str,
) -> Any:
    """Read station observations filtered to a time range from a Parquet file.

    Parameters
    ----------
    path : str
        Absolute path to the Parquet file.
    variable : str
        Variable column to retain (alongside ``datetime``, ``latitude``,
        ``longitude``, and ``station_id`` when present).
    from_dt : str
        Start of the time range (ISO-8601 datetime string, inclusive).
    to_dt : str
        End of the time range (ISO-8601 datetime string, inclusive).

    Returns
    -------
    pandas.DataFrame
        Observations in the requested window. Empty when no rows match.

    Raises
    ------
    ImportError
        If pandas or pyarrow is not installed.
    KeyError
        If *variable* column is absent in the Parquet file.
    """
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError(
            "pandas is required for read_parquet_time_range. "
            "Install datavia-weather or run `pip install pandas pyarrow`."
        ) from exc

    df = pd.read_parquet(path, engine="pyarrow")

    if variable not in df.columns:
        raise KeyError(
            f"Variable column '{variable}' not found in '{path}'. "
            f"Available columns: {list(df.columns)}"
        )

    df["datetime"] = pd.to_datetime(df["datetime"])
    mask = (df["datetime"] >= pd.Timestamp(from_dt)) & (
        df["datetime"] <= pd.Timestamp(to_dt)
    )
    return df.loc[mask].reset_index(drop=True)


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
                logger.error("Variable %s not found in NetCDF", variable)
                return {}

            # Select time range if specified
            if time_range:
                selected_ds = ds.sel(time=slice(time_range[0], time_range[1]))
            else:
                selected_ds = ds

            # Extract data at coordinates
            results: dict[str, Any] = {}
            for i, coord in enumerate(coords):
                try:
                    # Select nearest grid point
                    point_data = selected_ds.sel(
                        longitude=coord[0], latitude=coord[1], method="nearest"
                    )[variable]

                    results[f"point_{i}"] = {
                        "coordinates": coord.tolist(),
                        "values": point_data.values.tolist(),
                        "time": (
                            selected_ds.time.values.tolist()
                            if "time" in selected_ds.coords
                            else None
                        ),
                    }

                except Exception as e:
                    logger.warning(
                        "Failed to extract data for coordinate %s: %s", coord, e
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
        logger.error("Error processing NetCDF temporal data: %s", e)
        return {}
