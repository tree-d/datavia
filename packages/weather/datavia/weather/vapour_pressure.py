"""Vapour pressure deficit after FAO-56.

Vapour pressure deficit (VPD) is the difference between the saturation
vapour pressure of the air, ``es``, and its actual vapour pressure, ``ea``.
The formulas follow FAO Irrigation and Drainage Paper 56 (Allen, Pereira,
Raes and Smith, 1998, chapter 3):

- ``e°(T) = 0.6108 * exp(17.27 T / (T + 237.3))`` kPa, T in °C (eq. 11);
- daily ``es = (e°(Tmax) + e°(Tmin)) / 2`` (eq. 12), because ``e°`` is convex
  and ``e°(Tmean)`` would underestimate it;
- daily ``ea`` from a measured vapour pressure, or from the mean relative
  humidity as ``ea = RHmean / 100 * es`` (eq. 19);
- hourly ``es = e°(T)`` and ``ea = e°(Td)`` from the dewpoint (eq. 14).

Each weather source uses the form its data allow; see
:data:`~datavia.weather.source_registry.SOURCE_REGISTRY` and the downloaders
of ``"DWD_stations"``, ``"HYRAS"`` and ``"ERA5_land"``.  Every function
returns kPa, clips negative deficits (measurement noise) to 0 and propagates
NaN.  The functions use numpy ufuncs only, so ``xarray.DataArray`` inputs
(dask-backed ones included) give a ``DataArray`` of the same shape, still
lazy.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, cast

import numpy as np

# Imported at run time: the type-parameter constraints name xr.DataArray, and
# Sphinx evaluates them when rendering the signatures.
import xarray as xr

if TYPE_CHECKING:
    from collections.abc import Mapping

#: Pipeline variable name of the vapour pressure deficit.
VARIABLE: str = "vapour_pressure_deficit"

#: NetCDF variable name of the vapour pressure deficit in downloaded files
#: (HYRAS-style short name); ``nc_variable_map`` maps it to the pipeline name.
NC_NAME: str = "vpd"

#: CF attributes of the stored vapour pressure deficit.  Writers add
#: ``cell_methods`` (and ``grid_mapping`` for projected grids).
CF_ATTRIBUTES: Mapping[str, str] = MappingProxyType(
    {
        "standard_name": "water_vapor_saturation_deficit_in_air",
        "long_name": "Vapour Pressure Deficit (FAO-56)",
        "units": "kPa",
    }
)

#: NetCDF variable name of the actual vapour pressure (DWD station ``VPM``).
VAPOUR_PRESSURE_NC_NAME: str = "vp"

#: CF attributes of the stored actual vapour pressure (kPa, like the deficit).
VAPOUR_PRESSURE_CF_ATTRIBUTES: Mapping[str, str] = MappingProxyType(
    {
        "standard_name": "water_vapor_partial_pressure_in_air",
        "long_name": "Vapour Pressure",
        "units": "kPa",
    }
)

#: Saturation vapour pressure at 0 °C [kPa] (FAO-56 eq. 11).
_FAO56_PRESSURE_AT_0C_KPA: float = 0.6108

#: Dimensionless slope of the FAO-56 eq. 11 exponent.
_FAO56_SLOPE: float = 17.27

#: Temperature offset of the FAO-56 eq. 11 exponent [°C].
_FAO56_OFFSET_C: float = 237.3

#: Relative humidity of saturated air [%].
_SATURATED_PERCENT: float = 100.0


def saturation_vapour_pressure[Values: (float, np.ndarray, xr.DataArray)](
    temperature_c: Values,
) -> Values:
    """Return the saturation vapour pressure ``e°(T)`` (FAO-56 eq. 11).

    Parameters
    ----------
    temperature_c : float, np.ndarray or xr.DataArray
        Air temperature in °C.

    Returns
    -------
    float, np.ndarray or xr.DataArray
        Saturation vapour pressure in kPa, same type and shape as the input.
    """
    exponent = _FAO56_SLOPE * temperature_c / (temperature_c + _FAO56_OFFSET_C)
    return cast(Values, _FAO56_PRESSURE_AT_0C_KPA * np.exp(exponent))


def vapour_pressure_deficit[Values: (float, np.ndarray, xr.DataArray)](
    temperature_max_c: Values,
    temperature_min_c: Values,
    actual_vapour_pressure_kpa: Values,
) -> Values:
    """Return the daily deficit from a measured actual vapour pressure.

    ``es`` is the mean of ``e°`` at the daily maximum and minimum temperature
    (FAO-56 eq. 12); the deficit is ``max(es - ea, 0)``.

    Parameters
    ----------
    temperature_max_c, temperature_min_c : float, np.ndarray or xr.DataArray
        Daily maximum and minimum air temperature in °C.
    actual_vapour_pressure_kpa : float, np.ndarray or xr.DataArray
        Daily mean actual vapour pressure ``ea`` in kPa.

    Returns
    -------
    float, np.ndarray or xr.DataArray
        Vapour pressure deficit in kPa, at least 0; NaN where an input is NaN.
    """
    saturation = _mean_saturation_vapour_pressure(temperature_max_c, temperature_min_c)
    return _clip_negative(saturation - actual_vapour_pressure_kpa)


def vapour_pressure_deficit_from_relative_humidity[
    Values: (float, np.ndarray, xr.DataArray)
](
    temperature_max_c: Values,
    temperature_min_c: Values,
    relative_humidity_percent: Values,
) -> Values:
    """Return the daily deficit from the daily mean relative humidity.

    With ``ea = RHmean / 100 * es`` (FAO-56 eq. 19) the deficit is
    ``es * (1 - RHmean / 100)``, ``es`` as in :func:`vapour_pressure_deficit`.

    Parameters
    ----------
    temperature_max_c, temperature_min_c : float, np.ndarray or xr.DataArray
        Daily maximum and minimum air temperature in °C.
    relative_humidity_percent : float, np.ndarray or xr.DataArray
        Daily mean relative humidity in %.

    Returns
    -------
    float, np.ndarray or xr.DataArray
        Vapour pressure deficit in kPa, at least 0 (also for a humidity above
        100 %); NaN where an input is NaN.
    """
    saturation = _mean_saturation_vapour_pressure(temperature_max_c, temperature_min_c)
    dry_fraction = 1.0 - relative_humidity_percent / _SATURATED_PERCENT
    return _clip_negative(saturation * dry_fraction)


def vapour_pressure_deficit_from_dewpoint[Values: (float, np.ndarray, xr.DataArray)](
    temperature_c: Values, dewpoint_c: Values
) -> Values:
    """Return the deficit at one instant from temperature and dewpoint.

    ``es = e°(T)`` and ``ea = e°(Td)`` (FAO-56 eq. 14), for hourly data.

    Parameters
    ----------
    temperature_c : float, np.ndarray or xr.DataArray
        Air temperature in °C.
    dewpoint_c : float, np.ndarray or xr.DataArray
        Dewpoint temperature in °C.

    Returns
    -------
    float, np.ndarray or xr.DataArray
        Vapour pressure deficit in kPa, at least 0 (also for a dewpoint above
        the temperature); NaN where an input is NaN.
    """
    deficit = saturation_vapour_pressure(temperature_c) - saturation_vapour_pressure(
        dewpoint_c
    )
    return _clip_negative(deficit)


def _mean_saturation_vapour_pressure[Values: (float, np.ndarray, xr.DataArray)](
    temperature_max_c: Values, temperature_min_c: Values
) -> Values:
    """Return the daily ``es`` from the extremes (FAO-56 eq. 12), in kPa."""
    total = np.add(
        saturation_vapour_pressure(temperature_max_c),
        saturation_vapour_pressure(temperature_min_c),
    )
    return cast(Values, total / 2.0)


def _clip_negative[Values: (float, np.ndarray, xr.DataArray)](
    deficit: Values,
) -> Values:
    """Return *deficit* with negative values set to 0; NaN stays NaN."""
    # np.maximum propagates NaN and dispatches to xarray (__array_ufunc__).
    return cast(Values, np.maximum(deficit, 0.0))


__all__ = [
    "CF_ATTRIBUTES",
    "NC_NAME",
    "VAPOUR_PRESSURE_CF_ATTRIBUTES",
    "VAPOUR_PRESSURE_NC_NAME",
    "VARIABLE",
    "saturation_vapour_pressure",
    "vapour_pressure_deficit",
    "vapour_pressure_deficit_from_dewpoint",
    "vapour_pressure_deficit_from_relative_humidity",
]
