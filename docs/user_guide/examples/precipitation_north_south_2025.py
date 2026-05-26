#!/usr/bin/env python3
"""North–South precipitation transect across Germany — HYRAS 2025.

Computes monthly and annual precipitation totals for eight stations placed
along a north-to-south transect from Flensburg (Baltic coast) to
Berchtesgaden (Alpine fringe).  Uses the full 2025 calendar year so that
seasonal patterns are visible.

The north–south precipitation gradient is one of Germany's most studied
climatological features:

- The North German Plain is dry by European standards (550–700 mm/yr),
  with maritime westerly influence peaking in autumn/winter.
- The Alpine foreland and the mountains themselves receive 1 200–2 000 mm/yr,
  with a strong summer-convective maximum.

Researchers use this kind of transect to validate model output and to
calibrate drought-index baselines.

**Data note** — this script queries 365 days × 8 sites = 2 920 values from
a single on-disk NetCDF file; no network access is required.

Run::

    pixi run python docs/user_guide/examples/precipitation_north_south_2025.py
"""

from __future__ import annotations

import logging
import math
import sys
from datetime import date, timedelta

import numpy as np
from datavia.weather import WeatherPipeline

from datavia import Datavia

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Transect stations (north → south)
# ---------------------------------------------------------------------------

#: Eight stations along a north–south German transect.
TRANSECT: list[dict[str, object]] = [
    {"name": "Flensburg (N coast)", "lat": 54.79, "lon": 9.43},
    {"name": "Hamburg", "lat": 53.55, "lon": 9.99},
    {"name": "Hannover", "lat": 52.37, "lon": 9.73},
    {"name": "Kassel", "lat": 51.31, "lon": 9.50},
    {"name": "Würzburg", "lat": 49.80, "lon": 9.93},
    {"name": "Augsburg", "lat": 48.37, "lon": 10.90},
    {"name": "Rosenheim", "lat": 47.86, "lon": 12.13},
    {"name": "Berchtesgaden (Alps)", "lat": 47.63, "lon": 13.00},
]

_YEAR = 2025
_YEAR_START = date(_YEAR, 1, 1)
_YEAR_END = date(_YEAR, 12, 31)
_N_DAYS = (_YEAR_END - _YEAR_START).days + 1

_MONTH_NAMES = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


def build_pipeline() -> WeatherPipeline:
    """Create a HYRAS pipeline for the full year 2025.

    Returns
    -------
    WeatherPipeline
        Pipeline configured for ``total_precipitation`` over 2025.
    """
    return WeatherPipeline(
        config={
            "source": "HYRAS",
            "variables": ["total_precipitation"],
            "date_start": str(_YEAR_START),
            "date_end": str(_YEAR_END),
        }
    )


def collect_daily_precip(pipeline: WeatherPipeline) -> np.ndarray:
    """Query daily precipitation for all transect stations for the whole year.

    Issues a **single** ``get_data()`` call with all 8 station coordinates
    *and* all ``_N_DAYS`` timestamps batched, opening the NetCDF file only
    once.  HYRAS precipitation timestamps are at 06:00 UTC (end of
    accumulation period); nearest-time matching inside the getter handles
    the noon-UTC query offset automatically.

    Parameters
    ----------
    pipeline : WeatherPipeline
        Initialised pipeline covering 2025.

    Returns
    -------
    np.ndarray
        Precipitation array of shape ``(N_days, N_stations)`` in mm/day.
        ``NaN`` for any unavailable day/station.
    """
    coords = np.array([[s["lon"], s["lat"]] for s in TRANSECT])
    all_timestamps = [
        f"{(_YEAR_START + timedelta(days=i)).isoformat()}T12:00:00"
        for i in range(_N_DAYS)
    ]

    print(
        f"  Querying all {_N_DAYS} days x {len(TRANSECT)} stations in one batch call…",
        flush=True,
    )

    try:
        # Returns shape (N_stations, N_days); transpose to (N_days, N_stations).
        batch = pipeline.get_data(
            coords=coords,
            crs_coords="EPSG:4326",
            variable="total_precipitation",
            datetime_utc=all_timestamps,
        )
        precip = np.asarray(batch, dtype=float).T
    except Exception as exc:  # noqa: BLE001
        logger.warning("Batch precipitation query failed: %s", exc)
        precip = np.full((_N_DAYS, len(TRANSECT)), np.nan)

    return precip


def monthly_totals(precip: np.ndarray) -> np.ndarray:
    """Aggregate daily values into calendar-month totals.

    Parameters
    ----------
    precip : np.ndarray
        Shape ``(N_days, N_stations)`` daily precipitation in mm.

    Returns
    -------
    np.ndarray
        Shape ``(12, N_stations)`` monthly totals in mm.  ``NaN`` propagates
        when any day in the month is missing (safe-sums NaN-aware).
    """
    monthly = np.zeros((12, len(TRANSECT)))
    has_nan = np.zeros((12, len(TRANSECT)), dtype=bool)
    for day_idx in range(_N_DAYS):
        query_date = _YEAR_START + timedelta(days=day_idx)
        month_idx = query_date.month - 1
        row = precip[day_idx]
        nan_mask = np.isnan(row)
        has_nan[month_idx] |= nan_mask
        monthly[month_idx] += np.where(nan_mask, 0.0, row)
    # Propagate NaN for any month/station that had at least one missing day,
    # so callers can distinguish a genuine zero from a data gap.
    monthly[has_nan] = np.nan
    return monthly


def print_report(precip: np.ndarray, monthly: np.ndarray) -> None:
    """Print monthly totals, annual totals, and a simple ASCII bar chart.

    Parameters
    ----------
    precip : np.ndarray
        Daily array ``(N_days, N_stations)``.
    monthly : np.ndarray
        Monthly totals ``(12, N_stations)``.
    """
    annual = monthly.sum(axis=0)  # shape (N_stations,)
    nan_count = int(np.sum(np.isnan(precip)))

    print(f"\n=== North–South Precipitation Transect — Germany {_YEAR} ===")
    print(f"    Period: {_YEAR_START} → {_YEAR_END}  ({_N_DAYS} days)")
    if nan_count:
        print(f"    ⚠  {nan_count} NaN values in raw data")
    print()

    # --- Monthly table ---
    col_w = 9
    header = f"{'Month':<5}" + "".join(
        f"{str(s['name'])[: col_w - 1]:>{col_w}}" for s in TRANSECT
    )
    print(header)
    print("─" * len(header))
    for m_idx, m_name in enumerate(_MONTH_NAMES):
        row_vals = monthly[m_idx]
        cells = "".join(
            f"{'N/A':>{col_w}}" if math.isnan(v) else f"{v:>{col_w}.1f}"
            for v in row_vals
        )
        print(f"{m_name:<5}{cells}")
    print("─" * len(header))
    ann_cells = "".join(
        f"{'N/A':>{col_w}}" if math.isnan(v) else f"{v:>{col_w}.0f}" for v in annual
    )
    print(f"{'ANN':<5}{ann_cells}")
    print()

    # --- ASCII gradient bar (annual totals) ---
    print("  Annual totals — north to south (each █ ≈ 20 mm):")
    max_annual = (
        float(np.nanmax(annual)) if not all(math.isnan(v) for v in annual) else 1.0
    )
    for station, ann in zip(TRANSECT, annual):
        if math.isnan(ann):
            bar = "N/A"
        else:
            n_blocks = max(1, round(ann / 20))
            bar = "█" * n_blocks + f"  {ann:.0f} mm"
        print(f"  {str(station['name']):<28}  {bar}")
    print()


def main() -> None:
    """Run the north–south precipitation transect analysis.

    Steps
    -----
    1. Build a full-year 2025 HYRAS pipeline for precipitation.
    2. ``update_data()`` — should be a no-op if the 2025 file is cached.
    3. Query 365 days × 8 stations.
    4. Aggregate to monthly/annual totals and print.
    """
    print("Building HYRAS pipeline (total_precipitation, full year 2025)…")
    pipeline = build_pipeline()
    dv = Datavia(pipelines=[pipeline])
    dv()

    print("Calling update_data() — no downloads expected…")
    success = pipeline.update_data()
    if not success:
        print("ERROR: update_data() reported failure.", file=sys.stderr)
        sys.exit(1)

    print(f"Querying {_N_DAYS} days × {len(TRANSECT)} stations…")
    precip = collect_daily_precip(pipeline)

    monthly = monthly_totals(precip)
    print_report(precip, monthly)


if __name__ == "__main__":
    main()
