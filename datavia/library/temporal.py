"""Time-base helpers shared by all weather sources.

Conventions
-----------
* All timestamps are naive UTC.
* ERA5 and Open-Meteo stamp fluxes and accumulations at **interval end**:
  the value at ``01:00`` covers ``00:00-01:00``, and the value at ``00:00``
  of day D+1 covers the last hour of day D.
* A *day D* therefore consists of the stamps ``D 01:00 .. D+1 00:00``.  A
  variable's day may start at another UTC hour (``day_start_hour``; 6 for
  precipitation, matching HYRAS and DWD daily totals).
* ERA5-Land accumulations (``ssrd``, ``tp``) run from 00 UTC and reset after
  the ``00:00`` stamp, which holds the previous day's 24 h total.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import xarray as xr

_ONE_HOUR = pd.Timedelta(hours=1)
_STAMP_TOLERANCE = pd.Timedelta(minutes=30)


def deaccumulate_since_midnight(
    da: xr.DataArray, time_dim: str = "time", at: Any = None
) -> xr.DataArray:
    """Convert ERA5-Land accumulations (reset at 00 UTC) to hourly increments.

    * stamp ``01:00`` -> ``x[01:00]`` (first step after the reset; no
      predecessor needed),
    * every other stamp -> ``x[t] - x[t - 1 h]``.

    The ``00:00`` stamp needs ``x`` at ``23:00`` of the previous day; when that
    predecessor is absent from *da* the increment is NaN rather than a wrong
    value.  Result is in units per hour.

    Parameters
    ----------
    da : xr.DataArray
        Accumulated values with hourly interval-end stamps along *time_dim*.
    time_dim : str
        Name of the time dimension.
    at : sequence of datetime-like, optional
        Return increments only at these times, each rounded to the nearest
        hour (NaN when that stamp or its predecessor is absent).  The default
        returns one increment per stamp of *da*.
    """
    if at is None:
        stamps = pd.DatetimeIndex(da[time_dim].values)
        current = da
    else:
        stamps = pd.DatetimeIndex(at).round("1h")
        current = da.reindex({time_dim: stamps})
    prev = da.reindex({time_dim: stamps - _ONE_HOUR}).assign_coords(
        {time_dim: current[time_dim]}
    )
    first_step = xr.DataArray(
        stamps.hour == 1, dims=[time_dim], coords={time_dim: current[time_dim]}
    )
    return xr.where(first_step, current, current - prev).astype(da.dtype)


def accumulated_day_total(
    da: xr.DataArray,
    targets: Any,
    day_start_hour: int = 0,
    time_dim: str = "time",
    tolerance: pd.Timedelta = _STAMP_TOLERANCE,
) -> xr.DataArray:
    """Total over the day containing each target, from a running accumulation.

    Day D runs from ``D + day_start_hour`` to ``D + 1 day + day_start_hour``;
    a target belongs to the day whose window contains it.  With accumulation
    reset at 00 UTC the window total is::

        x[D+1 00:00] - x[D h0] + x[D+1 h0]      (h0 = day_start_hour > 0)
        x[D+1 00:00]                             (h0 = 0)

    Any stamp missing from *da* (beyond *tolerance*) makes that target NaN, so
    a partial day never passes for a total.  The result has one entry per
    target, indexed by the day start.
    """
    offset = pd.Timedelta(hours=day_start_hour)
    days = pd.DatetimeIndex([(pd.Timestamp(t) - offset).normalize() for t in targets])
    one_day = pd.Timedelta(days=1)

    def at(stamps: pd.DatetimeIndex) -> xr.DataArray:
        picked = da.reindex({time_dim: stamps}, method="nearest", tolerance=tolerance)
        return picked.assign_coords({time_dim: days})

    total = at(days + one_day)
    if day_start_hour:
        total = total - at(days + offset) + at(days + one_day + offset)
    return total


def daily_window(
    day: Any, day_start_hour: int = 0
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return the ``(exclusive_start, inclusive_end)`` stamps of the day holding *day*.

    Interval-end stamps belong to the window ``(start, end]``.  The day starts
    at ``day_start_hour`` UTC (0 by default; 6 for precipitation).
    """
    offset = pd.Timedelta(hours=day_start_hour)
    start = (pd.Timestamp(day) - offset).normalize() + offset
    return start, start + pd.Timedelta(days=1)


def to_daily(
    df: pd.DataFrame,
    value_col: str,
    how: str,
    day: pd.Timestamp,
    group_cols: list[str],
    hours_required: int = 24,
    day_start_hour: int = 0,
) -> pd.DataFrame:
    """Aggregate interval-end hourly records of *df* to one value per group for *day*.

    Keeps only groups with ``hours_required`` non-null hourly values, so a
    partial day never passes for a daily mean/sum.  *how* is ``"mean"`` or
    ``"sum"``.
    """
    start, end = daily_window(day, day_start_hour)
    sel = df[(df["datetime"] > start) & (df["datetime"] <= end)]
    grouped = sel.groupby(group_cols)[value_col]
    out = grouped.agg([how, "count"]).reset_index()
    out = out[out["count"] >= hours_required].drop(columns="count")
    return out.rename(columns={how: value_col})


__all__ = [
    "accumulated_day_total",
    "daily_window",
    "deaccumulate_since_midnight",
    "to_daily",
]
