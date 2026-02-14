"""
getData.py

This is the main entry point for data retrieval - Enhanced for Phase 1.2.
Now uses shared processor library for coordinate transformations and spatial queries.
"""

import logging

import numpy as np

from ..library import spatial_ops
from ..library.database.query import (
    check_source_exists,
    get_raster_metadata,
    get_raster_paths,
)
from ..library.interpolation import spatial_interpolate
from .interfaces import Getter

logger = logging.getLogger(__name__)


class getter_tiff(Getter):
    """Getter class for retrieving data from TIFF files."""

    def __init__(self, source_name: str, *args, **kwds):
        """Initialize getter with source name."""
        self.source_name = source_name

    def get_data(
        self,
        coords: np.ndarray,
        crs_coords: str = "EPSG:4326",
        interpolation_order: int = 3,
    ) -> np.ndarray:
        """
        Handles requests for TIFF data sources.

        Args:
            coords (np.ndarray): Array of coordinates in specified CRS.
                            - EPSG:4326: (lon, lat) pairs
                            - EPSG:25832: (x, y) pairs
                            - Shape: (n_points, 2)
            crs_coords (str): CRS of input coordinates. Defaults to "EPSG:4326".
            interpolation_order (int): Interpolation order for raster sampling.

        Returns:
            np.ndarray: Extracted raster values at coordinate locations.

        Raises:
            ValueError: If no TIFF paths are found for the source.
            RuntimeError: If data retrieval from TIFF fails.
        """
        coord_type = "lon/lat" if crs_coords == "EPSG:4326" else "x/y"
        logger.debug(
            f"Handling TIFF request for {self.source_name} at {len(coords)} ({coord_type}) coordinate pairs"
        )

        try:
            # Use new Library database query functions
            tiff_paths = get_raster_paths(self.source_name)
            logger.debug(
                f"Found {len(tiff_paths)} TIFF files for source: {self.source_name}"
            )

            # Also get metadata for enhanced logging
            metadata_list = get_raster_metadata(self.source_name)
            for metadata in metadata_list:
                logger.debug(
                    f"Available layer: {metadata['layer_name']} (CRS: {metadata['crs']})"
                )

        except Exception as db_error:
            logger.warning(f"Database query failed: {db_error}")
            tiff_paths = []

        if not tiff_paths:
            # Check if source exists at all
            source_exists = check_source_exists(self.source_name)
            if not source_exists:
                raise ValueError(f"Source '{self.source_name}' not found in database")
            else:
                raise ValueError(f"No TIFF files found for source: {self.source_name}")

        for tiff_path in tiff_paths:
            logger.info(f"Using TIFF file: {tiff_path}")
            try:
                # Pass coordinates with explicit CRS - processor handles coordinate order internally
                values = spatial_interpolate(
                    tiff_path=tiff_path,
                    coords=coords,
                    coords_crs=crs_coords,
                    interpolation_order=interpolation_order,
                )
                return values
            except Exception as extract_error:
                logger.warning(
                    f"Failed to extract values from {tiff_path}: {extract_error}"
                )

        raise RuntimeError(
            f"Failed to retrieve data from TIFF for source: {self.source_name}"
        )
