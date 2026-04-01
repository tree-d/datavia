"""
Elevation Pipeline (renamed from Topography).

Self-contained pipeline for elevation data using generic TIFF handling.
"""

import logging

from datavia.core.downloader_url import TiffDownloader
from datavia.core.getter_tiff import GetterTiff
from datavia.core.interfaces import Pipeline
from datavia.core.saver_tiff import TiffSaver

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
        getter = GetterTiff
        super().__init__(name, downloader, saver, getter, url=url)

    def update_data(
        self,
        reproject: bool = False,
        resolution_m: int | None = None,
    ) -> bool:
        """Update elevation data by downloading and saving if not already stored.

        Follows the canonical pipeline flow:
        1. Synchronise the filesystem and database (maintenance, Saver).
        2. Ask the Getter which layers are already stored (DB read).
        3. Download and save only when no data exists yet.

        Parameters
        ----------
        reproject : bool, optional
            When ``True`` the saved file is reprojected in-place to the
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
            ``True`` if elevation data was downloaded and saved successfully,
            or if data was already up to date.
        """
        if self.getter is None or self.downloader is None or self.saver is None:
            self()
        logger.info("Checking elevation data...")
        # Maintenance step: reconcile filesystem with DB metadata.
        self.saver.sync_files_and_database()
        existing_layers = self.getter.check_existing_layers()
        if existing_layers:
            logger.info("Elevation data already up to date.")
            return True
        logger.info("No elevation data found. Downloading new data...")
        return super().update_data(reproject=reproject, resolution_m=resolution_m)
