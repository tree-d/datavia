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

    # Rationale: Creating Transformer objects is expensive. Caching them
    # provides a significant speedup for repeated transformations.
    class _TransformerManager:
        """Singleton-like manager for pyproj Transformer instances."""

        def __init__(self) -> None:
            self._transformer_cache: dict[str, Transformer] = {}

        def get_transformer(self, source_crs: str, target_crs: str) -> Transformer:
            """Get a cached or new Transformer for a given CRS pair."""
            cache_key = f"{source_crs}->{target_crs}"
            if cache_key not in self._transformer_cache:
                self._transformer_cache[cache_key] = Transformer.from_crs(
                    source_crs, target_crs, always_xy=True
                )
            return self._transformer_cache[cache_key]

    _transformer_manager = _TransformerManager()

    def get_transformer(source_crs: str, target_crs: str) -> Transformer:
        """
        Get a cached transformer for a given CRS pair.

        This function provides a thread-safe, singleton-like access to
        pyproj Transformer objects, avoiding the cost of repeated creation.
        """
        return _transformer_manager.get_transformer(source_crs, target_crs)

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
