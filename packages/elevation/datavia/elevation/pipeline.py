"""
Elevation Pipeline (renamed from Topography).

Self-contained pipeline for elevation data using generic TIFF handling.
"""

import logging
from typing import Any

from datavia.core.downloader_url import TiffDownloader
from datavia.core.getter_tiff import GetterTiff
from datavia.core.interfaces import Pipeline
from datavia.core.saver_tiff import TiffSaver

logger = logging.getLogger(__name__)

#: Default WCS URL for the 200 m digital elevation model of Germany.
_DEFAULT_URL: str = (
    "https://sgx.geodatenzentrum.de/wcs_dgm200_inspire"
    "?VERSION=2.0.1&SERVICE=WCS&REQUEST=GetCoverage"
    "&COVERAGEID=dgm200_inspire__EL.GridCoverage"
    "&format=image/tiff&crs=EPSG:25832"
    "&bbox=280000,5235000,921000,6101000"
)

#: Keys that must be present in the config dict.
_REQUIRED_CONFIG_KEYS: frozenset[str] = frozenset({"source"})

#: All valid config keys (required + optional).
_KNOWN_CONFIG_KEYS: frozenset[str] = _REQUIRED_CONFIG_KEYS | frozenset({"url"})


class ElevationPipeline(Pipeline):
    """Complete elevation data pipeline using generic TIFF handling.

    Configured via a ``config`` dict with the following keys:

    - ``source`` (str, **required**): Identifier used for database isolation
      and file naming, e.g. ``"elevation"``.
    - ``url`` (str, optional): WCS URL for the elevation service.  Defaults
      to the 200 m DEM of Germany from GeoBasis-DE / BKG.

    Passing ``config=None`` (or calling with no arguments) is equivalent to
    ``config={"source": "elevation"}`` with all defaults applied.

    When registered with :class:`~datavia.core.datavia.Datavia`, the
    pipeline is reachable as ``dv.<source>`` (``dv.elevation`` by default).

    The default dataset is the BKG DGM200: 200 m resolution, Germany only,
    native CRS EPSG:25832.  :meth:`get_data` returns elevation in metres
    above sea level, as a ``(N,)`` array.  Points outside the raster give
    ``nan``.

    Usage::

        import numpy as np
        from datavia.elevation import ElevationPipeline

        pipe = ElevationPipeline()
        pipe.update_data()
        coords = np.array([[13.405, 52.52], [11.58, 48.14]])  # [lon, lat]
        heights = pipe.get_data(coords, crs_coords="EPSG:4326")
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialise the elevation pipeline.

        Parameters
        ----------
        config : dict[str, Any], optional
            Configuration dict.  When provided it must contain ``"source"``
            and may contain ``"url"`` to override the default WCS endpoint.
            When ``None`` (default) the pipeline is initialised with
            ``source="elevation"`` and the built-in Germany DEM URL.
        """
        if config is not None:
            Pipeline.validate_pipeline_config(
                config,
                _REQUIRED_CONFIG_KEYS,
                _KNOWN_CONFIG_KEYS,
                "ElevationPipeline",
            )
            name: str = config["source"]
            url: str = config.get("url", _DEFAULT_URL)
        else:
            name = "elevation"
            url = _DEFAULT_URL

        super().__init__(name, TiffDownloader, TiffSaver, GetterTiff, url=url)

    def update_data(
        self,
        reproject: bool = False,
        resolution_m: int | None = None,
    ) -> bool:
        """Update elevation data by downloading and saving if not already stored.

        Follows the canonical pipeline flow:

        1. Synchronise the filesystem and database via
           :meth:`~datavia.core.interfaces.Pipeline.sync_files_and_database`
           (Pipeline base class).  Removes orphan DB rows for deleted files
           and re-registers orphan disk files with no DB record.
        2. Ask the Getter which layers are already stored (DB read).
        3. Download and save only when no data exists yet.

        Because of step 3, once any layer is stored for this source, later
        calls do nothing, even if ``url`` has changed.  To force a fresh
        download, delete the source's GeoTIFF from
        ``get_config().data_directory``.  The next call removes the stale
        database row and downloads again.  Alternatively, use a new
        ``source`` name.

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
        self.sync_files_and_database()
        existing_layers = self.getter.get_existing_layers()
        if existing_layers:
            logger.info("Elevation data already up to date.")
            return True
        logger.info("No elevation data found. Downloading new data...")
        return super().update_data(reproject=reproject, resolution_m=resolution_m)
