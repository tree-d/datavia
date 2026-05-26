"""
Physical unit conversion utilities for ERA5 raw output values.

ERA5 delivers:
- Temperature in **Kelvin** (e.g. ``2m_temperature``).
- Precipitation as accumulated **metres** of water equivalent
  (``total_precipitation``).
- Surface solar radiation downwards (SSRD) as accumulated
  **J m⁻²** per time step (``surface_solar_radiation_downwards``).
  PAR is estimated as 50 % of shortwave → µmol m⁻² s⁻¹.

All conversion functions operate element-wise on numpy arrays (or scalars)
and are intentionally format-agnostic so that they can be called from
:class:`datavia.weather.getter_weather.GetterWeather` after interpolation,
keeping ``interpolation.py`` free of source-specific knowledge.
"""

from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
# Physical constants
# ---------------------------------------------------------------------------

#: Conversion offset from Kelvin to degrees Celsius.
_KELVIN_OFFSET: float = 273.15

#: Metres to millimetres conversion factor.
_M_TO_MM: float = 1000.0

#: Fraction of shortwave radiation that is photosynthetically active (PAR).
_PAR_FRACTION: float = 0.5

#: Conversion factor from W m⁻² to µmol(photons) m⁻² s⁻¹ for PAR.
#: Based on the approximation 1 W m⁻² ≈ 4.57 µmol m⁻² s⁻¹.
_W_TO_UMOL: float = 4.57

#: Seconds per day, used to convert J m⁻² day⁻¹ to W m⁻².
_SECONDS_PER_DAY: int = 86_400

# ---------------------------------------------------------------------------
# Public conversion functions
# ---------------------------------------------------------------------------


def kelvin_to_celsius(values: np.ndarray | float) -> np.ndarray | float:
    """Convert temperature values from Kelvin to degrees Celsius.

    Parameters
    ----------
    values : np.ndarray or float
        Temperature in Kelvin.  May be a scalar or an array of any shape.

    Returns
    -------
    np.ndarray or float
        Temperature in degrees Celsius.  Same type and shape as *values*.
    """
    return (
        np.asarray(values) - _KELVIN_OFFSET
        if isinstance(values, np.ndarray)
        else float(values) - _KELVIN_OFFSET
    )


def precipitation_m_to_mm(values: np.ndarray | float) -> np.ndarray | float:
    """Convert accumulated precipitation from metres to millimetres.

    Parameters
    ----------
    values : np.ndarray or float
        Precipitation accumulation in metres of water equivalent.

    Returns
    -------
    np.ndarray or float
        Precipitation in millimetres.  Same type and shape as *values*.
    """
    return (
        np.asarray(values) * _M_TO_MM
        if isinstance(values, np.ndarray)
        else float(values) * _M_TO_MM
    )


def ssrd_to_par(ssrd_daily_j_m2: np.ndarray | float) -> np.ndarray | float:
    """Convert daily SSRD accumulation to mean PAR flux.

    Converts surface solar radiation downwards (SSRD) expressed as a daily
    accumulation in J m⁻² to photosynthetically active radiation (PAR) in
    µmol(photons) m⁻² s⁻¹.

    The conversion pipeline is:
    1. J m⁻² day⁻¹  →  W m⁻² (divide by seconds per day).
    2. W m⁻²        →  PAR W m⁻² (multiply by :data:`_PAR_FRACTION` = 0.5).
    3. PAR W m⁻²    →  µmol m⁻² s⁻¹ (multiply by :data:`_W_TO_UMOL` ≈ 4.57).

    Parameters
    ----------
    ssrd_daily_j_m2 : np.ndarray or float
        Daily SSRD accumulation in J m⁻².

    Returns
    -------
    np.ndarray or float
        Mean PAR flux in µmol(photons) m⁻² s⁻¹.  Same type and shape as
        *ssrd_daily_j_m2*.
    """
    arr = np.asarray(ssrd_daily_j_m2, dtype=float)
    result = arr / _SECONDS_PER_DAY * _PAR_FRACTION * _W_TO_UMOL
    if np.ndim(ssrd_daily_j_m2) == 0:
        return float(result)
    return result


#: Maps ERA5 variable names to their conversion function.
#: Variables not listed here are returned unchanged.
_CONVERSION_MAP: dict[str, callable] = {  # type: ignore[type-arg]
    "2m_temperature": kelvin_to_celsius,
    "total_precipitation": precipitation_m_to_mm,
    "surface_solar_radiation_downwards": ssrd_to_par,
}


def convert_era5_variable(
    values: np.ndarray | float,
    variable: str,
) -> np.ndarray | float:
    """Dispatch a unit conversion by ERA5 variable name.

    .. deprecated::
        ``convert_era5_variable`` is superseded by
        :func:`datavia.weather.source_registry.apply_conversion`, which is
        source-aware and handles HYRAS, ERA5-Land, and future sources
        correctly.  This function is kept for backward compatibility with
        external callers and existing tests but is no longer called from
        :class:`~datavia.weather.getter_weather.GetterWeather`.

    Looks up *variable* in :data:`_CONVERSION_MAP` and applies the
    corresponding function.  Variables that do not require conversion (e.g.
    wind components, relative humidity) are returned unchanged.

    Parameters
    ----------
    values : np.ndarray or float
        Raw ERA5 values as returned by the CDS/interpolation layer.
    variable : str
        ERA5 variable name, e.g. ``"2m_temperature"`` or
        ``"total_precipitation"``.

    Returns
    -------
    np.ndarray or float
        Values in the target unit.  Same type and shape as *values*.
    """
    conversion_fn = _CONVERSION_MAP.get(variable)
    if conversion_fn is not None:
        return conversion_fn(values)
    return values
