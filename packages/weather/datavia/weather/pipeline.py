"""
WeatherPipeline — end-to-end weather data integration pipeline.

Integrates gridded reanalysis data (ERA5-Land or HYRAS) and optionally DWD
station observations (Parquet) from Germany into a single pipeline.  Downloads
are orchestrated by
:class:`~datavia.weather.composite_downloader.CompositeWeatherDownloader`,
which delegates to the appropriate grid downloader and optionally to
:class:`~datavia.weather.dwd_downloader.DWDStationDownloader`.

Files are registered in the ``weather_layers`` SQLite table via
:class:`~datavia.weather.saver_weather.SaverWeather` and retrieved by
:class:`~datavia.weather.getter_weather.GetterWeather`.

The pipeline's ``name`` is taken from ``config["source"]`` so that two
pipelines with different sources (e.g. ``"ERA5_land"`` and ``"HYRAS"``) write
to separate ``source_name`` entries in the database and never shadow each
other.

Usage::

    from datavia.weather import WeatherPipeline

    pipe = WeatherPipeline(config={
        "source":     "ERA5_land",
        "variables":  ["2m_temperature", "total_precipitation"],
        "date_start": "2024-06-01",
        "date_end":   "2024-06-30",
    })
    pipe.update_data()
    value = pipe.get_weather_data(
        lat=52.5,
        lon=13.4,
        variable="2m_temperature",
        datetime_utc="2024-06-15T12:00:00",
    )
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from datavia.core.interfaces import Pipeline

from .composite_downloader import CompositeWeatherDownloader
from .getter_weather import GetterWeather
from .saver_weather import SaverWeather

logger = logging.getLogger(__name__)

#: Required keys that every WeatherPipeline config must contain.
_REQUIRED_CONFIG_KEYS: frozenset[str] = frozenset(
    {"source", "variables", "date_start", "date_end"}
)

#: All valid WeatherPipeline config keys (required + optional).
_KNOWN_CONFIG_KEYS: frozenset[str] = _REQUIRED_CONFIG_KEYS | frozenset(
    {"era5_bbox", "dwd_stations", "unit_conversions", "buffer_days"}
)


class WeatherPipeline(Pipeline):
    """End-to-end pipeline for multi-source weather data integration.

    Composes :class:`~datavia.weather.composite_downloader.CompositeWeatherDownloader`,
    :class:`~datavia.weather.saver_weather.SaverWeather`, and
    :class:`~datavia.weather.getter_weather.GetterWeather` into a single
    object following the :class:`~datavia.core.interfaces.Pipeline` contract.

    The pipeline name is taken from ``config["source"]`` and is used as the
    ``source_name`` for all database entries, allowing multiple pipelines with
    different sources to coexist without collision.

    Parameters
    ----------
    config : dict[str, Any]
        Pipeline configuration.  Required keys: ``source``, ``variables``,
        ``date_start``, ``date_end``.  Optional keys: ``era5_bbox``,
        ``dwd_stations``, ``unit_conversions``, ``buffer_days``.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialise the pipeline, validate config, and set the source name.

        Validates *config* against the required and known key sets before any
        other logic runs, so callers see all validation errors in one
        ``ValueError``.

        Parameters
        ----------
        config : dict[str, Any]
            Pipeline configuration dict.  Must contain ``source``,
            ``variables``, ``date_start``, and ``date_end``.  The value of
            ``source`` (e.g. ``"ERA5_land"``, ``"HYRAS"``) becomes the
            pipeline's ``name`` and the ``source_name`` stored in the database.

        Raises
        ------
        ValueError
            If required keys are missing or unknown keys are present.
        """
        # Validate before any attribute assignment so errors surface immediately.
        Pipeline.validate_pipeline_config(
            config,
            required_keys=_REQUIRED_CONFIG_KEYS,
            known_keys=_KNOWN_CONFIG_KEYS,
            pipeline_name="WeatherPipeline",
        )
        # The source value becomes the pipeline name and DB source_name.
        source_name: str = config["source"]
        super().__init__(
            name=source_name,
            downloader=CompositeWeatherDownloader,
            saver=SaverWeather,
            getter=GetterWeather,
        )
        self._config: dict[str, Any] = config

    def __call__(self, *args: Any, **kwargs: Any) -> WeatherPipeline:
        """Instantiate the composite downloader, saver, and getter.

        Overrides the base :meth:`~datavia.core.interfaces.Pipeline.__call__`
        to pass the stored *config* to
        :class:`~datavia.weather.composite_downloader.CompositeWeatherDownloader`
        instead of a URL.

        Returns
        -------
        WeatherPipeline
            Self for method chaining.
        """
        self.downloader = CompositeWeatherDownloader(config=self._config)
        self.saver = SaverWeather(self.name)
        # Forward any user-provided unit conversion overrides so the getter
        # can pass them to apply_conversion for each variable.
        self.getter = GetterWeather(
            self.name,
            unit_overrides=self._config.get("unit_conversions"),
        )
        return self

    def update_data(
        self,
        reproject: bool = False,
        resolution_m: int | None = None,
    ) -> bool:
        """Download and register weather data.

        Runs the composite downloader (ERA5 + DWD), splits the returned
        newline-joined paths, and calls :meth:`SaverWeather.save` for each
        individual file.

        Parameters
        ----------
        reproject : bool, optional
            Ignored for weather files; present for interface compatibility.
            Defaults to ``False``.
        resolution_m : int, optional
            Ignored for weather files. Defaults to ``None``.

        Returns
        -------
        bool
            ``True`` when all files were saved successfully.
        """
        if not self.downloader or not self.saver:
            self()

        assert self.downloader is not None
        assert self.saver is not None

        combined_paths = self.downloader.download()
        if combined_paths == "failed":
            logger.error("Weather download failed.")
            return False

        all_saved = True
        for raw_path in combined_paths.splitlines():
            file_path = raw_path.strip()
            if file_path:
                success = self.saver.save(file_path)
                if not success:
                    logger.error("Failed to save weather file: %s", file_path)
                    all_saved = False

        return all_saved

    def get_weather_data(
        self,
        lat: float,
        lon: float,
        variable: str,
        datetime_utc: Any,
        radius_km: float = 50.0,
        station_weight: float = 0.6,
    ) -> float:
        """Convenience single-point weather query.

        Parameters
        ----------
        lat : float
            Geographic latitude in degrees North.
        lon : float
            Geographic longitude in degrees East.
        variable : str
            Variable name, e.g. ``"temperature_2m"``.
        datetime_utc : datetime-like
            Target UTC timestamp.
        radius_km : float, optional
            Station search radius in km. Defaults to 50 km.
        station_weight : float, optional
            Blending weight for station data. Defaults to 0.6.

        Returns
        -------
        float
            Interpolated value at the given coordinate and time.
            ``float("nan")`` when no data is available.
        """
        if not self.getter:
            self()

        assert self.getter is not None
        assert isinstance(self.getter, GetterWeather)

        return self.getter.get_weather_data(
            lat=lat,
            lon=lon,
            variable=variable,
            datetime_utc=datetime_utc,
            radius_km=radius_km,
            station_weight=station_weight,
        )

    def get_data(
        self,
        coords: np.ndarray,
        crs_coords: str = "EPSG:4326",
        interpolation_order: int = 3,
        band: int = 1,
        **kwargs: Any,
    ) -> np.ndarray:
        """Return interpolated weather values at all requested coordinates.

        Parameters
        ----------
        coords : np.ndarray
            Coordinate array of shape ``(N, 2)`` as ``[longitude, latitude]``
            pairs in EPSG:4326.
        crs_coords : str, optional
            CRS of input coordinates. Defaults to ``"EPSG:4326"``.
        interpolation_order : int, optional
            Accepted for interface compatibility; not used. Defaults to 3.
        band : int, optional
            Accepted for interface compatibility; not used. Defaults to 1.
        **kwargs : Any
            Forwarded to :meth:`GetterWeather.get_data`. Required:
            ``variable`` and ``datetime_utc``.

        Returns
        -------
        np.ndarray
            Shape ``(N,)`` array of interpolated values.
        """
        if not self.getter:
            self()

        assert self.getter is not None

        return self.getter.get_data(
            coords=coords,
            crs_coords=crs_coords,
            interpolation_order=interpolation_order,
            band=band,
            **kwargs,
        )

    def sync_files_and_database(self) -> bool:
        """Reconcile weather files on disk with the database, without downloading.

        Scans the configured data directory for weather files that belong to
        this pipeline's source and compares them against ``weather_layers`` DB
        rows.  Two repairs are performed:

        1. **Orphan DB rows** — rows whose ``uri`` points to a missing file are
           deleted from ``weather_layers``.
        2. **Orphan disk files** — files present on disk but absent from the
           database are re-registered via :meth:`SaverWeather.save`.

        This is useful after a database reset: it restores DB registration
        from already-downloaded files so that :meth:`update_data` (and a
        full re-download) is not necessary.

        Usage::

            pipe = WeatherPipeline(config={...})
            pipe()  # or Datavia(pipelines=[pipe])()
            pipe.sync_files_and_database()

        Returns
        -------
        bool
            ``True`` when synchronisation completed without errors.
        """
        if not self.saver:
            self()

        assert self.saver is not None
        assert isinstance(self.saver, SaverWeather)

        return self.saver.sync_files_and_database()
