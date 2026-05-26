"""
HiHydroSoil Downloader - vsicurl-based soil hydraulic property data acquisition.

Downloads individual coverages from the HiHydroSoil GeoTIFF catalogue at
http://opendap.biodt.eu/grasslands-pdt/soilMapsHiHydroSoil/ and clips
each file to the Germany bounding box using rasterio's window-based read.
No full file is downloaded; only the Germany tile is streamed via GDAL's
/vsicurl HTTP range-request layer, which is part of the GDAL core and
requires no additional dependencies beyond rasterio.
"""

import logging
import os
import tempfile
from typing import Any, ClassVar

import rasterio
from rasterio.windows import from_bounds

from datavia.core.interfaces import Downloader

logger = logging.getLogger(__name__)


class HiHydroSoilDownloader(Downloader):
    """Downloader for soil hydraulic property data from the HiHydroSoil catalogue.

    Streams individual GeoTIFF coverages from the OpenDAP HTTP server using
    GDAL's ``/vsicurl/`` virtual filesystem — only the bytes covering Germany
    are transferred via HTTP range requests. Each coverage is clipped to the
    Germany bounding box and written as a compressed single-band GeoTIFF so
    the caller can store it directly without any further processing.

    Supported properties (canonical pipeline names):
    ``"field_capacity"``, ``"wilting_point"``, ``"porosity"``,
    ``"hydraulic_conductivity"``.

    Available depths:
    ``"0-5cm"``, ``"5-15cm"``, ``"15-30cm"``, ``"30-60cm"``,
    ``"60-100cm"``, ``"100-200cm"``.
    """

    #: Base URL of the HiHydroSoil catalogue.
    _BASE_URL: ClassVar[str] = (
        "http://opendap.biodt.eu/grasslands-pdt/soilMapsHiHydroSoil"
    )

    #: Maps canonical pipeline property names to HiHydroSoil file prefixes.
    _CANONICAL_TO_PREFIX: ClassVar[dict[str, str]] = {
        "field_capacity": "WCpF2",
        "wilting_point": "WCpF4.2",
        "porosity": "WCsat",
        "hydraulic_conductivity": "Ksat",
    }

    #: Maps pipeline statistic tokens to the single-character file suffix used
    #: in HiHydroSoil filenames (e.g. ``"mean"`` → ``"M"``).
    _STATISTIC_TO_SUFFIX: ClassVar[dict[str, str]] = {
        "mean": "M",
    }

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialise the HiHydroSoil downloader.

        Parameters
        ----------
        config : dict[str, Any]
            Configuration dictionary with optional keys:

            - ``properties`` (list[str]): Canonical property names to download.
              Defaults to all four supported properties.
            - ``depths`` (list[str]): Depth layers to download.
              Defaults to all six available depth layers.
            - ``statistic`` (str): Statistical summary to retrieve.
              Only ``"mean"`` is currently supported. Defaults to ``"mean"``.
        """
        self.priority_properties: list[str] = config.get(
            "properties",
            [
                "field_capacity",
                "wilting_point",
                "porosity",
                "hydraulic_conductivity",
            ],
        )
        self.priority_depths: list[str] = config.get(
            "depths",
            ["0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm", "100-200cm"],
        )
        self.priority_statistic: str = config.get("statistic", "mean")

        # Germany bounding box (EPSG:4326) — identical to SoilGridsDownloader.
        self.germany_bbox: dict[str, float] = {
            "west": 5.866,
            "south": 47.270,
            "east": 15.042,
            "north": 55.058,
        }

        logger.info(
            "HiHydroSoilDownloader configured: %d properties x %d depths"
            " x statistic=%s",
            len(self.priority_properties),
            len(self.priority_depths),
            self.priority_statistic,
        )

    # ------------------------------------------------------------------
    # Public interface (Downloader contract)
    # ------------------------------------------------------------------

    def get_coverage_ids(
        self,
        properties: list[str] | None = None,
        depths: list[str] | None = None,
        statistic: str | None = None,
    ) -> list[str]:
        """Generate coverage IDs for a given property/depth/statistic combination.

        Only properties that have a known HiHydroSoil file prefix are
        included. Unknown properties are skipped with a debug log entry.

        Parameters
        ----------
        properties : list[str], optional
            Canonical property names. Defaults to ``self.priority_properties``.
        depths : list[str], optional
            Depth layer strings. Defaults to ``self.priority_depths``.
        statistic : str, optional
            Statistical summary identifier. Defaults to
            ``self.priority_statistic``.

        Returns
        -------
        list[str]
            Coverage identifiers in the format ``{property}_{depth}_{statistic}``,
            e.g. ``"field_capacity_0-5cm_mean"``.
        """
        effective_properties = properties or self.priority_properties
        effective_depths = depths or self.priority_depths
        effective_statistic = statistic or self.priority_statistic

        known = set(self._CANONICAL_TO_PREFIX)
        skipped = [p for p in effective_properties if p not in known]
        if skipped:
            logger.debug(
                "HiHydroSoilDownloader: skipping properties not in catalogue: %s",
                skipped,
            )

        coverage_ids = [
            f"{prop}_{depth}_{effective_statistic}"
            for prop in effective_properties
            if prop in known
            for depth in effective_depths
        ]
        logger.info("Generated %d HiHydroSoil coverage IDs", len(coverage_ids))
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
            Path to the first successfully downloaded file, or ``"failed"``
            if no coverage could be downloaded.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            results = self.download_coverages(self.get_coverage_ids(), temp_dir)
        return results[0][0] if results else "failed"

    def download_coverages(
        self,
        coverage_ids: list[str],
        output_dir: str,
    ) -> list[tuple[str, str]]:
        """Download a set of coverage IDs as Germany-clipped single-band GeoTIFFs.

        Streams only the Germany window from each global GeoTIFF via GDAL's
        ``/vsicurl/`` HTTP range-request layer. The source file is never
        downloaded in full.

        Parameters
        ----------
        coverage_ids : list[str]
            Canonical coverage identifiers to download,
            e.g. ``["field_capacity_0-5cm_mean"]``.
        output_dir : str
            Directory in which to write the individual ``{coverage_id}.tif``
            files. The directory must already exist.

        Returns
        -------
        list[tuple[str, str]]
            List of ``(absolute_path, coverage_id)`` pairs for every coverage
            that was downloaded successfully. Failed downloads are omitted.
        """
        results: list[tuple[str, str]] = []
        total = len(coverage_ids)

        for i, coverage_id in enumerate(coverage_ids, 1):
            path = self._download_single_coverage(coverage_id, output_dir)
            if path != "failed":
                results.append((path, coverage_id))
                logger.info("Downloaded %d/%d: %s", i, total, coverage_id)
            else:
                logger.warning(
                    "Failed to download HiHydroSoil coverage: %s", coverage_id
                )

        logger.info(
            "download_coverages finished: %d/%d successful", len(results), total
        )
        return results

    def get_remote_available_properties(self) -> dict[str, list[str]]:
        """Discover all properties and depth layers available in the HiHydroSoil catalogue.

        Fetches the HTTP directory listing at :attr:`_BASE_URL` and parses the
        HTML ``href`` links to extract every ``*_250m.tif`` filename. Each
        filename follows the pattern ``{prefix}_{depth}_{suffix}_250m.tif``;
        the prefix is reverse-mapped to a canonical property name via
        :attr:`_CANONICAL_TO_PREFIX`. Properties and depths not present in
        the directory listing are omitted, so genuinely new files uploaded to
        the server are discovered automatically.

        Falls back to an empty dict if the directory listing cannot be fetched
        or parsed.

        Returns
        -------
        dict[str, list[str]]
            Mapping of canonical property name → sorted list of available depth
            strings, e.g.
            ``{"field_capacity": ["0-5cm", "5-15cm", ...], ...}``.
        """
        import re
        import urllib.request

        prefix_to_canonical = {v: k for k, v in self._CANONICAL_TO_PREFIX.items()}
        catalogue: dict[str, set[str]] = {}

        listing_url = self._BASE_URL + "/"
        try:
            with urllib.request.urlopen(listing_url, timeout=15) as response:  # nosec B310 — URL is always derived from the hardcoded _BASE_URL constant (http://), never from user input
                html = response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            logger.warning(
                "HiHydroSoil: could not fetch directory listing from '%s': %s",
                listing_url,
                exc,
            )
            return {}

        # Extract all .tif filenames from href attributes in the listing.
        # Expected filename format: {prefix}_{depth}_{suffix}_250m.tif
        # Example: WCpF2_0-5cm_M_250m.tif
        for filename in re.findall(r'href="([^"]+_250m\.tif)"', html):
            # Strip path components — keep only the basename.
            basename = filename.rsplit("/", 1)[-1]
            # Remove the fixed trailing token to get "{prefix}_{depth}_{suffix}".
            core = basename.removesuffix("_250m.tif")
            parts = core.split("_")
            if len(parts) != 3:
                logger.debug(
                    "HiHydroSoil: skipping unexpected filename format '%s'", basename
                )
                continue
            file_prefix, depth, _suffix = parts
            canonical = prefix_to_canonical.get(file_prefix)
            if canonical is None:
                logger.debug(
                    "HiHydroSoil: unknown file prefix '%s' in '%s' — not in _CANONICAL_TO_PREFIX",
                    file_prefix,
                    basename,
                )
                continue
            catalogue.setdefault(canonical, set()).add(depth)

        result = {prop: sorted(depths) for prop, depths in sorted(catalogue.items())}
        logger.info(
            "HiHydroSoil remote catalogue: %d properties, depths per property: %s",
            len(result),
            {p: len(d) for p, d in result.items()},
        )
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_url(self, coverage_id: str) -> str | None:
        """Construct the full HTTP URL for a canonical coverage ID.

        Translates the canonical property name and statistic token to the
        HiHydroSoil filename convention
        (``{prefix}_{depth}_{suffix}_250m.tif``) and prepends ``_BASE_URL``.
        Uses longest-prefix matching to handle compound property names that
        contain underscores (e.g. ``"field_capacity"``).

        Parameters
        ----------
        coverage_id : str
            Canonical coverage identifier, e.g. ``"field_capacity_0-5cm_mean"``.

        Returns
        -------
        str | None
            Fully qualified URL such as
            ``".../WCpF2_0-5cm_M_250m.tif"``, or ``None`` if the property
            or statistic token cannot be translated.
        """
        matched_prop: str | None = None
        remainder: str = ""
        for prop in sorted(self._CANONICAL_TO_PREFIX, key=len, reverse=True):
            prefix = prop + "_"
            if coverage_id.startswith(prefix):
                matched_prop = prop
                remainder = coverage_id[len(prefix) :]
                break

        if matched_prop is None:
            logger.error(
                "Cannot build URL: no HiHydroSoil property prefix matches '%s'",
                coverage_id,
            )
            return None

        # remainder is "{depth}_{statistic}"
        sep = remainder.find("_")
        if sep == -1:
            logger.error(
                "Malformed coverage ID (missing statistic token): '%s'", coverage_id
            )
            return None
        depth = remainder[:sep]
        statistic = remainder[sep + 1 :]

        file_prefix = self._CANONICAL_TO_PREFIX[matched_prop]
        file_suffix = self._STATISTIC_TO_SUFFIX.get(statistic)
        if file_suffix is None:
            logger.error(
                "Unknown statistic '%s' for HiHydroSoil coverage '%s'. "
                "Supported statistics: %s",
                statistic,
                coverage_id,
                list(self._STATISTIC_TO_SUFFIX),
            )
            return None

        filename = f"{file_prefix}_{depth}_{file_suffix}_250m.tif"
        return f"{self._BASE_URL}/{filename}"

    def _download_single_coverage(self, coverage_id: str, output_dir: str) -> str:
        """Stream a single coverage from the HiHydroSoil catalogue, clipped to Germany.

        Opens the remote GeoTIFF through GDAL's ``/vsicurl/`` virtual
        filesystem so only the bytes intersecting the Germany bounding box
        are transferred via HTTP range requests. The clipped raster is
        written as a compressed, tiled GeoTIFF.

        Parameters
        ----------
        coverage_id : str
            Canonical coverage identifier, e.g. ``"field_capacity_0-5cm_mean"``.
        output_dir : str
            Directory where the clipped GeoTIFF will be written.

        Returns
        -------
        str
            Absolute path to the written GeoTIFF, or ``"failed"`` if the
            streaming or clip step did not succeed.
        """
        url = self._build_url(coverage_id)
        if url is None:
            return "failed"

        vsicurl_path = f"/vsicurl/{url}"
        output_path = os.path.join(output_dir, f"{coverage_id}.tif")

        try:
            with (
                rasterio.Env(
                    # Prevent GDAL from probing for sidecar files (.aux, .msk, …)
                    # on the remote server, which would trigger 502 responses for
                    # files that do not exist there.
                    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
                ),
                rasterio.open(vsicurl_path) as src,
            ):
                bbox = self.germany_bbox
                window = from_bounds(
                    left=bbox["west"],
                    bottom=bbox["south"],
                    right=bbox["east"],
                    top=bbox["north"],
                    transform=src.transform,
                )
                data = src.read(1, window=window)
                germany_transform = src.window_transform(window)

                profile = src.profile.copy()
                profile.update(
                    driver="GTiff",
                    height=data.shape[0],
                    width=data.shape[1],
                    transform=germany_transform,
                    count=1,
                    crs=src.crs,
                    compress="lzw",
                    tiled=True,
                    blockxsize=256,
                    blockysize=256,
                )

            with rasterio.open(output_path, "w", **profile) as dst:
                dst.write(data, 1)

            if os.path.getsize(output_path) > 0:
                logger.debug(
                    "Saved Germany-clipped HiHydroSoil coverage: %s", output_path
                )
                return output_path

            logger.error(
                "HiHydroSoil clip produced empty file for coverage '%s'", coverage_id
            )
            return "failed"

        except Exception as exc:
            logger.error(
                "Failed to download/clip HiHydroSoil coverage '%s' from '%s': %s",
                coverage_id,
                url,
                exc,
            )
            return "failed"
