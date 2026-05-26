#!/usr/bin/env python3
"""Benchmark ERA5 CDS queue behaviour across chunk sizes.

Downloads 1 calendar year of ERA5-Land data for Germany using five different
chunking strategies and records wall time, number of CDS jobs, and per-job
elapsed time for each.  The results let you compare whether smaller or larger
CDS jobs are faster for the current queue.

CDS per-request size limit
--------------------------
Copernicus CDS enforces a per-request size limit.  A single request for 5
variables over a full quarter at 0.1° resolution over Germany already exceeds
it (``403 Forbidden — cost limits exceeded``).  The ``*_1var`` strategies work
around this by submitting **one variable at a time**, which keeps each
individual request small enough regardless of the time-chunk length.

Strategies
----------
- ``monthly``              — all vars together, 1 job/month           (12 jobs/year)
- ``quarterly``            — all vars together, 1 job/quarter         (4 jobs/year)  ⚠ may 403
- ``yearly``               — all vars together, 1 job/year            (1 job/year)   ⚠ may 403
- ``quarterly_1var``       — 1 variable/job, quarterly chunks         (4 × N jobs)
- ``yearly_1var``          — 1 variable/job, 1 full year per job      (N jobs)
- ``spatial_4tile_yearly`` — Germany split 2×2 tiles, 1 job/tile/year (4 jobs)

where N = number of variables (default 5).

The ``spatial_4tile_yearly`` strategy explores a third axis: instead of
splitting by time or by variable, it splits the bounding box into four
spatial tiles (NW, NE, SW, SE of Germany).  Each tile is ~¼ the area, so
the per-request data volume is ~¼ of a full-Germany yearly request.  This
tests whether the CDS size limit can be cleared by spatial sub-selection
alone, without sacrificing temporal resolution.

Estimated data volume
---------------------
5 variables × 1 year × Germany 0.1° grid ≈ 1.26 GB per strategy run.
Six strategies × six years = ~7.5 GB total on disk.

The script downloads each strategy to a **separate year** (configurable) so
you end up with useful data rather than triplicates of the same period:

- ``monthly``              → *year_monthly*              (default 2022) — already tested
- ``quarterly``            → *year_quarterly*            (default 2023) — likely 403
- ``yearly``               → *year_yearly*               (default 2024) — likely 403
- ``quarterly_1var``       → *year_quarterly_1var*       (default 2023) — retries 2023 safely
- ``yearly_1var``          → *year_yearly_1var*          (default 2024) — retries 2024 safely
- ``spatial_4tile_yearly`` → *year_spatial_4tile_yearly* (default 2025) — spatial tiling test

Requirements
------------
- A valid ``~/.cdsapirc`` credentials file.
  See https://cds.climate.copernicus.eu/how-to-api
- The ``datavia-weather[era5]`` package installed in the active environment.

Usage
-----
Run all three strategies back-to-back::

    pixi run python scripts/benchmark_era5_chunking.py

Because each strategy takes 20–60 minutes depending on the CDS queue, it is
strongly recommended to run the script inside a ``tmux`` session so that
closing the terminal or putting the laptop to sleep does not interrupt the
download::

    tmux new -s era5bench
    pixi run python scripts/benchmark_era5_chunking.py
    # Ctrl+B, D  — detach (script keeps running)
    tmux attach -t era5bench  # reattach later to check progress

Pressing Ctrl+C inside the session cancels the currently active CDS job on
the Copernicus server (``job.delete()`` is called automatically) before
exiting cleanly.

Run a single strategy::

    pixi run python scripts/benchmark_era5_chunking.py --strategy quarterly_1var

Override the year assigned to a strategy::

    pixi run python scripts/benchmark_era5_chunking.py \\
        --year-quarterly-1var 2021 --year-yearly-1var 2020

Use fewer variables for a faster / lighter test (~0.75 GB per run)::

    pixi run python scripts/benchmark_era5_chunking.py --variables 2m_temperature,total_precipitation,surface_solar_radiation_downwards
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

#: Five ERA5-Land variables that give ~1.26 GB per year over Germany.
DEFAULT_VARIABLES: list[str] = [
    "2m_temperature",
    "total_precipitation",
    "surface_solar_radiation_downwards",
    "2m_dewpoint_temperature",
    "10m_u_component_of_wind",
]

#: Default years assigned to each strategy (one year per strategy so the
#: six runs produce distinct, non-overlapping datasets).
DEFAULT_YEAR_MONTHLY: int = 2022
DEFAULT_YEAR_QUARTERLY: int = 2023
DEFAULT_YEAR_YEARLY: int = 2024
DEFAULT_YEAR_QUARTERLY_1VAR: int = 2023  # Retry 2023 with per-variable chunking.
DEFAULT_YEAR_YEARLY_1VAR: int = 2024  # Retry 2024 with per-variable chunking.
DEFAULT_YEAR_SPATIAL_4TILE_YEARLY: int = 2025  # Spatial-tiling test on 2025.

#: Germany bounding box [N, W, S, E] — matches ERA5Downloader default.
#: Used to derive the 2×2 spatial tile sub-boxes.
_BENCHMARK_BBOX: list[float] = [55.1, 5.9, 47.3, 15.0]

#: Strategies to run (in order) when --strategy is not specified.
ALL_STRATEGIES: list[str] = [
    "monthly",
    "quarterly",
    "yearly",
    "quarterly_1var",
    "yearly_1var",
    "spatial_4tile_yearly",
]

#: Maps strategy name → config dict consumed by ``_run_strategy``.
#:
#: Keys:
#:   chunk_by      – temporal chunking granularity (see ERA5Downloader).
#:   per_variable  – if True, one ERA5Downloader call per variable.
#:   spatial_tiles – if 4, splits _BENCHMARK_BBOX into a 2×2 grid of tiles.
_STRATEGY_CONFIG: dict[str, dict[str, object]] = {
    "monthly": {"chunk_by": "monthly", "per_variable": False, "spatial_tiles": 1},
    "quarterly": {"chunk_by": "quarterly", "per_variable": False, "spatial_tiles": 1},
    "yearly": {"chunk_by": "yearly", "per_variable": False, "spatial_tiles": 1},
    "quarterly_1var": {
        "chunk_by": "quarterly",
        "per_variable": True,
        "spatial_tiles": 1,
    },
    "yearly_1var": {"chunk_by": "yearly", "per_variable": True, "spatial_tiles": 1},
    "spatial_4tile_yearly": {
        "chunk_by": "yearly",
        "per_variable": False,
        "spatial_tiles": 4,
    },
}


# ---------------------------------------------------------------------------
# Spatial helpers
# ---------------------------------------------------------------------------


def _split_bbox_2x2(bbox: list[float]) -> list[list[float]]:
    """Split a ``[N, W, S, E]`` bounding box into a 2×2 grid of four tiles.

    The midpoint is snapped to the nearest 0.1° to stay on the ERA5-Land grid.
    Tiles are returned in reading order: NW, NE, SW, SE.

    Parameters
    ----------
    bbox : list[float]
        Source bounding box ``[north, west, south, east]`` in decimal degrees.

    Returns
    -------
    list[list[float]]
        Four sub-boxes, each ``[north, west, south, east]``.
    """
    north, west, south, east = bbox
    mid_lat: float = round((north + south) / 2, 1)
    mid_lon: float = round((west + east) / 2, 1)
    return [
        [north, west, mid_lat, mid_lon],  # NW
        [north, mid_lon, mid_lat, east],  # NE
        [mid_lat, west, south, mid_lon],  # SW
        [mid_lat, mid_lon, south, east],  # SE
    ]


def _download_tile_with_fallback(
    bbox: list[float],
    variables: list[str],
    year: int,
    chunk_by: str,
    depth: int = 0,
    max_depth: int = 3,
) -> tuple[list[str], int]:
    """Download a spatial tile, recursively subdividing if the CDS request is too large.

    If the CDS API returns a 403 "cost limits exceeded" / "too large" error,
    the tile is split into four 2×2 sub-tiles and each is retried independently.
    This recurses up to *max_depth* times before giving up.

    Parameters
    ----------
    bbox : list[float]
        Bounding box ``[north, west, south, east]`` in degrees.
    variables : list[str]
        ERA5 variable names to request.
    year : int
        Calendar year to download.
    chunk_by : str
        Temporal chunking granularity passed to ``ERA5Downloader``.
    depth : int, optional
        Current recursion depth.  Starts at 0 for the initial call.
    max_depth : int, optional
        Maximum number of times a tile may be subdivided before raising.
        Defaults to 3 (giving tiles as small as 1/64 of the original area).

    Returns
    -------
    tuple[list[str], int]
        ``(paths, n_jobs)`` — absolute paths of downloaded NetCDF files and
        the total number of CDS API calls made.

    Raises
    ------
    RuntimeError
        If the tile is still too large after *max_depth* subdivisions, or if
        the failure is not a size-limit error.
    """
    from datavia.weather.era5_downloader import ERA5Downloader

    if depth > max_depth:
        raise RuntimeError(
            f"Spatial tile {bbox} is still too large after {max_depth} "
            "subdivisions.  Try reducing the number of variables or using "
            "a finer chunk_by strategy."
        )

    dl = ERA5Downloader(
        variables=variables,
        date_start=f"{year}-01-01",
        date_end=f"{year}-12-31",
        buffer_days=0,
        chunk_by=chunk_by,
        bbox=bbox,
    )
    try:
        raw_paths = dl.download()
        paths = [p.strip() for p in raw_paths.splitlines() if p.strip()]
        n_jobs = len(dl._get_chunks(f"{year}-01-01"))
        return paths, n_jobs
    except RuntimeError as exc:
        exc_msg = str(exc).lower()
        if "too large" in exc_msg or "cost limits" in exc_msg:
            sub_tiles = _split_bbox_2x2(bbox)
            logger.warning(
                "Tile %s too large at depth %d — splitting into %d sub-tiles and retrying.",
                bbox,
                depth,
                len(sub_tiles),
            )
            all_paths: list[str] = []
            total_jobs: int = 0
            for sub_tile in sub_tiles:
                sub_paths, sub_jobs = _download_tile_with_fallback(
                    sub_tile, variables, year, chunk_by, depth + 1, max_depth
                )
                all_paths.extend(sub_paths)
                total_jobs += sub_jobs
            return all_paths, total_jobs
        raise


@dataclass
class StrategyResult:
    """Timing and job-count results for a single chunking strategy.

    Attributes
    ----------
    strategy : str
        Chunking strategy name (``"monthly"``, ``"quarterly"``, ``"yearly"``).
    year : int
        Calendar year downloaded.
    n_jobs : int
        Total number of CDS API calls submitted.
    total_wall_s : float
        Wall-clock seconds from first job submission to last download complete.
    job_times_s : list[float]
        Per-job wall-clock seconds.
    paths : list[str]
        Absolute paths of the downloaded NetCDF files.
    """

    strategy: str
    year: int
    n_jobs: int = 0
    total_wall_s: float = 0.0
    job_times_s: list[float] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)

    @property
    def avg_job_s(self) -> float:
        """Average per-job wall time in seconds."""
        if not self.job_times_s:
            return 0.0
        return sum(self.job_times_s) / len(self.job_times_s)

    @property
    def max_job_s(self) -> float:
        """Maximum single-job wall time in seconds."""
        return max(self.job_times_s, default=0.0)


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


def _run_strategy(
    strategy: str,
    year: int,
    variables: list[str],
) -> StrategyResult:
    """Execute one chunking strategy for a full calendar year and return timing.

    Reads ``_STRATEGY_CONFIG[strategy]`` to determine ``chunk_by``,
    ``per_variable``, and ``spatial_tiles``.  Downloaded files are real NetCDF
    files written to the OS temp directory.

    Parameters
    ----------
    strategy : str
        One of the keys in ``_STRATEGY_CONFIG``.
    year : int
        Calendar year to download (full year Jan 1 – Dec 31).
    variables : list[str]
        ERA5 variable names to request.

    Returns
    -------
    StrategyResult
        Populated with job count, wall times, and output file paths.
    """
    from datavia.weather.era5_downloader import ERA5Downloader

    cfg = _STRATEGY_CONFIG[strategy]
    chunk_by: str = str(cfg["chunk_by"])
    per_variable: bool = bool(cfg["per_variable"])
    spatial_tiles: int = int(cfg.get("spatial_tiles", 1))  # type: ignore[call-overload]

    result = StrategyResult(strategy=strategy, year=year)

    # Build spatial bbox groups: 4 tiles (2×2 split) or a single default bbox.
    bbox_groups: list[list[float] | None] = (
        _split_bbox_2x2(_BENCHMARK_BBOX) if spatial_tiles == 4 else [None]
    )

    # Build variable groups: one per variable, or all together.
    variable_groups: list[list[str]] = (
        [[v] for v in variables] if per_variable else [variables]
    )

    # Count expected total jobs upfront for logging.
    sample_dl = ERA5Downloader(
        variables=variable_groups[0],
        date_start=f"{year}-01-01",
        date_end=f"{year}-12-31",
        buffer_days=0,
        chunk_by=chunk_by,
    )
    chunks_per_group = sample_dl._get_chunks(f"{year}-01-01")
    total_groups = len(bbox_groups) * len(variable_groups)
    result.n_jobs = len(chunks_per_group) * total_groups

    logger.info(
        "=== Strategy: %s | Year: %d | Jobs: %d (%d bbox(es) × %d var_group(s) × %d chunk(s)) ===",
        strategy,
        year,
        result.n_jobs,
        len(bbox_groups),
        len(variable_groups),
        len(chunks_per_group),
    )

    wall_start = time.monotonic()

    for bbox in bbox_groups:
        for var_group in variable_groups:
            if bbox is not None:
                # Spatial tile — use recursive fallback to handle size-limit errors.
                tile_paths, tile_jobs = _download_tile_with_fallback(
                    bbox=bbox,
                    variables=var_group,
                    year=year,
                    chunk_by=chunk_by,
                )
                result.paths.extend(tile_paths)
                # Update job count in case subdivision happened.
                result.n_jobs += tile_jobs - len(chunks_per_group)
                if tile_jobs > 0:
                    approx_per_job = (time.monotonic() - wall_start) / tile_jobs
                    result.job_times_s.extend([approx_per_job] * tile_jobs)
            else:
                dl_kwargs: dict[str, object] = dict(
                    variables=var_group,
                    date_start=f"{year}-01-01",
                    date_end=f"{year}-12-31",
                    buffer_days=0,
                    chunk_by=chunk_by,
                )
                dl = ERA5Downloader(**dl_kwargs)  # type: ignore[arg-type]
                group_wall_start = time.monotonic()
                raw_paths = dl.download()
                group_elapsed = time.monotonic() - group_wall_start

                result.paths.extend(
                    p.strip() for p in raw_paths.splitlines() if p.strip()
                )
                if chunks_per_group:
                    per_job = group_elapsed / len(chunks_per_group)
                    result.job_times_s.extend([per_job] * len(chunks_per_group))

    result.total_wall_s = time.monotonic() - wall_start

    logger.info(
        "=== Strategy %s complete: %.1f s total, ~%.1f s/job ===",
        strategy,
        result.total_wall_s,
        result.avg_job_s,
    )
    return result


# ---------------------------------------------------------------------------
# Summary reporting
# ---------------------------------------------------------------------------


def _print_summary(results: list[StrategyResult]) -> None:
    """Print a formatted comparison table to stdout.

    Parameters
    ----------
    results : list[StrategyResult]
        One result object per strategy that was run.
    """
    width = 90
    print()
    print("=" * width)
    print("ERA5 chunking benchmark — summary".center(width))
    print("=" * width)
    header = (
        f"{'Strategy':<18}  {'Year':>4}  {'Jobs':>5}  "
        f"{'Total (s)':>10}  {'Avg/job (s)':>12}  {'Max/job (s)':>12}"
    )
    print(header)
    print("-" * width)
    for r in results:
        print(
            f"{r.strategy:<18}  {r.year:>4}  {r.n_jobs:>5}  "
            f"{r.total_wall_s:>10.1f}  {r.avg_job_s:>12.1f}  {r.max_job_s:>12.1f}"
        )
    print("=" * width)
    print()
    print("Notes:")
    print("  • Timings include CDS queue wait + transfer.  Queue wait dominates.")
    print("  • Each strategy downloaded a different year; absolute times are")
    print("    affected by CDS load at that moment.  Run again for confirmation.")
    print("  • 'quarterly'/'yearly' (all vars together) may fail with 403 if the")
    print("    request is too large.  Use 'quarterly_1var'/'yearly_1var' instead.")
    print("  • '_1var' strategies submit one variable per job, trading more jobs")
    print("    for smaller request sizes that never hit the CDS size limit.")
    print("  • 'spatial_4tile_yearly' splits Germany into 4 tiles (2×2) and submits")
    print("    one yearly job per tile.  Tests spatial sub-selection as an")
    print("    alternative to temporal or variable splitting.")
    print()
    if results:
        fastest = min(results, key=lambda r: r.total_wall_s)
        print(
            f"  → Fastest strategy for this run: {fastest.strategy!r} ({fastest.total_wall_s:.1f} s)"
        )
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Parameters
    ----------
    argv : list[str], optional
        Argument list.  Defaults to ``sys.argv[1:]``.

    Returns
    -------
    argparse.Namespace
        Parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--strategy",
        choices=ALL_STRATEGIES,
        default=None,
        help="Run only this strategy.  Defaults to all six.",
    )
    parser.add_argument(
        "--year-monthly",
        type=int,
        default=DEFAULT_YEAR_MONTHLY,
        metavar="YEAR",
        help=f"Year for the 'monthly' strategy (default: {DEFAULT_YEAR_MONTHLY}).",
    )
    parser.add_argument(
        "--year-quarterly",
        type=int,
        default=DEFAULT_YEAR_QUARTERLY,
        metavar="YEAR",
        help=f"Year for the 'quarterly' strategy (default: {DEFAULT_YEAR_QUARTERLY}).",
    )
    parser.add_argument(
        "--year-yearly",
        type=int,
        default=DEFAULT_YEAR_YEARLY,
        metavar="YEAR",
        help=f"Year for the 'yearly' strategy (default: {DEFAULT_YEAR_YEARLY}).",
    )
    parser.add_argument(
        "--year-quarterly-1var",
        type=int,
        default=DEFAULT_YEAR_QUARTERLY_1VAR,
        metavar="YEAR",
        help=(
            f"Year for the 'quarterly_1var' strategy "
            f"(default: {DEFAULT_YEAR_QUARTERLY_1VAR})."
        ),
    )
    parser.add_argument(
        "--year-yearly-1var",
        type=int,
        default=DEFAULT_YEAR_YEARLY_1VAR,
        metavar="YEAR",
        help=(
            f"Year for the 'yearly_1var' strategy "
            f"(default: {DEFAULT_YEAR_YEARLY_1VAR})."
        ),
    )
    parser.add_argument(
        "--year-spatial-4tile-yearly",
        type=int,
        default=DEFAULT_YEAR_SPATIAL_4TILE_YEARLY,
        metavar="YEAR",
        help=(
            f"Year for the 'spatial_4tile_yearly' strategy "
            f"(default: {DEFAULT_YEAR_SPATIAL_4TILE_YEARLY})."
        ),
    )
    parser.add_argument(
        "--variables",
        type=lambda s: [v.strip() for v in s.split(",")],
        default=DEFAULT_VARIABLES,
        metavar="VAR,VAR,...",
        help=(
            "Comma-separated ERA5 variable names.  "
            f"Defaults to {len(DEFAULT_VARIABLES)} variables (~1.26 GB/year)."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Run the benchmark and print a summary table.

    Parameters
    ----------
    argv : list[str], optional
        Command-line arguments.  Defaults to ``sys.argv[1:]``.
    """
    args = _parse_args(argv)

    strategy_years: dict[str, int] = {
        "monthly": args.year_monthly,
        "quarterly": args.year_quarterly,
        "yearly": args.year_yearly,
        "quarterly_1var": args.year_quarterly_1var,
        "yearly_1var": args.year_yearly_1var,
        "spatial_4tile_yearly": args.year_spatial_4tile_yearly,
    }

    strategies_to_run = [args.strategy] if args.strategy else ALL_STRATEGIES

    print()
    print("ERA5 chunking benchmark")
    print(f"  Variables : {args.variables}")
    print(f"  Strategies: {strategies_to_run}")
    for s in strategies_to_run:
        print(f"    {s:<12} → year {strategy_years[s]}")
    print()
    print("Ensure ~/.cdsapirc is configured before continuing.")
    print()

    results: list[StrategyResult] = []
    for strategy in strategies_to_run:
        year = strategy_years[strategy]
        try:
            result = _run_strategy(
                strategy=strategy,
                year=year,
                variables=args.variables,
            )
            results.append(result)
        except KeyboardInterrupt:
            logger.warning(
                "Interrupted during strategy %r — partial results follow.", strategy
            )
            break
        except Exception as exc:
            logger.error("Strategy %r failed: %s", strategy, exc)
            continue

    if results:
        _print_summary(results)
    else:
        logger.error("No strategies completed successfully.")
        sys.exit(1)


if __name__ == "__main__":
    main()
