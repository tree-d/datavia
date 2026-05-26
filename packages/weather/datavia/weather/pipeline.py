"""
WeatherPipeline — end-to-end weather data integration pipeline.

Integrates ERA5 gridded reanalysis data (NetCDF) and DWD station observations
(Parquet) from Germany into a single pipeline.  Downloads are orchestrated by
:class:`~datavia.weather.composite_downloader.CompositeWeatherDownloader`,
which delegates to :class:`~datavia.weather.era5_downloader.ERA5Downloader`
and :class:`~datavia.weather.dwd_downloader.DWDStationDownloader`.

Files are registered in the ``weather_layers`` SQLite table via
:class:`~datavia.weather.saver_weather.SaverWeather` and retrieved by
:class:`~datavia.weather.getter_weather.GetterWeather`.

Usage::

    from datavia.weather import WeatherPipeline

    pipe = WeatherPipeline()
    pipe.update_data()
    value = pipe.get_weather_data(
        lat=52.5,
        lon=13.4,
        variable="temperature_2m",
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

#: Pipeline source-name constant; matches the CLI route and DB prefix.
_PIPELINE_NAME: str = "weather"


class WeatherPipeline(Pipeline):
    """End-to-end pipeline for multi-source weather data integration.

    Composes :class:`~datavia.weather.composite_downloader.CompositeWeatherDownloader`,
    :class:`~datavia.weather.saver_weather.SaverWeather`, and
    :class:`~datavia.weather.getter_weather.GetterWeather` into a single
    object following the :class:`~datavia.core.interfaces.Pipeline` contract.

    The pipeline name ``"weather"`` is used as a prefix for all database table
    entries and data directory file names.

    Parameters
    ----------
    config : dict[str, Any], optional
        Configuration forwarded to
        :class:`~datavia.weather.composite_downloader.CompositeWeatherDownloader`.
        See :class:`~datavia.weather.composite_downloader.CompositeWeatherDownloader`
        for accepted keys (``variables``, ``date_start``, ``date_end``, etc.).
    """

    name: str = _PIPELINE_NAME

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialise the pipeline with optional downloader configuration.

        Parameters
        ----------
        config : dict[str, Any], optional
            Downloader configuration forwarded to
            :class:`~datavia.weather.composite_downloader.CompositeWeatherDownloader`.
        """
        super().__init__(
            name=_PIPELINE_NAME,
            downloader=CompositeWeatherDownloader,
            saver=SaverWeather,
            getter=GetterWeather,
        )
        self._config: dict[str, Any] = config or {}

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
        self.getter = GetterWeather(self.name)
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
