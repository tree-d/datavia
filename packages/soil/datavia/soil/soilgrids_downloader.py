"""
SoilGrids Downloader - WCS-based soil property data acquisition.

Responsible exclusively for fetching individual coverage files from the
SoilGrids WCS API and writing them to a temporary directory. No database
access, no alignment logic, no file combining — those concerns belong to the
saver and the pipeline orchestration layer.
"""

import logging
import math
import os
import tempfile
from typing import Any, ClassVar

from pyproj import Transformer
from soilgrids import SoilGrids

from datavia.config import get_config
from datavia.core.interfaces import Downloader

logger = logging.getLogger(__name__)


class SoilGridsDownloader(Downloader):
    """Downloader for soil property data from the SoilGrids WCS API.

    Downloads a configurable selection of soil properties and depth layers
    for Germany. Each coverage is written as an individual single-band
    GeoTIFF so that the caller can inspect spatial alignment before deciding
    how to combine or store the files.

    **CRS split**: the SoilGrids WCS endpoint only advertises a limited set
    of CRSs that can change between service versions. The downloader probes
    the API at initialisation time via :meth:`_fetch_supported_crs_urns` and
    picks the best match for the project-configured CRS, falling back to
    EPSG:4326 when no match is found. Reprojection to the project CRS is the
    responsibility of the saver / pipeline layer.
    """

    # Fallback CRS URNs used when the live API probe fails (network issues,
    # service outage). Kept intentionally minimal — only EPSG:4326 is
    # universally safe as a WCS download CRS.
    _FALLBACK_API_CRS: frozenset[str] = frozenset({"urn:ogc:def:crs:EPSG::4326"})

    # Service ID used to probe the API for supported CRSs. Clay is one of the
    # most stable SoilGrids properties and is very unlikely to be removed.
    _PROBE_SERVICE_ID: str = "clay"

    #: Maps canonical pipeline property names to SoilGrids API service IDs.
    #: Entries are only needed for properties whose pipeline name differs from
    #: the upstream service identifier.
    _CANONICAL_TO_API: ClassVar[dict[str, str]] = {
        "ph": "phh2o",  # pH measured in water
        "carbon": "soc",  # soil organic carbon
    }

    def __init__(self, config: dict[str, Any]):
        """Initialize the SoilGrids downloader.

        Parameters
        ----------
        config : dict[str, Any]
            Configuration dictionary with optional keys:

            - ``properties`` (list[str]): Soil properties to download.
              Defaults to ``["clay", "sand", "silt", "ph", "carbon"]``.
            - ``depths`` (list[str]): Depth layers to download.
              Defaults to ``["0-5cm", "5-15cm"]``.
            - ``statistic`` (str): Statistical summary to retrieve.
              Defaults to ``"mean"``.

            The CRS used for all WCS requests is derived from
            ``get_config().default_crs`` (e.g. ``"EPSG:25832"``). The
            canonical Germany bounding box is always stored in EPSG:4326 and
            reprojected on the fly so the downloader works correctly with any
            CRS supported by the SoilGrids service.
        """
        self.sg = SoilGrids()
        logger.info("SoilGrids package initialized successfully")

        self.priority_properties = config.get(
            "properties", ["clay", "sand", "silt", "ph", "carbon"]
        )
        self.priority_depths = config.get("depths", ["0-5cm", "5-15cm"])
        self.priority_statistic = config.get("statistic", "mean")

        # Desired ground resolution in metres.
        # For projected CRS this becomes resx/resy directly.
        # For geographic CRS (e.g. EPSG:4326) the correct pixel count is
        # derived from the bbox span so we still achieve ~250 m pixels.
        self.resolution_m = 250

        # The SoilGrids WCS API supports only a limited set of CRSs that can
        # change between service versions. We store the configured CRS here
        # and resolve the actual download CRS lazily in download_coverages()
        # via _resolve_crs_urn(), so no network request is made at init time.
        self._config_crs = get_config().default_crs
        self.crs_urn: str | None = None  # set on first download_coverages() call

        # Germany bounding box — always kept in EPSG:4326 as the canonical
        # geographic reference. Reprojected via _bbox_in_crs() at download
        # time so adapting the region or CRS requires no structural changes.
        self.germany_bbox = {
            "west": 5.866,
            "south": 47.270,
            "east": 15.042,
            "north": 55.058,
        }

        logger.info(
            "SoilGrids downloader configured: %d properties x %d depths x statistic=%s",
            len(self.priority_properties),
            len(self.priority_depths),
            self.priority_statistic,
        )

    def get_coverage_ids(
        self,
        properties: list[str] | None = None,
        depths: list[str] | None = None,
        statistic: str | None = None,
    ) -> list[str]:
        """Generate WCS coverage IDs for a given property/depth/statistic combination.

        Each parameter defaults to the instance-level value set during
        ``__init__`` so the method can be called with no arguments for the
        standard use-case, or with explicit overrides for targeted
        one-off operations.

        Parameters
        ----------
        properties : list[str], optional
            Soil property names. Defaults to ``self.priority_properties``.
        depths : list[str], optional
            Depth layer strings. Defaults to ``self.priority_depths``.
        statistic : str, optional
            Statistical summary identifier. Defaults to
            ``self.priority_statistic``.

        Returns
        -------
        list[str]
            Coverage identifiers in the format ``{property}_{depth}_{statistic}``,
            e.g. ``"clay_0-5cm_mean"``.
        """
        effective_properties = properties or self.priority_properties
        effective_depths = depths or self.priority_depths
        effective_statistic = statistic or self.priority_statistic
        coverage_ids = [
            f"{prop}_{depth}_{effective_statistic}"
            for prop in effective_properties
            for depth in effective_depths
        ]
        logger.info("Generated %d coverage IDs", len(coverage_ids))
        return coverage_ids

    def download(self) -> str:
        """Satisfy the :class:`~datavia.core.interfaces.Downloader` abstract interface.

        :meth:`~datavia.soil.pipeline.SoilPipeline.update_data` calls
        :meth:`download_coverages` directly with only the missing coverage IDs,
        so this method is never invoked in normal use. It is provided solely to
        fulfil the interface contract.

        Returns
        -------
        str
            Path to the first successfully downloaded single-band file, or
            ``"failed"`` if no coverage could be downloaded.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            results = self.download_coverages(self.get_coverage_ids(), temp_dir)
        return results[0][0] if results else "failed"

    def download_coverages(
        self,
        coverage_ids: list[str],
        output_dir: str,
    ) -> list[tuple[str, str]]:
        """Download a specific set of coverage IDs as individual single-band GeoTIFFs.

        Only the requested coverage IDs are fetched. This is the primary entry
        point for incremental downloads where the pipeline has already computed
        which coverages are missing.

        Parameters
        ----------
        coverage_ids : list[str]
            Coverage identifiers to download, e.g. ``["clay_0-5cm_mean"]``.
        output_dir : str
            Directory in which to write the individual ``{coverage_id}.tif`` files.
            The directory must already exist.

        Returns
        -------
        list[tuple[str, str]]
            List of ``(absolute_path, coverage_id)`` pairs for every coverage
            that was downloaded successfully. Failed downloads are omitted.
        """
        results: list[tuple[str, str]] = []
        total = len(coverage_ids)
        self._resolve_crs_urn()

        try:
            from tqdm import tqdm  # type: ignore[import]

            coverage_iter: Any = tqdm(coverage_ids, desc="SoilGrids", unit="coverage")
        except ImportError:
            coverage_iter = iter(coverage_ids)

        for i, coverage_id in enumerate(coverage_iter, 1):
            path = self._download_single_coverage(coverage_id, output_dir)
            if path != "failed":
                results.append((path, coverage_id))
                logger.info("Downloaded %d/%d: %s", i, total, coverage_id)
            else:
                logger.warning("Failed to download coverage: %s", coverage_id)

        logger.info(
            "download_coverages finished: %d/%d successful", len(results), total
        )
        return results

    def get_remote_available_properties(self) -> dict[str, list[str]]:
        """Discover all properties/depths on the live SoilGrids WCS API.

        Iterates every service registered in :attr:`SoilGrids.MAP_SERVICES` —
        not just the locally configured properties — so previously unknown
        services added upstream are discovered automatically. For each service
        the full WCS ``contents`` list is queried and each coverage identifier
        is parsed to extract the depth and statistic tokens.

        Returns
        -------
        dict[str, list[str]]
            Mapping of canonical property name → sorted list of available depth
            strings, e.g.
            ``{"clay": ["0-5cm", "5-15cm", ...], "ph": ["0-5cm", ...]}``.
            Returns an empty dict if the WCS service cannot be reached.
        """
        api_to_canonical = {v: k for k, v in self._CANONICAL_TO_API.items()}
        catalogue: dict[str, set[str]] = {}

        for service_id in SoilGrids.MAP_SERVICES:
            try:
                _wcs, coverage_list = self.sg._get_service_and_coverage_list(service_id)
                if not coverage_list:
                    logger.debug(
                        "SoilGrids service '%s' returned empty coverage list"
                        " — skipping",
                        service_id,
                    )
                    continue
                canonical = api_to_canonical.get(service_id, service_id)
                for coverage_id in coverage_list:
                    # Coverage IDs follow the pattern {service_id}_{depth}_{statistic}
                    # e.g. "clay_0-5cm_mean". Service IDs never contain underscores.
                    parts = coverage_id.split("_")
                    if len(parts) >= 3 and parts[0] == service_id:
                        depth = parts[1]
                        catalogue.setdefault(canonical, set()).add(depth)
                    else:
                        logger.debug(
                            "SoilGrids: unexpected coverage ID format '%s' "
                            "for service '%s' - skipping",
                            coverage_id,
                            service_id,
                        )
            except Exception as exc:
                logger.debug(
                    "SoilGrids service '%s' not reachable: %s", service_id, exc
                )

        result = {prop: sorted(depths) for prop, depths in sorted(catalogue.items())}
        logger.info(
            "SoilGrids remote catalogue: %d properties, depths per property: %s",
            len(result),
            {p: len(d) for p, d in result.items()},
        )
        return result

    def _download_single_coverage(self, coverage_id: str, temp_dir: str) -> str:
        """Download a single WCS coverage and write it to a temporary GeoTIFF.

        Translates user-facing property names to SoilGrids API service IDs
        (e.g. ``"carbon"`` → ``"soc"``, ``"ph"`` → ``"phh2o"``), then requests
        the coverage via the SoilGrids WCS service. Uses the Germany bounding
        box and the resolution configured on this instance.

        Parameters
        ----------
        coverage_id : str
            Coverage identifier in the format ``{property}_{depth}_{statistic}``.
        temp_dir : str
            Directory where the single-coverage GeoTIFF will be written.

        Returns
        -------
        str
            Absolute path to the downloaded GeoTIFF, or ``"failed"`` if the
            download or file validation did not succeed.
        """
        try:
            service_id = coverage_id.partition("_")[
                0
            ]  # e.g. 'clay' from 'clay_0-5cm_mean'
            temp_filepath = os.path.join(temp_dir, f"{coverage_id}.tif")

            # Map user-friendly property names to SoilGrids API service IDs
            if service_id in self._CANONICAL_TO_API:
                api_service_id = self._CANONICAL_TO_API[service_id]
                coverage_id_api = coverage_id.replace(
                    service_id + "_", api_service_id + "_", 1
                )
                service_id = api_service_id
            else:
                coverage_id_api = coverage_id

            bbox = self._bbox_in_crs(self.crs_urn)
            width, height = self._pixel_dims_for_geographic_bbox(bbox, self.crs_urn)
            self._get_coverage_data(
                service_id=service_id,
                coverage_id=coverage_id_api,
                west=bbox["west"],
                south=bbox["south"],
                east=bbox["east"],
                north=bbox["north"],
                height=height,
                width=width,
                crs=self.crs_urn,
                output=temp_filepath,
            )

            if os.path.exists(temp_filepath) and os.path.getsize(temp_filepath) > 0:
                logger.debug("Saved SoilGrids coverage: %s", temp_filepath)
                return temp_filepath

            logger.error(
                "SoilGrids download produced empty/missing file: %s", temp_filepath
            )
            return "failed"

        except Exception as exc:
            logger.error("Failed to download coverage %s: %s", coverage_id, exc)
            return "failed"

    def _resolve_crs_urn(self) -> str:
        """Return the OGC URN to use for WCS requests, probing the API if needed.

        The result is cached in ``self.crs_urn`` after the first call so the
        network probe only happens once per downloader instance, and only when
        an actual download is triggered.

        Returns
        -------
        str
            OGC URN of the CRS to use for WCS requests, e.g.
            ``"urn:ogc:def:crs:EPSG::4326"``.
        """
        if self.crs_urn is not None:
            return self.crs_urn

        config_urn = self._epsg_to_urn(self._config_crs)
        supported = self._fetch_supported_crs_urns()
        if config_urn in supported:
            self.crs_urn = config_urn
        else:
            self.crs_urn = "urn:ogc:def:crs:EPSG::4326"
            logger.warning(
                "Configured CRS %s is not supported by the SoilGrids WCS API. "
                "Downloading in EPSG:4326 instead. "
                "Reprojection to %s should be handled by the saver layer.",
                self._config_crs,
                self._config_crs,
            )
        logger.info("SoilGrids downloader will request coverages in %s", self.crs_urn)
        return self.crs_urn

    def _fetch_supported_crs_urns(self) -> frozenset[str]:
        """Query the SoilGrids WCS service for the CRSs it currently supports.

        Probes :attr:`_PROBE_SERVICE_ID` to retrieve the live
        ``supportedCRS`` list from the service's capabilities document.
        Falls back to :attr:`_FALLBACK_API_CRS` if the request fails so that
        a network outage during initialisation does not abort the process.

        Returns
        -------
        frozenset[str]
            OGC URN strings for every CRS advertised by the service, e.g.
            ``frozenset({"urn:ogc:def:crs:EPSG::4326", ...})``. Returns
            :attr:`_FALLBACK_API_CRS` on any error.
        """
        try:
            wcs, coverage_list = self.sg._get_service_and_coverage_list(
                self._PROBE_SERVICE_ID
            )
            # Any coverage from the service shares the same supported CRS list.
            probe_id = coverage_list[0] if coverage_list else None
            if probe_id is None:
                logger.warning(
                    "SoilGrids CRS probe: empty coverage list for '%s', "
                    "using fallback CRS set.",
                    self._PROBE_SERVICE_ID,
                )
                return self._FALLBACK_API_CRS

            coverage_obj = self.sg._get_coverage_obj(wcs, coverage_list, probe_id)
            supported = frozenset(
                crs_obj.getcodeurn() for crs_obj in coverage_obj.supportedCRS
            )
            logger.info(
                "SoilGrids WCS advertises %d supported CRS(s): %s",
                len(supported),
                sorted(supported),
            )
            return supported
        except Exception as exc:
            logger.warning(
                "Could not probe SoilGrids WCS for supported CRSs (%s). "
                "Using fallback: %s.",
                exc,
                sorted(self._FALLBACK_API_CRS),
            )
            return self._FALLBACK_API_CRS

    @staticmethod
    def _epsg_to_urn(crs: str) -> str:
        """Convert ``"EPSG:XXXXX"`` notation to the OGC URN used by WCS requests.

        Parameters
        ----------
        crs : str
            CRS string in ``"EPSG:XXXXX"`` format.

        Returns
        -------
        str
            OGC URN, e.g. ``"urn:ogc:def:crs:EPSG::25832"``.
        """
        code = crs.rsplit(":", maxsplit=1)[-1]
        return f"urn:ogc:def:crs:EPSG::{code}"

    def _bbox_in_crs(self, crs_urn: str) -> dict[str, float]:
        """Return the Germany bounding box expressed in *crs_urn*.

        The canonical bbox (stored in EPSG:4326) is returned as-is for
        geographic CRSs. For projected CRSs the corners are transformed via
        :class:`~pyproj.Transformer` so the WCS request uses the correct
        metric coordinates.

        Parameters
        ----------
        crs_urn : str
            Target CRS in OGC URN notation, e.g.
            ``"urn:ogc:def:crs:EPSG::25832"``.

        Returns
        -------
        dict[str, float]
            Bounding box with keys ``west``, ``south``, ``east``, ``north``
            in the coordinate system of *crs_urn*.
        """
        if "4326" in crs_urn:
            return self.germany_bbox

        epsg_code = crs_urn.rsplit("::", maxsplit=1)[-1]
        transformer = Transformer.from_crs(
            "EPSG:4326", f"EPSG:{epsg_code}", always_xy=True
        )
        west, south = transformer.transform(
            self.germany_bbox["west"], self.germany_bbox["south"]
        )
        east, north = transformer.transform(
            self.germany_bbox["east"], self.germany_bbox["north"]
        )
        logger.debug(
            "Reprojected Germany bbox to EPSG:%s: W=%.1f S=%.1f E=%.1f N=%.1f",
            epsg_code,
            west,
            south,
            east,
            north,
        )
        return {"west": west, "south": south, "east": east, "north": north}

    def _pixel_dims_for_geographic_bbox(
        self,
        bbox: dict[str, float],
        crs_urn: str,
    ) -> tuple[int | None, int | None]:
        """Compute pixel width/height needed for ``self.resolution_m``.

        For projected CRSs ``resx``/``resy`` are used instead of pixel counts,
        so this method returns ``(None, None)`` — the caller must then set
        ``resx = resy = self.resolution_m``.

        For geographic CRSs (EPSG:4326) the pixel count is derived from the
        bbox span and an approximate metres-per-degree conversion at the
        centre latitude of the bbox, ensuring the downloaded raster
        actually achieves the configured ground resolution.

        Parameters
        ----------
        bbox : dict[str, float]
            Bounding box in the CRS described by *crs_urn* with keys
            ``west``, ``south``, ``east``, ``north``.
        crs_urn : str
            OGC URN of the CRS, e.g. ``"urn:ogc:def:crs:EPSG::4326"``.

        Returns
        -------
        tuple[int | None, int | None]
            ``(width, height)`` pixel counts for geographic CRS, or
            ``(None, None)`` for projected CRS.
        """
        if "4326" not in crs_urn:
            # Projected CRS: resolution supplied via resx/resy, not pixel count.
            return None, None

        lat_centre = (bbox["south"] + bbox["north"]) / 2.0
        metres_per_deg_lon = 111_320.0 * math.cos(math.radians(lat_centre))
        metres_per_deg_lat = 111_320.0

        width = round(
            (bbox["east"] - bbox["west"]) * metres_per_deg_lon / self.resolution_m
        )
        height = round(
            (bbox["north"] - bbox["south"]) * metres_per_deg_lat / self.resolution_m
        )

        logger.debug(
            "Geographic CRS pixel dims for ~%d m resolution: %d x %d",
            self.resolution_m,
            width,
            height,
        )
        return width, height

    def _get_coverage_data(
        self,
        service_id: str,
        coverage_id: str,
        crs: str,
        west: float,
        south: float,
        east: float,
        north: float,
        output: str,
        width: int | None = None,
        height: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Request one coverage from SoilGrids WCS and write it to disk.

        Validates the CRS against the coverage's supported CRS list and derives
        the correct resolution parameters. For EPSG:4326 the pixel dimensions
        must be supplied via ``width``/``height``; for projected CRSs a fixed
        250 m resolution is used instead.

        Parameters
        ----------
        service_id : str
            SoilGrids service identifier, e.g. ``"clay"`` or ``"soc"``.
        coverage_id : str
            Full coverage identifier, e.g. ``"clay_0-5cm_mean"``.
        crs : str
            CRS in URN notation, e.g. ``"urn:ogc:def:crs:EPSG::4326"``.
        west, south, east, north : float
            Bounding box in the coordinate system defined by ``crs``.
        output : str
            Absolute path for the output GeoTIFF file (must end with ``.tif``).
        width : int, optional
            Pixel width of the requested coverage (required for EPSG:4326).
        height : int, optional
            Pixel height of the requested coverage (required for EPSG:4326).
        **kwargs
            Additional keyword arguments are accepted but ignored.

        Raises
        ------
        ValueError
            If ``crs`` is not supported by the coverage, the bounding box is
            invalid, ``width``/``height`` are missing for EPSG:4326, or the
            output path does not end with ``.tif``.
        Exception
            If the WCS server returns an error response.
        """
        try:
            wcs, coverage_list = self.sg._get_service_and_coverage_list(service_id)
            coverage_obj = self.sg._get_coverage_obj(wcs, coverage_list, coverage_id)

            # Validate requested CRS against those advertised by the service
            crs_list = [CRS.getcodeurn() for CRS in coverage_obj.supportedCRS]
            if crs not in crs_list:
                raise ValueError(
                    f"CRS {crs!r} not supported for {coverage_id!r}. "
                    f"Available: {crs_list}"
                )

            # Derive resolution parameters from the CRS type
            if "4326" in crs:
                if not (width and height):
                    raise ValueError(
                        "width and height are required when crs is EPSG:4326"
                    )
                resx = resy = None
            else:
                width = height = None
                resx = resy = self.resolution_m

            if west > east or south > north:
                raise ValueError(
                    f"Invalid bounding box: west={west}, south={south}, "
                    f"east={east}, north={north}"
                )

            if not output.endswith(".tif"):
                raise ValueError(f"Output path must end with '.tif', got: {output!r}")

            bbox = (west, south, east, north)
            response = wcs.getCoverage(
                identifier=coverage_id,
                crs=crs,
                bbox=bbox,
                resx=resx,
                resy=resy,
                width=width,
                height=height,
                response_crs=crs,
                format="GEOTIFF_INT16",
            )

            if response.info()["Content-Type"] == "image/tiff":
                with open(output, "wb") as fh:
                    fh.write(response.read())
            else:
                error_body = response.read().decode("utf-8")
                raise Exception(f"WCS server error for {coverage_id!r}: {error_body}")

        except Exception as exc:
            logger.error("SoilGrids WCS request failed for %s: %s", coverage_id, exc)
            raise
