"""
SoilGrids Downloader - WCS-based soil property data acquisition.

Responsible exclusively for fetching individual coverage files from the
SoilGrids WCS API and writing them to a temporary directory. No database
access, no alignment logic, no file combining — those concerns belong to the
saver and the pipeline orchestration layer.
"""

import logging
import os
import tempfile
from typing import Any

from soilgrids import SoilGrids

from datavia.core.interfaces import Downloader

logger = logging.getLogger(__name__)


class SoilGridsDownloader(Downloader):
    """Downloader for soil property data from the SoilGrids WCS API.

    Downloads a configurable selection of soil properties and depth layers
    for Germany. Each coverage is written as an individual single-band
    GeoTIFF so that the caller can inspect spatial alignment before deciding
    how to combine or store the files.
    """

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
        """
        self.sg = SoilGrids()
        logger.info("SoilGrids package initialized successfully")

        self.priority_properties = config.get(
            "properties", ["clay", "sand", "silt", "ph", "carbon"]
        )
        self.priority_depths = config.get("depths", ["0-5cm", "5-15cm"])
        self.priority_statistic = config.get("statistic", "mean")
        self.resolution = 250  # metres

        # Germany bounding box (EPSG:4326)
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

        for i, coverage_id in enumerate(coverage_ids, 1):
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
            service_id = coverage_id.split("_")[0]  # e.g. 'clay' from 'clay_0-5cm_mean'
            temp_filepath = os.path.join(temp_dir, f"{coverage_id}.tif")

            # Map user-friendly property names to SoilGrids API service IDs
            _service_id_aliases: dict[str, str] = {
                "carbon": "soc",  # soil organic carbon
                "ph": "phh2o",  # pH measured in water
            }
            if service_id in _service_id_aliases:
                api_service_id = _service_id_aliases[service_id]
                coverage_id_api = coverage_id.replace(
                    service_id + "_", api_service_id + "_", 1
                )
                service_id = api_service_id
            else:
                coverage_id_api = coverage_id

            self._get_coverage_data(
                service_id=service_id,
                coverage_id=coverage_id_api,
                west=self.germany_bbox["west"],
                south=self.germany_bbox["south"],
                east=self.germany_bbox["east"],
                north=self.germany_bbox["north"],
                height=self.resolution,
                width=self.resolution,
                crs="urn:ogc:def:crs:EPSG::4326",
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
        """Request a single coverage from the SoilGrids WCS service and write it to disk.

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
                resx = resy = 250

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
