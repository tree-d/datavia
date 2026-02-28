"""
Soil Pipeline - Complete soil data integration.

Self-contained pipeline incorporating all soil development from the fetcher architecture:
- SoilGrids API integration (from SoilGridsAPIDownloader)
- Multi-band TIFF processing (from TiffSaver enhancements)
- Selective approach (280 MB vs 5.5 GB from masterplan)
- Database isolation and metadata storage

Follows masterplan Phase 1.3 objectives:
- Week 1-2: SoilGrids API integration (TIFF-based soil properties)
- Future: BÜK shapefile integration (vector-based soil classification)
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
    """Specialized downloader for SoilGrids API integration.

    Incorporates all development from SoilGridsAPIDownloader in fetcher architecture.
    Implements masterplan's selective strategy (280 MB vs 5.5 GB).
    """

    def __init__(self, config: dict[str, Any]):
        """Initialize SoilGrids downloader with configuration."""
        self.config = config

        self.sg = SoilGrids()
        logger.info("SoilGrids package initialized successfully")

        # Selective approach configuration from masterplan
        self.priority_properties = config.get(
            "properties", ["clay", "sand", "silt", "ph", "carbon"]
        )
        self.priority_depths = config.get("depths", ["0-5cm", "5-15cm"])
        self.priority_statistic = "mean"
        self.resolution = 250  # meters

        # Germany bounding box (EPSG:4326 for SoilGrids API)
        self.germany_bbox = {
            "west": 5.866,
            "south": 47.270,
            "east": 15.042,
            "north": 55.058,
        }

        logger.info(
            f"SoilGrids downloader configured: {len(self.priority_properties)} properties x {len(self.priority_depths)} depths"
        )

    def get_coverage_ids(self) -> list[str]:
        """Generate selective coverage IDs for priority soil properties."""
        coverage_ids = []
        for prop in self.priority_properties:
            for depth in self.priority_depths:
                coverage_id = f"{prop}_{depth}_{self.priority_statistic}"
                coverage_ids.append(coverage_id)

        logger.info(f"Generated {len(coverage_ids)} selective coverage IDs")
        return coverage_ids

    def download(self, output_path: str) -> str:
        """Download SoilGrids data using selective approach.

        Incorporates all logic from fetcher/downloaders.py SoilGridsAPIDownloader.
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
        """Download a single soil property coverage using soilgrids package."""
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
        """Combine individual TIFF files into multi-band TIFF."""
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
        """Enhance coverage ID to readable description.

        From fetcher/aggregators.py _enhance_band_description()
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
        """Fetch coverage data from SoilGrids WCS service.

        Adapted from fetcher/downloaders.py SoilGridsAPIDownloader.get_coverage_data()
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
    """Complete soil data pipeline."""

    def __init__(
        self,
        name: str = "soil",
        properties: list[str] | None = None,
        depths: list[str] | None = None,
    ):
        """Initialize soil pipeline.
        Properties and depths can be customized,
        but default to the most commonly used ones
        based on masterplan and SoilGrids API capabilities.
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
        logger.info(
            f"SoilPipeline initialized with properties: {self.properties} and depths: {self.depths}"
        )

    def __call__(self, *args, **kwds):
        config = {
            "properties": self.properties,
            "depths": self.depths,
        }
        return super().__call__(config, *args, **kwds)

    def get_data(
        self, coords: np.ndarray, properties: list[str] | None = None, **kwargs
    ) -> dict[str, np.ndarray]:
        """Get soil property values at coordinates.

        Delegates to the getter which handles TIFF access and band selection.
        Uses band metadata stored by TiffSaver to map property names to band
        indices without opening the TIFF file directly.
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
        """Update soil data from SoilGrids API."""
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
        """Get list of available soil properties."""
        return self.properties.copy()

    def get_data_info(self) -> dict[str, Any]:
        """Get detailed information about soil pipeline data."""
        base_info = super().get_data_info()
        base_info.update(
            {
                "available_properties": self.get_available_properties(),
                "data_source_api": "SoilGrids REST API",
                "coverage": "Germany",
                "resolution": "250m",
                "format": "Multi-band GeoTIFF",
            }
        )
        return base_info
