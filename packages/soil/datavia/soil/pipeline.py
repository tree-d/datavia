"""
Soil Pipeline - Complete soil data integration.

Provides end-to-end access to SoilGrids soil property data for Germany:
- Downloads selected soil properties via the SoilGrids WCS API.
- Stores results as a multi-band GeoTIFF and registers metadata in PostGIS.
- Exposes coordinate-based value retrieval via a GetterTiff-backed interface.

Planned extensions:
- BÜK shapefile integration (vector-based soil classification).
"""

import logging
import os
import tempfile

import numpy as np

from datavia.config import get_config
from datavia.core.interfaces import Pipeline

from .multiband_getter import MultibandGetter
from .multiband_saver import MultibandSaver
from .soilgrids_downloader import SoilGridsDownloader

logger = logging.getLogger(__name__)


class SoilPipeline(Pipeline):
    """End-to-end pipeline for SoilGrids soil property data.

    Orchestrates downloading, storing, and querying multi-band GeoTIFF files
    containing soil properties for Germany. Uses :class:`SoilGridsDownloader`
    for incremental data acquisition, :class:`MultibandSaver` for alignment-aware
    storage and PostGIS metadata management, and :class:`MultibandGetter` for
    coverage-ID-based coordinate value retrieval.
    """

    def __init__(
        self,
        name: str = "soil",
        properties: list[str] | None = None,
        depths: list[str] | None = None,
        value: str = "mean",
    ):
        """Initialize the soil pipeline with a configurable set of properties and depth layers.

        Parameters
        ----------
        name : str, optional
            Identifier used for database isolation and file naming.
            Defaults to ``"soil"``.
        properties : list[str], optional
            Soil properties to download and expose. Supported values are a
            subset of the SoilGrids API properties:
            ``"bdod"``, ``"cec"``, ``"cfvo"``, ``"clay"``, ``"nitrogen"``,
            ``"ocd"``, ``"ocs"``, ``"phh2o"``, ``"sand"``, ``"silt"``,
            ``"soc"``, ``"wv0010"``, ``"wv0033"``, ``"wv1500"``.
            Pipeline-friendly aliases ``"ph"`` and ``"carbon"`` are also accepted.
            Defaults to ``["clay", "sand", "silt", "ph", "carbon"]``.
        depths : list[str], optional
            Depth layers to include. Available options:
            ``"0-5cm"``, ``"0-30cm"``, ``"5-15cm"``, ``"15-30cm"``,
            ``"30-60cm"``, ``"60-100cm"``, ``"100-200cm"``.
            Defaults to ``["0-5cm", "5-15cm"]``.
        value : str, optional
            Statistical summary to retrieve for each property/depth combination.
            Supported values are "Q0.05", "Q0.5", "Q0.95", "mean", "uncertainty".
        """
        if properties is None:
            properties = ["clay", "sand", "silt", "ph", "carbon"]
        super().__init__(
            name,
            downloader=SoilGridsDownloader,
            saver=MultibandSaver,
            getter=MultibandGetter,
            url=None,
        )
        self.data_source = "SoilGrids"
        if depths is None:
            depths = ["0-5cm", "5-15cm"]
        self.depths = depths
        if properties:
            self.properties = properties
        else:
            self.properties = ["clay", "sand", "silt", "ph", "carbon"]

        self.statistic = value
        logger.info(
            f"SoilPipeline initialized with properties: {self.properties} and depths: {self.depths}"
        )

    def __call__(self, *args, **kwds):
        """Run the pipeline to download and store soil data.

        Assembles the downloader configuration from the properties and depth
        layers set on this instance and delegates execution to the parent
        :class:`~datavia.core.interfaces.Pipeline`.

        Parameters
        ----------
        *args
            Positional arguments forwarded to the parent pipeline.
        **kwds
            Keyword arguments forwarded to the parent pipeline.

        Returns
        -------
        Any
            Return value of the parent pipeline call.
        """
        config = {
            "properties": self.properties,
            "depths": self.depths,
            "statistic": self.statistic,
        }
        return super().__call__(config, *args, **kwds)

    def get_data(
        self,
        coords: np.ndarray,
        properties: list[str] | None = None,
        crs_coords: str = "EPSG:4326",
        interpolation_order: int = 3,
    ) -> dict[str, np.ndarray]:
        """Return soil property values at the given coordinates.

        Each coverage ID stored in the database that matches a requested
        property becomes an entry in the returned dict. This means multiple
        depth layers for the same property (e.g. ``"clay_0-5cm_mean"`` and
        ``"clay_5-15cm_mean"``) are returned as separate entries so that no
        depth information is silently discarded.

        Parameters
        ----------
        coords : np.ndarray
            Array of coordinates with shape ``(n_points, 2)``.
            Expected column order: ``(longitude, latitude)`` for EPSG:4326.
        properties : list[str], optional
            Canonical property names to retrieve, e.g. ``["clay", "ph"]``.
            Defaults to all properties configured on this instance
            (``self.properties``). Pass ``None`` to return all stored coverages.
        crs_coords : str, optional
            CRS of the input coordinates. Defaults to ``"EPSG:4326"``.
        interpolation_order : int, optional
            Interpolation order (1 = bilinear, 3 = cubic). Defaults to 3.

        Returns
        -------
        dict[str, np.ndarray]
            Mapping from **coverage ID** (e.g. ``"clay_0-5cm_mean"``) to a
            1-D array of interpolated values, one entry per coordinate.
            Coverage IDs that could not be extracted map to arrays filled
            with ``NaN``.

        Raises
        ------
        RuntimeError
            If the pipeline has not been initialised (call the pipeline first).
        """
        if not self.getter:
            raise RuntimeError("Pipeline not initialized. Call the pipeline first.")

        # Build coverage_id → (uri, band_index) from the DB
        coverage_map = self.getter.get_coverage_map()

        if not coverage_map:
            logger.warning(
                "No band metadata available for source '%s'. Run update_data() first.",
                self.name,
            )
            return {}

        # Filter to requested properties: keep coverage IDs whose leading
        # property token (e.g. "clay" from "clay_0-5cm_mean") matches.
        requested_props = set(properties or self.properties)
        matching_ids = [
            cov_id
            for cov_id in coverage_map
            if self._parse_property_from_description(cov_id) in requested_props
        ]

        if not matching_ids:
            logger.warning(
                "None of the requested properties %s found in stored coverages %s",
                sorted(requested_props),
                sorted(coverage_map.keys()),
            )
            return {}

        results: dict[str, np.ndarray] = {}
        for coverage_id in matching_ids:
            try:
                values = self.getter.get_data_for_coverage(
                    coverage_id=coverage_id,
                    coords=coords,
                    crs_coords=crs_coords,
                    interpolation_order=interpolation_order,
                )
                results[coverage_id] = values
                logger.debug("Extracted values for coverage '%s'", coverage_id)
            except Exception as exc:
                logger.warning("Failed to extract coverage '%s': %s", coverage_id, exc)
                results[coverage_id] = np.full(len(coords), np.nan)

        logger.info(
            "Extracted %d coverage(s) for %d coordinate(s): %s",
            len(results),
            len(coords),
            sorted(results.keys()),
        )
        return results

    def _parse_property_from_description(self, description: str) -> str | None:
        """Parse property name from an enhanced band description.

        Maps the human-readable descriptions written by TiffSaver/SoilGridsDownloader
        back to the canonical property keys used by this pipeline (e.g. ``"clay"``,
        ``"ph"``, ``"carbon"``).

        Note: API service IDs (``phh2o``, ``soc``) are intentionally mapped back to
        the user-facing names (``ph``, ``carbon``) so they match ``self.properties``.
        """
        try:
            # Map enhanced descriptions to this pipeline's canonical property names.
            # Keys are substrings of the descriptions produced by
            # SoilGridsDownloader._enhance_band_description().
            property_mappings = {
                "Clay content": "clay",
                "Sand content": "sand",
                "Silt content": "silt",
                # pH: API uses 'phh2o', pipeline exposes as 'ph'
                "pH in water": "ph",
                # Carbon: API uses 'soc', pipeline exposes as 'carbon'
                "Soil organic carbon": "carbon",
                "Organic carbon content": "carbon",
            }

            for desc_pattern, prop_name in property_mappings.items():
                if desc_pattern.lower() in description.lower():
                    return prop_name

            # Fallback: parse from coverage_id pattern (e.g. "clay_0-5cm_mean")
            if "_" in description:
                parts = description.split("_")
                if parts:
                    prop_candidate = parts[0].lower()
                    # Reverse-alias API names to pipeline names
                    _api_to_pipeline = {"phh2o": "ph", "soc": "carbon"}
                    prop_candidate = _api_to_pipeline.get(
                        prop_candidate, prop_candidate
                    )
                    if prop_candidate in ["clay", "sand", "silt", "ph", "carbon"]:
                        return prop_candidate

            return None

        except Exception:
            return None

    def update_data(self) -> bool:
        """Download missing soil coverages and register them in the database.

        Computes the delta between the configured coverage IDs and those
        already stored in the database, downloads only the missing files, then
        hands them to :class:`MultibandSaver` for alignment-checked storage.

        If a file was manually deleted from the data directory, the stale
        database rows are removed by a sync step before the delta is computed,
        so the affected coverages are treated as missing and re-downloaded
        automatically.

        Returns
        -------
        bool
            ``True`` if all missing coverages were downloaded and stored
            successfully, or if there was nothing to download.  ``False`` if
            the download or storage step failed.
        """
        if not self.downloader or not self.saver or not self.getter:
            # Lazy-initialise pipeline components if not yet done
            self()

        # --- Reconcile filesystem with DB -------------------------------------
        # Removes DB entries whose files have been manually deleted so that the
        # delta computation below treats those coverages as missing.
        self.saver.sync_files_and_database()

        # --- Delta computation -------------------------------------------------
        needed_ids = set(self.downloader.get_coverage_ids())
        stored_ids = self.getter.get_stored_coverage_ids()
        delta = needed_ids - stored_ids

        if not delta:
            logger.info(
                "All %d coverage(s) already present in the database — nothing to download",
                len(needed_ids),
            )
            return True

        logger.info(
            "Downloading %d missing coverage(s): %s",
            len(delta),
            sorted(delta),
        )

        # --- Download ----------------------------------------------------------
        data_dir = get_config().data_directory
        temp_dir = os.path.join(str(data_dir), "temp")
        os.makedirs(temp_dir, exist_ok=True)

        try:
            with tempfile.TemporaryDirectory(dir=temp_dir) as work_dir:
                new_files = self.downloader.download_coverages(sorted(delta), work_dir)

                if not new_files:
                    logger.error("No coverages downloaded successfully")
                    return False

                # --- Save (alignment check + stack + DB registration) ----------
                success = self.saver.save_coverages(new_files)

        except Exception as exc:
            logger.error("Error during update_data: %s", exc)
            return False

        if success:
            logger.info(
                "Successfully stored %d new coverage(s) for source '%s'",
                len(new_files),
                self.name,
            )
        else:
            logger.error("Failed to store downloaded coverages")
        return success

    def get_available_properties(self) -> list[str]:
        """Return canonical property names that are actually present in the database.

        Reads the stored coverage IDs via
        :meth:`~datavia.soil.multiband_getter.MultibandGetter.get_stored_coverage_ids`
        and translates each one to its canonical property name using
        :meth:`_parse_property_from_description`.

        Falls back to the configured ``self.properties`` list when the getter
        is not yet initialised or no data has been stored yet.

        Returns
        -------
        list[str]
            Deduplicated canonical property names found in the database, e.g.
            ``["clay", "sand", "silt", "ph", "carbon"]``, ordered by first
            occurrence. Returns the configured list if the database is empty.
        """
        if self.getter:
            stored_ids = self.getter.get_stored_coverage_ids()
            if stored_ids:
                available: list[str] = []
                for coverage_id in sorted(stored_ids):
                    prop = self._parse_property_from_description(coverage_id)
                    if prop and prop not in available:
                        available.append(prop)
                if available:
                    return available

        logger.debug(
            "No band metadata found in database — returning configured properties."
        )
        return self.properties.copy()

    def get_remote_available_properties(self) -> list[str]:
        """Return soil properties confirmed as available on the SoilGrids API.

        Queries the SoilGrids WCS service for each configured property to
        verify it is actually offered upstream. Translates user-facing alias
        names (``"ph"``, ``"carbon"``) to their API service IDs before
        querying, and maps results back to canonical pipeline names. Useful
        for validating ``properties`` before starting a download or for
        discovering the full set of properties the service offers.

        Returns
        -------
        list[str]
            Canonical property names confirmed as available on SoilGrids, e.g.
            ``["clay", "sand", "silt", "ph", "carbon"]``. Returns an empty
            list if the API cannot be reached.
        """
        # Same alias table as SoilGridsDownloader._download_single_coverage
        _pipeline_to_api = {"carbon": "soc", "ph": "phh2o"}
        _api_to_pipeline = {v: k for k, v in _pipeline_to_api.items()}

        available_remote: list[str] = []
        for prop in self.downloader.priority_properties:
            api_service_id = _pipeline_to_api.get(prop, prop)
            try:
                self.downloader.sg._get_service_and_coverage_list(api_service_id)
                # No exception means the service exists upstream
                canonical = _api_to_pipeline.get(api_service_id, api_service_id)
                if canonical not in available_remote:
                    available_remote.append(canonical)
            except Exception as exc:
                logger.debug(
                    "Property '%s' (API: '%s') not available on SoilGrids: %s",
                    prop,
                    api_service_id,
                    exc,
                )
        return available_remote
