"""
Physical unit conversion utilities for ERA5 raw output values.

ERA5 delivers:
- Temperature in **Kelvin** (e.g. ``2m_temperature``).
- Precipitation as accumulated **metres** of water equivalent
  (``total_precipitation``).
- Surface solar radiation downwards (SSRD) as accumulated
  **J m⁻²**, running since 00 UTC (``surface_solar_radiation_downwards``);
  convert to a per-period total before :func:`ssrd_to_par`.
  PAR is estimated as 50 % of shortwave → µmol m⁻² s⁻¹.

All conversion functions operate element-wise on numpy arrays (or scalars)
and are intentionally format-agnostic so that they can be called from
:class:`datavia.weather.getter_weather.GetterWeather` after interpolation,
keeping ``interpolation.py`` free of source-specific knowledge.
"""

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


def w_m2_to_par(irradiance_w_m2: np.ndarray | float) -> np.ndarray | float:
    """Convert shortwave irradiance to PAR photon flux.

    Converts global shortwave irradiance in W m⁻² to photosynthetically
    active radiation (PAR) in µmol(photons) m⁻² s⁻¹:

    1. W m⁻²     →  PAR W m⁻² (multiply by :data:`_PAR_FRACTION` = 0.5).
    2. PAR W m⁻² →  µmol m⁻² s⁻¹ (multiply by :data:`_W_TO_UMOL` ≈ 4.57).

    Parameters
    ----------
    irradiance_w_m2 : np.ndarray or float
        Shortwave irradiance in W m⁻².

    Returns
    -------
    np.ndarray or float
        PAR flux in µmol(photons) m⁻² s⁻¹.  Same type and shape as
        *irradiance_w_m2*.
    """
    arr = np.asarray(irradiance_w_m2, dtype=float)
    result = arr * _PAR_FRACTION * _W_TO_UMOL
    if np.ndim(irradiance_w_m2) == 0:
        return float(result)
    return result


def ssrd_to_par(
    ssrd_j_m2: np.ndarray | float, period_s: float = _SECONDS_PER_DAY
) -> np.ndarray | float:
    """Convert an SSRD energy total over *period_s* seconds to mean PAR flux.

    *ssrd_j_m2* must already be a total over exactly one period, not a running
    accumulation: a daily total (``period_s=86400``, default) or a de-accumulated
    hourly increment (``period_s=3600``).  See
    :func:`datavia.library.temporal.deaccumulate_since_midnight`.

    1. J m⁻² per period  →  W m⁻² (divide by *period_s*).
    2. W m⁻²             →  PAR µmol m⁻² s⁻¹ (see :func:`w_m2_to_par`).

    Parameters
    ----------
    ssrd_j_m2 : np.ndarray or float
        SSRD energy total in J m⁻² over one period.
    period_s : float, optional
        Length of the period in seconds.  Defaults to one day.

    Returns
    -------
    np.ndarray or float
        Mean PAR flux in µmol(photons) m⁻² s⁻¹.  Same type and shape as
        *ssrd_j_m2*.
    """
    arr = np.asarray(ssrd_j_m2, dtype=float)
    return w_m2_to_par(arr / period_s)


__all__ = [
    "kelvin_to_celsius",
    "precipitation_m_to_mm",
    "ssrd_to_par",
    "w_m2_to_par",
]
