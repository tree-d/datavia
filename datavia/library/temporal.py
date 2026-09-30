"""Time-base helpers shared by all weather sources.

Conventions
-----------
* All timestamps are naive UTC.
* In daily mode a target's **calendar date D** is the day label; its time of
  day is ignored.  Day D spans ``D h0`` to ``D+1 h0`` with ``h0`` the
  variable's ``day_start_hour`` (0; 6 for precipitation, the HYRAS/DWD day).
* Three kinds of series (:data:`SeriesType`):

  - ``"daily"`` — one stamp per day (HYRAS).  The stamp dated D is day D,
    whatever its hour (tas 00:00, pr 06:00, rsds 12:00).
  - ``"instantaneous"`` — hourly snapshots (temperature, humidity).  Day D
    is the mean of the 24 stamps in ``[D 00:00, D+1 00:00)``.
  - ``"accumulated"`` — ERA5-Land running totals since 00 UTC (``tp``,
    ``ssrd``); see :func:`deaccumulate_since_midnight`.

* ERA5 and Open-Meteo stamp fluxes at **interval end**: the value at
  ``01:00`` covers ``00:00-01:00``, so flux day D holds the stamps in
  ``(D h0, D+1 h0]``.
* Every helper returns NaN when a needed stamp is missing, so a partial day
  never passes for a daily value.
"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd
import xarray as xr

SeriesType = Literal["daily", "instantaneous", "accumulated"]
SERIES_TYPES: tuple[str, ...] = ("daily", "instantaneous", "accumulated")

_ONE_HOUR = pd.Timedelta(hours=1)
_ONE_DAY = pd.Timedelta(days=1)
_STAMP_TOLERANCE = pd.Timedelta(minutes=30)
#: Median time step from which a series counts as daily-native.
_DAILY_STEP = pd.Timedelta(hours=23)


def day_labels(targets: Any) -> pd.DatetimeIndex:
    """Return the day label (calendar date at 00:00) of each target."""
    return pd.DatetimeIndex([pd.Timestamp(t).normalize() for t in targets])


def day_window(day: Any, day_start_hour: int = 0) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return the ``(start, end)`` bounds of the day labelled by *day*'s date.

    ``start = D + day_start_hour`` and ``end = start + 1 day``.  Interval-end
    stamps belong to ``(start, end]``, instantaneous stamps to ``[start, end)``.
    """
    start = pd.Timestamp(day).normalize() + pd.Timedelta(hours=day_start_hour)
    return start, start + _ONE_DAY


def infer_series_type(times: Any) -> SeriesType:
    """Return ``"daily"`` for a time axis stepping by about a day, else
    ``"instantaneous"``.  An axis with fewer than two stamps counts as daily."""
    idx = pd.DatetimeIndex(times).sort_values()
    if len(idx) < 2:
        return "daily"
    step = pd.Series(idx).diff().median()
    return "daily" if step >= _DAILY_STEP else "instantaneous"


def _pick(
    da: xr.DataArray,
    stamps: pd.DatetimeIndex,
    labels: Any,
    time_dim: str,
    tolerance: pd.Timedelta = _STAMP_TOLERANCE,
) -> xr.DataArray:
    """Values of *da* at *stamps* (nearest within *tolerance*, else NaN),
    relabelled with *labels* along *time_dim*."""
    picked = da.reindex({time_dim: stamps}, method="nearest", tolerance=tolerance)
    return picked.assign_coords({time_dim: labels})


def select_nearest(
    da: xr.DataArray,
    targets: Any,
    time_dim: str = "time",
    tolerance: pd.Timedelta = _STAMP_TOLERANCE,
) -> xr.DataArray:
    """Value at the stamp nearest each target, NaN if none is within *tolerance*."""
    stamps = pd.DatetimeIndex(targets)
    return _pick(da, stamps, stamps, time_dim, tolerance)


def select_day(da: xr.DataArray, targets: Any, time_dim: str = "time") -> xr.DataArray:
    """Daily-native data: the stamp dated D for each target's day D.

    The stamp's hour is ignored, so data stamped at 00:00, 06:00 or 12:00 all
    resolve to the same day.  A day without a stamp gives NaN.
    """
    by_day = da.assign_coords(
        {time_dim: pd.DatetimeIndex(da[time_dim].values).normalize()}
    )
    return by_day.reindex({time_dim: day_labels(targets)})


def instantaneous_day_mean(
    da: xr.DataArray,
    targets: Any,
    time_dim: str = "time",
    tolerance: pd.Timedelta = _STAMP_TOLERANCE,
) -> xr.DataArray:
    """Mean of the 24 hourly stamps ``[D 00:00, D+1 00:00)`` of each target's day.

    NaN when any of the 24 stamps is missing.  The result is indexed by day.
    """
    days = day_labels(targets)
    hours = np.arange(24) * np.timedelta64(1, "h")
    stamps = pd.DatetimeIndex((days.values[:, None] + hours).ravel())
    picked = da.reindex({time_dim: stamps}, method="nearest", tolerance=tolerance)
    means = picked.drop_vars(time_dim).coarsen({time_dim: 24}).mean(skipna=False)
    return means.assign_coords({time_dim: days})


def deaccumulate_since_midnight(
    da: xr.DataArray,
    at: Any = None,
    time_dim: str = "time",
    tolerance: pd.Timedelta = _STAMP_TOLERANCE,
) -> xr.DataArray:
    """Convert ERA5-Land accumulations to hourly increments (units per hour).

    ERA5-Land ``tp``/``ssrd`` run from 00 UTC and reset after the ``00:00``
    stamp, which holds the previous day's 24 h total.  Hence:

    * stamp ``01:00`` -> ``x[01:00]`` (first step after the reset),
    * every other stamp -> ``x[t] - x[t - 1 h]``.

    A missing stamp or predecessor gives NaN.  Small negative differences,
    which occur in real ERA5-Land data, are clipped to 0.

    Parameters
    ----------
    da : xr.DataArray
        Accumulated values with hourly interval-end stamps along *time_dim*.
    at : sequence of datetime-like, optional
        Return the increment of the hour containing each time, i.e. the
        hour ending at ``t.ceil("1h")``.  The default returns one increment
        per stamp of *da*.
    time_dim : str
        Name of the time dimension.
    tolerance : pd.Timedelta
        Maximum offset between a wanted stamp and the stamp used.
    """
    if at is None:
        stamps = pd.DatetimeIndex(da[time_dim].values)
        current = da
    else:
        stamps = pd.DatetimeIndex(at).ceil("1h")
        current = _pick(da, stamps, stamps, time_dim, tolerance)
    prev = _pick(da, stamps - _ONE_HOUR, current[time_dim].values, time_dim, tolerance)
    first_step = xr.DataArray(
        stamps.hour == 1, dims=[time_dim], coords={time_dim: current[time_dim]}
    )
    return xr.where(first_step, current, current - prev).clip(min=0)


def accumulated_day_total(
    da: xr.DataArray,
    targets: Any,
    day_start_hour: int = 0,
    time_dim: str = "time",
    tolerance: pd.Timedelta = _STAMP_TOLERANCE,
) -> xr.DataArray:
    """Total over each target's day D from an ERA5-Land running accumulation.

    With the accumulation reset at 00 UTC, the total of ``(D h0, D+1 h0]`` is::

        x[D+1 00:00] - x[D h0] + x[D+1 h0]      (h0 = day_start_hour > 0)
        x[D+1 00:00]                             (h0 = 0)

    A missing stamp gives NaN.  The result is clipped to 0 and indexed by day.
    """
    days = day_labels(targets)
    offset = pd.Timedelta(hours=day_start_hour)

    def at(stamps: pd.DatetimeIndex) -> xr.DataArray:
        return _pick(da, stamps, days, time_dim, tolerance)

    total = at(days + _ONE_DAY)
    if day_start_hour:
        total = total - at(days + offset) + at(days + _ONE_DAY + offset)
    return total.clip(min=0)


def to_daily(
    df: pd.DataFrame,
    value_col: str,
    how: str,
    day: Any,
    group_cols: list[str],
    hours_required: int = 24,
    day_start_hour: int = 0,
    interval_end: bool = True,
) -> pd.DataFrame:
    """Aggregate hourly records of *df* to one value per group for *day*'s date.

    *how* is ``"mean"`` or ``"sum"``.  Interval-end records (fluxes) are
    taken from ``(start, end]``, instantaneous ones from ``[start, end)``; see
    :func:`day_window`.  Only groups with ``hours_required`` non-null values
    are kept, so a partial day never passes for a daily value.
    """
    start, end = day_window(day, day_start_hour)
    t = df["datetime"]
    in_day = (t > start) & (t <= end) if interval_end else (t >= start) & (t < end)
    grouped = df[in_day].groupby(group_cols)[value_col]
    out = grouped.agg([how, "count"]).reset_index()
    out = out[out["count"] >= hours_required].drop(columns="count")
    return out.rename(columns={how: value_col})


__all__ = [
    "SERIES_TYPES",
    "SeriesType",
    "accumulated_day_total",
    "day_labels",
    "day_window",
    "deaccumulate_since_midnight",
    "infer_series_type",
    "instantaneous_day_mean",
    "select_day",
    "select_nearest",
    "to_daily",
]
