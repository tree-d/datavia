"""
Coordinate transformation library.

Thread-safe coordinate reference system transformations
used by multiple pipelines for coordinate conversions.
"""

import logging

import numpy as np

logger = logging.getLogger(__name__)

# Try to import rasterio.warp for bounds transformation
try:
    from rasterio.warp import transform_bounds as _rasterio_transform_bounds

    RASTERIO_WARP_AVAILABLE = True
except ImportError:
    logger.warning("rasterio.warp not available - transform_bbox will raise")
    RASTERIO_WARP_AVAILABLE = False

# Try to import pyproj for coordinate transformations
try:
    from pyproj import CRS as PROJ_CRS
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


def is_geographic_crs(crs_str: str) -> bool:
    """Return ``True`` if *crs_str* identifies a geographic (angular-unit) CRS.

    A geographic CRS uses angular units (degrees) such as EPSG:4326, while a
    projected CRS uses linear units (metres) such as EPSG:25832. The
    distinction is used to decide whether a pixel resolution expressed in
    metres is applicable directly to the CRS.

    Parameters
    ----------
    crs_str : str
        Any CRS string accepted by pyproj (e.g. ``"EPSG:4326"``,
        ``"urn:ogc:def:crs:EPSG::25832"``).

    Returns
    -------
    bool
        ``True`` for geographic CRSs, ``False`` for projected CRSs or when
        pyproj is unavailable.

    Raises
    ------
    Exception
        Errors from pyproj are caught and logged; the function returns
        ``False`` on failure so callers can proceed conservatively.
    """
    if not PYPROJ_AVAILABLE:
        logger.warning(
            "pyproj not available - cannot determine if CRS %s is geographic; "
            "assuming projected",
            crs_str,
        )
        return False
    try:
        return PROJ_CRS.from_string(crs_str).is_geographic
    except Exception as exc:
        logger.error("Could not parse CRS %s: %s", crs_str, exc)
        return False


def transform_bbox(
    bounds: tuple[float, float, float, float],
    source_crs: str,
    target_crs: str,
) -> tuple[float, float, float, float]:
    """Reproject a bounding box from *source_crs* to *target_crs*.

    Uses ``rasterio.warp.transform_bounds`` internally, which densifies the
    boundary into intermediate points before projecting. This avoids the
    accuracy loss that occurs when only the four corner points are transformed,
    and is particularly important for large bounding boxes or when crossing
    zone boundaries.

    Parameters
    ----------
    bounds : tuple[float, float, float, float]
        Bounding box as ``(left, bottom, right, top)`` in *source_crs*.
    source_crs : str
        Source CRS string (e.g. ``"EPSG:4326"``).
    target_crs : str
        Target CRS string (e.g. ``"EPSG:25832"``).

    Returns
    -------
    tuple[float, float, float, float]
        Reprojected bounding box as ``(left, bottom, right, top)`` in
        *target_crs*.

    Raises
    ------
    RuntimeError
        If ``rasterio.warp`` is not available.
    Exception
        Any error raised by ``rasterio.warp.transform_bounds``.
    """
    if not RASTERIO_WARP_AVAILABLE:
        raise RuntimeError(
            "rasterio.warp is not available - cannot reproject bounding box"
        )
    left, bottom, right, top = bounds
    return _rasterio_transform_bounds(source_crs, target_crs, left, bottom, right, top)


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
