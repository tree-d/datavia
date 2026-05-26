"""
GetterWeather — Getter implementation for NetCDF and Parquet weather data.

Retrieves interpolated weather values at requested coordinates and datetimes
by querying the ``weather_layers`` database table for relevant files and
delegating to the library interpolation functions.

When both gridded (NetCDF / ERA5) and station (Parquet / DWD) files cover the
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
from datavia.library.unit_conversions import convert_era5_variable

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

    The :meth:`get_data` method accepts coordinates as a ``(N, 2)`` array of
    ``[lon, lat]`` pairs (EPSG:4326) in line with the base
    :class:`~datavia.core.interfaces.Getter` contract.  Pass ``variable`` and
    ``datetime_utc`` as keyword arguments:

    .. code-block:: python

        getter = GetterWeather("era5")
        values = getter.get_data(
            coords=np.array([[13.4, 52.5]]),
            variable="temperature_2m",
            datetime_utc="2024-06-15T12:00:00",
        )
    """

    def __init__(self, source_name: str) -> None:
        """Initialise getter with the source identifier.

        Parameters
        ----------
        source_name : str
            Unique source identifier, e.g. ``"era5"`` or ``"dwd_stations"``
            or the composite ``"weather"`` used by :class:`WeatherPipeline`.
        """
        self.source_name: str = source_name

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
            pairs in EPSG:4326.
        crs_coords : str, optional
            CRS of the input coordinates. Only ``"EPSG:4326"`` is supported
            for weather data. Defaults to ``"EPSG:4326"``.
        interpolation_order : int, optional
            Accepted for interface compatibility; not used. Defaults to 3.
        band : int, optional
            Accepted for interface compatibility; not used. Defaults to 1.
        **kwargs : Any
            Required keyword arguments:

            - ``variable`` (str): Variable name, e.g. ``"temperature_2m"``.
            - ``datetime_utc`` (datetime-like): Target UTC timestamp (single)
              or list of timestamps (time-series).
            - ``radius_km`` (float, optional): Station search radius in km.
              Defaults to :data:`_DEFAULT_RADIUS_KM`.
            - ``station_weight`` (float, optional): Blending weight for station
              data. Defaults to :data:`_DEFAULT_STATION_WEIGHT`.

        Returns
        -------
        np.ndarray
            Interpolated values at each coordinate, shape ``(N,)``.
            Points outside the covered area or time window are ``NaN``.

        Raises
        ------
        ValueError
            If ``variable`` or ``datetime_utc`` is not provided in *kwargs*,
            or if ``crs_coords`` is not ``"EPSG:4326"``.
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
        if crs_coords != "EPSG:4326":
            raise ValueError(
                f"GetterWeather only supports EPSG:4326 coordinates, "
                f"got '{crs_coords}'."
            )

        coords_arr = np.asarray(coords, dtype=float)
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

        results = np.full(len(coords_arr), np.nan)

        for i, coord in enumerate(coords_arr):
            lon, lat = float(coord[0]), float(coord[1])
            gridded_val: float | None = None
            station_val: float | None = None

            if nc_files:
                try:
                    raw_gridded = interpolate_netcdf(
                        nc_files[0], lat, lon, variable, datetime_utc
                    )
                    # Apply ERA5 unit conversions (K→°C, m→mm, SSRD→PAR).
                    gridded_val = float(convert_era5_variable(raw_gridded, variable))
                except Exception as exc:
                    logger.warning(
                        "NetCDF interpolation failed for (%.4f, %.4f): %s",
                        lat,
                        lon,
                        exc,
                    )

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
                    # Keep the NaN as fallback so station_val is set even
                    # when all files return NaN (avoids silently missing data).
                    station_val = candidate

            if (
                gridded_val is not None
                and station_val is not None
                and not np.isnan(station_val)
            ):
                results[i] = blend_gridded_and_station(
                    gridded_val, station_val, station_weight
                )
            elif gridded_val is not None:
                results[i] = gridded_val
            elif station_val is not None:
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
