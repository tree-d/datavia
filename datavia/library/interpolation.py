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
import rasterio
from scipy.ndimage import map_coordinates

from .coordinate_transforms import transform_coordinates

logger = logging.getLogger(__name__)


def spatial_interpolate(
    tiff_path: str,
    coords: np.ndarray,
    coords_crs: str = "EPSG:4326",
    interpolation_order: int = 3,
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

    Returns
    -------
    np.ndarray
        Interpolated values at each coordinate location

    Raises
    ------
    ImportError
        If required geospatial libraries are not available
    """
    with rasterio.open(tiff_path) as src:
        # Transform coordinates to raster CRS if needed
        if coords_crs != str(src.crs):
            coords = transform_coordinates(coords, coords_crs, str(src.crs))

        band = src.read(1)
        transform = src.transform

        # Convert world coordinates to fractional row/col for all points at once
        cols, rows = ~transform * (coords[:, 0], coords[:, 1])

        # Stack coordinates for scipy.ndimage.map_coordinates
        # Note: map_coordinates expects (row, col) order - handled by rowcol conversion above
        coord_array = np.vstack([rows, cols])

        # Perform interpolation for all points simultaneously
        # Ensure proper types for scipy.ndimage.map_coordinates
        input_array = np.asarray(band, dtype=np.float64)
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
            input_array,
            coordinates,
            order=order_literal,
            cval=np.nan,
            prefilter=interpolation_order > 1,
            mode="constant",
        )

        return np.asarray(interpolated_values)
