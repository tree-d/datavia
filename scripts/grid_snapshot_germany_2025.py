#!/usr/bin/env python3
"""Multi-variable snapshot on a regular Germany grid — HYRAS 2025.

Samples temperature and precipitation on a **5 × 4 regular lat/lon grid**
covering mainland Germany and prints a compact ASCII map for three
representative days:

- 1 January 2025  — mid-winter (year-boundary edge case)
- 15 April 2025   — spring (near-mean conditions)
- 15 July 2025    — mid-summer

Each grid point is queried once per day per variable with a single
vectorised :meth:`~datavia.weather.pipeline.WeatherPipeline.get_data` call,
so the script exercises the bulk-coordinate path end-to-end.

For researchers this kind of snapshot is the starting point for spatial
pattern analysis — e.g. comparing temperature maps before and after a
front passage, or validating model output against gridded observations.

Run (no network access required — uses cached 2025 HYRAS files)::

    python scripts/grid_snapshot_germany_2025.py
"""

from __future__ import annotations

import logging
import math
import sys
from datetime import date

import numpy as np

from datavia import Datavia
from datavia.weather import WeatherPipeline

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regular grid over mainland Germany
# ---------------------------------------------------------------------------
# Bounding box: lon 6.0 – 15.0 E, lat 47.5 – 55.0 N (5 × 4 points)

_LON_EDGES = np.linspace(6.0, 15.0, 5)  # 6, 8.25, 10.5, 12.75, 15
_LAT_EDGES = np.linspace(55.0, 47.5, 4)  # 55, 52.5, 50, 47.5 (north → south)

#: Grid as a flat list of (lon, lat) pairs — column-major (west → east, N → S).
GRID_COORDS: np.ndarray = np.array(
    [[lon, lat] for lat in _LAT_EDGES for lon in _LON_EDGES]
)

#: Snapshot dates to query.
SNAPSHOT_DATES: list[date] = [
    date(2025, 1, 1),  # year boundary
    date(2025, 4, 15),  # spring
    date(2025, 7, 15),  # summer
]

#: Variables to query — both must be in the pipeline config.
VARIABLES: list[tuple[str, str]] = [
    ("2m_temperature", "°C"),
    ("total_precipitation", "mm"),
]

_N_LATS = len(_LAT_EDGES)
_N_LONS = len(_LON_EDGES)


def build_pipeline() -> WeatherPipeline:
    """Create a HYRAS pipeline covering all three snapshot dates.

    Returns
    -------
    WeatherPipeline
        Pipeline for both temperature and precipitation over full 2025.
    """
    return WeatherPipeline(
        config={
            "source": "HYRAS",
            "variables": ["2m_temperature", "total_precipitation"],
            "date_start": "2025-01-01",
            "date_end": "2025-12-31",
        }
    )


def query_snapshot(
    pipeline: WeatherPipeline,
    snapshot_date: date,
    variable: str,
) -> np.ndarray:
    """Return a grid snapshot for one date and one variable.

    Parameters
    ----------
    pipeline : WeatherPipeline
        Initialised pipeline.
    snapshot_date : date
        Date to query (queried at 12:00 UTC).
    variable : str
        Pipeline variable name.

    Returns
    -------
    np.ndarray
        Shape ``(N_lats, N_lons)`` value grid, ``NaN`` where unavailable.
    """
    datetime_utc = f"{snapshot_date.isoformat()}T12:00:00"
    flat_values = pipeline.get_data(
        coords=GRID_COORDS,
        crs_coords="EPSG:4326",
        variable=variable,
        datetime_utc=datetime_utc,
    )
    return flat_values.reshape(_N_LATS, _N_LONS)


def ascii_map(grid: np.ndarray, label: str, unit: str, fmt: str = ".1f") -> str:
    """Render a (N_lats × N_lons) value grid as a compact ASCII table.

    Parameters
    ----------
    grid : np.ndarray
        Shape ``(N_lats, N_lons)`` — north at row 0.
    label : str
        Variable name used in the header.
    unit : str
        Unit string shown next to values.
    fmt : str, optional
        Format spec for the numbers. Defaults to ``".1f"``.

    Returns
    -------
    str
        Multi-line string ready to print.
    """
    col_w = 8
    lon_header = "  lat↓  lon→  " + "".join(f"{lon:>{col_w}.1f}" for lon in _LON_EDGES)
    lines = [f"  {label}  [{unit}]", lon_header, "  " + "─" * (len(lon_header) - 2)]
    for row_idx, lat in enumerate(_LAT_EDGES):
        cells = []
        for col_idx in range(_N_LONS):
            v = grid[row_idx, col_idx]
            if math.isnan(v):
                cells.append(f"{'N/A':>{col_w}}")
            else:
                cells.append(f"{v:{col_w}{fmt}}")
        lines.append(f"  {lat:>6.1f}°N  {''.join(cells)}")
    return "\n".join(lines)


def print_report(
    results: dict[tuple[date, str], np.ndarray],
    nan_stats: dict[tuple[date, str], int],
) -> None:
    """Print all snapshot ASCII maps with per-snapshot NaN warnings.

    Parameters
    ----------
    results : dict
        Keys are ``(date, variable)`` tuples; values are ``(N_lats, N_lons)``
        grids.
    nan_stats : dict
        Same keys; values are the NaN count per snapshot.
    """
    print("\n=== Multi-Variable Grid Snapshot — Germany 2025 (HYRAS) ===")
    print(
        f"    Grid: {_N_LONS} × {_N_LATS} points"
        f"  lon {_LON_EDGES[0]:.1f}–{_LON_EDGES[-1]:.1f}°E"
        f"  lat {_LAT_EDGES[-1]:.1f}–{_LAT_EDGES[0]:.1f}°N"
    )

    for snap_date in SNAPSHOT_DATES:
        print(f"\n{'─' * 60}")
        print(f"  Snapshot: {snap_date}  (queried at 12:00 UTC)")
        for variable, unit in VARIABLES:
            grid = results[(snap_date, variable)]
            nans = nan_stats[(snap_date, variable)]
            if nans:
                print(f"  ⚠  {nans}/{grid.size} values are NaN for {variable}")
            fmt = ".1f" if variable == "2m_temperature" else ".2f"
            print()
            print(ascii_map(grid, variable, unit, fmt=fmt))
    print()


def main() -> None:
    """Run the multi-variable grid snapshot analysis.

    Steps
    -----
    1. Build a HYRAS pipeline for both variables over all of 2025.
    2. ``update_data()`` — no-op if files are cached.
    3. For each of 3 snapshot dates × 2 variables: one ``get_data()`` call.
    4. Render and print results.
    """
    print("Building HYRAS pipeline (temperature + precipitation, 2025)…")
    pipeline = build_pipeline()
    dv = Datavia(pipelines=[pipeline])
    dv()

    print("Calling update_data() — no downloads expected…")
    success = pipeline.update_data()
    if not success:
        print("ERROR: update_data() reported failure.", file=sys.stderr)
        sys.exit(1)

    n_total = len(SNAPSHOT_DATES) * len(VARIABLES)
    print(f"Running {n_total} grid queries ({len(GRID_COORDS)} points each)…")

    results: dict[tuple[date, str], np.ndarray] = {}
    nan_stats: dict[tuple[date, str], int] = {}

    for snap_date in SNAPSHOT_DATES:
        for variable, _unit in VARIABLES:
            print(f"  {snap_date}  {variable}…", end=" ", flush=True)
            try:
                grid = query_snapshot(pipeline, snap_date, variable)
                results[(snap_date, variable)] = grid
                nan_stats[(snap_date, variable)] = int(np.sum(np.isnan(grid)))
                print("OK")
            except Exception as exc:  # noqa: BLE001
                logger.error("Query failed for %s/%s: %s", snap_date, variable, exc)
                results[(snap_date, variable)] = np.full((_N_LATS, _N_LONS), np.nan)
                nan_stats[(snap_date, variable)] = _N_LATS * _N_LONS
                print(f"FAILED — {exc}")

    print_report(results, nan_stats)


if __name__ == "__main__":
    main()
