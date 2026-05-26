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
from .coverage_manager import CoverageCell, CoverageManager
from .getter_weather import GetterWeather
from .saver_weather import SaverWeather

logger = logging.getLogger(__name__)

#: Default Germany bounding box as ``(west, south, east, north)`` in EPSG:4326.
#: Used when the pipeline config contains no explicit ``era5_bbox``.
_GERMANY_BBOX_WSNE: tuple[float, float, float, float] = (5.9, 47.3, 15.0, 55.1)

#: Required keys that every WeatherPipeline config must contain.
_REQUIRED_CONFIG_KEYS: frozenset[str] = frozenset(
    {"source", "variables", "date_start", "date_end"}
)

#: All valid WeatherPipeline config keys (required + optional).
_KNOWN_CONFIG_KEYS: frozenset[str] = _REQUIRED_CONFIG_KEYS | frozenset(
    {
        "era5_bbox",
        "dwd_stations",
        "unit_conversions",
        "buffer_days",
        "temporal_resolution",
    }
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
        ``dwd_stations``, ``unit_conversions``, ``buffer_days``,
        ``temporal_resolution``.
    replace : bool, optional
        When ``True`` the stored config is completely replaced by *config* on
        construction.  When ``False`` (default) the config is merged with any
        previously stored values — this matches the behaviour of
        :meth:`reconfigure`.  For a freshly created instance both values are
        equivalent because there is no prior config.
    """

    def __init__(self, config: dict[str, Any], replace: bool = False) -> None:
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
        replace : bool, optional
            Reserved for future use when upgrading an existing instance.
            Currently unused during initial construction; included so that
            :meth:`reconfigure` can pass the flag consistently.
            Defaults to ``False``.

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
        self._config: dict[str, Any] = dict(config)

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
            temporal_resolution=self._config.get("temporal_resolution", "daily"),
        )
        return self

    def get_config(self) -> dict[str, Any]:
        """Return a copy of the effective pipeline configuration.

        Returns a shallow copy so callers cannot mutate the internal state
        inadvertently.  Use :meth:`reconfigure` to change the pipeline config.

        Returns
        -------
        dict[str, Any]
            Copy of the current pipeline configuration dict.
        """
        return dict(self._config)

    def reconfigure(
        self,
        config_updates: dict[str, Any],
        replace: bool = False,
    ) -> None:
        """Update the pipeline configuration and re-initialise components.

        Applies *config_updates* to the stored configuration.  By default the
        updates are *merged* (delta mode): only keys present in *config_updates*
        are changed; all other keys keep their current values.  When *replace*
        is ``True`` the entire stored config is replaced by *config_updates*
        (which must then satisfy all required-key constraints).

        After updating the config the downloader and getter instances are
        rebuilt so that the next :meth:`update_data` or :meth:`get_data` call
        uses the new parameters.  The saver is not rebuilt because it depends
        only on the immutable ``source_name`` (``config["source"]``), which
        cannot change via :meth:`reconfigure`.

        ``config["source"]`` cannot be changed via this method.  Attempting to
        supply a different ``"source"`` value raises ``ValueError`` because the
        source name is baked into the pipeline's ``name``, the database rows,
        and the on-disk filenames.  Create a new :class:`WeatherPipeline`
        instance instead.

        Parameters
        ----------
        config_updates : dict[str, Any]
            Keys and values to apply to the stored configuration.  In delta
            mode only the listed keys are changed.  In replace mode this dict
            must include all required keys.
        replace : bool, optional
            When ``True`` the stored config is replaced entirely by
            *config_updates*.  When ``False`` (default) *config_updates* is
            merged into the current config.

        Raises
        ------
        ValueError
            If *config_updates* contains a ``"source"`` key whose value
            differs from the pipeline's current source name.
        ValueError
            If the merged or replacement config fails validation (missing
            required keys or unknown keys present).
        """
        new_source = config_updates.get("source")
        if new_source is not None and new_source != self._config["source"]:
            raise ValueError(
                f"WeatherPipeline.reconfigure: cannot change 'source' from "
                f"'{self._config['source']}' to '{new_source}'. "
                "Create a new WeatherPipeline instance instead."
            )

        if replace:
            candidate = dict(config_updates)
        else:
            candidate = {**self._config, **config_updates}

        Pipeline.validate_pipeline_config(
            candidate,
            required_keys=_REQUIRED_CONFIG_KEYS,
            known_keys=_KNOWN_CONFIG_KEYS,
            pipeline_name="WeatherPipeline.reconfigure",
        )

        self._config = candidate

        # Rebuild the downloader so the next update_data() uses the new config.
        self.downloader = CompositeWeatherDownloader(config=self._config)
        # Rebuild the getter in case unit_conversions or temporal_resolution changed.
        self.getter = GetterWeather(
            self.name,
            unit_overrides=self._config.get("unit_conversions"),
            temporal_resolution=self._config.get("temporal_resolution", "daily"),
        )
        logger.info(
            "WeatherPipeline '%s': configuration updated. mode=%s",
            self.name,
            "replace" if replace else "merge",
        )

    def update_data(
        self,
        reproject: bool = False,
        resolution_m: int | None = None,
    ) -> bool:
        """Download and register weather data.

        First reconciles on-disk files with the database by calling
        :meth:`~datavia.core.interfaces.Pipeline.sync_files_and_database`
        (inherited from :class:`~datavia.core.interfaces.Pipeline`).  This
        removes stale DB rows for deleted files and re-registers any files
        that exist on disk but were not yet recorded, ensuring the DB
        accurately reflects the current state before the download delta is
        computed.

        Then runs the composite downloader (ERA5 + DWD), splits the returned
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

        assert self.downloader is not None  # nosec B101
        assert self.saver is not None  # nosec B101

        # Reconcile disk with DB before checking what to download (BUG-01 fix).
        self.sync_files_and_database()

        # Compute the uncovered (bbox, date_range) cells before issuing any
        # requests.  This avoids redundant downloads on repeated update_data()
        # calls and supports incremental spatial or temporal extension.
        coverage_manager = CoverageManager(self.name, self._config["variables"])
        req_bbox = self._get_request_bbox()
        missing_cells = coverage_manager.missing_spatiotemporal(
            req_bbox,
            self._config["date_start"],
            self._config["date_end"],
        )

        if not missing_cells:
            logger.info(
                "WeatherPipeline '%s': all requested data already registered. "
                "Nothing to download.",
                self.name,
            )
            return True

        logger.info(
            "WeatherPipeline '%s': %d cell(s) to download.",
            self.name,
            len(missing_cells),
        )

        all_saved = True
        for cell in missing_cells:
            cell_config = self._build_cell_config(cell)
            cell_downloader = CompositeWeatherDownloader(config=cell_config)
            combined_paths = cell_downloader.download()
            if combined_paths == "failed":
                logger.error(
                    "WeatherPipeline '%s': download failed for cell %s.",
                    self.name,
                    cell,
                )
                all_saved = False
                continue

            for raw_path in combined_paths.splitlines():
                file_path = raw_path.strip()
                if file_path:
                    success = self.saver.save(file_path)
                    if not success:
                        logger.error("Failed to save weather file: %s", file_path)
                        all_saved = False

        return all_saved

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_request_bbox(self) -> tuple[float, float, float, float]:
        """Return the configured bounding box as ``(west, south, east, north)``.

        ERA5 stores the bbox in the config as ``[north, west, south, east]``
        (CDS API convention).  This method converts it to the Shapely-standard
        ``(west, south, east, north)`` tuple used by :class:`CoverageManager`.

        For sources that do not declare an explicit bbox (HYRAS, DWD), the
        Germany default :data:`_GERMANY_BBOX_WSNE` is returned.

        Returns
        -------
        tuple[float, float, float, float]
            Bounding box as ``(west, south, east, north)`` in EPSG:4326 degrees.
        """
        era5_bbox = self._config.get("era5_bbox")
        if era5_bbox:
            # era5_bbox is stored as [north, west, south, east] per CDS convention.
            n, w, s, e = era5_bbox
            return (w, s, e, n)
        return _GERMANY_BBOX_WSNE

    def _build_cell_config(self, cell: CoverageCell) -> dict[str, Any]:
        """Build a per-cell download config from the pipeline config and a cell.

        Copies the pipeline config and overrides ``date_start`` and
        ``date_end`` with the cell's values.  For ``"ERA5_land"`` sources, also
        overrides ``era5_bbox`` with the cell's bbox converted back to the CDS
        API format ``[north, west, south, east]``.

        Parameters
        ----------
        cell : CoverageCell
            The coverage cell defining the spatial and temporal extent to
            download.

        Returns
        -------
        dict[str, Any]
            Updated config dict suitable for
            :class:`~datavia.weather.composite_downloader.CompositeWeatherDownloader`.
        """
        cell_config = dict(self._config)
        cell_config["date_start"] = cell.date_start
        cell_config["date_end"] = cell.date_end
        if self._config.get("source") == "ERA5_land":
            w, s, e, n = cell.bbox
            # Convert back to CDS API convention: [north, west, south, east].
            cell_config["era5_bbox"] = [n, w, s, e]
        return cell_config

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

        assert self.getter is not None  # nosec B101
        assert isinstance(self.getter, GetterWeather)  # nosec B101

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

        assert self.getter is not None  # nosec B101

        return self.getter.get_data(
            coords=coords,
            crs_coords=crs_coords,
            interpolation_order=interpolation_order,
            band=band,
            **kwargs,
        )
