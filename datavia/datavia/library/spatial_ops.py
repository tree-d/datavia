"""
Complete spatial operations library migrated from processor.

Contains all TIFF/raster operations with full processor functionality:
- Value extraction at coordinates
- Multi-band TIFF processing
- Raster sampling and analysis
- Band metadata handling
- Spatial bounds validation

All functions are thread-safe for parallel pipeline use.
"""

import numpy as np
from typing import Tuple, Dict, Any, Optional, List
import logging

logger = logging.getLogger(__name__)

# Try to import rasterio - required for TIFF operations
try:
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio import features, mask
    from rasterio.warp import reproject, Resampling

    RASTERIO_AVAILABLE = True
except ImportError:
    logger.warning("rasterio not available - TIFF operations will be limited")
    RASTERIO_AVAILABLE = False


def extract_values_at_coords(
    tiff_path: str, coords: np.ndarray, source_crs: str = "EPSG:4326", band: int = 1
) -> np.ndarray:
    """Extract TIFF values at given coordinates.

    Enhanced version from processor with band selection support.
    Thread-safe and used by multiple pipelines simultaneously.

    Parameters
    ----------
    tiff_path : str
        Path to GeoTIFF file
    coords : np.ndarray
        Coordinate array [[lon, lat], ...] in source_crs
    source_crs : str
        Coordinate reference system of input coordinates
    band : int
        Band number to extract (1-indexed)

    Returns
    -------
    np.ndarray
        Values extracted from TIFF at coordinate locations
    """
    if not RASTERIO_AVAILABLE:
        logger.error("rasterio not available - cannot extract TIFF values")
        return np.full(len(coords), np.nan)

    try:
        with rasterio.open(tiff_path) as src:
            # Transform coordinates to TIFF CRS if needed
            if source_crs != src.crs.to_string():
                from .coordinate_transforms import transform_coordinates

                coords_transformed = transform_coordinates(
                    coords, source_crs, src.crs.to_string()
                )
            else:
                coords_transformed = coords

            # Use rasterio's sample method for efficient extraction
            values = list(src.sample(coords_transformed, indexes=band))
            result = np.array(
                [float(val[0]) if val.size > 0 else np.nan for val in values]
            )

            # Handle nodata values
            if src.nodata is not None:
                result = np.where(result == src.nodata, np.nan, result)

            return result

    except Exception as e:
        logger.error(f"Error extracting values from {tiff_path}: {e}")
        return np.full(len(coords), np.nan)


def raster_sample(
    tiff_path: str, coords: np.ndarray, method: str = "nearest"
) -> np.ndarray:
    """Enhanced raster sampling with interpolation methods.

    Migrated from processor with interpolation support.
    """
    if not RASTERIO_AVAILABLE:
        return np.full(len(coords), np.nan)

    try:
        with rasterio.open(tiff_path) as src:
            if method == "nearest":
                return extract_values_at_coords(tiff_path, coords)
            elif method == "bilinear":
                return _bilinear_sample(src, coords)
            else:
                logger.warning(f"Unknown sampling method: {method}, using nearest")
                return extract_values_at_coords(tiff_path, coords)

    except Exception as e:
        logger.error(f"Error in raster sampling: {e}")
        return np.full(len(coords), np.nan)


def _bilinear_sample(src, coords: np.ndarray) -> np.ndarray:
    """Bilinear interpolation sampling."""
    try:
        # Read full array for bilinear interpolation
        data = src.read(1)
        values = []

        for coord in coords:
            # Convert coordinates to pixel indices
            row, col = src.index(coord[0], coord[1])

            # Bilinear interpolation
            if 0 <= row < src.height - 1 and 0 <= col < src.width - 1:
                # Get 4 surrounding pixels
                tl = data[int(row), int(col)]  # top-left
                tr = data[int(row), int(col) + 1]  # top-right
                bl = data[int(row) + 1, int(col)]  # bottom-left
                br = data[int(row) + 1, int(col) + 1]  # bottom-right

                # Fractional parts for interpolation
                row_frac = row - int(row)
                col_frac = col - int(col)

                # Bilinear interpolation
                top = tl * (1 - col_frac) + tr * col_frac
                bottom = bl * (1 - col_frac) + br * col_frac
                value = top * (1 - row_frac) + bottom * row_frac
                values.append(float(value))
            else:
                values.append(np.nan)

        return np.array(values)

    except Exception as e:
        logger.error(f"Bilinear sampling failed: {e}")
        return np.full(len(coords), np.nan)


def process_multiband_tiff(
    tiff_path: str, band_descriptions: List[str] = None
) -> Dict[str, Any]:
    """Process multi-band TIFF with enhanced metadata extraction.

    Migrated from aggregators functionality in fetcher.
    """
    if not RASTERIO_AVAILABLE:
        return {}

    try:
        with rasterio.open(tiff_path) as src:
            info = {
                "band_count": src.count,
                "shape": (src.height, src.width),
                "crs": src.crs.to_string() if src.crs else None,
                "bounds": src.bounds,
                "bands": [],
            }

            # Process each band
            for i in range(1, src.count + 1):
                band_info = {
                    "band_number": i,
                    "dtype": str(src.dtypes[i - 1]),
                    "nodata": src.nodatavals[i - 1] if src.nodatavals else None,
                }

                # Get band description
                try:
                    desc = src.get_band_description(i)
                    if not desc and band_descriptions and len(band_descriptions) >= i:
                        desc = band_descriptions[i - 1]
                    if not desc:
                        desc = f"band_{i}"

                    band_info["description"] = _enhance_band_description(desc)
                except Exception:
                    band_info["description"] = f"band_{i}"

                info["bands"].append(band_info)

            return info

    except Exception as e:
        logger.error(f"Error processing multiband TIFF: {e}")
        return {}


def _enhance_band_description(desc: str) -> str:
    """Enhance band description with property metadata.

    Migrated from aggregators._enhance_band_description()
    """
    try:
        if not desc or "_" not in desc:
            return desc

        parts = desc.split("_")
        if len(parts) >= 3:
            property_name = parts[0]
            depth = parts[1]
            statistic = parts[2]

            property_info = {
                "clay": "Clay content (%)",
                "sand": "Sand content (%)",
                "silt": "Silt content (%)",
                "phh2o": "pH in water",
                "carbon": "Organic carbon content (‰)",
                "soc": "Soil organic carbon (g/kg)",
                "nitrogen": "Total nitrogen content (g/kg)",
            }

            prop_desc = property_info.get(property_name, property_name)
            return f"{prop_desc} at {depth} depth ({statistic} value)"

        return desc

    except Exception as e:
        logger.debug(f"Failed to enhance band description '{desc}': {e}")
        return desc


def read_geotiff_metadata(tiff_path: str) -> Dict[str, Any]:
    """Read GeoTIFF metadata without loading data.

    This function is used by TiffSaver for metadata extraction.

    Parameters
    ----------
    tiff_path : str
        Path to GeoTIFF file

    Returns
    -------
    Dict[str, Any]
        Metadata dictionary with CRS, bounds, shape, etc.
    """
    if not RASTERIO_AVAILABLE:
        logger.error("rasterio not available - cannot read TIFF metadata")
        return {}

    try:
        with rasterio.open(tiff_path) as src:
            return {
                "crs": src.crs.to_string() if src.crs else "EPSG:4326",
                "bounds": src.bounds,
                "shape": (src.height, src.width),
                "transform": src.transform,
                "dtype": str(src.dtypes[0]),
                "nodata": src.nodata,
                "count": src.count,
            }
    except Exception as e:
        logger.error(f"Error reading metadata from {tiff_path}: {e}")
        return {}


def get_geotiff_bounds(tiff_path: str) -> Optional[Tuple[float, float, float, float]]:
    """Get spatial bounds of GeoTIFF.

    Parameters
    ----------
    tiff_path : str
        Path to GeoTIFF file

    Returns
    -------
    Optional[Tuple[float, float, float, float]]
        Bounds as (left, bottom, right, top) or None if error
    """
    if not RASTERIO_AVAILABLE:
        return None

    try:
        with rasterio.open(tiff_path) as src:
            return src.bounds
    except Exception as e:
        logger.error(f"Error getting bounds from {tiff_path}: {e}")
        return None


def validate_coordinates_in_bounds(
    coords: np.ndarray, bounds: Tuple[float, float, float, float]
) -> np.ndarray:
    """Check which coordinates are within given bounds.

    Parameters
    ----------
    coords : np.ndarray
        Coordinate array [[lon, lat], ...]
    bounds : Tuple[float, float, float, float]
        Bounds as (left, bottom, right, top)

    Returns
    -------
    np.ndarray
        Boolean array indicating which coordinates are within bounds
    """
    left, bottom, right, top = bounds

    lon_valid = (coords[:, 0] >= left) & (coords[:, 0] <= right)
    lat_valid = (coords[:, 1] >= bottom) & (coords[:, 1] <= top)

    return lon_valid & lat_valid
