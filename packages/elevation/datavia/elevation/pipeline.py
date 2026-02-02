"""
Elevation Pipeline (renamed from Topography).

Self-contained pipeline for elevation data using generic TIFF handling.
"""

import numpy as np
from typing import Dict, Any
import logging
import os

from datavia.core.interfaces import Pipeline
from datavia.core.getter_tiff import getter_tiff
from datavia.core.saver_tiff import TiffSaver
from datavia.core.downloader_url import TiffDownloader

logger = logging.getLogger(__name__)


class ElevationPipeline(Pipeline):
    """Complete elevation data pipeline using generic TIFF handling. Its name is 'elevation'."""

    def __init__(
        self,
        name: str = "elevation",
        url: str = "https://sgx.geodatenzentrum.de/wcs_dgm200_inspire?VERSION=2.0.1&SERVICE=WCS&REQUEST=GetCoverage&COVERAGEID=dgm200_inspire__EL.GridCoverage&format=image/tiff&crs=EPSG:25832&bbox=280000,5235000,921000,6101000",
    ):
        """Initialize the elevation pipeline with TIFF handlers."""
        downloader = TiffDownloader
        saver = TiffSaver
        getter = getter_tiff
        super().__init__(name, downloader, saver, getter, url=url)

    def update_data(self):
        """Update elevation data by downloading and saving new TIFF data."""
        logger.info("Checking elevation data...")
        files = self.saver.sync_files_and_database()
        if not files:
            logger.info("No elevation data found. Downloading new data...")
            return super().update_data()
        else:
            logger.info("Elevation data already up to date.")
            return True
