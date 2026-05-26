"""
getter_tiff.py

Main entry point for TIFF data retrieval.
Uses shared library code for coordinate transformations and spatial queries.
Supports both single-band (default: band 1) and multi-band TIFFs.
"""

import logging
from typing import Any

import numpy as np

from ..library.database.query import (
    check_source_exists,
    get_band_metadata,
    get_raster_metadata,
    get_raster_paths,
)
from ..library.interpolation import spatial_interpolate
from .interfaces import Getter

logger = logging.getLogger(__name__)


class GetterTiff(Getter):
    """Getter class for retrieving data from TIFF files.

    Supports single-band and multi-band GeoTIFFs. For multi-band files the
    desired band can be selected with the ``band`` parameter in
    :meth:`get_data`. Use :meth:`get_band_mapping` to look up which band
    index corresponds to which description/property stored in the database.
    """

    def __init__(self, source_name: str, *args: Any, **kwds: Any) -> None:
        """Initialize getter with source name."""
        self.source_name = source_name

    def get_data(
        self,
        coords: np.ndarray,
        crs_coords: str = "EPSG:4326",
        interpolation_order: int = 3,
        band: int = 1,
        **kwargs: Any,
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
            band (int): Band number to extract (1-indexed). Defaults to 1 for
                        backward compatibility with single-band sources. For
                        multi-band TIFFs use :meth:`get_band_mapping` to find
                        the correct band index for a given property.

        Returns:
            np.ndarray: Extracted raster values at coordinate locations.

        Raises:
            ValueError: If no TIFF paths are found for the source.
            RuntimeError: If data retrieval from TIFF fails.
        """
        coord_type = "lon/lat" if crs_coords == "EPSG:4326" else "x/y"
        logger.debug(
            "Handling TIFF request for %s at %d (%s) coordinate pairs, band=%d",
            self.source_name,
            len(coords),
            coord_type,
            band,
        )

        try:
            # Use library database query functions
            tiff_paths = get_raster_paths(self.source_name)
            logger.debug(
                f"Found {len(tiff_paths)} TIFF files for source: {self.source_name}"
            )

            # Also get metadata for enhanced logging
            metadata_list = get_raster_metadata(self.source_name)
            for metadata in metadata_list:
                logger.debug(
                    "Available layer: %s (CRS: %s)",
                    metadata["layer_name"],
                    metadata["crs"],
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
                # spatial_interpolate handles band selection via the band parameter
                values = spatial_interpolate(
                    tiff_path=tiff_path,
                    coords=coords,
                    coords_crs=crs_coords,
                    interpolation_order=interpolation_order,
                    band=band,
                )
                return values
            except Exception as extract_error:
                logger.warning(
                    f"Failed to extract values from {tiff_path}: {extract_error}"
                )

        raise RuntimeError(
            f"Failed to retrieve data from TIFF for source: {self.source_name}"
        )

    def get_existing_layers(self) -> set[str]:
        """Return layer names already registered in the database for this source.

        Queries the ``raster_layers`` table via
        :func:`~datavia.library.database.query.get_raster_metadata` and
        returns the set of layer names that belong to this source.  An empty
        set is returned when no data has been stored yet or when the database
        is unavailable.

        Returns
        -------
        set[str]
            Layer names present in the database, e.g.
            ``{"elevation_dgm200"}``. Empty when nothing is stored.
        """
        try:
            metadata_list = get_raster_metadata(self.source_name)
            return {m["layer_name"] for m in metadata_list}
        except Exception as exc:
            logger.warning(
                "Could not retrieve existing layers for source '%s': %s",
                self.source_name,
                exc,
            )
            return set()

    def get_registered_uris(self) -> set[str]:
        """Return the set of file URIs currently registered for this source.

        Queries the ``raster_layers`` table for all distinct ``uri`` values
        belonging to this source.  Used by
        :meth:`~datavia.core.interfaces.Pipeline.sync_files_and_database`
        to compare what the database knows about against what is on disk.

        Returns
        -------
        set[str]
            Absolute file paths registered for this source.  Returns an
            empty set when nothing has been stored yet or the database is
            unavailable.
        """
        try:
            metadata_list = get_raster_metadata(self.source_name)
            return {m["uri"] for m in metadata_list if m.get("uri")}
        except Exception as exc:
            logger.warning(
                "Could not retrieve registered URIs for source '%s': %s",
                self.source_name,
                exc,
            )
            return set()

    def get_band_mapping(self, layer_name: str | None = None) -> dict[str, int]:
        """Return a mapping from band description to band index for this source.

        Queries the ``raster_band_metadata`` table (populated by
        :class:`~datavia.core.saver_tiff.TiffSaver` when saving multi-band
        TIFFs). When *layer_name* is ``None`` the bands of all layers for this
        source are merged; the last writer wins on description collision.

        Args:
            layer_name (str | None): Restrict lookup to a specific layer.
                Defaults to ``None`` (all layers for the source).

        Returns:
            dict[str, int]: Mapping of ``{description: band_index}`` as stored
            in the database. Returns an empty dict when no band metadata is
            available (e.g. single-band sources or data not yet downloaded).
        """
        try:
            # get_band_metadata returns {layer_name: [{band_index, description}, ...]}
            all_band_meta = get_band_metadata(self.source_name)

            if not all_band_meta:
                logger.debug(
                    f"No band metadata found for source '{self.source_name}'. "
                    "Source may be single-band or not yet downloaded."
                )
                return {}

            mapping: dict[str, int] = {}

            for lname, bands in all_band_meta.items():
                # Filter to a specific layer when requested
                if layer_name is not None and lname != layer_name:
                    continue
                for band_info in bands:
                    desc = band_info.get("description", "")
                    idx = band_info.get("band_index")
                    if desc and idx is not None:
                        mapping[desc] = int(idx)

            logger.debug(f"Band mapping for source '{self.source_name}': {mapping}")
            return mapping

        except Exception as e:
            logger.warning(
                f"Could not retrieve band mapping for '{self.source_name}': {e}"
            )
            return {}
