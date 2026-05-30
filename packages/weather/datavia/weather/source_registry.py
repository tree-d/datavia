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
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import numpy as np

from datavia.library.unit_conversions import (
    kelvin_to_celsius,
    precipitation_m_to_mm,
    ssrd_to_par,
)

# HYRASDownloader is introduced in Phase D.  Import it lazily so that the
# registry is fully usable before that file exists.
try:
    from .hyras_downloader import HYRASDownloader as _HYRASDownloader
except ImportError:
    _HYRASDownloader = None  # type: ignore[assignment,misc]

from .era5_downloader import ERA5Downloader

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
}

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
#: ``grid_downloader`` is ``None`` for ``"DWD_stations"`` because that source
#: has no gridded component, and also for ``"HYRAS"`` until
#: :class:`~datavia.weather.hyras_downloader.HYRASDownloader` is implemented
#: (Phase D).
SOURCE_REGISTRY: dict[str, dict[str, Any]] = {
    "ERA5_land": {
        "grid_downloader": ERA5Downloader,
        "conversions": {
            "2m_temperature": {"from": "K", "to": "degC"},
            "total_precipitation": {"from": "m", "to": "mm"},
            "surface_solar_radiation_downwards": {"from": "J_m2", "to": "PAR"},
        },
        # Maps ECMWF short variable names (as stored in ERA5-Land NetCDF files)
        # to the pipeline variable names used throughout the datavia API.
        "nc_variable_map": {
            "t2m": "2m_temperature",
            "tp": "total_precipitation",
            "ssrd": "surface_solar_radiation_downwards",
        },
        # Fixed Germany-extent grid for ZarrStoreManager.  Bounds are extended
        # by one cell beyond the pipeline bbox so bilinear interpolation does
        # not degrade at the exact boundary.  Lat runs north-to-south to match
        # ERA5's native array layout and avoid a flip on write.
        "zarr_grid": {
            "crs": "EPSG:4326",
            "latitude": np.arange(55.6, 47.0, -0.1).round(
                1
            ),  # 87 points — extended to 55.6°N to cover any plausible Germany bbox
            "longitude": np.arange(5.4, 15.6, 0.1).round(
                1
            ),  # 102 points — extended to 5.4°W-15.5°E for any plausible Germany bbox
            "time_freq": "1h",
            "dtype": "float32",
            "fill_value": float("nan"),
            # Point-query optimised: 720 h = 1 month; 5x5 cells ~= 0.5 x 0.5 deg.
            "chunks": {"time": 720, "latitude": 5, "longitude": 5},
            "codec": {"cname": "zstd", "clevel": 3, "shuffle": "shuffle"},
        },
    },
    "HYRAS": {
        # Populated with HYRASDownloader once Phase D is implemented.
        "grid_downloader": _HYRASDownloader,
        # HYRAS files are already in target units (°C, mm/day, W/m²).
        "conversions": {},
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
        },
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
        # Station-only source — no gridded downloader.
        "grid_downloader": None,
        "conversions": {},
    },
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
        component (``"DWD_stations"``) or when the class has not yet been
        implemented (``"HYRAS"`` before Phase D).

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
    return SOURCE_REGISTRY[source_name]["grid_downloader"]


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
        # nc_variable_map is NC→pipeline; build the reverse (pipeline→NC) on
        # the fly.  The reverse is a 1-to-1 mapping by construction.
        pipeline_to_nc = {v: k for k, v in nc_var_map.items()}
        return pipeline_to_nc.get(pipeline_variable, pipeline_variable)
    return pipeline_variable


def apply_conversion(
    source_name: str,
    variable: str,
    value: Any,
    user_overrides: dict[str, dict[str, str]] | None = None,
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
    conversion_fn = _FROM_TO_CONVERSION_MAP.get((from_unit, to_unit))

    if conversion_fn is None:
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

    return conversion_fn(value)


def get_valid_variables(source_name: str) -> frozenset[str] | None:
    """Return the set of pipeline variable names supported by *source_name*.

    Reads the ``nc_variable_map`` values from :data:`SOURCE_REGISTRY`.  These
    are the human-readable pipeline variable names (e.g. ``"2m_temperature"``,
    ``"total_precipitation"``) that callers may pass to
    :class:`~datavia.weather.pipeline.WeatherPipeline`.

    Returns ``None`` for sources that have no ``nc_variable_map`` (currently
    ``"DWD_stations"``), indicating that no compile-time variable validation is
    possible for that source.

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
