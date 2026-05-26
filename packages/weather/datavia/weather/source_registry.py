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

from typing import TYPE_CHECKING, Any

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

# ---------------------------------------------------------------------------
# Conversion dispatch map
# ---------------------------------------------------------------------------

#: Maps ``(from_unit, to_unit)`` string pairs to the conversion callable.
#: Extend this table when new sources with different raw units are added.
FROM_TO_CONVERSION_MAP: dict[tuple[str, str], Any] = {
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
    },
    "HYRAS": {
        # Populated with HYRASDownloader once Phase D is implemented.
        "grid_downloader": _HYRASDownloader,
        # HYRAS files are already in target units (°C, mm/day, W/m²).
        "conversions": {},
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


def get_grid_downloader_class(source_name: str) -> "type[Downloader] | None":
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
    conversion_fn = FROM_TO_CONVERSION_MAP.get((from_unit, to_unit))

    if conversion_fn is None:
        # Spec exists but no matching function — return unchanged and log.
        import logging

        logging.getLogger(__name__).warning(
            "No conversion function found for (%s → %s) "
            "(source=%s, variable=%s). Returning raw value.",
            from_unit,
            to_unit,
            source_name,
            variable,
        )
        return value

    return conversion_fn(value)
