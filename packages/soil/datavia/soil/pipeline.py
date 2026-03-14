"""
Soil Pipeline - Complete soil data integration.

Provides end-to-end access to SoilGrids soil property data for Germany:
- Downloads each soil property/depth combination as an individual single-band GeoTIFF.
- Registers each file as its own layer in PostGIS.
- Supports incremental downloads: only missing coverages are fetched.
- Exposes coordinate-based value retrieval via coverage-ID-specific file routing.

Planned extensions:
- BÜK shapefile integration (vector-based soil classification).
"""

import logging
import os
import tempfile

import numpy as np

from datavia.config import get_config
from datavia.core.getter_tiff import GetterTiff
from datavia.core.interfaces import Pipeline
from datavia.core.saver_tiff import TiffSaver
from datavia.library.database.query import get_layer_by_name
from datavia.library.interpolation import spatial_interpolate

from .soilgrids_downloader import SoilGridsDownloader

logger = logging.getLogger(__name__)


class SoilGetterTiff(GetterTiff):
    """GetterTiff extended with per-coverage-ID file routing for soil data.

    Each soil coverage is stored as its own single-band GeoTIFF layer
    (e.g. ``soil_clay_0-5cm_mean``). :meth:`get_data_for_coverage` routes
    directly to the correct file instead of iterating over all source paths,
    and :meth:`get_stored_coverage_ids` lists which coverages are registered
    in the database.
    """

    def get_stored_coverage_ids(self) -> set[str]:
        """Return coverage IDs registered in the database for this source.

        Strips the ``{source_name}_`` prefix from each layer name returned by
        :meth:`check_existing_layers` so callers receive plain coverage IDs
        (e.g. ``"clay_0-5cm_mean"``) instead of full layer names.

        Returns
        -------
        set[str]
            Coverage IDs present in the database, e.g.
            ``{"clay_0-5cm_mean", "sand_5-15cm_mean"}``. Returns an empty set
            before any data has been stored.
        """
        prefix = f"{self.source_name}_"
        return {
            layer[len(prefix) :]
            for layer in self.check_existing_layers()
            if layer.startswith(prefix)
        }

    def get_data_for_coverage(
        self,
        coverage_id: str,
        coords: np.ndarray,
        crs_coords: str = "EPSG:4326",
        interpolation_order: int = 3,
    ) -> np.ndarray:
        """Return raster values at *coords* from the file for *coverage_id*.

        Resolves the layer URI for ``{source_name}_{coverage_id}`` in
        ``raster_layers`` and delegates to
        :func:`~datavia.library.interpolation.spatial_interpolate`.

        Parameters
        ----------
        coverage_id : str
            Coverage identifier, e.g. ``"clay_0-5cm_mean"``.
        coords : np.ndarray
            Coordinate array of shape ``(n_points, 2)`` as
            ``(longitude, latitude)`` for EPSG:4326.
        crs_coords : str, optional
            CRS of the input coordinates. Defaults to ``"EPSG:4326"``.
        interpolation_order : int, optional
            Interpolation order (1 = bilinear, 3 = cubic). Defaults to 3.

        Returns
        -------
        np.ndarray
            Interpolated values at each coordinate, shape ``(n_points,)``.

        Raises
        ------
        ValueError
            If *coverage_id* is not found in the database.
        RuntimeError
            If raster value extraction fails.
        """
        layer_name = f"{self.source_name}_{coverage_id}"
        layer = get_layer_by_name(layer_name, self.source_name)
        if layer is None:
            raise ValueError(
                f"Coverage '{coverage_id}' not found in database "
                f"(looked for layer '{layer_name}')."
            )
        uri = layer["uri"]
        try:
            return spatial_interpolate(
                tiff_path=uri,
                coords=coords,
                coords_crs=crs_coords,
                interpolation_order=interpolation_order,
                band=1,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to extract coverage '{coverage_id}' from '{uri}': {exc}"
            ) from exc


class SoilPipeline(Pipeline):
    """End-to-end pipeline for SoilGrids soil property data.

    One single-band GeoTIFF is stored per coverage ID
    (e.g. ``soil_clay_0-5cm_mean.tif``). Downloads are incremental: only
    coverage IDs not yet present in the data directory are fetched. Manual
    file deletions are reconciled automatically before each update.
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
            saver=TiffSaver,
            getter=SoilGetterTiff,
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
    ) -> np.ndarray | dict[str, np.ndarray]:
        """Return soil property values at the given coordinates.

        For single property requests, returns the interpolated values directly
        as a 1-D array. For multiple properties, returns a dict mapping each
        coverage ID to its interpolated values. Multiple depth layers for the
        same property (e.g. ``"clay_0-5cm_mean"`` and ``"clay_5-15cm_mean"``)
        are returned as separate entries so that no depth information is
        silently discarded.

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
        np.ndarray | dict[str, np.ndarray]
            If exactly one coverage ID matches the request, returns a 1-D array
            of interpolated values with shape ``(n_points,)``. If multiple
            coverage IDs match, returns a mapping from **coverage ID** (e.g.
            ``"clay_0-5cm_mean"``) to a 1-D array of interpolated values, one
            entry per coordinate. Returns an empty dict if no matching
            coverages are found or if extraction fails.

        Raises
        ------
        RuntimeError
            If the pipeline has not been initialised (call the pipeline first).
        """
        if not self.getter:
            raise RuntimeError("Pipeline not initialized. Call the pipeline first.")

        stored_ids = self.getter.get_stored_coverage_ids()
        if not stored_ids:
            logger.warning(
                "No data stored for source '%s'. Run update_data() first.", self.name
            )
            return {}

        requested_props = set(properties or self.properties)

        # Warn about any property names that are not recognised at all so the
        # caller realises the typo/alias problem before digging into empty results.
        _known = {
            "bdod",
            "cec",
            "cfvo",
            "clay",
            "nitrogen",
            "ocd",
            "ocs",
            "phh2o",
            "sand",
            "silt",
            "soc",
            "wv0010",
            "wv0033",
            "wv1500",
            "ph",
            "carbon",
        }
        unknown_props = sorted(requested_props - _known)
        if unknown_props:
            logger.warning(
                "Unknown propert%s requested: %s. "
                "Call get_available_properties() to see what is stored locally.",
                "y" if len(unknown_props) == 1 else "ies",
                unknown_props,
            )

        matching_ids = [
            cov_id
            for cov_id in sorted(stored_ids)
            if self._parse_property_from_description(cov_id) in requested_props
        ]

        # Warn about recognised properties that simply have no stored coverage yet.
        matched_props = {
            self._parse_property_from_description(cov_id) for cov_id in matching_ids
        } - {None}
        missing_props = sorted((requested_props & _known) - matched_props)
        if missing_props:
            logger.warning(
                "Propert%s %s not found in local database. "
                "Run update_data() to download them, "
                "or call get_available_properties() to see what is available.",
                "y" if len(missing_props) == 1 else "ies",
                missing_props,
            )

        if not matching_ids:
            return {}

        if len(matching_ids) == 1:
            logger.info(
                "Extracting values for coverage '%s' at %d coordinate(s)",
                matching_ids[0],
                len(coords),
            )
            try:
                return self.getter.get_data_for_coverage(
                    coverage_id=matching_ids[0],
                    coords=coords,
                    crs_coords=crs_coords,
                    interpolation_order=interpolation_order,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to extract coverage '%s': %s",
                    matching_ids[0],
                    exc,
                )
                return {}

        results: dict[str, np.ndarray] = {}
        for coverage_id in matching_ids:
            try:
                results[coverage_id] = self.getter.get_data_for_coverage(
                    coverage_id=coverage_id,
                    coords=coords,
                    crs_coords=crs_coords,
                    interpolation_order=interpolation_order,
                )
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
        """Return the canonical property name from a coverage ID.

        Coverage IDs are raw strings like ``"clay_0-5cm_mean"`` or the API
        service variant ``"phh2o_0-5cm_mean"``. The first underscore-separated
        token is the property identifier; API aliases (``phh2o``, ``soc``) are
        mapped back to the user-facing names (``ph``, ``carbon``).

        Parameters
        ----------
        description : str
            Raw coverage ID as stored in ``raster_layers.layer_name``.

        Returns
        -------
        str | None
            Canonical property name, or ``None`` if the token is not
            recognised.
        """
        try:
            if "_" not in description:
                return None
            prop_candidate = description.split("_")[0].lower()
            _api_to_pipeline = {"phh2o": "ph", "soc": "carbon"}
            prop_candidate = _api_to_pipeline.get(prop_candidate, prop_candidate)
            canonical = ["clay", "sand", "silt", "ph", "carbon"]
            return prop_candidate if prop_candidate in canonical else None
        except Exception:
            return None

    def update_data(self) -> bool:
        """Download missing soil coverages and register each as its own layer.

        Computes the delta between the configured coverage IDs and those
        already present on disk and in the database. Only missing coverages
        are downloaded. Manually deleted files are detected by
        :meth:`~datavia.core.saver_tiff.TiffSaver.sync_files_and_database`
        before the delta is computed so they are re-downloaded automatically.

        Returns
        -------
        bool
            ``True`` if all missing coverages were downloaded and stored, or
            if nothing was missing. ``False`` if any download or save step
            failed.
        """
        if not self.downloader or not self.saver or not self.getter:
            self()

        # --- Validate configured properties against known API names -----------
        _known_api = {
            "bdod",
            "cec",
            "cfvo",
            "clay",
            "nitrogen",
            "ocd",
            "ocs",
            "phh2o",
            "sand",
            "silt",
            "soc",
            "wv0010",
            "wv0033",
            "wv1500",
            "ph",
            "carbon",
        }
        unrecognised = sorted(set(self.properties) - _known_api)
        if unrecognised:
            logger.warning(
                "Unrecognised propert%s in pipeline configuration: %s. "
                "Call get_remote_available_properties() to see what the "
                "SoilGrids API currently offers.",
                "y" if len(unrecognised) == 1 else "ies",
                unrecognised,
            )

        self.saver.sync_files_and_database()

        # --- Delta computation -------------------------------------------------
        needed_ids = set(self.downloader.get_coverage_ids())
        stored_ids = self.getter.get_stored_coverage_ids()
        delta = needed_ids - stored_ids

        if not delta:
            logger.info(
                "All %d coverage(s) already present — nothing to download",
                len(needed_ids),
            )
            return True

        logger.info("Downloading %d missing coverage(s): %s", len(delta), sorted(delta))

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

                # --- Save each coverage as its own layer -----------------------
                saved = sum(1 for path, _ in new_files if self.saver.save(path))
        except Exception as exc:
            logger.error("Error during update_data: %s", exc)
            return False

        logger.info(
            "Saved %d/%d coverage(s) for source '%s'",
            saved,
            len(new_files),
            self.name,
        )
        return saved == len(new_files)

    def get_available_properties(self) -> list[str]:
        """Return canonical property names present in the database.

        Reads stored coverage IDs from ``raster_layers`` via
        :meth:`SoilGetterTiff.get_stored_coverage_ids` and translates each to
        its canonical property name. Falls back to ``self.properties`` when
        no data has been stored yet.

        Returns
        -------
        list[str]
            Deduplicated canonical property names, e.g.
            ``["clay", "sand", "silt", "ph", "carbon"]``. Returns the
            configured list if the database is empty.
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

        logger.debug("No stored layers found — returning configured properties.")
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
