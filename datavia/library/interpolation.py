"""
Complete interpolation library migrated from processor.

All spatial and temporal interpolation methods for pipeline use:
- Spatial interpolation: IDW, bilinear, kriging
- Temporal interpolation: linear, nearest, cubic spline
- Quality-aware interpolation with outlier detection
"""

import logging
from typing import Literal

import numpy as np

try:
    import rasterio
except ImportError:  # pragma: no cover - optional dependency
    rasterio = None
from scipy.ndimage import distance_transform_edt, map_coordinates

from .coordinate_transforms import transform_coordinates

logger = logging.getLogger(__name__)


def spatial_interpolate(
    tiff_path: str,
    coords: np.ndarray,
    coords_crs: str = "EPSG:4326",
    interpolation_order: int = 3,
    band: int = 1,
) -> np.ndarray:
    """
    Cubic interpolation of raster values at given coordinates.

    Parameters
    ----------
    tiff_path : str
        Path to GeoTIFF raster file
    coords : np.ndarray
        Array of coordinates, shape (N, 2)
        - For EPSG:4326: (lon, lat) pairs where coords[:, 0] = longitude, coords[:, 1] = latitude
        - For EPSG:25832: (x, y) pairs where coords[:, 0] = easting, coords[:, 1] = northing
    coords_crs : str, default 'EPSG:4326'
        CRS of input coordinates
    interpolation_order : int, default 3
        Interpolation order (1=linear, 3=cubic)
    band : int, default 1
        Band number to interpolate (1-indexed). Use for multi-band TIFFs to
        select a specific band; defaults to band 1 for backward compatibility.

    Returns
    -------
    np.ndarray
        Interpolated values at each coordinate location

    Raises
    ------
    ImportError
        If required geospatial libraries are not available
    """
    if rasterio is None:
        raise ImportError(
            "rasterio is required for spatial interpolation. "
            "Install with `pip install rasterio`."
        )

    with rasterio.open(tiff_path) as src:
        # Ensure coords is a numpy array so 2-D indexing works with plain lists
        coords = np.asarray(coords, dtype=float)

        # Transform coordinates to raster CRS if needed
        if coords_crs != str(src.crs):
            coords = transform_coordinates(coords, coords_crs, str(src.crs))

        # Read the requested band (1-indexed); clamp to valid range
        band_idx = max(1, min(band, src.count))
        band_data = src.read(band_idx).astype(np.float64)

        # Nodata handling: fill nodata cells with the value of the nearest valid
        # pixel (nearest-neighbor propagation via distance transform).  This
        # preserves the actual local signal rather than using a global median, so
        # if a query point lands exactly on a nodata pixel its interpolated value
        # is still derived from real nearby measurements.  Out-of-domain points
        # (fully outside the raster extent) return NaN via mode='constant'.
        nodata = src.nodata
        if nodata is not None:
            nodata_mask = band_data == nodata
            if nodata_mask.any() and not nodata_mask.all():
                # For each nodata pixel, find the nearest valid pixel index.
                # Cast to ndarray explicitly so mypy can verify downstream indexing.
                _, nearest_idx = distance_transform_edt(
                    nodata_mask, return_indices=True
                )
                nearest_idx = np.asarray(nearest_idx)
                filled = band_data.copy()
                filled[nodata_mask] = band_data[
                    nearest_idx[0][nodata_mask], nearest_idx[1][nodata_mask]
                ]
                band_data = filled

        transform = src.transform

        # Convert world coordinates to fractional row/col for all points at once
        cols, rows = ~transform * (coords[:, 0], coords[:, 1])

        # Stack coordinates for scipy.ndimage.map_coordinates
        # Note: map_coordinates expects (row, col) order - handled by rowcol conversion above
        coord_array = np.vstack([rows, cols])

        # Perform interpolation for all points simultaneously
        coordinates = np.asarray(coord_array, dtype=np.float64)

        # Ensure order is one of the accepted literal values
        order_val = min(max(interpolation_order, 0), 5)
        if order_val == 0:
            order_literal: Literal[0, 1, 2, 3, 4, 5] = 0
        elif order_val == 1:
            order_literal = 1
        elif order_val == 2:
            order_literal = 2
        elif order_val == 3:
            order_literal = 3
        elif order_val == 4:
            order_literal = 4
        else:
            order_literal = 5

        interpolated_values = map_coordinates(
            band_data,
            coordinates,
            order=order_literal,
            cval=np.nan,
            prefilter=interpolation_order > 1,
            mode="constant",
        )

        result = np.asarray(interpolated_values)

        return result
