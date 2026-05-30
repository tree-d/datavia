"""
GetterWeather — Getter implementation for Zarr-backed and Parquet weather data.

Retrieves interpolated weather values at requested coordinates and datetimes.

For sources with a ``zarr_grid`` entry in ``SOURCE_REGISTRY`` (e.g.
``"HYRAS"``, ``"ERA5_land"``), data is read directly from the per-(source,
variable, year) Zarr store via
:class:`~datavia.weather.zarr_store_manager.ZarrStoreManager`.
For other sources, the ``weather_layers`` database table is queried and the
result is read from a NetCDF file.

Unit conversions are source-aware: each source has its own conversion rules
in ``SOURCE_REGISTRY``.  HYRAS data is already in target units and passes
through unchanged.  ERA5 raw values (Kelvin, metres, J m⁻²) are converted
automatically.

When both gridded (Zarr) and station (Parquet / DWD) files cover the
requested time window the results are blended via
:func:`datavia.library.interpolation.blend_gridded_and_station`.
"""

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import xarray as xr

import numpy as np
import pandas as pd

from datavia.core.interfaces import Getter
from datavia.library.database.query import get_weather_metadata, get_weather_paths
from datavia.library.interpolation import (
    blend_gridded_and_station,
    interpolate_dataset,
    interpolate_netcdf,
    interpolate_station_parquet,
)

from .source_registry import SOURCE_REGISTRY, apply_conversion, get_nc_variable_name
from .zarr_store_manager import ZarrStoreManager

logger = logging.getLogger(__name__)

#: Default search radius for station IDW interpolation.
_DEFAULT_RADIUS_KM: float = 50.0

#: Default weight given to station data when blending with gridded values.
_DEFAULT_STATION_WEIGHT: float = 0.6


class GetterWeather(Getter):
    """Retrieve weather data from NetCDF or Parquet files by coordinate and time.

    Supports three retrieval modes:

    1. **Zarr** (sources with ``zarr_grid`` in ``SOURCE_REGISTRY``) —
       bilinear spatial + nearest-time interpolation from the Zarr store via
       :func:`~datavia.library.interpolation.interpolate_dataset`.
    2. **NetCDF** (sources without ``zarr_grid``) — bilinear spatial +
       nearest-time interpolation via
       :func:`~datavia.library.interpolation.interpolate_netcdf`.
    3. **Station Parquet** (all sources) — inverse-distance-weighted station
       average via
       :func:`~datavia.library.interpolation.interpolate_station_parquet`,
       optionally blended with gridded results.

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

        Checks both the ``weather_layers`` DB table and the Zarr store
        directories on disk.  Zarr variables are discovered by scanning
        ``<data_dir>/<source_name>/`` for sub-directories that contain at
        least one ``*.zarr`` directory, which allows this method to work
        even after a DB loss (before ``rebuild_from_store`` has been run).

        Returns
        -------
        set[str]
            Variable names available for this source.  Returns an empty set
            when no data has been stored yet.
        """
        from datavia.config import get_config

        db_variables = {
            row["variable"]
            for row in get_weather_metadata(self.source_name)
            if row.get("variable")
        }
        try:
            cfg = get_config()
            mgr = ZarrStoreManager(str(cfg.data_directory), self.source_name)
            zarr_variables = mgr.list_available_variables()
        except (KeyError, Exception):
            zarr_variables = set()
        return db_variables | zarr_variables

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
              query.  Timezone-aware values (e.g. strings ending in ``"Z"``
              or :class:`~datetime.datetime` objects with a
              :attr:`~datetime.datetime.tzinfo`) are accepted: they are
              converted to UTC and stripped of timezone information before
              any file look-up or interpolation, so they are always
              comparable with the timezone-naive time axes stored in
              ERA5/HYRAS NetCDF files.  When a list is passed the return
              shape is ``(N, T)`` instead of ``(N,)``.
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
        # Coerce a single-element list to a scalar so that interpolate_netcdf
        # always receives either a plain value (single-time) or a list with at
        # least two entries (multi-time), avoiding shape mismatches.
        if isinstance(datetime_utc, (list, tuple)) and len(datetime_utc) == 1:
            datetime_utc = datetime_utc[0]

        # Normalise timezone-aware timestamps to timezone-naive UTC so they are
        # always comparable with the naive datetime64 time axes in NetCDF files.
        # This handles ISO-8601 strings ending in 'Z' as well as datetime objects
        # with tzinfo.  Elements in a list are normalised individually.
        def _to_naive_utc(dt: Any) -> Any:
            """Strip timezone from a single timestamp value, converting to UTC.

            Parameters
            ----------
            dt : Any
                A datetime-like value (string, Timestamp, datetime, etc.).

            Returns
            -------
            Any
                A timezone-naive :class:`pandas.Timestamp` if *dt* carried
                timezone information; otherwise *dt* is returned unchanged.
            """
            ts = pd.Timestamp(str(dt))
            if ts.tzinfo is not None:
                return ts.tz_convert("UTC").tz_localize(None)
            return dt

        if isinstance(datetime_utc, (list, tuple)):
            datetime_utc = [_to_naive_utc(dt) for dt in datetime_utc]
        else:
            datetime_utc = _to_naive_utc(datetime_utc)

        is_multi_time = isinstance(datetime_utc, (list, tuple))
        n_times = len(datetime_utc) if is_multi_time else 1

        from_dt = (
            pd.Timestamp(datetime_utc).isoformat()
            if not isinstance(datetime_utc, (list, tuple))
            else pd.Timestamp(datetime_utc[0]).isoformat()
        )
        to_dt = (
            pd.Timestamp(datetime_utc).isoformat()
            if not isinstance(datetime_utc, (list, tuple))
            else pd.Timestamp(datetime_utc[-1]).isoformat()
        )

        # For Zarr-enabled sources the store is the primary data backend.
        # The DB is only queried to find station Parquet paths; the Zarr store
        # path is constructed directly from source_name / variable / year.
        has_zarr_grid = "zarr_grid" in SOURCE_REGISTRY.get(self.source_name, {})

        nc_paths = get_weather_paths(self.source_name, variable, from_dt, to_dt)

        # Separate by format. For Zarr sources nc_files will typically be
        # empty; the Zarr open is independent of the DB.
        nc_files = [p for p in nc_paths if p.endswith(".nc")]
        parquet_files = [p for p in nc_paths if p.endswith(".parquet")]

        if not has_zarr_grid and not nc_files and not parquet_files:
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

        # --- Gridded path ---
        if nc_files or has_zarr_grid:
            try:
                lats = coords_arr[:, 1]
                lons = coords_arr[:, 0]
                raw_batch = None

                if has_zarr_grid:
                    zarr_ds = _try_open_zarr(self.source_name, variable, from_dt, to_dt)
                    if zarr_ds is not None:
                        with zarr_ds:
                            raw_batch = interpolate_dataset(
                                zarr_ds,
                                lats,
                                lons,
                                variable,
                                datetime_utc,
                                input_crs=crs_coords,
                                temporal_resolution=self._temporal_resolution,
                            )
                    elif nc_files:
                        # Zarr store not yet written (e.g. first run not completed or
                        # legacy .nc rows still registered).  Fall back to NetCDF so
                        # that data already on disk is not silently unavailable.
                        logger.debug(
                            "No Zarr store for %s/%s — falling back to NetCDF.",
                            self.source_name,
                            variable,
                        )
                        nc_variable = get_nc_variable_name(self.source_name, variable)
                        raw_batch = interpolate_netcdf(
                            nc_files[0],
                            lats,
                            lons,
                            nc_variable,
                            datetime_utc,
                            input_crs=crs_coords,
                            temporal_resolution=self._temporal_resolution,
                        )
                    elif not parquet_files:
                        raise RuntimeError(
                            f"No Zarr store found for source='{self.source_name}', "
                            f"variable='{variable}', time=[{from_dt}, {to_dt}]. "
                            "Run the pipeline update first."
                        )
                else:
                    nc_variable = get_nc_variable_name(self.source_name, variable)
                    raw_batch = interpolate_netcdf(
                        nc_files[0],
                        lats,
                        lons,
                        nc_variable,
                        datetime_utc,
                        input_crs=crs_coords,
                        temporal_resolution=self._temporal_resolution,
                    )

                if raw_batch is not None:
                    if is_multi_time:
                        # interpolate_dataset returns (T, N) when timestamps is a list;
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
                        # raw_batch shape: (N,) for single timestamp; scalar when N=1.
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
                logger.error(
                    "NetCDF batch interpolation failed for source='%s',"
                    " variable='%s': %s",
                    self.source_name,
                    variable,
                    exc,
                    exc_info=True,
                )
                raise

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


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _try_open_zarr(
    source_name: str,
    variable: str,
    from_dt: str,
    to_dt: str,
) -> xr.Dataset | None:
    """Attempt to open the Zarr stores covering *from_dt*-*to_dt* for *variable*.

    Returns ``None`` (without raising) when no Zarr stores exist for the
    requested period, the source has no ``zarr_grid`` entry, or the data
    directory cannot be determined.  This allows ``GetterWeather.get_data``
    to fall back silently to the legacy NetCDF path.

    Parameters
    ----------
    source_name : str
        Source identifier, e.g. ``"ERA5_land"``.
    variable : str
        Datavia variable name.
    from_dt : str
        Start of the requested period (ISO date string ``YYYY-MM-DD``).
    to_dt : str
        End of the requested period (ISO date string ``YYYY-MM-DD``).

    Returns
    -------
    xr.Dataset or None
        Lazy concatenated Dataset covering the requested years, or ``None``
        when no Zarr stores are available.
    """
    from datavia.config import get_config

    entry = SOURCE_REGISTRY.get(source_name, {})
    if "zarr_grid" not in entry:
        return None

    try:
        cfg = get_config()
        mgr = ZarrStoreManager(str(cfg.data_directory), source_name)
        start_year = pd.Timestamp(from_dt).year
        end_year = pd.Timestamp(to_dt).year
        years = list(range(start_year, end_year + 1))
        return mgr.open_multi_year(variable, years)
    except Exception:
        return None
