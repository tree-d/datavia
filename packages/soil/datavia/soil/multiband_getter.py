"""
MultibandGetter - coverage-ID-based raster retrieval for multi-band soil files.

Extends GetterTiff to resolve a coverage ID (e.g. ``"clay_0-5cm_mean"``) directly
to the specific file URI and band index stored in the database, bypassing the base
class's "try every file" iteration which is unsafe when multiple multi-band GeoTIFFs
exist for the same source (one per alignment group).
"""

import logging

import numpy as np

from datavia.core.getter_tiff import GetterTiff
from datavia.library.database.query import get_band_metadata, get_raster_metadata
from datavia.library.interpolation import spatial_interpolate

logger = logging.getLogger(__name__)


class MultibandGetter(GetterTiff):
    """Getter that resolves coverage IDs to specific files and band indices.

    Extends :class:`~datavia.core.getter_tiff.GetterTiff` so that raster
    sampling is directed to the exact ``(file_uri, band_index)`` pair recorded
    in the database rather than iterating over all files for the source. This
    is required when there are multiple multi-band GeoTIFFs for the same source
    — one per distinct alignment group.

    Band descriptions in the database must be raw coverage IDs (not
    human-readable labels), as written by
    :class:`~datavia.soil.multiband_saver.MultibandSaver`.
    """

    def get_coverage_map(self) -> dict[str, tuple[str, int]]:
        """Build a lookup table from coverage ID to file URI and band index.

        Joins ``raster_band_metadata`` with ``raster_layers`` URIs to produce a
        flat mapping that allows direct file access without iterating over all
        layers. Band descriptions must be raw coverage IDs as stored by
        :class:`~datavia.soil.multiband_saver.MultibandSaver`.

        Returns
        -------
        dict[str, tuple[str, int]]
            Mapping ``{coverage_id: (file_uri, band_index)}``. Returns an
            empty dict when no band metadata is available (e.g. before the
            first download).
        """
        try:
            all_band_meta = get_band_metadata(self.source_name)
            metadata_list = get_raster_metadata(self.source_name)
            layer_to_uri: dict[str, str] = {
                m["layer_name"]: m["uri"] for m in metadata_list if m.get("uri")
            }

            coverage_map: dict[str, tuple[str, int]] = {}
            for layer_name, bands in all_band_meta.items():
                uri = layer_to_uri.get(layer_name)
                if not uri:
                    logger.warning(
                        "Layer '%s' has band metadata but no URI in raster_layers",
                        layer_name,
                    )
                    continue
                for band_info in bands:
                    coverage_id = band_info.get("description", "")
                    band_index = band_info.get("band_index")
                    if coverage_id and band_index is not None:
                        coverage_map[coverage_id] = (uri, int(band_index))

            logger.debug(
                "Coverage map for source '%s': %d entries",
                self.source_name,
                len(coverage_map),
            )
            return coverage_map

        except Exception as exc:
            logger.warning(
                "Could not build coverage map for '%s': %s", self.source_name, exc
            )
            return {}

    def get_stored_coverage_ids(self) -> set[str]:
        """Return all coverage IDs currently registered in the database.

        Convenience wrapper around :meth:`get_coverage_map` for use by the
        pipeline's delta computation.

        Returns
        -------
        set[str]
            Coverage IDs stored for this source, e.g.
            ``{"clay_0-5cm_mean", "sand_5-15cm_mean"}``. Returns an empty set
            before the first download.
        """
        return set(self.get_coverage_map().keys())

    def get_data_for_coverage(
        self,
        coverage_id: str,
        coords: np.ndarray,
        crs_coords: str = "EPSG:4326",
        interpolation_order: int = 3,
    ) -> np.ndarray:
        """Return raster values for a specific coverage ID at the given coordinates.

        Resolves the coverage ID to its ``(file_uri, band_index)`` pair via
        :meth:`get_coverage_map`, then delegates to
        :func:`~datavia.library.interpolation.spatial_interpolate`.

        Parameters
        ----------
        coverage_id : str
            Coverage identifier as stored in the database, e.g.
            ``"clay_0-5cm_mean"``.
        coords : np.ndarray
            Array of coordinates with shape ``(n_points, 2)``.
            Expected column order: ``(longitude, latitude)`` for EPSG:4326.
        crs_coords : str, optional
            CRS of the input coordinates. Defaults to ``"EPSG:4326"``.
        interpolation_order : int, optional
            Interpolation order passed to ``spatial_interpolate``
            (1 = bilinear, 3 = cubic). Defaults to 3.

        Returns
        -------
        np.ndarray
            Interpolated raster values at each coordinate location, shape
            ``(n_points,)``.

        Raises
        ------
        ValueError
            If *coverage_id* is not found in the database.
        RuntimeError
            If raster value extraction fails.
        """
        coverage_map = self.get_coverage_map()
        if coverage_id not in coverage_map:
            available = sorted(coverage_map.keys())
            raise ValueError(
                f"Coverage '{coverage_id}' not found for source '{self.source_name}'. "
                f"Available: {available}"
            )

        uri, band_index = coverage_map[coverage_id]
        logger.debug(
            "Extracting coverage '%s' from band %d of '%s'",
            coverage_id,
            band_index,
            uri,
        )

        try:
            return spatial_interpolate(
                tiff_path=uri,
                coords=coords,
                coords_crs=crs_coords,
                interpolation_order=interpolation_order,
                band=band_index,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to extract coverage '{coverage_id}' from '{uri}': {exc}"
            ) from exc
