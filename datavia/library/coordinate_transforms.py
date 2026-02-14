"""
Coordinate transformation library.

Thread-safe coordinate reference system transformations
used by multiple pipelines for coordinate conversions.
"""

import logging

import numpy as np

logger = logging.getLogger(__name__)

# Try to import pyproj for coordinate transformations
try:
    from pyproj import Transformer

    PYPROJ_AVAILABLE = True

    # Cache transformers for common CRS pairs to improve performance
    _transformer_cache = {}

    def get_transformer(source_crs: str, target_crs: str) -> Transformer:
        """Get cached transformer for CRS pair."""
        cache_key = f"{source_crs}->{target_crs}"
        if cache_key not in _transformer_cache:
            _transformer_cache[cache_key] = Transformer.from_crs(
                source_crs, target_crs, always_xy=True
            )
        return _transformer_cache[cache_key]

except ImportError:
    logger.warning(
        "pyproj not available - coordinate transformations will use identity"
    )
    PYPROJ_AVAILABLE = False


def transform_coordinates(
    coords: np.ndarray, source_crs: str, target_crs: str
) -> np.ndarray:
    """Transform coordinates between coordinate reference systems.

    Thread-safe function used by multiple pipelines.

    Parameters
    ----------
    coords : np.ndarray
        Input coordinates as [[x, y], ...] or [[lon, lat], ...]
    source_crs : str
        Source coordinate reference system (e.g., 'EPSG:4326')
    target_crs : str
        Target coordinate reference system (e.g., 'EPSG:25832')

    Returns
    -------
    np.ndarray
        Transformed coordinates in target CRS
    """
    if source_crs == target_crs:
        return coords.copy()

    if not PYPROJ_AVAILABLE:
        logger.warning("pyproj not available - returning coordinates unchanged")
        return coords.copy()

    try:
        transformer = get_transformer(source_crs, target_crs)

        # Transform coordinates using itransform - designed for arrays
        # itransform expects an iterable of coordinate pairs
        transformed_coords = list(transformer.itransform(coords))

        # Convert back to numpy array
        return np.array(transformed_coords)

    except Exception as e:
        logger.error(
            f"Error transforming coordinates from {source_crs} to {target_crs}: {e}"
        )
        return coords.copy()  # Return original coordinates on error
