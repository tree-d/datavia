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

import numpy as np
from typing import Dict, Any, List, Optional
import logging
import os
import tempfile

from ..core.interfaces import Pipeline, Downloader, Saver, Getter
from ..core.getter_tiff import getter_tiff
from ..core.saver_tiff import TiffSaver
from ..library.spatial_ops import extract_values_at_coords

logger = logging.getLogger(__name__)


class SoilGridsDownloader(Downloader):
    """Specialized downloader for SoilGrids API integration.

    Incorporates all development from SoilGridsAPIDownloader in fetcher architecture.
    Implements masterplan's selective strategy (280 MB vs 5.5 GB).
    """

    def __init__(self, config: Dict[str, Any]):
        """Initialize SoilGrids downloader with configuration."""
        self.config = config

        # Import soilgrids package (available in pixi environment)
        try:
            from soilgrids import SoilGrids

            self.sg = SoilGrids()
            logger.info("SoilGrids package initialized successfully")
        except ImportError as e:
            logger.error(f"Failed to import soilgrids package: {e}")
            raise

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
            f"SoilGrids downloader configured: {len(self.priority_properties)} properties × {len(self.priority_depths)} depths"
        )

    def get_coverage_ids(self) -> List[str]:
        """Generate selective coverage IDs for priority soil properties."""
        coverage_ids = []
        for prop in self.priority_properties:
            for depth in self.priority_depths:
                coverage_id = f"{prop}_{depth}_{self.priority_statistic}"
                coverage_ids.append(coverage_id)

        logger.info(f"Generated {len(coverage_ids)} selective coverage IDs")
        return coverage_ids

    def download_soil_data(self, output_path: str) -> str:
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
            logger.error(f"SoilGrids download failed: {str(e)}")
            return "failed"

    def _download_single_coverage(self, coverage_id: str, temp_dir: str) -> str:
        """Download a single soil property coverage using soilgrids package."""
        try:
            # Parse coverage_id to get service_id (property)
            service_id = coverage_id.split("_")[
                0
            ]  # e.g., 'clay' from 'clay_0-5cm_mean'
            temp_filepath = os.path.join(temp_dir, f"{coverage_id}.tif")

            # Handle special cases
            if service_id == "carbon":
                service_id = "soc"  # soil organic carbon uses 'soc' in SoilGrids
                coverage_id_api = coverage_id.replace("carbon", "soc")
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
            logger.error(f"Failed to download coverage {coverage_id}: {str(e)}")
            return "failed"

    def _combine_coverages(self, temp_files: List[tuple], output_path: str) -> str:
        """Combine individual TIFF files into multi-band TIFF."""
        try:
            import rasterio

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
                    logger.warning(f"Failed to open {temp_file}: {str(e)}")

            if not src_files:
                logger.error("No valid TIFF files to combine")
                return "failed"

            # Create multi-band output using first file as template
            profile = src_files[0].profile.copy()
            profile.update(count=len(src_files))  # Multi-band

            # Transform to standard CRS (EPSG:25832 from masterplan)
            profile.update(crs="EPSG:25832")

            with rasterio.open(output_path, "w", **profile) as dst:
                for i, (src, coverage_id) in enumerate(zip(src_files, coverage_ids), 1):
                    try:
                        data = src.read(1)  # Read single band
                        dst.write(data, i)  # Write to band i

                        # Store enhanced band metadata
                        enhanced_desc = self._enhance_band_description(coverage_id)
                        dst.set_band_description(i, enhanced_desc)
                        logger.debug(f"Added band {i}: {enhanced_desc}")
                    except Exception as e:
                        logger.warning(
                            f"Failed to process band {coverage_id}: {str(e)}"
                        )

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
            logger.error(f"Failed to combine coverages: {str(e)}")
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

    def __init__(self, name: str = "soil"):
        """Initialize soil pipeline."""
        super().__init__(
            name,
            downloader=SoilGridsDownloader,
            saver=TiffSaver,
            getter=getter_tiff,
            url=None,
        )

        # Default soil properties
        self.properties = ["clay", "sand", "silt", "ph", "carbon"]
        self.data_source = "SoilGrids"

    def __call__(self, *args, **kwds):
        return super().__call__(*args, **kwds)

    def get_data(
        self, coords: np.ndarray, properties: Optional[List[str]] = None, **kwargs
    ) -> Dict[str, np.ndarray]:
        """Get soil property values at coordinates."""
        try:
            # Get latest soil TIFF file using pipeline name
            my_layers = self.saver.get_my_raster_layers(self.name)

            if not my_layers:
                logger.warning("No soil data available. Run update first.")
                return {
                    prop: np.full(len(coords), np.nan)
                    for prop in (properties or self.properties)
                }

            # Get most recent layer
            latest_layer = max(my_layers, key=lambda x: x.get("acquisition_time", ""))
            tiff_path = latest_layer["uri"]

            # Get band metadata to map properties to bands
            band_mapping = self._get_band_mapping(tiff_path)

            # Extract values for requested properties
            requested_props = properties or self.properties
            results = {}

            for prop in requested_props:
                if prop in band_mapping:
                    band_num = band_mapping[prop]
                    values = self._extract_band_values(tiff_path, coords, band_num)
                    results[prop] = values
                    logger.debug(f"Extracted {prop} values from band {band_num}")
                else:
                    logger.warning(f"Property {prop} not available in soil data")
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

    def _get_band_mapping(self, tiff_path: str) -> Dict[str, int]:
        """Get mapping from property names to band numbers."""
        try:
            import rasterio

            band_mapping = {}
            with rasterio.open(tiff_path) as src:
                for i in range(1, src.count + 1):
                    try:
                        desc = src.get_band_description(i) or f"band_{i}"
                        # Parse enhanced description to get property name
                        prop_name = self._parse_property_from_description(desc)
                        if prop_name:
                            band_mapping[prop_name] = i
                    except Exception:
                        pass

            logger.debug(f"Band mapping: {band_mapping}")
            return band_mapping

        except Exception as e:
            logger.error(f"Failed to get band mapping: {e}")
            return {}

    def _parse_property_from_description(self, description: str) -> Optional[str]:
        """Parse property name from enhanced band description."""
        try:
            # Map enhanced descriptions back to property names
            property_mappings = {
                "Clay content": "clay",
                "Sand content": "sand",
                "Silt content": "silt",
                "pH in water": "ph",
                "Organic carbon content": "carbon",
                "Soil organic carbon": "carbon",
            }

            for desc_pattern, prop_name in property_mappings.items():
                if desc_pattern.lower() in description.lower():
                    return prop_name

            # Fallback: parse from coverage_id pattern
            if "_" in description:
                parts = description.split("_")
                if len(parts) >= 1:
                    prop_candidate = parts[0].lower()
                    if prop_candidate in [
                        "clay",
                        "sand",
                        "silt",
                        "ph",
                        "phh2o",
                        "carbon",
                        "soc",
                    ]:
                        return "carbon" if prop_candidate == "soc" else prop_candidate

            return None

        except Exception:
            return None

    def _extract_band_values(
        self, tiff_path: str, coords: np.ndarray, band: int
    ) -> np.ndarray:
        """Extract values from specific band using library function."""
        try:
            # Use library function with band parameter
            values = extract_values_at_coords(
                tiff_path, coords, source_crs="EPSG:4326", band=band
            )
            return values
        except Exception as e:
            logger.error(f"Error extracting band {band} values: {e}")
            return np.full(len(coords), np.nan)

    def update_data(self) -> bool:
        """Update soil data from SoilGrids API."""
        try:
            from ..config import get_config

            # Generate output path using pipeline name
            data_dir = get_config().data_directory
            output_path = os.path.join(data_dir, f"{self.name}_data.tif")

            # Download data using SoilGrids downloader
            logger.info("Starting soil data update from SoilGrids API")
            filepath = self.downloader.download_soil_data(output_path)

            if filepath == "failed":
                logger.error("Failed to download soil data")
                return False

            # Save metadata using pipeline name for database isolation
            success = self.saver.save_tiff_metadata(
                filepath,
                self.data_source,
                self.name,  # Use pipeline name instead of pipeline_id
                additional_metadata={
                    "properties": self.properties,
                    "data_source": "SoilGrids",
                    "api_url": "https://rest.soilgrids.org/soilgrids/v2.0/",
                },
            )

            if success:
                logger.info("Successfully updated soil data from SoilGrids")
            else:
                logger.error("Failed to save soil metadata")

            return success

        except Exception as e:
            logger.error(f"Error updating soil data: {e}")
            return False

    def get_available_properties(self) -> List[str]:
        """Get list of available soil properties."""
        return self.properties.copy()

    def get_data_info(self) -> Dict[str, Any]:
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
