"""
CompositeWeatherDownloader — orchestrates ERA5 and DWD station downloads.

Presents a single :class:`~datavia.core.interfaces.CompositeDownloader`-
compatible interface to :class:`~datavia.weather.pipeline.WeatherPipeline`.
Internally it delegates to
:class:`~datavia.weather.era5_downloader.ERA5Downloader` and
:class:`~datavia.weather.dwd_downloader.DWDStationDownloader`, running them
in sequence and returning both output paths as a newline-joined string so the
pipeline can hand them to ``SaverWeather.save()`` one at a time.

The two-path string convention is an internal protocol between
``CompositeWeatherDownloader`` and ``WeatherPipeline``; callers outside the
package should not depend on it.
"""

from __future__ import annotations

import logging
from typing import Any

from datavia.core.interfaces import CompositeDownloader, Downloader

from .dwd_downloader import DWDStationDownloader
from .era5_downloader import ERA5Downloader

logger = logging.getLogger(__name__)


class CompositeWeatherDownloader(CompositeDownloader):
    """Download ERA5 gridded data and DWD station observations in sequence.

    Both sub-downloaders are configured through a shared *config* dict so the
    pipeline only needs to pass configuration once.  Keys used:

    - ``variables`` (list[str]): Variable names forwarded to both backends.
      ERA5 maps them to CDS variable names; Open-Meteo uses the same names.
    - ``date_start`` (str): Download start date.
    - ``date_end`` (str): Download end date.
    - ``era5_bbox`` (list[float], optional): Override for the ERA5 bounding box.
    - ``dwd_stations`` (list[dict], optional): Override for the station list.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialise both sub-downloaders from a shared configuration dict.

        Parameters
        ----------
        config : dict[str, Any], optional
            Configuration dictionary. All keys are optional and fall back to
            the sub-downloader defaults when absent. See class docstring for
            recognised keys.
        """
        super().__init__()
        cfg = config or {}
        variables = cfg.get("variables", ["temperature_2m"])
        date_start = cfg.get("date_start")
        date_end = cfg.get("date_end")

        self._era5 = ERA5Downloader(
            variables=variables,
            date_start=date_start,
            date_end=date_end,
            bbox=cfg.get("era5_bbox"),
        )
        self._dwd = DWDStationDownloader(
            variables=variables,
            date_start=date_start,
            date_end=date_end,
            stations=cfg.get("dwd_stations"),
        )
        logger.info(
            "CompositeWeatherDownloader initialised — variables: %s, %s to %s",
            variables,
            date_start,
            date_end,
        )

    # ------------------------------------------------------------------
    # CompositeDownloader interface
    # ------------------------------------------------------------------

    @property
    def downloaders(self) -> list[Downloader]:
        """Return the ERA5 and DWD sub-downloaders.

        Returns
        -------
        list[Downloader]
            ``[ERA5Downloader, DWDStationDownloader]``
        """
        return [self._era5, self._dwd]

    def download(self) -> str:
        """Run ERA5 and DWD downloads and return the two output paths.

        Each sub-downloader writes its result to a separate temporary file.
        The two absolute paths are returned as a newline-joined string so
        ``WeatherPipeline`` can split and pass them individually to
        ``SaverWeather.save()``.

        Returns
        -------
        str
            Newline-joined absolute file paths for every successful download,
            e.g.::

                /tmp/era5_abc123.nc\n/tmp/dwd_stations_xyz789.parquet

            If ERA5 is unavailable (missing ``cdsapi`` package or missing
            ``~/.cdsapirc`` credentials) the ERA5 path is omitted and only
            the DWD path is returned — allowing partial updates without
            blocking station data.

            Returns ``"failed"`` only when *all* sub-downloads fail.

        Raises
        ------
        RuntimeError
            If the DWD download raises an unexpected exception (logged and
            re-raised so the pipeline can mark the update as failed).
        """
        paths: list[str] = []

        logger.info("CompositeWeatherDownloader: starting ERA5 download")
        try:
            era5_path = self._era5.download()
            logger.info("CompositeWeatherDownloader: ERA5 done -> %s", era5_path)
            paths.append(era5_path)
        except (ImportError, RuntimeError) as exc:
            logger.warning(
                "CompositeWeatherDownloader: ERA5 download skipped (%s). "
                "Continuing with DWD station data only. "
                "To enable ERA5, install cdsapi and create ~/.cdsapirc.",
                exc,
            )

        logger.info("CompositeWeatherDownloader: starting DWD download")
        dwd_path = self._dwd.download()
        logger.info("CompositeWeatherDownloader: DWD done -> %s", dwd_path)
        paths.append(dwd_path)

        if not paths:
            return "failed"
        return "\n".join(paths)
