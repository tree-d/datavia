"""Time-base helpers shared by all weather sources.

Conventions
-----------
* All timestamps are naive UTC.
* ERA5 and Open-Meteo stamp fluxes and accumulations at **interval end**:
  the value at ``01:00`` covers ``00:00-01:00``, and the value at ``00:00``
  of day D+1 covers the last hour of day D.
* A *day D* therefore consists of the stamps ``D 01:00 .. D+1 00:00``.
* ERA5-Land accumulations (``ssrd``, ``tp``) run from 00 UTC and reset after
  the ``00:00`` stamp, which holds the previous day's 24 h total.
"""

from __future__ import annotations

import pandas as pd
import xarray as xr

_ONE_HOUR = pd.Timedelta(hours=1)


def deaccumulate_since_midnight(
    da: xr.DataArray, time_dim: str = "time"
) -> xr.DataArray:
    """Convert ERA5-Land accumulations (reset at 00 UTC) to hourly increments.

    * stamp ``01:00`` -> ``x[01:00]`` (first step after the reset; no
      predecessor needed),
    * every other stamp -> ``x[t] - x[t - 1 h]``.

    The ``00:00`` stamp needs ``x`` at ``23:00`` of the previous day; when that
    predecessor is absent from *da* the increment is NaN rather than a wrong
    value.  Result has the same coordinates and units as *da* (per hour).
    """
    times = pd.DatetimeIndex(da[time_dim].values)
    prev = da.reindex({time_dim: times - _ONE_HOUR}).assign_coords(
        {time_dim: da[time_dim]}
    )
    increment = da - prev
    first_step = xr.DataArray(
        times.hour == 1, dims=[time_dim], coords={time_dim: da[time_dim]}
    )
    return xr.where(first_step, da, increment).astype(da.dtype)


def daily_window(day: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return the ``(exclusive_start, inclusive_end)`` stamps of UTC day *day*."""
    start = pd.Timestamp(day).normalize()
    return start, start + pd.Timedelta(days=1)


def to_daily(
    df: pd.DataFrame,
    value_col: str,
    how: str,
    day: pd.Timestamp,
    group_cols: list[str],
    hours_required: int = 24,
) -> pd.DataFrame:
    """Aggregate interval-end hourly records of *df* to one value per group for *day*.

    Keeps only groups with ``hours_required`` non-null hourly values, so a
    partial day never passes for a daily mean/sum.  *how* is ``"mean"`` or
    ``"sum"``.
    """
    start, end = daily_window(day)
    sel = df[(df["datetime"] > start) & (df["datetime"] <= end)]
    grouped = sel.groupby(group_cols)[value_col]
    out = grouped.agg([how, "count"]).reset_index()
    out = out[out["count"] >= hours_required].drop(columns="count")
    return out.rename(columns={how: value_col})


__all__ = ["daily_window", "deaccumulate_since_midnight", "to_daily"]
