"""
GetterWeather — Getter implementation for NetCDF and Parquet weather data.

Retrieves interpolated weather values at requested coordinates and datetimes
by querying the ``weather_layers`` database table for relevant files and
delegating to the library interpolation functions.

Unit conversions are source-aware: each source (``ERA5_land``, ``HYRAS``,
etc.) has its own conversion rules in the source registry.  HYRAS data is
already in target units and passes through unchanged.  ERA5 raw values
(Kelvin, metres, J m⁻²) are converted automatically.

When both gridded (NetCDF) and station (Parquet / DWD) files cover the
requested time window the results are blended via
:func:`datavia.library.interpolation.blend_gridded_and_station`.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from datavia.core.interfaces import Getter
from datavia.library.database.query import get_weather_metadata, get_weather_paths
from datavia.library.interpolation import (
    blend_gridded_and_station,
    interpolate_netcdf,
    interpolate_station_parquet,
)

from .source_registry import apply_conversion, get_nc_variable_name

logger = logging.getLogger(__name__)

#: Default search radius for station IDW interpolation.
_DEFAULT_RADIUS_KM: float = 50.0

#: Default weight given to station data when blending with gridded values.
_DEFAULT_STATION_WEIGHT: float = 0.6


class GetterWeather(Getter):
    """Retrieve weather data from NetCDF or Parquet files by coordinate and time.

    Supports three retrieval modes based on the files registered in
    ``weather_layers`` for the configured source:

    1. **NetCDF only** — bilinear spatial + nearest-time interpolation via
       :func:`~datavia.library.interpolation.interpolate_netcdf`.
    2. **Parquet only** — inverse-distance-weighted station average via
       :func:`~datavia.library.interpolation.interpolate_station_parquet`.
    3. **Both** — blended result via
       :func:`~datavia.library.interpolation.blend_gridded_and_station`.

    After gridded interpolation, source-aware unit conversions are applied
    via :func:`~datavia.weather.source_registry.apply_conversion`.  ERA5
    raw values (Kelvin, metres, J m⁻²) are converted to target units;
    HYRAS values (already in °C / mm / W m⁻²) pass through unchanged.

    The :meth:`get_data` method accepts coordinates as a ``(N, 2)`` array of
    ``[lon, lat]`` pairs (EPSG:4326) in line with the base
    :class:`~datavia.core.interfaces.Getter` contract.  Pass ``variable`` and
    ``datetime_utc`` as keyword arguments:

    .. code-block:: python

        getter = GetterWeather("ERA5_land")
        values = getter.get_data(
            coords=np.array([[13.4, 52.5]]),
            variable="2m_temperature",
            datetime_utc="2024-06-15T12:00:00",
        )
    """

    def __init__(
        self,
        source_name: str,
        unit_overrides: dict[str, dict[str, str]] | None = None,
        temporal_resolution: str = "daily",
    ) -> None:
        """Initialise getter with the source identifier and optional unit overrides.

        Parameters
        ----------
        source_name : str
            Unique source identifier matching a key in
            :data:`~datavia.weather.source_registry.SOURCE_REGISTRY`, e.g.
            ``"ERA5_land"`` or ``"HYRAS"``.
        unit_overrides : dict[str, dict[str, str]], optional
            Per-variable conversion overrides in the form
            ``{"2m_temperature": {"from": "K", "to": "degC"}}``.  Takes
            precedence over the registry defaults for matched variable names.
            Forwarded from ``config["unit_conversions"]`` by
            :class:`WeatherPipeline`.
        temporal_resolution : str, optional
            ``"daily"`` (default) or ``"hourly"``.  Forwarded to
            :func:`~datavia.library.interpolation.interpolate_netcdf` for
            each query.
        """
        self.source_name: str = source_name
        self._unit_overrides: dict[str, dict[str, str]] | None = unit_overrides
        self._temporal_resolution: str = temporal_resolution

    # ------------------------------------------------------------------
    # Getter interface
    # ------------------------------------------------------------------

    def get_existing_layers(self) -> set[str]:
        """Return the set of variable names registered for this source.

        Delegates to
        :func:`~datavia.library.database.query.get_weather_metadata`.

        Returns
        -------
        set[str]
            Variable names present in the database, e.g.
            ``{"temperature_2m", "precipitation"}``.  Returns an empty set
            when no data has been stored yet.
        """
        rows = get_weather_metadata(self.source_name)
        return {row["variable"] for row in rows if row.get("variable")}

    def get_registered_uris(self) -> set[str]:
        """Return the set of file URIs currently registered for this source.

        Queries ``weather_layers`` for all distinct ``uri`` values belonging
        to this source.  A single file may produce multiple rows (one per
        variable), so distinct URIs are returned rather than row counts.

        Used by
        :meth:`~datavia.core.interfaces.Pipeline.sync_files_and_database`
        to compare what the database knows about against what is on disk.

        Returns
        -------
        set[str]
            Absolute file paths registered for this source.  Returns an
            empty set when nothing has been stored yet or the database is
            unavailable.
        """
        rows = get_weather_metadata(self.source_name)
        return {row["uri"] for row in rows if row.get("uri")}

    def get_data(
        self,
        coords: np.ndarray,
        crs_coords: str = "EPSG:4326",
        interpolation_order: int = 3,
        band: int = 1,
        **kwargs: Any,
    ) -> np.ndarray:
        """Return interpolated weather values at all requested coordinates.

        Weather data is retrieved by variable name and datetime, not by band,
        so *interpolation_order* and *band* are accepted for interface
        compatibility but are not used internally.

        Parameters
        ----------
        coords : np.ndarray
            Coordinate array of shape ``(N, 2)`` as ``[longitude, latitude]``
            (or ``[easting, northing]``) pairs in *crs_coords*.
        crs_coords : str, optional
            CRS of the input coordinates, e.g. ``"EPSG:4326"`` (default) or
            ``"EPSG:3035"``.  Any CRS understood by ``pyproj`` is accepted.
            Coordinates are reprojected to the file's native CRS automatically.
        interpolation_order : int, optional
            Accepted for interface compatibility; not used. Defaults to 3.
        band : int, optional
            Accepted for interface compatibility; not used. Defaults to 1.
        **kwargs : Any
            Required keyword arguments:

            - ``variable`` (str): Variable name, e.g. ``"temperature_2m"``.
            - ``datetime_utc`` (datetime-like or list): Target UTC timestamp
              (single) or list of T timestamps for a batched time-series
              query.  When a list is passed the return shape is ``(N, T)``
              instead of ``(N,)``.
            - ``radius_km`` (float, optional): Station search radius in km.
              Defaults to :data:`_DEFAULT_RADIUS_KM`.  Ignored when
              ``datetime_utc`` is a list (multi-timestamp station blending
              is not supported).
            - ``station_weight`` (float, optional): Blending weight for
              station data. Defaults to :data:`_DEFAULT_STATION_WEIGHT`.
              Ignored when ``datetime_utc`` is a list.

        Returns
        -------
        np.ndarray
            - Shape ``(N,)`` when a single timestamp is supplied.
            - Shape ``(N, T)`` when a list of T timestamps is supplied;
              rows correspond to coordinates, columns to timestamps.
            Points outside the covered area or time window are ``NaN``.

        Raises
        ------
        ValueError
            If ``variable`` or ``datetime_utc`` is not provided in *kwargs*.
        RuntimeError
            If no weather files are found for the requested variable / time.
        """
        variable: str = kwargs.get("variable", "")
        datetime_utc = kwargs.get("datetime_utc")
        radius_km: float = float(kwargs.get("radius_km", _DEFAULT_RADIUS_KM))
        station_weight: float = float(
            kwargs.get("station_weight", _DEFAULT_STATION_WEIGHT)
        )

        if not variable:
            raise ValueError(
                "GetterWeather.get_data requires 'variable' as a keyword argument."
            )
        if datetime_utc is None:
            raise ValueError(
                "GetterWeather.get_data requires 'datetime_utc' as a keyword argument."
            )
        coords_arr = np.asarray(coords, dtype=float)
        n_coords = len(coords_arr)
        is_multi_time = (
            isinstance(datetime_utc, (list, tuple)) and len(datetime_utc) > 1
        )
        n_times = len(datetime_utc) if is_multi_time else 1

        from_dt = (
            str(datetime_utc)
            if not isinstance(datetime_utc, (list, tuple))
            else str(datetime_utc[0])
        )
        to_dt = (
            str(datetime_utc)
            if not isinstance(datetime_utc, (list, tuple))
            else str(datetime_utc[-1])
        )

        nc_paths = get_weather_paths(self.source_name, variable, from_dt, to_dt)

        # Separate by format (query returns all; filter by extension).
        # A single call is made and split to avoid a redundant DB query.
        nc_files = [p for p in nc_paths if p.endswith(".nc")]
        parquet_files = [p for p in nc_paths if p.endswith(".parquet")]

        if not nc_files and not parquet_files:
            raise RuntimeError(
                f"No weather files found for source='{self.source_name}', "
                f"variable='{variable}', time=[{from_dt}, {to_dt}]. "
                f"Run the pipeline update first."
            )

        # Multi-timestamp path: returns shape (N, T); single-timestamp: shape (N,).
        results = (
            np.full((n_coords, n_times), np.nan)
            if is_multi_time
            else np.full(n_coords, np.nan)
        )

        # --- Gridded NetCDF path (single batch call for all coords) ---
        if nc_files:
            try:
                nc_variable = get_nc_variable_name(self.source_name, variable)
                lats = coords_arr[:, 1]
                lons = coords_arr[:, 0]
                raw_batch = interpolate_netcdf(
                    nc_files[0],
                    lats,
                    lons,
                    nc_variable,
                    datetime_utc,
                    input_crs=crs_coords,
                    temporal_resolution=self._temporal_resolution,
                )
                if is_multi_time:
                    # interpolate_netcdf returns (T, N) when timestamps is a list;
                    # normalise to (N, T) so callers always get coords-first layout.
                    raw_arr = np.asarray(raw_batch, dtype=float)
                    if (
                        raw_arr.ndim == 2
                        and raw_arr.shape[0] == n_times
                        and raw_arr.shape[0] != n_coords
                    ):
                        raw_arr = raw_arr.T  # (T, N) → (N, T)
                    converted = apply_conversion(
                        self.source_name, variable, raw_arr, self._unit_overrides
                    )
                    results = np.asarray(converted, dtype=float)
                else:
                    # raw_batch shape: (N,) for a single timestamp; scalar when N=1.
                    raw_arr = np.atleast_1d(np.asarray(raw_batch, dtype=float))
                    for i, raw_val in enumerate(raw_arr):
                        if not np.isnan(raw_val):
                            results[i] = float(
                                apply_conversion(
                                    self.source_name,
                                    variable,
                                    raw_val,
                                    self._unit_overrides,
                                )
                            )
            except Exception as exc:
                logger.warning("NetCDF batch interpolation failed: %s", exc)

        # --- Station Parquet path: per-coord; only supported for a single timestamp ---
        # Multi-timestamp station blending is not yet implemented; skip the station
        # path when the caller supplies a list of times.
        if is_multi_time:
            return results

        for i, coord in enumerate(coords_arr):
            lon, lat = float(coord[0]), float(coord[1])
            station_val: float | None = None

            if parquet_files:
                for parquet_path in parquet_files:
                    try:
                        candidate = interpolate_station_parquet(
                            parquet_path,
                            lat,
                            lon,
                            variable,
                            datetime_utc,
                            radius_km,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Station interpolation failed for (%.4f, %.4f) in '%s': %s",
                            lat,
                            lon,
                            parquet_path,
                            exc,
                        )
                        continue
                    if not np.isnan(candidate):
                        station_val = candidate
                        break
                    station_val = candidate

            gridded_val = results[i] if not np.isnan(results[i]) else None

            if (
                gridded_val is not None
                and station_val is not None
                and not np.isnan(station_val)
            ):
                results[i] = blend_gridded_and_station(
                    gridded_val, station_val, station_weight
                )
            elif station_val is not None and gridded_val is None:
                results[i] = station_val

        return results

    def get_weather_data(
        self,
        lat: float,
        lon: float,
        variable: str,
        datetime_utc: Any,
        radius_km: float = _DEFAULT_RADIUS_KM,
        station_weight: float = _DEFAULT_STATION_WEIGHT,
    ) -> float:
        """Convenience single-point query returning a scalar value.

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
            Station search radius in km. Defaults to
            :data:`_DEFAULT_RADIUS_KM`.
        station_weight : float, optional
            Station blending weight. Defaults to :data:`_DEFAULT_STATION_WEIGHT`.

        Returns
        -------
        float
            Interpolated value at the given coordinate and time.
            ``float("nan")`` when no data is available.
        """
        coords = np.array([[lon, lat]])
        result = self.get_data(
            coords=coords,
            variable=variable,
            datetime_utc=datetime_utc,
            radius_km=radius_km,
            station_weight=station_weight,
        )
        return float(result[0])
