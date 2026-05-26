#!/usr/bin/env python3
"""Example: querying the datavia weather pipeline from a user project.

Demonstrates a realistic crop-monitoring workflow that:

1. Configures a :class:`~datavia.weather.WeatherPipeline` backed by HYRAS
   (daily gridded data from DWD OpenData — **no credentials required**).
2. Optionally blends HYRAS with DWD point-station observations.
3. Downloads data for a one-week summer window (incremental — re-running is
   safe; already-present files are skipped).
4. Queries daily 2-m temperature and precipitation at several agricultural
   sites across Germany.
5. Prints a formatted summary table.

Run::

    pixi run python docs/user_guide/examples/example_weather_query.py

To also include DWD station blending, set the environment variable::

    USE_DWD_STATIONS=1 pixi run python docs/user_guide/examples/example_weather_query.py

ERA5 support is documented at the bottom of ``main()`` but commented out
because it requires a free Copernicus CDS account (``~/.cdsapirc``).
See https://cds.climate.copernicus.eu/how-to-api for setup instructions.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import date, timedelta

import numpy as np

from datavia import Datavia
from datavia.weather import WeatherPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Site definitions
# ---------------------------------------------------------------------------

#: Agricultural monitoring sites in Germany (name, latitude°N, longitude°E).
MONITORING_SITES: list[dict[str, object]] = [
    {"name": "Münster (NRW)", "lat": 51.96, "lon": 7.63},
    {"name": "Magdeburg (Saxony-A)", "lat": 52.12, "lon": 11.63},
    {"name": "Würzburg (Bavaria)", "lat": 49.80, "lon": 9.93},
    {"name": "Rostock (coast)", "lat": 54.08, "lon": 12.13},
    {"name": "Freiburg (Black Frst)", "lat": 47.99, "lon": 7.85},
]

#: DWD station list used when ``USE_DWD_STATIONS=1``.
DWD_REFERENCE_STATIONS: list[dict[str, object]] = [
    {"id": "Muenster", "latitude": 51.96, "longitude": 7.63},
    {"id": "Magdeburg", "latitude": 52.12, "longitude": 11.63},
    {"id": "Wuerzburg", "latitude": 49.80, "longitude": 9.93},
    {"id": "Rostock", "latitude": 54.09, "longitude": 12.11},
    {"id": "Freiburg", "latitude": 48.00, "longitude": 7.85},
]

# ---------------------------------------------------------------------------
# Pipeline factory
# ---------------------------------------------------------------------------


def build_hyras_pipeline(
    date_start: str,
    date_end: str,
    include_dwd_stations: bool = False,
) -> WeatherPipeline:
    """Create a HYRAS-backed :class:`WeatherPipeline` for the given period.

    Parameters
    ----------
    date_start : str
        ISO-8601 start date, e.g. ``"2023-07-01"``.
    date_end : str
        ISO-8601 end date, e.g. ``"2023-07-07"``.
    include_dwd_stations : bool, optional
        When ``True``, adds DWD point-station observations (via the
        Open-Meteo archive API) alongside the HYRAS grid so that
        :meth:`~datavia.weather.getter_weather.GetterWeather.get_weather_data`
        can blend both sources. Defaults to ``False``.

    Returns
    -------
    WeatherPipeline
        An uninitialised pipeline ready to be passed to a
        :class:`~datavia.core.datavia.Datavia` controller or called directly.
    """
    config: dict[str, object] = {
        "source": "HYRAS",
        "variables": [
            "2m_temperature",
            "total_precipitation",
        ],
        "date_start": date_start,
        "date_end": date_end,
    }

    if include_dwd_stations:
        config["dwd_stations"] = DWD_REFERENCE_STATIONS

    return WeatherPipeline(config=config)


# ---------------------------------------------------------------------------
# Data access helpers
# ---------------------------------------------------------------------------


def download_weather_data(pipeline: WeatherPipeline) -> bool:
    """Trigger the pipeline's download/registration step.

    Calling this multiple times is safe: already-present files on disk and
    already-registered database rows are silently skipped.

    Parameters
    ----------
    pipeline : WeatherPipeline
        A fully initialised pipeline (i.e. ``pipeline()`` has been called or
        it was passed through :class:`~datavia.core.datavia.Datavia`).

    Returns
    -------
    bool
        ``True`` when all files were downloaded and registered successfully.
    """
    logger.info("Downloading weather data (incremental — existing files skipped)…")
    success = pipeline.update_data()
    if success:
        logger.info("Weather data download complete.")
    else:
        logger.warning("One or more weather files could not be downloaded.")
    return success


def query_daily_temperature(
    pipeline: WeatherPipeline,
    lat: float,
    lon: float,
    query_dates: list[date],
) -> list[float]:
    """Return daily midday 2-m temperature (°C) for a single site.

    Queries the pipeline at 12:00 UTC for each requested date and collects
    the results.  ``float("nan")`` is returned for dates where no data are
    available.

    Parameters
    ----------
    pipeline : WeatherPipeline
        Initialised pipeline with ``"2m_temperature"`` in its variable list.
    lat : float
        Site latitude in degrees North.
    lon : float
        Site longitude in degrees East.
    query_dates : list[date]
        Dates to query.

    Returns
    -------
    list[float]
        Daily midday temperatures in °C, one entry per element of
        *query_dates*.
    """
    temperatures: list[float] = []
    for query_date in query_dates:
        datetime_utc = f"{query_date.isoformat()}T12:00:00"
        value = pipeline.get_weather_data(
            lat=lat,
            lon=lon,
            variable="2m_temperature",
            datetime_utc=datetime_utc,
        )
        temperatures.append(value)
    return temperatures


def query_multisite_snapshot(
    pipeline: WeatherPipeline,
    sites: list[dict[str, object]],
    snapshot_date: date,
    variable: str,
) -> np.ndarray:
    """Return a variable snapshot for all sites at midday on *snapshot_date*.

    Uses :meth:`~datavia.weather.pipeline.WeatherPipeline.get_data` to
    perform a single vectorised interpolation call for all coordinates.

    Parameters
    ----------
    pipeline : WeatherPipeline
        Initialised pipeline that covers *snapshot_date*.
    sites : list[dict]
        List of site dicts, each containing ``"lat"`` and ``"lon"`` keys.
    snapshot_date : date
        Date for which to retrieve values (queried at 12:00 UTC).
    variable : str
        Variable name, e.g. ``"2m_temperature"`` or ``"total_precipitation"``.

    Returns
    -------
    np.ndarray
        Shape ``(N,)`` array of values, one per site.  ``NaN`` where no data
        are available.
    """
    coords = np.array([[s["lon"], s["lat"]] for s in sites])
    datetime_utc = f"{snapshot_date.isoformat()}T12:00:00"
    return pipeline.get_data(
        coords=coords,
        crs_coords="EPSG:4326",
        variable=variable,
        datetime_utc=datetime_utc,
    )


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def print_daily_series(
    site_name: str,
    query_dates: list[date],
    temperatures: list[float],
) -> None:
    """Print a compact daily temperature table for one site.

    Parameters
    ----------
    site_name : str
        Human-readable site label printed as the table heading.
    query_dates : list[date]
        Ordered list of dates.
    temperatures : list[float]
        Temperature values in °C, aligned index-by-index with *query_dates*.
    """
    print(f"\n  {site_name}")
    print("  " + "-" * 36)
    for query_date, temp in zip(query_dates, temperatures):
        if math.isnan(temp):
            value_str = "   N/A  "
        else:
            value_str = f"{temp:+6.1f} °C"
        print(f"  {query_date}  {value_str}")


def print_multisite_snapshot(
    sites: list[dict[str, object]],
    values: np.ndarray,
    snapshot_date: date,
    variable: str,
    unit: str,
) -> None:
    """Print a one-day snapshot for all sites side by side.

    Parameters
    ----------
    sites : list[dict]
        Site dicts with ``"name"`` keys.
    values : np.ndarray
        Values for each site, shape ``(N,)``.
    snapshot_date : date
        The date the snapshot represents.
    variable : str
        Variable label for the table header.
    unit : str
        Physical unit string shown next to values.
    """
    print(f"\n  {variable}  —  {snapshot_date} 12:00 UTC")
    print("  " + "-" * 50)
    for site, value in zip(sites, values):
        if math.isnan(float(value)):
            value_str = "N/A"
        else:
            value_str = f"{float(value):.2f} {unit}"
        print(f"  {str(site['name']):<28}  {value_str}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Download HYRAS weather data and query it at agricultural monitoring sites.

    Workflow
    --------
    1. Build a HYRAS pipeline for a 7-day window ending 10 days ago (so the
       HYRAS annual file is guaranteed to exist on DWD OpenData).
    2. Initialise the SQLite database and register the pipeline through the
       :class:`~datavia.core.datavia.Datavia` controller.
    3. Download and register the HYRAS NetCDF files (incremental — re-running
       picks up from where the last run left off).
    4. Query daily midday 2-m temperature for the first site (Münster).
    5. Query a one-day precipitation snapshot for all sites.

    To enable DWD station blending::

        USE_DWD_STATIONS=1 pixi run python docs/user_guide/examples/example_weather_query.py

    ERA5 example (commented out — requires ``~/.cdsapirc``)::

        era5_pipeline = WeatherPipeline(config={
            "source":     "ERA5_land",
            "variables":  ["2m_temperature", "total_precipitation"],
            "date_start": date_start,
            "date_end":   date_end,
            "era5_bbox":  [55.1, 5.9, 47.3, 15.0],  # N, W, S, E — all Germany
        })
        dv = Datavia(pipelines=[era5_pipeline])
        dv()
        era5_pipeline.update_data()
    """
    # -----------------------------------------------------------------------
    # 1. Determine query window
    #    Use a completed year window well within HYRAS archive coverage.
    #    HYRAS publishes annual files; the previous full year is always safe.
    # -----------------------------------------------------------------------
    today = date.today()
    # Pick a 7-day window from one complete calendar year ago so the annual
    # HYRAS NetCDF is guaranteed to be published on DWD OpenData.
    year_offset = timedelta(days=365)
    date_end = (today - year_offset).replace(month=7, day=7)
    date_start = date_end - timedelta(days=6)

    include_dwd = os.getenv("USE_DWD_STATIONS", "0") == "1"

    logger.info(
        "Query window: %s → %s  (DWD stations: %s)",
        date_start,
        date_end,
        include_dwd,
    )

    # -----------------------------------------------------------------------
    # 2. Configure the pipeline and initialise the database
    # -----------------------------------------------------------------------
    hyras_pipeline = build_hyras_pipeline(
        date_start=str(date_start),
        date_end=str(date_end),
        include_dwd_stations=include_dwd,
    )

    # The Datavia controller initialises the SQLite schema and lazily wires
    # each pipeline's downloader / saver / getter on first use.
    dv = Datavia(pipelines=[hyras_pipeline])
    dv()

    # -----------------------------------------------------------------------
    # 3. Download HYRAS NetCDF (and optionally DWD Parquet)
    # -----------------------------------------------------------------------
    success = download_weather_data(hyras_pipeline)
    if not success:
        logger.error("Download step reported failures — results may be incomplete.")

    # -----------------------------------------------------------------------
    # 4. Daily temperature series for one site
    # -----------------------------------------------------------------------
    query_dates = [date_start + timedelta(days=i) for i in range(7)]
    muenster = MONITORING_SITES[0]

    print("\n=== Daily 2-m temperature at Münster (NRW) ===")
    temperatures = query_daily_temperature(
        pipeline=hyras_pipeline,
        lat=float(muenster["lat"]),  # type: ignore[arg-type]
        lon=float(muenster["lon"]),  # type: ignore[arg-type]
        query_dates=query_dates,
    )
    print_daily_series(
        site_name=str(muenster["name"]),
        query_dates=query_dates,
        temperatures=temperatures,
    )

    # -----------------------------------------------------------------------
    # 5. Multi-site precipitation snapshot for the last day of the window
    # -----------------------------------------------------------------------
    print("\n=== Total-precipitation snapshot — all monitoring sites ===")
    precip_values = query_multisite_snapshot(
        pipeline=hyras_pipeline,
        sites=MONITORING_SITES,
        snapshot_date=date_end,
        variable="total_precipitation",
    )
    print_multisite_snapshot(
        sites=MONITORING_SITES,
        values=precip_values,
        snapshot_date=date_end,
        variable="total_precipitation",
        unit="mm",
    )

    # -----------------------------------------------------------------------
    # 6. Summary statistics
    # -----------------------------------------------------------------------
    valid_temps = [t for t in temperatures if not math.isnan(t)]
    if valid_temps:
        print(
            f"\nMünster week summary: "
            f"min={min(valid_temps):.1f} °C  "
            f"mean={sum(valid_temps) / len(valid_temps):.1f} °C  "
            f"max={max(valid_temps):.1f} °C"
        )

    valid_precip = [float(v) for v in precip_values if not math.isnan(float(v))]
    if valid_precip:
        print(
            f"Precipitation range across sites: "
            f"{min(valid_precip):.1f} – {max(valid_precip):.1f} mm"
        )

    print()


if __name__ == "__main__":
    main()
