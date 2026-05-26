"""
CompositeWeatherDownloader — orchestrates gridded and DWD station downloads.

Presents a single :class:`~datavia.core.interfaces.CompositeDownloader`-
compatible interface to :class:`~datavia.weather.pipeline.WeatherPipeline`.
Internally it uses :func:`~datavia.weather.source_registry.get_grid_downloader_class`
to select the appropriate grid downloader (e.g.
:class:`~datavia.weather.era5_downloader.ERA5Downloader` or
:class:`~datavia.weather.hyras_downloader.HYRASDownloader`) and optionally
instantiates :class:`~datavia.weather.dwd_downloader.DWDStationDownloader`
when station data is requested.  All output paths are returned as a
newline-joined string so the pipeline can hand them to
``SaverWeather.save()`` one at a time.

The multi-path string convention is an internal protocol between
``CompositeWeatherDownloader`` and ``WeatherPipeline``; callers outside the
package should not depend on it.
"""

from __future__ import annotations

import logging
from typing import Any

from datavia.core.interfaces import CompositeDownloader, Downloader

from .dwd_downloader import DWDStationDownloader
from .source_registry import get_grid_downloader_class

logger = logging.getLogger(__name__)


class CompositeWeatherDownloader(CompositeDownloader):
    """Download gridded weather data and optionally DWD station data in sequence.

    The active sub-downloaders depend on ``config["source"]`` and the presence
    of ``config["dwd_stations"]``:

    .. list-table::
       :header-rows: 1

       * - ``source``
         - ``dwd_stations`` in config?
         - Active downloaders
       * - ``"ERA5_land"``
         - no
         - :class:`~datavia.weather.era5_downloader.ERA5Downloader`
       * - ``"ERA5_land"``
         - yes
         - ERA5 + :class:`~datavia.weather.dwd_downloader.DWDStationDownloader`
       * - ``"HYRAS"``
         - no
         - :class:`~datavia.weather.hyras_downloader.HYRASDownloader`
       * - ``"HYRAS"``
         - yes
         - HYRAS + DWD
       * - ``"DWD_stations"``
         - (implied)
         - DWD only

    Keys used from *config*:

    - ``source`` (str): Source identifier (see table above).
      Defaults to ``"ERA5_land"``.
    - ``variables`` (list[str]): Variable names forwarded to all backends.
    - ``date_start`` (str): Download start date.
    - ``date_end`` (str): Download end date.
    - ``era5_bbox`` (list[float], optional): Override for the ERA5 bounding box.
    - ``buffer_days`` (int, optional): Extra days prepended for ERA5
      accumulative variables.
    - ``dwd_stations`` (list[dict], optional): Override for the DWD station
      list.  When present, a DWD downloader is always added alongside the
      grid downloader.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialise sub-downloaders from a shared configuration dict.

        Parameters
        ----------
        config : dict[str, Any], optional
            Configuration dictionary.  See class docstring for recognised keys.
            When *config* is omitted the ``"ERA5_land"`` grid downloader is
            used with all sub-downloader defaults.
        """
        super().__init__()
        cfg = config or {}
        source: str = cfg.get("source", "ERA5_land")
        variables: list[str] = cfg.get("variables", ["temperature_2m"])
        date_start: str | None = cfg.get("date_start")
        date_end: str | None = cfg.get("date_end")

        # --- Grid downloader (ERA5, HYRAS, or None for DWD-only) ---
        grid_class = get_grid_downloader_class(source)
        if grid_class is not None:
            grid_kwargs: dict[str, Any] = {
                "variables": variables,
                "date_start": date_start,
                "date_end": date_end,
            }
            # bbox and buffer_days are ERA5-specific and must not be forwarded
            # to other grid downloaders (e.g. HYRASDownloader) that do not
            # accept them.
            if source == "ERA5_land":
                grid_kwargs["bbox"] = cfg.get("era5_bbox")
                grid_kwargs["buffer_days"] = cfg.get("buffer_days", 1)
            self._grid: Downloader | None = grid_class(**grid_kwargs)
        else:
            self._grid = None

        # --- DWD station downloader (when explicitly configured or DWD-only) ---
        use_dwd = "dwd_stations" in cfg or source == "DWD_stations"
        if use_dwd:
            self._dwd: DWDStationDownloader | None = DWDStationDownloader(
                variables=variables,
                date_start=date_start,
                date_end=date_end,
                stations=cfg.get("dwd_stations"),
            )
        else:
            self._dwd = None

        logger.info(
            "CompositeWeatherDownloader initialised — source=%s, variables=%s,"
            " %s to %s",
            source,
            variables,
            date_start,
            date_end,
        )

    # ------------------------------------------------------------------
    # CompositeDownloader interface
    # ------------------------------------------------------------------

    @property
    def downloaders(self) -> list[Downloader]:
        """Return the active sub-downloaders in execution order.

        Returns
        -------
        list[Downloader]
            Non-None downloaders in order: grid downloader (if any) then
            :class:`~datavia.weather.dwd_downloader.DWDStationDownloader`
            (if any).
        """
        return [d for d in (self._grid, self._dwd) if d is not None]

    def download(self) -> str:
        """Run all active sub-downloads and return their output paths.

        Each sub-downloader writes its result to a separate temporary file.
        The absolute paths are returned as a newline-joined string so
        ``WeatherPipeline`` can split and pass them individually to
        ``SaverWeather.save()``.

        Returns
        -------
        str
            Newline-joined absolute file paths for every successful download.
            If the grid downloader is unavailable (e.g. missing ``cdsapi`` for
            ERA5, or a network error) its path is omitted and only station
            data is returned — allowing partial updates without blocking.

            Returns ``"failed"`` only when *all* active sub-downloads fail.

        Raises
        ------
        RuntimeError
            If the DWD download raises an unexpected exception (logged and
            re-raised so the pipeline can mark the update as failed).
        """
        paths: list[str] = []

        if self._grid is not None:
            logger.info("CompositeWeatherDownloader: starting grid download")
            try:
                grid_path = self._grid.download()
                logger.info("CompositeWeatherDownloader: grid done -> %s", grid_path)
                paths.append(grid_path)
            except (ImportError, RuntimeError) as exc:
                logger.warning(
                    "CompositeWeatherDownloader: grid download skipped (%s). "
                    "Continuing with DWD station data only. "
                    "To enable ERA5, install cdsapi and create ~/.cdsapirc.",
                    exc,
                )

        if self._dwd is not None:
            logger.info("CompositeWeatherDownloader: starting DWD download")
            dwd_path = self._dwd.download()
            logger.info("CompositeWeatherDownloader: DWD done -> %s", dwd_path)
            paths.append(dwd_path)

        if not paths:
            return "failed"
        return "\n".join(paths)
