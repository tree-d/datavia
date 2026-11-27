"""
Source registry for WeatherPipeline grid downloaders and unit conversions.

Maps each named weather source (e.g. ``"ERA5_land"``, ``"HYRAS"``,
``"DWD_stations"``) to:

- The :class:`~datavia.core.interfaces.Downloader` subclass responsible for
  fetching its gridded data.
- A per-variable conversion table describing the ``from``/``to`` unit pair
  that needs to be applied after interpolation.

Public API
----------
- :data:`SOURCE_REGISTRY` — full registry dict.
- :func:`get_grid_downloader_class` — look up the downloader class for a source.
- :func:`apply_conversion` — apply the correct unit conversion for a variable,
  with optional user-provided overrides.
- :func:`convert_units` / :func:`get_target_unit` — unit conversion between
  sources, used to bring station values to the gridded target unit.
- :func:`is_accumulated`, :func:`get_day_start_hour` and
  :func:`get_station_aggregation` — time-base settings per variable
  (:func:`station_data_supports` tells whether station data can be blended
  at a temporal resolution); see
  :mod:`datavia.library.temporal` for the conventions.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import numpy as np

from datavia.library.unit_conversions import (
    kelvin_to_celsius,
    precipitation_m_to_mm,
    ssrd_to_par,
    w_m2_to_par,
)

from .dwd_kriging_downloader import NC_VARIABLES, DWDKrigingDownloader
from .era5_downloader import ACCUMULATED_VARIABLES, ERA5Downloader
from .hyras_downloader import HYRASDownloader
from .vapour_pressure import NC_NAME as VPD_NC_NAME

if TYPE_CHECKING:
    from datavia.core.interfaces import Downloader

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conversion dispatch map
# ---------------------------------------------------------------------------

#: Maps ``(from_unit, to_unit)`` string pairs to the conversion callable.
#: Extend this table when new sources with different raw units are added.
_FROM_TO_CONVERSION_MAP: dict[tuple[str, str], Any] = {
    ("K", "degC"): kelvin_to_celsius,
    ("m", "mm"): precipitation_m_to_mm,
    ("J_m2", "PAR"): ssrd_to_par,
    ("W_m2", "PAR"): w_m2_to_par,
}

#: UTC hour at which the precipitation day starts (HYRAS and DWD convention).
_PRECIPITATION_DAY_START_HOUR: int = 6

# ---------------------------------------------------------------------------
# ERA5-Land grid boundary constants
# ---------------------------------------------------------------------------

#: Northern boundary of the ERA5-Land Zarr skeleton (degrees North).
#: Extended by one cell beyond the typical Germany pipeline bbox.
_ERA5_LAND_LAT_MAX: float = 55.6

#: Southern boundary of the ERA5-Land Zarr skeleton (degrees North).
_ERA5_LAND_LAT_MIN: float = 47.0

#: Western boundary of the ERA5-Land Zarr skeleton (degrees East).
_ERA5_LAND_LON_MIN: float = 5.4

#: Eastern boundary of the ERA5-Land Zarr skeleton (degrees East).
_ERA5_LAND_LON_MAX: float = 15.6

#: Fixed WGS84 envelope (west, south, east, north) of the national HYRAS
#: EPSG:3035 1 km grid, shared by "HYRAS" and the kriged "DWD_stations".
_HYRAS_COVERAGE_BBOX: tuple[float, float, float, float] = (
    5.307613684411584,
    47.07781030622338,
    15.707082890486497,
    55.08908843154143,
)

# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

#: Registry mapping source names to their downloader class and per-variable
#: unit conversion rules.
#:
#: Structure::
#:
#:     {
#:         "<source_name>": {
#:             "grid_downloader": <Downloader subclass> | None,
#:             "conversions": {
#:                 "<variable>": {"from": "<unit>", "to": "<unit>"},
#:                 ...
#:             },
#:         },
#:         ...
#:     }
#:
#: ``"DWD_stations"`` is gridded too (kriged station data); its entry also
#: holds the settings of the station Parquet supplement (``native_units``,
#: ``daily_aggregation``, ...), which any gridded source can blend in.
SOURCE_REGISTRY: dict[str, dict[str, Any]] = {
    "ERA5_land": {
        "grid_downloader": ERA5Downloader,
        "conversions": {
            "2m_temperature": {"from": "K", "to": "degC"},
            "total_precipitation": {"from": "m", "to": "mm"},
            "surface_solar_radiation_downwards": {"from": "J_m2", "to": "PAR"},
        },
        # Running totals since 00 UTC; see datavia.library.temporal.
        "accumulated_variables": ACCUMULATED_VARIABLES,
        # Unlisted variables use the 00-24 UTC day.
        "day_start_hour": {"total_precipitation": _PRECIPITATION_DAY_START_HOUR},
        # Maps ECMWF short variable names (as stored in ERA5-Land NetCDF files)
        # to the pipeline variable names used throughout the datavia API.
        "nc_variable_map": {
            "t2m": "2m_temperature",
            "tp": "total_precipitation",
            "ssrd": "surface_solar_radiation_downwards",
            # Derived hourly by ERA5Downloader from t2m and d2m (kPa).
            VPD_NC_NAME: "vapour_pressure_deficit",
        },
        # Fixed Germany-extent grid for ZarrStoreManager.  Bounds are extended
        # by one cell beyond the pipeline bbox so bilinear interpolation does
        # not degrade at the exact boundary.  Lat runs north-to-south to match
        # ERA5's native array layout and avoid a flip on write.
        "zarr_grid": {
            "crs": "EPSG:4326",
            "latitude": np.arange(_ERA5_LAND_LAT_MAX, _ERA5_LAND_LAT_MIN, -0.1).round(
                1
            ),
            "longitude": np.arange(_ERA5_LAND_LON_MIN, _ERA5_LAND_LON_MAX, 0.1).round(
                1
            ),
            "time_freq": "1h",
            "dtype": "float32",
            "fill_value": float("nan"),
            # Point-query optimised: 720 h = 1 month; 5x5 cells ~= 0.5 x 0.5 deg.
            "chunks": {"time": 720, "latitude": 5, "longitude": 5},
            "codec": {"cname": "zstd", "clevel": 3, "shuffle": "shuffle"},
        },
    },
    "HYRAS": {
        "grid_downloader": HYRASDownloader,
        # HYRAS files are already in target units (°C, mm/day) except radiation:
        # rsds is W/m², converted to PAR to match every other source.
        "conversions": {
            "surface_solar_radiation_downwards": {"from": "W_m2", "to": "PAR"},
        },
        # One stamp per day, dated with the day it describes (tas 00:00,
        # pr 06:00 for 06-06 UTC, rsds 12:00; verified in the DWD files).
        # Daily mode selects by date, so no day_start_hour is needed here.
        # Maps NetCDF CF variable names (as they appear inside the .nc file)
        # to the pipeline variable names used throughout the datavia API.
        # Needed because HYRAS uses short CF names (e.g. "tas") while the rest
        # of the pipeline uses descriptive names (e.g. "2m_temperature").
        "nc_variable_map": {
            "tas": "2m_temperature",
            "tasmax": "temperature_2m_max",
            "tasmin": "temperature_2m_min",
            "pr": "total_precipitation",
            "rsds": "surface_solar_radiation_downwards",
            "hurs": "relative_humidity_2m",
            # Derived by HYRASDownloader from tasmax, tasmin and hurs (kPa).
            VPD_NC_NAME: "vapour_pressure_deficit",
        },
        # Fixed WGS84 envelope of the national EPSG:3035 grid.  HYRAS does
        # not support bbox-subset downloads, so coverage checks must use this
        # same envelope for both requests and rebuilt store registrations.
        "coverage_bbox": _HYRAS_COVERAGE_BBOX,
        # HYRAS stores are kept in their native ETRS89-LAEA (EPSG:3035)
        # projection.  The x/y coordinate arrays are derived from the
        # downloaded .nc files at write time — no pre-defined grid is required.
        # Coordinate reprojection happens at query time inside
        # interpolate_dataset / _build_spatial_interp_coords.
        "zarr_grid": {
            "crs": "EPSG:3035",
            "time_freq": "1D",
            "dtype": "float32",
            "fill_value": float("nan"),
            # Chunk keys use x/y (projected) instead of longitude/latitude.
            "chunks": {"time": 365, "y": 10, "x": 10},
            "codec": {"cname": "zstd", "clevel": 3, "shuffle": "shuffle"},
        },
    },
    "DWD_stations": {
        # Daily DWD station observations (Climate Data Center), interpolated
        # by external drift kriging onto the HYRAS 1 km grid; see
        # datavia.weather.dwd_kriging_downloader.  Values are in target units.
        "grid_downloader": DWDKrigingDownloader,
        "conversions": {},
        # Kriged grids are stored under HYRAS's short names, with HYRAS's
        # time stamps (D 00:00; precipitation D 06:00).
        "nc_variable_map": {
            NC_VARIABLES[variable]["name"]: variable for variable in NC_VARIABLES
        },
        # The grid is the HYRAS grid, so the coverage envelope is HYRAS's.
        "coverage_bbox": _HYRAS_COVERAGE_BBOX,
        "zarr_grid": {
            "crs": "EPSG:3035",
            "time_freq": "1D",
            "dtype": "float32",
            "fill_value": float("nan"),
            "chunks": {"time": 365, "y": 10, "x": 10},
            "codec": {"cname": "zstd", "clevel": 3, "shuffle": "shuffle"},
        },
        # --- Station supplement (Parquet, see DWDStationDownloader) ---
        # Units of the station parquet columns, used to convert station values
        # to the gridded source's target unit before blending.
        "native_units": {
            "2m_temperature": "degC",
            "temperature_2m_max": "degC",
            "temperature_2m_min": "degC",
            "total_precipitation": "mm",
            "relative_humidity_2m": "%",
            "vapour_pressure_deficit": "kPa",
        },
        # One record per station and day: the daily selection takes that
        # single value ("hours_required": 1), so mean and sum are the same.
        "daily_aggregation": {
            "2m_temperature": "mean",
            "temperature_2m_max": "mean",
            "temperature_2m_min": "mean",
            "relative_humidity_2m": "mean",
            "vapour_pressure_deficit": "mean",
            "total_precipitation": "sum",
        },
        "station_records_per_day": 1,
        # The station values are daily: hourly queries do not blend them.
        "station_temporal_resolution": "daily",
        # Precipitation (06-06 UTC) is stamped at its window end, D+1 06:00;
        # the other variables at their window start, D 00:00.
        "interval_end_variables": frozenset({"total_precipitation"}),
        "day_start_hour": {"total_precipitation": _PRECIPITATION_DAY_START_HOUR},
    },
}

#: Pre-computed reverse maps: ``pipeline_variable`` → NetCDF name, per source.
#: Built once at module load from :data:`SOURCE_REGISTRY` to avoid rebuilding
#: the reverse mapping on every call to :func:`get_nc_variable_name`.
_PIPELINE_TO_NC_VARIABLE: dict[str, dict[str, str]] = {
    source: {v: k for k, v in entry.get("nc_variable_map", {}).items()}
    for source, entry in SOURCE_REGISTRY.items()
}

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_grid_downloader_class(source_name: str) -> type[Downloader] | None:
    """Return the grid downloader class registered for *source_name*.

    Parameters
    ----------
    source_name : str
        A key in :data:`SOURCE_REGISTRY`, e.g. ``"ERA5_land"``.

    Returns
    -------
    type[Downloader] or None
        The downloader class, or ``None`` when the source has no gridded
        component.

    Raises
    ------
    KeyError
        If *source_name* is not present in :data:`SOURCE_REGISTRY`.
    """
    if source_name not in SOURCE_REGISTRY:
        raise KeyError(
            f"Unknown weather source '{source_name}'. "
            f"Valid sources: {sorted(SOURCE_REGISTRY)}"
        )
    downloader_class: type[Downloader] | None = SOURCE_REGISTRY[source_name][
        "grid_downloader"
    ]
    return downloader_class


def get_day_start_hour(source_name: str, variable: str) -> int:
    """Return the UTC hour at which *variable*'s daily window starts.

    Parameters
    ----------
    source_name : str
        A key in :data:`SOURCE_REGISTRY`; unknown sources give 0.
    variable : str
        Pipeline variable name.

    Returns
    -------
    int
        0 (the 00-24 UTC day) unless the source registry lists the variable
        under ``"day_start_hour"`` (precipitation: 6).
    """
    return int(
        SOURCE_REGISTRY.get(source_name, {}).get("day_start_hour", {}).get(variable, 0)
    )


def is_accumulated(source_name: str, variable: str) -> bool:
    """Return whether *variable* is stored as a running total since 00 UTC.

    Parameters
    ----------
    source_name : str
        A key in :data:`SOURCE_REGISTRY`; unknown sources give ``False``.
    variable : str
        Pipeline variable name.

    Returns
    -------
    bool
        ``True`` if the source lists *variable* under
        ``"accumulated_variables"`` (ERA5-Land precipitation and radiation).
    """
    entry = SOURCE_REGISTRY.get(source_name, {})
    accumulated: frozenset[str] = entry.get("accumulated_variables", frozenset())
    return variable in accumulated


def get_station_aggregation(variable: str, temporal_resolution: str) -> dict[str, Any]:
    """Return the daily-selection keyword arguments for DWD station data.

    The result is passed to
    :func:`~datavia.library.interpolation.interpolate_station_parquet`.  The
    station Parquet holds one record per station and day, stamped so that
    the day window of :func:`~datavia.library.temporal.day_window` contains
    exactly it.

    Parameters
    ----------
    variable : str
        Pipeline variable name.
    temporal_resolution : str
        ``"daily"`` or ``"hourly"``.  Station data are daily, so callers skip
        the station blend in hourly mode (see :func:`station_data_supports`);
        here ``"hourly"`` selects the nearest stamp
        (``daily_aggregation=None``).

    Returns
    -------
    dict[str, Any]
        ``daily_aggregation``, ``day_start_hour``, ``interval_end`` and
        ``hours_required`` (records needed per day).
    """
    entry = SOURCE_REGISTRY["DWD_stations"]
    how = entry["daily_aggregation"].get(variable)
    return {
        "daily_aggregation": how if temporal_resolution == "daily" else None,
        "day_start_hour": get_day_start_hour("DWD_stations", variable),
        "interval_end": variable in entry["interval_end_variables"],
        "hours_required": entry["station_records_per_day"],
    }


def station_data_supports(temporal_resolution: str) -> bool:
    """Return whether DWD station data can be blended at *temporal_resolution*.

    Parameters
    ----------
    temporal_resolution : str
        ``"daily"`` or ``"hourly"``.

    Returns
    -------
    bool
        ``True`` when the station records have this resolution (the station
        Parquet is daily, so only ``"daily"``).
    """
    station_resolution: str = SOURCE_REGISTRY["DWD_stations"][
        "station_temporal_resolution"
    ]
    return station_resolution == temporal_resolution


def get_target_unit(
    source_name: str,
    variable: str,
    user_overrides: dict[str, dict[str, str]] | None = None,
) -> str | None:
    """Return the unit :func:`apply_conversion` produces for *variable*.

    Parameters
    ----------
    source_name : str
        A key in :data:`SOURCE_REGISTRY`; unknown sources have no conversions.
    variable : str
        Pipeline variable name.
    user_overrides : dict[str, dict[str, str]], optional
        Per-variable ``{"from": ..., "to": ...}`` overrides, as in
        :func:`apply_conversion`; they take precedence over the registry.

    Returns
    -------
    str or None
        The target unit, or ``None`` when no conversion applies, so the value
        stays in the source's native unit.
    """
    spec = {
        **SOURCE_REGISTRY.get(source_name, {}).get("conversions", {}),
        **(user_overrides or {}),
    }.get(variable)
    return spec["to"] if spec else None


def convert_units(
    value: Any, from_unit: str, to_unit: str, period_s: float | None = None
) -> Any:
    """Convert *value* from *from_unit* to *to_unit*.

    Equal units pass *value* through unchanged.  *period_s* is the length in
    seconds of the period an energy total (``J_m2``) covers; ``None`` means
    one day.

    Raises
    ------
    ValueError
        If no conversion between the two units is registered.
    """
    if from_unit == to_unit:
        return value
    conversion_fn = _FROM_TO_CONVERSION_MAP.get((from_unit, to_unit))
    if conversion_fn is None:
        raise ValueError(f"No conversion registered from {from_unit} to {to_unit}.")
    if period_s is not None and from_unit == "J_m2":
        return conversion_fn(value, period_s=period_s)
    return conversion_fn(value)


def get_nc_variable_name(source_name: str, pipeline_variable: str) -> str:
    """Return the NetCDF variable name for a given pipeline variable and source.

    For sources whose NetCDF files use short CF variable names (e.g. HYRAS
    stores temperature as ``"tas"`` rather than ``"2m_temperature"``), this
    function translates the pipeline-level variable name to the name actually
    present inside the file, as required by :func:`interpolate_netcdf`.

    Falls back to *pipeline_variable* unchanged when the source has no
    ``nc_variable_map`` or when the variable is not listed there.

    Parameters
    ----------
    source_name : str
        A key in :data:`SOURCE_REGISTRY`, e.g. ``"HYRAS"``.
    pipeline_variable : str
        Pipeline-level variable name, e.g. ``"2m_temperature"``.

    Returns
    -------
    str
        NetCDF variable name, e.g. ``"tas"`` for HYRAS temperature, or
        *pipeline_variable* itself when no mapping is needed.
    """
    nc_var_map: dict[str, str] = SOURCE_REGISTRY.get(source_name, {}).get(
        "nc_variable_map", {}
    )
    if nc_var_map:
        # Use the pre-computed pipeline_variable → NC name reverse map.
        return _PIPELINE_TO_NC_VARIABLE.get(source_name, {}).get(
            pipeline_variable, pipeline_variable
        )
    return pipeline_variable


def apply_conversion(
    source_name: str,
    variable: str,
    value: Any,
    user_overrides: dict[str, dict[str, str]] | None = None,
    period_s: float | None = None,
) -> Any:
    """Apply the appropriate unit conversion for *variable* from *source_name*.

    Merges *user_overrides* (keyed by variable name) over the registry
    defaults, then applies the resolved conversion function.  If no conversion
    is needed the original *value* is returned unchanged.

    Parameters
    ----------
    source_name : str
        A key in :data:`SOURCE_REGISTRY`, e.g. ``"ERA5_land"``.
    variable : str
        Variable name, e.g. ``"2m_temperature"``.
    value : np.ndarray or float
        Raw value(s) as returned by the interpolation layer.
    user_overrides : dict[str, dict[str, str]], optional
        Per-variable override map of the form
        ``{"2m_temperature": {"from": "K", "to": "degC"}}``.
        When provided, entries here take precedence over the registry defaults
        for the matched variable names.
    period_s : float, optional
        Length in seconds of the period an energy total (``J_m2``) covers.
        ``None`` means one day; pass ``3600`` for de-accumulated hourly values.

    Returns
    -------
    np.ndarray or float
        Value(s) in the target unit, or *value* unchanged when no conversion
        applies.  Same type and shape as *value*.

    Raises
    ------
    KeyError
        If *source_name* is not present in :data:`SOURCE_REGISTRY`.
    """
    if source_name not in SOURCE_REGISTRY:
        raise KeyError(
            f"Unknown weather source '{source_name}'. "
            f"Valid sources: {sorted(SOURCE_REGISTRY)}"
        )

    # Merge registry defaults with user-provided overrides.
    registry_conversions: dict[str, dict[str, str]] = SOURCE_REGISTRY[source_name][
        "conversions"
    ]
    effective_conversions = {**registry_conversions, **(user_overrides or {})}

    conversion_spec = effective_conversions.get(variable)
    if conversion_spec is None:
        # No conversion defined for this variable — return as-is.
        return value

    from_unit = conversion_spec["from"]
    to_unit = conversion_spec["to"]
    try:
        return convert_units(value, from_unit, to_unit, period_s=period_s)
    except ValueError:
        # Spec exists but no matching function — return unchanged and log.
        logger.warning(
            "No conversion function found for (%s → %s) "
            "(source=%s, variable=%s). Returning raw value.",
            from_unit,
            to_unit,
            source_name,
            variable,
        )
        return value


def get_valid_variables(source_name: str) -> frozenset[str] | None:
    """Return the set of pipeline variable names supported by *source_name*.

    Reads the ``nc_variable_map`` values from :data:`SOURCE_REGISTRY`.  These
    are the human-readable pipeline variable names (e.g. ``"2m_temperature"``,
    ``"total_precipitation"``) that callers may pass to
    :class:`~datavia.weather.pipeline.WeatherPipeline`.

    Returns ``None`` for sources that have no ``nc_variable_map``, indicating
    that no compile-time variable validation is possible for that source.

    Parameters
    ----------
    source_name : str
        A key in :data:`SOURCE_REGISTRY`, e.g. ``"ERA5_land"`` or
        ``"DWD_stations"``.

    Returns
    -------
    frozenset[str] or None
        Frozenset of valid pipeline variable names when the source has a known
        variable map, or ``None`` when validation is not applicable.

    Raises
    ------
    KeyError
        If *source_name* is not present in :data:`SOURCE_REGISTRY`.
    """
    if source_name not in SOURCE_REGISTRY:
        raise KeyError(
            f"Unknown weather source '{source_name}'. "
            f"Valid sources: {sorted(SOURCE_REGISTRY)}"
        )
    nc_var_map: dict[str, str] = SOURCE_REGISTRY[source_name].get("nc_variable_map", {})
    if not nc_var_map:
        return None
    return frozenset(nc_var_map.values())
