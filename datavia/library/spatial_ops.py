"""Spatial raster operations for pipeline use.

Provides in-place GeoTIFF reprojection. Coordinate-based value extraction and
raster sampling are handled by :func:`datavia.library.interpolation.spatial_interpolate`,
which is called by :class:`datavia.core.getter_tiff.GetterTiff`.
"""

import logging
import os
import tempfile

import numpy as np

from .coordinate_transforms import is_geographic_crs

logger = logging.getLogger(__name__)

# Try to import rasterio - required for TIFF operations
try:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import calculate_default_transform
    from rasterio.warp import reproject as warp_reproject

    RASTERIO_AVAILABLE = True
except ImportError:
    logger.warning("rasterio not available - TIFF operations will be limited")
    RASTERIO_AVAILABLE = False


def reproject_tiff(
    filepath: str,
    target_crs: str,
    resolution_m: int | None = None,
) -> None:
    """Reproject a GeoTIFF in-place to *target_crs*.

    Writes the reprojected raster to a sibling temporary file, then
    atomically replaces the original so the caller always sees a consistent
    file. All bands are reprojected using bilinear resampling.

    For projected (metric) CRSs *resolution_m* is forwarded to
    ``calculate_default_transform`` so the caller can control the output pixel
    size exactly. For geographic CRSs the parameter is ignored and rasterio
    derives an appropriate output resolution automatically (the unit would be
    degrees, not metres).

    Parameters
    ----------
    filepath : str
        Absolute path to the GeoTIFF to reproject. Modified in-place.
    target_crs : str
        Destination CRS string (e.g. ``"EPSG:25832"``).
    resolution_m : int, optional
        Desired output pixel size in metres. Ignored for geographic target
        CRSs. Defaults to ``None`` (rasterio auto-derives the resolution).

    Raises
    ------
    RuntimeError
        If rasterio is not available.
    rasterio.errors.RasterioIOError
        If the file cannot be opened or written.
    Exception
        Any error from the warp operation; the original file is preserved on
        failure.
    """
    if not RASTERIO_AVAILABLE:
        raise RuntimeError("rasterio is not available — cannot reproject GeoTIFF")

    with rasterio.open(filepath) as src:
        src_crs = src.crs
        if src_crs and src_crs.to_string() == target_crs:
            logger.debug(
                "File already in target CRS %s — skipping reprojection.",
                target_crs,
            )
            return

        # Only apply a metric resolution for projected CRSs; in a geographic
        # CRS the unit is degrees and the parameter would produce nonsense.
        use_resolution = resolution_m is not None and not is_geographic_crs(target_crs)
        transform, width, height = calculate_default_transform(
            src_crs,
            target_crs,
            src.width,
            src.height,
            *src.bounds,
            resolution=resolution_m if use_resolution else None,
        )

        meta = src.meta.copy()
        meta.update(crs=target_crs, transform=transform, width=width, height=height)

        dir_name = os.path.dirname(filepath)
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".tif", dir=dir_name)
        os.close(tmp_fd)

        try:
            with (
                rasterio.open(filepath) as src_inner,
                rasterio.open(tmp_path, "w", **meta) as dst,
            ):
                for band_index in range(1, src_inner.count + 1):
                    warp_reproject(
                        source=rasterio.band(src_inner, band_index),
                        destination=rasterio.band(dst, band_index),
                        src_transform=src_inner.transform,
                        src_crs=src_inner.crs,
                        dst_transform=transform,
                        dst_crs=target_crs,
                        resampling=Resampling.bilinear,
                    )
        except Exception:
            os.unlink(tmp_path)
            raise

    os.replace(tmp_path, filepath)
    logger.info(
        "Reprojected %s → %s (resolution_m=%s)", filepath, target_crs, resolution_m
    )
