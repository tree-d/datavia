"""
Soil Pipeline - Complete soil data integration.

Provides end-to-end access to soil property data for Germany from two sources:

SoilGrids (WCS API):
- Properties: clay, sand, silt, ph, carbon and more.
- Downloads each property/depth combination as an individual single-band GeoTIFF.

HiHydroSoil (HTTP GeoTIFF catalogue, vsicurl streaming):
- Properties: field_capacity, wilting_point, porosity, hydraulic_conductivity.
- Six depth layers per property; only the Germany window is downloaded.

Both sources are managed through a single SoilPipeline instance backed by a
CompositeDownloader that routes each coverage ID to the correct remote service.
All data is registered as individual single-band GeoTIFF layers in PostGIS.
Downloads are incremental: only missing coverages are fetched on each call.

Planned extensions:
- BÜK shapefile integration (vector-based soil classification).
"""

import logging
import os
import tempfile
from typing import ClassVar

import numpy as np

from datavia.config import get_config
from datavia.core.getter_tiff import GetterTiff
from datavia.core.interfaces import Pipeline
from datavia.core.saver_tiff import TiffSaver
from datavia.library.database.query import get_layer_by_name
from datavia.library.interpolation import spatial_interpolate

from .composite_downloader import CompositeDownloader
from .soilgrids_downloader import SoilGridsDownloader  # noqa: F401 (re-exported)

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
        :meth:`get_existing_layers` so callers receive plain coverage IDs
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
            for layer in self.get_existing_layers()
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
    """End-to-end pipeline for multi-source soil property data.

    Integrates SoilGrids (WCS) and HiHydroSoil (HTTP GeoTIFF) into a single
    pipeline. One single-band GeoTIFF is stored per coverage ID (e.g.
    ``soil_clay_0-5cm_mean.tif``, ``soil_field_capacity_0-5cm_mean.tif``).
    Downloads are incremental: only coverage IDs not yet present in the data
    directory are fetched. Manual file deletions are reconciled automatically
    before each update.

    Class-level alias tables are the single source of truth for translating
    between source API names (e.g. ``"soc"``, ``"WCpF2"``) and the canonical
    pipeline names used throughout (``"carbon"``, ``"field_capacity"``).
    All methods that parse, filter, or compare coverage IDs reference these
    constants instead of defining their own inline mappings.
    """

    #: Maps all API property names (SoilGrids + HiHydroSoil) to canonical pipeline names.
    _API_TO_PIPELINE: ClassVar[dict[str, str]] = {
        # SoilGrids
        "phh2o": "ph",
        "soc": "carbon",
        # HiHydroSoil
        "WCpF2": "field_capacity",
        "WCpF4.2": "wilting_point",
        "WCsat": "porosity",
        "Ksat": "hydraulic_conductivity",
    }

    #: Reverse mapping: canonical pipeline name → source-specific API service ID.
    _PIPELINE_TO_API: ClassVar[dict[str, str]] = {
        # SoilGrids
        "ph": "phh2o",
        "carbon": "soc",
        # HiHydroSoil
        "field_capacity": "WCpF2",
        "wilting_point": "WCpF4.2",
        "porosity": "WCsat",
        "hydraulic_conductivity": "Ksat",
    }

    #: All valid property tokens (canonical names and API aliases) across all sources.
    #: Used for prefix-based coverage ID parsing and property validation.
    _KNOWN_PROPERTIES: ClassVar[frozenset[str]] = frozenset(
        {
            # SoilGrids API names
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
            # SoilGrids canonical aliases
            "ph",
            "carbon",
            # HiHydroSoil canonical names
            "field_capacity",
            "wilting_point",
            "porosity",
            "hydraulic_conductivity",
            # HiHydroSoil API names (kept for normalisation of any legacy stored IDs)
            "WCpF2",
            "WCpF4.2",
            "WCsat",
            "Ksat",
        }
    )

    def __init__(
        self,
        name: str = "soil",
        properties: list[str] | None = None,
        depths: list[str] | None = None,
        value: str = "mean",
    ) -> None:
        """Initialize the soil pipeline with a configurable set of properties and depth layers.

        Parameters
        ----------
        name : str, optional
            Identifier used for database isolation and file naming.
            Defaults to ``"soil"``.
        properties : list[str], optional
            Soil properties to download and expose. Accepts canonical names
            from both SoilGrids (``"clay"``, ``"sand"``, ``"silt"``,
            ``"ph"``, ``"carbon"``, ``"bdod"``, ``"cec"``, ``"cfvo"``,
            ``"nitrogen"``, ``"ocd"``, ``"ocs"``) and HiHydroSoil
            (``"field_capacity"``, ``"wilting_point"``, ``"porosity"``,
            ``"hydraulic_conductivity"``).
            Defaults to ``["clay", "sand", "silt", "ph", "carbon",
            "field_capacity", "wilting_point", "porosity",
            "hydraulic_conductivity"]``.
        depths : list[str], optional
            Depth layers applied to all properties across both sources.
            Both SoilGrids and HiHydroSoil share the same six standard
            layers: ``"0-5cm"``, ``"5-15cm"``, ``"15-30cm"``,
            ``"30-60cm"``, ``"60-100cm"``, ``"100-200cm"``.
            SoilGrids also offers ``"0-30cm"`` for the ``ocs`` property.
            Defaults to ``["0-5cm", "5-15cm"]``.
        value : str, optional
            Statistical summary to retrieve for each property/depth combination.
            SoilGrids supports ``"Q0.05"``, ``"Q0.5"``, ``"Q0.95"``,
            ``"mean"``, ``"uncertainty"``.
            HiHydroSoil supports only ``"mean"``.
            Defaults to ``"mean"``.
        """
        if properties is None:
            properties = [
                "clay",
                "sand",
                "silt",
                "ph",
                "carbon",
                "field_capacity",
                "wilting_point",
                "porosity",
                "hydraulic_conductivity",
            ]
        super().__init__(
            name,
            downloader=CompositeDownloader,
            saver=TiffSaver,
            getter=SoilGetterTiff,
            url=None,
        )
        self.data_source = "SoilGrids+HiHydroSoil"
        self.depths = depths if depths is not None else ["0-5cm", "5-15cm"]
        self.properties = properties
        self.statistic = value
        logger.info(
            "SoilPipeline initialized — properties: %s, depths: %s, statistic: %s",
            self.properties,
            self.depths,
            self.statistic,
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
        config: dict = {
            "properties": self.properties,
            "depths": self.depths,
            "statistic": self.statistic,
        }
        return super().__call__(config, *args, **kwds)

    def configure(
        self,
        properties: list[str] | None = None,
        depths: list[str] | None = None,
        value: str | None = None,
    ) -> None:
        """Update the pipeline-level configuration attributes in place.

        Changed attributes are propagated to the downloader instance when it
        has already been initialised (i.e. after the first ``__call__``). Call
        :meth:`update_data` afterwards to download any newly requested
        coverages.

        Parameters
        ----------
        properties : list[str], optional
            Replacement list of soil properties (any source). When ``None``
            the current value is kept unchanged.
        depths : list[str], optional
            Replacement depth layers applied to all sources. When ``None``
            the current value is kept unchanged.
        value : str, optional
            Replacement statistic identifier (e.g. ``"Q0.05"``, ``"mean"``).
            When ``None`` the current value is kept unchanged.
        """
        if properties is not None:
            self.properties = properties
        if depths is not None:
            self.depths = depths
        if value is not None:
            self.statistic = value

        logger.info(
            "SoilPipeline reconfigured — properties=%s, depths=%s, statistic=%s",
            self.properties,
            self.depths,
            self.statistic,
        )

    def get_data(
        self,
        coords: np.ndarray,
        properties: list[str] | str | None = None,
        depths: list[str] | str | None = None,
        value: str | None = None,
        crs_coords: str = "EPSG:4326",
        interpolation_order: int = 3,
    ) -> np.ndarray | dict[str, np.ndarray]:
        """Return soil property values at the given coordinates.

        For single property requests, returns the interpolated values directly
        as a 1-D array. For multiple properties, returns a dict mapping each
        coverage ID to its interpolated values. Each coverage ID encodes the
        property, depth, and statistic (e.g. ``"clay_0-5cm_mean"``), so
        results are always unambiguous.

        Parameters
        ----------
        coords : np.ndarray
            Array of coordinates with shape ``(n_points, 2)``.
            Expected column order: ``(longitude, latitude)`` for EPSG:4326.
        properties : list[str] or str, optional
            Canonical property names to retrieve, e.g. ``["clay", "ph"]``.
            A plain string (``"clay"``) is accepted as shorthand for a
            single-element list. Defaults to ``self.properties``.
        depths : list[str] or str, optional
            Depth layers to include, e.g. ``["0-5cm"]`` or ``"0-5cm"``.
            A plain string is accepted as shorthand for a single-element
            list. Defaults to ``self.depths``.
        value : str, optional
            Statistic to retrieve, e.g. ``"Q0.05"`` or ``"mean"``.
            Defaults to ``self.statistic``.
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

        # Coerce plain strings to single-element lists so callers can write
        # properties="clay" or depths="0-5cm" as a convenience shorthand.
        if isinstance(properties, str):
            properties = [properties]
        if isinstance(depths, str):
            depths = [depths]

        stored_ids = self.getter.get_stored_coverage_ids()
        if not stored_ids:
            logger.warning(
                "No data stored for source '%s'. Run update_data() first.", self.name
            )
            return {}

        requested_props = set(properties or self.properties)

        # Warn about any property names that are not recognised at all so the
        # caller realises the typo/alias problem before digging into empty results.
        unknown_props = sorted(requested_props - self._KNOWN_PROPERTIES)
        if unknown_props:
            logger.warning(
                "Unknown propert%s requested: %s. "
                "Call get_available_properties() to see what is stored locally.",
                "y" if len(unknown_props) == 1 else "ies",
                unknown_props,
            )

        effective_depths = set(depths or self.depths)
        effective_statistic = value or self.statistic

        matching_ids = [
            cov_id
            for cov_id in sorted(stored_ids)
            if self._parse_property_from_description(cov_id) in requested_props
            and self._parse_depth_from_coverage_id(cov_id) in effective_depths
            and self._parse_statistic_from_coverage_id(cov_id) == effective_statistic
        ]

        # Warn per missing coverage ID (e.g. "carbon_60-100cm_mean not found").
        # Build the full set of coverage IDs the caller intended to retrieve,
        # then subtract the ones that matched so each gap is reported individually.
        requested_ids = {
            f"{prop}_{depth}_{effective_statistic}"
            for prop in (requested_props & self._KNOWN_PROPERTIES)
            for depth in effective_depths
        }
        normalised_stored = {self._normalize_coverage_id(cid) for cid in stored_ids}
        missing_ids = sorted(requested_ids - set(matching_ids) - normalised_stored)
        for missing_id in missing_ids:
            logger.warning(
                "Coverage '%s' not found in local database. "
                "Run update_data() to download it.",
                missing_id,
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

    def _split_coverage_id(self, coverage_id: str) -> tuple[str, str, str] | None:
        """Split a coverage ID into its three constituent tokens.

        Coverage IDs follow the pattern ``{property}_{depth}_{statistic}``
        where the property token may itself contain underscores for compound
        names such as ``"field_capacity"`` or ``"hydraulic_conductivity"``.
        Positional ``split("_")`` is therefore unreliable; this method
        matches the longest known property token as a prefix instead.

        Parameters
        ----------
        coverage_id : str
            Raw coverage ID as stored in ``raster_layers.layer_name``,
            e.g. ``"clay_0-5cm_mean"`` or ``"field_capacity_0-5cm_mean"``.

        Returns
        -------
        tuple[str, str, str] | None
            ``(property_token, depth, statistic)`` triple on success, or
            ``None`` if the ID cannot be matched against any known property
            token.
        """
        # Try longest tokens first so "field_capacity" wins over any shorter
        # prefix that might accidentally match.
        for token in sorted(self._KNOWN_PROPERTIES, key=len, reverse=True):
            prefix = token + "_"
            if coverage_id.startswith(prefix):
                remainder = coverage_id[len(prefix) :]
                sep = remainder.find("_")
                if sep == -1:
                    continue  # no statistic token — malformed ID
                depth = remainder[:sep]
                statistic = remainder[sep + 1 :]
                if depth and statistic:
                    return token, depth, statistic
        return None

    def _parse_depth_from_coverage_id(self, coverage_id: str) -> str | None:
        """Return the depth token from a coverage ID.

        Coverage IDs follow the pattern ``{property}_{depth}_{statistic}``
        where the property token may contain underscores (e.g.
        ``"field_capacity_0-5cm_mean"``). Delegates to
        :meth:`_split_coverage_id` for robust prefix-based parsing.

        Parameters
        ----------
        coverage_id : str
            Raw coverage ID as stored in ``raster_layers.layer_name``.

        Returns
        -------
        str | None
            Depth string such as ``"0-5cm"``, or ``None`` if the ID cannot
            be matched against any known property token.
        """
        parsed = self._split_coverage_id(coverage_id)
        return parsed[1] if parsed is not None else None

    def _parse_statistic_from_coverage_id(self, coverage_id: str) -> str | None:
        """Return the statistic token from a coverage ID.

        Coverage IDs follow the pattern ``{property}_{depth}_{statistic}``
        where the property token may contain underscores (e.g.
        ``"field_capacity_0-5cm_mean"``). Delegates to
        :meth:`_split_coverage_id` for robust prefix-based parsing.

        Parameters
        ----------
        coverage_id : str
            Raw coverage ID as stored in ``raster_layers.layer_name``.

        Returns
        -------
        str | None
            Statistic string such as ``"mean"`` or ``"Q0.05"``, or ``None``
            if the ID cannot be matched against any known property token.
        """
        parsed = self._split_coverage_id(coverage_id)
        return parsed[2] if parsed is not None else None

    def _parse_property_from_description(self, description: str) -> str | None:
        """Return the canonical property name from a coverage ID.

        Handles both single-word (``"clay_0-5cm_mean"``) and compound
        (``"field_capacity_0-5cm_mean"``) property tokens, as well as raw
        API service names (``"phh2o"``, ``"soc"``, ``"WCpF2"``), by
        delegating to :meth:`_split_coverage_id` and then applying
        :attr:`_API_TO_PIPELINE`.

        Parameters
        ----------
        description : str
            Raw coverage ID as stored in ``raster_layers.layer_name``.

        Returns
        -------
        str | None
            Canonical property name (e.g. ``"ph"``, ``"field_capacity"``),
            or ``None`` if the description cannot be parsed.
        """
        try:
            parsed = self._split_coverage_id(description)
            if parsed is None:
                return None
            prop_token = parsed[0]
            return self._API_TO_PIPELINE.get(prop_token, prop_token)
        except Exception:
            return None

    def _normalize_coverage_id(self, coverage_id: str) -> str:
        """Return *coverage_id* with any API property name replaced by its pipeline alias.

        Stored coverage IDs may use either an API service name
        (e.g. ``"soc_0-5cm_mean"``, ``"WCpF2_0-5cm_mean"``) or the
        canonical pipeline alias (``"carbon_0-5cm_mean"``,
        ``"field_capacity_0-5cm_mean"``). This method normalises both forms
        so they compare equal during delta computation. Delegates to
        :meth:`_split_coverage_id` for robustness with compound names.

        Parameters
        ----------
        coverage_id : str
            Raw coverage ID, e.g. ``"soc_0-5cm_mean"`` or
            ``"field_capacity_0-5cm_mean"``.

        Returns
        -------
        str
            Coverage ID with the property token replaced by its canonical
            pipeline name. Returns the original string unchanged when the
            ID cannot be parsed.
        """
        parsed = self._split_coverage_id(coverage_id)
        if parsed is None:
            return coverage_id
        prop_token, depth, statistic = parsed
        canonical_prop = self._API_TO_PIPELINE.get(prop_token, prop_token)
        return f"{canonical_prop}_{depth}_{statistic}"

    def update_data(
        self,
        properties: list[str] | None = None,
        depths: list[str] | None = None,
        value: str | None = None,
        reproject: bool = False,
        resolution_m: int | None = None,
    ) -> bool:
        """Download missing soil coverages and register each as its own layer.

        Computes the delta between the configured coverage IDs and those
        already present on disk and in the database. Only missing coverages
        are downloaded. Manually deleted files are detected by
        :meth:`~datavia.core.saver_tiff.TiffSaver.sync_files_and_database`
        before the delta is computed so they are re-downloaded automatically.

        Parameters
        ----------
        properties : list[str] or str, optional
            Override the set of properties to download for this call only.
            A plain string is accepted as shorthand for a single-element list.
            The pipeline-level ``self.properties`` is not mutated. Defaults
            to ``self.properties``.
        depths : list[str] or str, optional
            Override the set of depth layers for this call only. A plain
            string is accepted as shorthand for a single-element list.
            Defaults to ``self.depths``.
        value : str, optional
            Override the statistic for this call only. Defaults to
            ``self.statistic``.
        reproject : bool, optional
            When ``True`` each saved coverage is reprojected in-place to the
            application-wide default CRS (``get_config().default_crs``).
            Defaults to ``False``.
        resolution_m : int, optional
            Target pixel resolution in metres applied during reprojection.
            Only meaningful for projected (metric) CRSs. When ``None``
            rasterio derives the resolution automatically.
            Defaults to ``None``.

        Returns
        -------
        bool
            ``True`` if all missing coverages were downloaded and stored, or
            if nothing was missing. ``False`` if any download or save step
            failed.
        """
        if not self.downloader or not self.saver or not self.getter:
            self()

        # Coerce plain strings to single-element lists so callers can write
        # properties="clay" or depths="0-5cm" as a convenience shorthand.
        if isinstance(properties, str):
            properties = [properties]
        if isinstance(depths, str):
            depths = [depths]

        # --- Validate configured properties against all known property tokens --
        effective_properties = properties or self.properties
        unrecognised = sorted(set(effective_properties) - self._KNOWN_PROPERTIES)
        if unrecognised:
            logger.warning(
                "Unrecognised propert%s in pipeline configuration: %s. "
                "Call get_available_properties() to see what is stored locally.",
                "y" if len(unrecognised) == 1 else "ies",
                unrecognised,
            )

        self.saver.sync_files_and_database()

        # --- Delta computation -------------------------------------------------
        # Pass effective values directly; get_coverage_ids() falls back to the
        # downloader's own defaults when a parameter is None.
        needed_ids = set(
            self.downloader.get_coverage_ids(
                properties=properties or self.properties,
                depths=depths or self.depths,
                statistic=value or self.statistic,
            )
        )
        stored_ids = self.getter.get_stored_coverage_ids()
        # Normalise stored IDs to pipeline naming (e.g. soc → carbon) before
        # the set difference so that data downloaded under an API name is not
        # re-downloaded simply because the name differs from the pipeline alias.
        normalised_stored = {self._normalize_coverage_id(cid) for cid in stored_ids}
        delta = needed_ids - normalised_stored

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
                saved = sum(
                    1
                    for path, _ in new_files
                    if self.saver.save(
                        path, reproject=reproject, resolution_m=resolution_m
                    )
                )
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

    def get_remote_available_properties(self) -> dict[str, list[str]]:
        """Discover all properties and depth layers available across all remote sources.

        Delegates to
        :meth:`~datavia.soil.composite_downloader.CompositeDownloader.get_remote_available_properties`,
        which queries both the SoilGrids WCS service (live network probe
        against every service in ``SoilGrids.MAP_SERVICES``) and the
        HiHydroSoil HTTP catalogue (directory listing parse). The pipeline
        is initialised lazily if it has not been called yet.

        Unlike :meth:`get_available_properties`, which reflects what is
        stored locally, this method reflects what is currently offered by
        the upstream remote services — including any new properties or depth
        layers added since the last download.

        Returns
        -------
        dict[str, list[str]]
            Mapping of canonical property name → sorted list of available
            depth strings across all remote sources, e.g.
            ``{"clay": ["0-5cm", "5-15cm", ...], "field_capacity": [...]}``.
            Returns an empty dict if no remote source can be reached.
        """
        if not self.downloader:
            self()
        return self.downloader.get_remote_available_properties()
