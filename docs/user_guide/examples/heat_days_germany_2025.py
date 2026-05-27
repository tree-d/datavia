#!/usr/bin/env python3
"""Summer heat-day ranking across 15 German cities — HYRAS 2025.

Retrieves daily mean 2-m air temperatures from the HYRAS grid for the
meteorological summer (June – August 2025) at 15 cities spanning all German
federal states.  From those 92 × 15 values the script computes:

- **Warm days** (Tₘₑₐₙ > 20 °C): count per city
- **Hot days**  (Tₘₑₐₙ > 25 °C): count per city
- **Peak temperature**: maximum daily mean and the date it occurred
- **Summer mean**: arithmetic mean of all 92 daily values

Output is a formatted table ranked from hottest to coolest city.

This kind of analysis is the first step in Germany's growing heat-stress
monitoring programmes — urban planners and public-health researchers use the
number of hot days as a proxy for heat-related illness risk.

Run (no downloads — data must already be present for 2025)::

    pixi run python docs/user_guide/examples/heat_days_germany_2025.py

The script relies only on data already downloaded by
``example_weather_query.py`` and will **not** trigger any additional
network requests.
"""

import logging
import math
import sys
from datetime import date, timedelta

import numpy as np

from datavia import Datavia
from datavia.weather import WeatherPipeline

logging.basicConfig(
    level=logging.WARNING,  # suppress pipeline INFO noise; we print our own
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# City definitions  (15 cities, one per federal state where possible)
# ---------------------------------------------------------------------------

#: 15 German cities covering all major climate regions.
#: Columns: name, latitude °N, longitude °E.
CITIES: list[dict[str, object]] = [
    # North German Plain — maritime influence
    {"name": "Flensburg", "lat": 54.79, "lon": 9.43},
    {"name": "Hamburg", "lat": 53.55, "lon": 9.99},
    {"name": "Rostock", "lat": 54.08, "lon": 12.13},
    {"name": "Bremen", "lat": 53.08, "lon": 8.80},
    {"name": "Hannover", "lat": 52.37, "lon": 9.73},
    # Central Germany — transitional
    {"name": "Berlin", "lat": 52.52, "lon": 13.40},
    {"name": "Leipzig", "lat": 51.34, "lon": 12.37},
    {"name": "Dresden", "lat": 51.05, "lon": 13.74},
    {"name": "Erfurt", "lat": 50.98, "lon": 11.03},
    # West Germany — Atlantic influence
    {"name": "Cologne", "lat": 50.94, "lon": 6.96},
    {"name": "Frankfurt/M.", "lat": 50.11, "lon": 8.68},
    {"name": "Saarbrücken", "lat": 49.23, "lon": 7.00},
    # South Germany — continental / Alpine fringe
    {"name": "Stuttgart", "lat": 48.78, "lon": 9.18},
    {"name": "Munich", "lat": 48.14, "lon": 11.58},
    {"name": "Freiburg", "lat": 47.99, "lon": 7.85},
]

# Meteorological summer: June 1 – August 31
_SUMMER_START = date(2025, 6, 1)
_SUMMER_END = date(2025, 8, 31)
_N_DAYS = (_SUMMER_END - _SUMMER_START).days + 1


def build_pipeline() -> WeatherPipeline:
    """Create a HYRAS pipeline covering meteorological summer 2025.

    Returns
    -------
    WeatherPipeline
        Pipeline configured for ``2m_temperature`` over summer 2025.
    """
    return WeatherPipeline(
        config={
            "source": "HYRAS",
            "variables": ["2m_temperature"],
            "date_start": str(_SUMMER_START),
            "date_end": str(_SUMMER_END),
        }
    )


def collect_temperatures(pipeline: WeatherPipeline) -> np.ndarray:
    """Query daily mean temperature for all cities across the full summer.

    Issues a **single** :meth:`~datavia.weather.pipeline.WeatherPipeline.get_data`
    call with all 15 city coordinates *and* all ``_N_DAYS`` timestamps
    vectorised, opening the NetCDF file only once.

    Parameters
    ----------
    pipeline : WeatherPipeline
        Initialised pipeline covering summer 2025.

    Returns
    -------
    np.ndarray
        Temperature array of shape ``(N_days, N_cities)`` in °C.
        ``NaN`` is used for any day/city pair where data are unavailable.
    """
    coords = np.array([[c["lon"], c["lat"]] for c in CITIES])
    all_timestamps = [
        f"{(_SUMMER_START + timedelta(days=i)).isoformat()}T12:00:00"
        for i in range(_N_DAYS)
    ]

    print(
        f"  Querying all {_N_DAYS} days × {len(CITIES)} cities in one batch call…",
        flush=True,
    )

    try:
        # Returns shape (N_cities, N_days); transpose to (N_days, N_cities).
        batch = pipeline.get_data(
            coords=coords,
            crs_coords="EPSG:4326",
            variable="2m_temperature",
            datetime_utc=all_timestamps,
        )
        temps = np.asarray(batch, dtype=float).T
    except Exception as exc:  # noqa: BLE001
        logger.warning("Batch temperature query failed: %s", exc)
        temps = np.full((_N_DAYS, len(CITIES)), np.nan)

    return temps


def compute_statistics(temps: np.ndarray) -> list[dict]:
    """Derive summer statistics per city from the temperature matrix.

    Parameters
    ----------
    temps : np.ndarray
        Shape ``(N_days, N_cities)`` temperature array in °C.

    Returns
    -------
    list[dict]
        One dict per city with keys ``name``, ``mean``, ``peak``,
        ``peak_date``, ``warm_days`` (T > 20 °C), ``hot_days`` (T > 25 °C).
        Cities with all-NaN data are still included with ``NaN`` statistics.
    """
    results = []
    for city_idx, city in enumerate(CITIES):
        col = temps[:, city_idx]
        valid = col[~np.isnan(col)]

        if valid.size == 0:
            results.append(
                {
                    "name": city["name"],
                    "mean": float("nan"),
                    "peak": float("nan"),
                    "peak_date": None,
                    "warm_days": 0,
                    "hot_days": 0,
                }
            )
            continue

        peak_idx = int(np.argmax(col[~np.isnan(col)]))
        # Map back to original indices for the date
        valid_day_indices = [i for i in range(_N_DAYS) if not math.isnan(col[i])]
        peak_day_offset = valid_day_indices[peak_idx]

        results.append(
            {
                "name": city["name"],
                "mean": float(np.nanmean(col)),
                "peak": float(np.nanmax(col)),
                "peak_date": _SUMMER_START + timedelta(days=peak_day_offset),
                "warm_days": int(np.sum(col > 20.0)),
                "hot_days": int(np.sum(col > 25.0)),
            }
        )

    return sorted(
        results, key=lambda r: (not math.isnan(r["mean"]), r["mean"]), reverse=True
    )


def print_report(stats: list[dict]) -> None:
    """Print a formatted ranking table to stdout.

    Parameters
    ----------
    stats : list[dict]
        List returned by :func:`compute_statistics`, already sorted hottest-first.
    """
    header = (
        f"\n{'Rank':<5} {'City':<16} {'Mean°C':>7} {'Peak°C':>8} "
        f"{'Peak date':<12} {'Warm':>6} {'Hot':>6}"
    )
    rule = "─" * len(header.rstrip())
    print("\n=== German Summer 2025 Heat-Day Ranking (HYRAS) ===")
    print(f"    Period: {_SUMMER_START} → {_SUMMER_END}  ({_N_DAYS} days)")
    print(f"    Warm days = Tₘₑₐₙ > 20 °C  |  Hot days = Tₘₑₐₙ > 25 °C\n")
    print(header)
    print(rule)

    for rank, row in enumerate(stats, start=1):
        mean_s = f"{row['mean']:+6.1f}" if not math.isnan(row["mean"]) else "   N/A"
        peak_s = f"{row['peak']:+6.1f}" if not math.isnan(row["peak"]) else "   N/A"
        date_s = str(row["peak_date"]) if row["peak_date"] else "    —   "
        print(
            f"{rank:<5} {str(row['name']):<16} {mean_s:>7} {peak_s:>8} "
            f"{date_s:<12} {row['warm_days']:>6} {row['hot_days']:>6}"
        )

    valid_stats = [r for r in stats if not math.isnan(r["mean"])]
    if valid_stats:
        overall_mean = sum(r["mean"] for r in valid_stats) / len(valid_stats)
        print(rule)
        print(f"{'Germany avg':>28}  {overall_mean:+6.1f}")
    print()


def main() -> None:
    """Orchestrate the heat-day analysis for summer 2025.

    Steps
    -----
    1. Build and wire a HYRAS pipeline via the Datavia controller.
    2. Call ``update_data()`` — should be a no-op if files are already present.
    3. Query all 92 days × 15 cities.
    4. Compute and print statistics.

    Exits with code 1 if ``update_data()`` reports a failure (network error,
    missing file, etc.) so the script can be called from CI.
    """
    print("Building HYRAS pipeline for summer 2025…")
    pipeline = build_pipeline()
    dv = Datavia(pipelines=[pipeline])
    dv()

    print("Triggering update_data() — expect no downloads if 2025 data is cached…")
    success = pipeline.update_data()
    if not success:
        print("ERROR: update_data() reported failure.", file=sys.stderr)
        sys.exit(1)

    print(f"Collecting temperatures for {_N_DAYS} days × {len(CITIES)} cities…")
    temps = collect_temperatures(pipeline)

    nan_count = int(np.sum(np.isnan(temps)))
    total = temps.size
    print(f"  Done. {total - nan_count}/{total} values retrieved successfully.")
    if nan_count:
        print(f"  ⚠ {nan_count} NaN values — check pipeline logs for details.")

    stats = compute_statistics(temps)
    print_report(stats)


if __name__ == "__main__":
    main()
