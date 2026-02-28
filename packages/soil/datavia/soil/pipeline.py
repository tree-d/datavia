"""
Soil Pipeline - Complete soil data integration.

Provides end-to-end access to SoilGrids soil property data for Germany:
- Downloads selected soil properties via the SoilGrids WCS API.
- Stores results as a multi-band GeoTIFF and registers metadata in PostGIS.
- Exposes coordinate-based value retrieval via a GetterTiff-backed interface.

Planned extensions:
- BÜK shapefile integration (vector-based soil classification).
"""

import datetime
import logging
import os
import tempfile
from typing import Any

import numpy as np
import rasterio
from soilgrids import SoilGrids

from datavia.config import get_config
from datavia.core.getter_tiff import GetterTiff
from datavia.core.interfaces import Downloader, Pipeline
from datavia.core.saver_tiff import TiffSaver

logger = logging.getLogger(__name__)


class SoilGridsDownloader(Downloader):
    """Downloader for soil property data from the SoilGrids WCS API.

    Downloads a configurable selection of soil properties and depth layers
    for Germany, combining them into a single multi-band GeoTIFF to keep
    storage requirements manageable.
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
        """
        self.config = config

        self.sg = SoilGrids()
        logger.info("SoilGrids package initialized successfully")

        # Selective approach configuration from masterplan
        self.priority_properties = config.get(
            "properties", ["clay", "sand", "silt", "ph", "carbon"]
        )
        self.priority_depths = config.get("depths", ["0-5cm", "5-15cm"])
        self.priority_statistic = config.get(
            "statistic", "mean"
        )  # Default to 'mean' if not specified
        self.resolution = 250  # meters

        # Germany bounding box (EPSG:4326 for SoilGrids API)
        self.germany_bbox = {
            "west": 5.866,
            "south": 47.270,
            "east": 15.042,
            "north": 55.058,
        }

        logger.info(
            f"SoilGrids downloader configured: {len(self.priority_properties)} properties x {len(self.priority_depths)} depths x 1 statistic ({self.priority_statistic})"
        )

    def get_coverage_ids(self) -> list[str]:
        """Generate WCS coverage IDs for all configured property/depth combinations.

        Returns
        -------
        list[str]
            Coverage identifiers in the format ``{property}_{depth}_{statistic}``,
            e.g. ``"clay_0-5cm_mean"``.
        """
        coverage_ids = []
        for prop in self.priority_properties:
            for depth in self.priority_depths:
                coverage_id = f"{prop}_{depth}_{self.priority_statistic}"
                coverage_ids.append(coverage_id)

        logger.info(f"Generated {len(coverage_ids)} selective coverage IDs")
        return coverage_ids

    def download(self, output_path: str) -> str:
        """Download all configured soil coverages and combine them into one multi-band GeoTIFF.

        Each configured property/depth combination is downloaded individually and
        then merged into a single output file where each band corresponds to one
        coverage. Individual downloads are performed in a temporary directory.

        Parameters
        ----------
        output_path : str
            Absolute path for the resulting multi-band GeoTIFF file.

        Returns
        -------
        str
            Path to the written multi-band GeoTIFF, or ``"failed"`` if the
            download could not be completed.
        """
        try:
            coverage_ids = self.get_coverage_ids()
            logger.info(
                f"Starting SoilGrids selective download: {len(coverage_ids)} coverages"
            )

            # Create temporary directory for individual coverage files
            temp_dir = os.path.dirname(output_path)
            os.makedirs(temp_dir, exist_ok=True)

            with tempfile.TemporaryDirectory(dir=temp_dir) as temp_work_dir:
                temp_files = []

                # Download each coverage using soilgrids package
                for i, coverage_id in enumerate(coverage_ids):
                    temp_file = self._download_single_coverage(
                        coverage_id, temp_work_dir
                    )
                    if temp_file != "failed":
                        temp_files.append((temp_file, coverage_id))
                        logger.info(
                            f"Downloaded {i + 1}/{len(coverage_ids)}: {coverage_id}"
                        )
                    else:
                        logger.warning(f"Failed to download coverage: {coverage_id}")

                if not temp_files:
                    logger.error("No SoilGrids coverages downloaded successfully")
                    return "failed"

                # Combine individual TIFF files into multi-band TIFF
                combined_file = self._combine_coverages(temp_files, output_path)
                logger.info(f"SoilGrids selective download completed: {combined_file}")
                return combined_file

        except Exception as e:
            logger.error(f"SoilGrids download failed: {e}")
            return "failed"

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
            # Parse coverage_id to get service_id (property)
            service_id = coverage_id.split("_")[
                0
            ]  # e.g., 'clay' from 'clay_0-5cm_mean'
            temp_filepath = os.path.join(temp_dir, f"{coverage_id}.tif")

            # Map user-friendly property names to SoilGrids API service IDs
            _service_id_aliases: dict[str, str] = {
                "carbon": "soc",  # soil organic carbon
                "ph": "phh2o",  # pH in H2O
            }
            if service_id in _service_id_aliases:
                api_service_id = _service_id_aliases[service_id]
                coverage_id_api = coverage_id.replace(
                    service_id + "_", api_service_id + "_", 1
                )
                service_id = api_service_id
            else:
                coverage_id_api = coverage_id

            # Use soilgrids package for download
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

            # Validate downloaded file
            if os.path.exists(temp_filepath) and os.path.getsize(temp_filepath) > 0:
                logger.debug(f"Saved SoilGrids coverage: {temp_filepath}")
                return temp_filepath
            else:
                logger.error(
                    f"SoilGrids download resulted in empty/missing file: {temp_filepath}"
                )
                return "failed"

        except Exception as e:
            logger.error(f"Failed to download coverage {coverage_id}: {e}")
            return "failed"

    def _combine_coverages(self, temp_files: list[tuple], output_path: str) -> str:
        """Merge individual single-band GeoTIFFs into one multi-band GeoTIFF.

        Uses the first file as the spatial profile template. Each input file
        contributes one band to the output. The original CRS from the
        downloaded data is preserved. Band descriptions are set via
        :meth:`_enhance_band_description`.

        Parameters
        ----------
        temp_files : list[tuple[str, str]]
            List of ``(filepath, coverage_id)`` pairs to merge.
        output_path : str
            Absolute path for the resulting multi-band GeoTIFF.

        Returns
        -------
        str
            Path to the written multi-band GeoTIFF, or ``"failed"`` if merging
            failed or no valid input files were found.
        """
        try:
            # Read all input files
            src_files = []
            coverage_ids = []
            for temp_file, coverage_id in temp_files:
                try:
                    src = rasterio.open(temp_file)
                    src_files.append(src)
                    coverage_ids.append(coverage_id)
                    logger.debug(f"Opened coverage file: {coverage_id}")
                except Exception as e:
                    logger.warning(f"Failed to open {temp_file}: {e}")

            if not src_files:
                logger.error("No valid TIFF files to combine")
                return "failed"

            # Create multi-band output using first file as template.
            # Keep the original CRS from the downloaded data (EPSG:4326) so that
            # coordinate lookups in GetterTiff work correctly. Reprojection to
            # EPSG:25832 can be added later as a dedicated transform step.
            profile = src_files[0].profile.copy()
            profile.update(count=len(src_files))  # Multi-band

            with rasterio.open(output_path, "w", **profile) as dst:
                for i, (src, coverage_id) in enumerate(
                    zip(src_files, coverage_ids, strict=False), 1
                ):
                    try:
                        data = src.read(1)  # Read single band
                        dst.write(data, i)  # Write to band i

                        # Store enhanced band metadata
                        enhanced_desc = self._enhance_band_description(coverage_id)
                        dst.set_band_description(i, enhanced_desc)
                        logger.debug(f"Added band {i}: {enhanced_desc}")
                    except Exception as e:
                        logger.warning(f"Failed to process band {coverage_id}: {e}")

            # Close source files
            for src in src_files:
                src.close()

            logger.info(
                f"Combined {len(src_files)} coverages into multi-band TIFF: {output_path}"
            )
            return output_path

        except ImportError:
            logger.error("rasterio not available - cannot combine coverages")
            return "failed"
        except Exception as e:
            logger.error(f"Failed to combine coverages: {e}")
            return "failed"

    def _enhance_band_description(self, coverage_id: str) -> str:
        """Convert a raw coverage ID into a human-readable band description.

        Maps known property identifiers to descriptive labels including the
        physical unit, depth layer, and statistical summary, e.g.
        ``"clay_0-5cm_mean"`` → ``"Clay content (%) at 0-5cm depth (mean value)"``.
        Returns the original coverage ID unchanged if parsing fails.

        Parameters
        ----------
        coverage_id : str
            Coverage identifier in the format ``{property}_{depth}_{statistic}``.

        Returns
        -------
        str
            Human-readable description, or the original ``coverage_id`` if the
            format is unrecognised or an error occurs during parsing.
        """
        try:
            if "_" not in coverage_id:
                return coverage_id

            parts = coverage_id.split("_")
            if len(parts) >= 3:
                property_name = parts[0]
                depth = parts[1]
                statistic = parts[2]

                property_info = {
                    "clay": "Clay content (%)",
                    "sand": "Sand content (%)",
                    "silt": "Silt content (%)",
                    "ph": "pH in water",
                    "phh2o": "pH in water",
                    "carbon": "Organic carbon content (‰)",
                    "soc": "Soil organic carbon (g/kg)",
                }

                prop_desc = property_info.get(property_name, property_name)
                return f"{prop_desc} at {depth} depth ({statistic} value)"

            return coverage_id

        except Exception as e:
            logger.debug(f"Failed to enhance coverage description '{coverage_id}': {e}")
            return coverage_id

    def _get_coverage_data(
        self,
        service_id,
        coverage_id,
        crs,
        west,
        south,
        east,
        north,
        output,
        width=None,
        height=None,
        **kwargs,
    ):
        """Request a single coverage from the SoilGrids WCS service and write it to disk.

        Validates the CRS against the coverage's supported CRS list and derives
        the correct resolution parameters from it. For EPSG:4326 the pixel
        dimensions must be supplied via ``width``/``height``; for projected
        CRSs a fixed 250 m resolution is used instead.

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

            # Validate CRS
            crs_list = [CRS.getcodeurn() for CRS in coverage_obj.supportedCRS]
            if crs not in crs_list:
                raise ValueError(f"CRS {crs} not supported. Available: {crs_list}")

            # Set resolution parameters
            if "4326" in crs:
                if not (width and height):
                    raise ValueError("Width and height required for EPSG:4326")
                resx = resy = None
            else:
                width = height = None
                resx = resy = 250

            # Validate bounding box
            if west > east or south > north:
                raise ValueError("Invalid bounding box coordinates")
            bbox = (west, south, east, north)

            # Validate output file extension
            if not output.endswith(".tif"):
                raise ValueError("Output file must have .tif extension")

            # Make WCS request
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

            # Save response
            if response.info()["Content-Type"] == "image/tiff":
                with open(output, "wb") as file:
                    file.write(response.read())
            else:
                error_info = response.read().decode("utf-8")
                raise Exception(f"WCS server error: {error_info}")

        except Exception as e:
            logger.error(f"SoilGrids WCS request failed: {e}")
            raise


class SoilPipeline(Pipeline):
    """End-to-end pipeline for SoilGrids soil property data.

    Orchestrates downloading, storing, and querying multi-band GeoTIFF files
    containing soil properties for Germany. Uses :class:`SoilGridsDownloader`
    for data acquisition, :class:`~datavia.core.saver_tiff.TiffSaver` for
    storing data and registering metadata in PostGIS, and
    :class:`~datavia.core.getter_tiff.GetterTiff` for coordinate-based value
    retrieval.
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
            getter=GetterTiff,
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
        self, coords: np.ndarray, properties: list[str] | None = None, **kwargs
    ) -> dict[str, np.ndarray]:
        """Return soil property values at the given coordinates.

        Retrieves the band-to-description mapping from the database via
        :meth:`~datavia.core.getter_tiff.GetterTiff.get_band_mapping`, resolves
        each requested property to its corresponding band index, and delegates
        raster value extraction to
        :meth:`~datavia.core.getter_tiff.GetterTiff.get_data`. Properties that
        are not available in the stored data are returned as arrays of ``NaN``.

        Parameters
        ----------
        coords : np.ndarray
            Array of coordinates with shape ``(n_points, 2)``.
            Expected order is ``(longitude, latitude)`` for EPSG:4326.
        properties : list[str], optional
            Subset of soil properties to retrieve. Defaults to all properties
            configured on this instance (``self.properties``).
        **kwargs
            Additional keyword arguments forwarded to
            :meth:`~datavia.core.getter_tiff.GetterTiff.get_data`.

        Returns
        -------
        dict[str, np.ndarray]
            Mapping from property name to a 1-D array of extracted values,
            one value per coordinate. Unavailable properties map to arrays
            filled with ``NaN``.

        Raises
        ------
        RuntimeError
            If the pipeline has not been initialised (getter is ``None``).
        """
        try:
            if not self.getter:
                raise RuntimeError("Pipeline not initialized. Call the pipeline first.")

            # Retrieve description→band_index mapping from the getter (DB-backed)
            raw_band_mapping = self.getter.get_band_mapping()

            if not raw_band_mapping:
                logger.warning(
                    "No band metadata available for source '%s'. "
                    "Run update_data() first.",
                    self.name,
                )
                return {
                    prop: np.full(len(coords), np.nan)
                    for prop in (properties or self.properties)
                }

            # Translate description keys → property names
            prop_to_band: dict[str, int] = {}
            for description, band_idx in raw_band_mapping.items():
                prop_name = self._parse_property_from_description(description)
                if prop_name and prop_name not in prop_to_band:
                    prop_to_band[prop_name] = band_idx

            requested_props = properties or self.properties
            results: dict[str, np.ndarray] = {}

            for prop in requested_props:
                if prop in prop_to_band:
                    band_num = prop_to_band[prop]
                    # Delegate value extraction to the getter
                    values = self.getter.get_data(coords, band=band_num, **kwargs)
                    results[prop] = values
                    logger.debug(f"Extracted {prop} values from band {band_num}")
                else:
                    logger.warning(f"Property '{prop}' not available in soil data")
                    results[prop] = np.full(len(coords), np.nan)

            logger.info(
                f"Extracted soil data for {len(coords)} coordinates: {list(results.keys())}"
            )
            return results

        except Exception as e:
            logger.error(f"Error getting soil data: {e}")
            return {
                prop: np.full(len(coords), np.nan)
                for prop in (properties or self.properties)
            }

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
        """Download the latest soil data from SoilGrids and register it in the database.

        Generates a dated output path, runs the downloader to produce a
        multi-band GeoTIFF, and passes the result to the saver which writes
        file metadata to PostGIS.

        Returns
        -------
        bool
            ``True`` if both the download and the metadata save succeeded,
            ``False`` otherwise.
        """
        try:
            # Generate output path using pipeline name.
            # Filename follows the TiffSaver convention: {anything}_{anything}_{tag}.tif
            # where tag becomes part of the layer name: "{source_name}_{tag}".
            date_str = datetime.date.today().strftime("%Y%m%d")
            data_dir = get_config().data_directory
            temp_path = os.path.join(data_dir, "temp", f"soil_{date_str}_soilgrids.tif")

            # Download data using SoilGrids downloader
            logger.info("Starting soil data update from SoilGrids API")
            filepath = self.downloader.download(temp_path)

            if filepath == "failed":
                logger.error("Failed to download soil data")
                return False

            # Save metadata using pipeline name for database isolation
            """ success = self.saver.save_tiff_metadata(
                filepath,
                self.data_source,
                self.name,
                additional_metadata={
                    "properties": self.properties,
                    "data_source": "SoilGrids",
                    "api_url": "https://rest.soilgrids.org/soilgrids/v2.0/",
                },
            ) """
            success = self.saver.save(filepath)

            if success:
                logger.info("Successfully updated soil data from SoilGrids")
            else:
                logger.error("Failed to save soil metadata")

            return success

        except Exception as e:
            logger.error(f"Error updating soil data: {e}")
            return False

    def get_available_properties(self) -> list[str]:
        """Return soil properties that are actually present in the database.

        Queries the band metadata stored by
        :class:`~datavia.core.saver_tiff.TiffSaver` via
        :meth:`~datavia.core.getter_tiff.GetterTiff.get_band_mapping` and
        translates the human-readable band descriptions back to canonical
        property names using :meth:`_parse_property_from_description`.

        Falls back to the configured ``self.properties`` list when the getter
        is not yet initialised or no band metadata exists in the database
        (e.g. before the first :meth:`update_data` call).

        Returns
        -------
        list[str]
            Property names found in the database, e.g.
            ``["clay", "sand", "silt", "ph", "carbon"]``, or the configured
            list if the database has no data yet.
        """
        if self.getter:
            raw_band_mapping = self.getter.get_band_mapping()
            if raw_band_mapping:
                available: list[str] = []
                for description in raw_band_mapping:
                    prop = self._parse_property_from_description(description)
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
