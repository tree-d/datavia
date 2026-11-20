"""DWDStationDownloader — daily DWD station observations as a Parquet file.

Supplies the *station supplement* of a gridded weather pipeline: when a
``"HYRAS"`` or ``"ERA5_land"`` pipeline is configured with
``config["dwd_stations"]``, the daily observations of the DWD station network
are saved next to the gridded data, and
:class:`~datavia.weather.getter_weather.GetterWeather` blends an
inverse-distance-weighted station estimate into the gridded value at query
time (daily mode, single timestamps only).

The observations come from the DWD Climate Data Center through
:class:`~datavia.weather.dwd_cdc.CDCClient` (step 1 of the ``DWD_stations``
source; the kriged grids of that source are produced by
:mod:`datavia.weather.dwd_kriging_downloader`).

**Parquet layout** (long format, one row per station and day): columns
``station_id``, ``latitude``, ``longitude``, ``elevation``, ``datetime`` and
one column per variable.  ``datetime`` is naive UTC and marks the window of
the daily value the way :func:`datavia.library.temporal.day_window` expects
it:

- precipitation (the 06-06 UTC day D) is stamped at the window's end,
  D+1 06:00 (an *interval-end* variable);
- the other variables (00-24 UTC values) are stamped at the window's start,
  D 00:00.

So the daily selection in
:func:`~datavia.library.interpolation.interpolate_station_parquet` finds
exactly one record per station and day.
"""

from __future__ import annotations

import logging
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from datavia.core.interfaces import Downloader

from .dwd_cdc import CDC_PRODUCTS, CDCClient

logger = logging.getLogger(__name__)

#: Variables stamped at the end of their (06-06 UTC) window.
_INTERVAL_END_HOURS: dict[str, int] = {"total_precipitation": 6}


def _utc_today() -> date:
    """Return today's date in UTC."""
    return datetime.now(UTC).date()


class DWDStationDownloader(Downloader):
    """Download daily DWD station observations into a Parquet file.

    Parameters
    ----------
    variables : list[str], optional
        Pipeline variable names, keys of
        :data:`~datavia.weather.dwd_cdc.CDC_PRODUCTS`.  Defaults to
        ``["2m_temperature"]``.
    date_start, date_end : date or str, optional
        First and last day (inclusive).  Default: yesterday (UTC), the newest
        published day.
    stations : list[str] or bool, optional
        DWD station ids (five digits, e.g. ``"00044"``) to keep, or ``True``
        / ``None`` for every station.
    data_dir : str or Path, optional
        datavia data directory; the station archives are cached in
        ``<data_dir>/DWD_stations/_cdc``.  Defaults to
        ``get_config().data_directory``.
    client : CDCClient, optional
        Station data client to use instead of the default one.

    Raises
    ------
    ValueError
        If a variable is not supported, or *stations* is a list of dicts (the
        format of the former Open-Meteo downloader).
    """

    #: Pipeline variable names this downloader can fetch.
    SUPPORTED_VARIABLES: frozenset[str] = frozenset(CDC_PRODUCTS)

    @classmethod
    def split_supported(cls, variables: list[str]) -> tuple[list[str], list[str]]:
        """Split *variables* into ``(supported, unsupported)``, keeping order.

        Parameters
        ----------
        variables : list[str]
            Pipeline variable names.

        Returns
        -------
        tuple[list[str], list[str]]
            The supported and the unsupported variables.
        """
        supported = [v for v in variables if v in cls.SUPPORTED_VARIABLES]
        unsupported = [v for v in variables if v not in cls.SUPPORTED_VARIABLES]
        return supported, unsupported

    def __init__(
        self,
        variables: list[str] | None = None,
        date_start: date | str | None = None,
        date_end: date | str | None = None,
        stations: list[str] | bool | None = None,
        data_dir: str | Path | None = None,
        client: CDCClient | None = None,
    ) -> None:
        """Initialise the downloader; see the class docstring for the parameters."""
        self.variables: list[str] = variables or ["2m_temperature"]
        unknown = [v for v in self.variables if v not in self.SUPPORTED_VARIABLES]
        if unknown:
            raise ValueError(
                f"Unsupported DWD station variables {unknown}. "
                f"Supported: {sorted(self.SUPPORTED_VARIABLES)}"
            )
        self.station_ids: list[str] | None = normalise_station_ids(stations)
        yesterday = _utc_today() - timedelta(days=1)
        self.date_start: date = _as_date(date_start) or yesterday
        self.date_end: date = min(_as_date(date_end) or yesterday, yesterday)
        if data_dir is None:
            from datavia.config import get_config

            data_dir = get_config().data_directory
        self.client = client or CDCClient(Path(data_dir) / "DWD_stations" / "_cdc")

    def download(self) -> str:
        """Read the stations' daily values and write them to a Parquet file.

        Returns
        -------
        str
            Absolute path of the written (temporary) ``.parquet`` file.

        Raises
        ------
        RuntimeError
            If the station data cannot be downloaded, or no station has data.
        """
        if self.date_end < self.date_start:
            raise RuntimeError(
                f"No published DWD station day in {self.date_start} .. {self.date_end}"
            )
        keys = ["station_id", "latitude", "longitude", "elevation", "datetime"]
        df = self._variable_frame(self.variables[0])
        for variable in self.variables[1:]:
            df = df.merge(self._variable_frame(variable), on=keys, how="outer")
        if df.empty:
            raise RuntimeError("DWD station download produced no records.")
        df = df.sort_values(["station_id", "datetime"], ignore_index=True)

        with tempfile.NamedTemporaryFile(
            suffix=".parquet", prefix="dwd_stations_", delete=False
        ) as output_file:
            output_path = output_file.name
        df.to_parquet(output_path, engine="pyarrow", compression="snappy", index=False)
        logger.info(
            "DWD station download complete: %d records, %d stations -> %s",
            len(df),
            df["station_id"].nunique(),
            output_path,
        )
        return output_path

    def _variable_frame(self, variable: str) -> pd.DataFrame:
        """Long-format frame of one variable: one row per station and valid day.

        Returns
        -------
        pd.DataFrame
            Columns ``station_id, latitude, longitude, elevation, datetime`` and
            *variable*; ``datetime`` is the stamp described in the module
            docstring.
        """
        data = self.client.station_data(
            variable, self.date_start, self.date_end, station_ids=self.station_ids
        )
        station, day = np.nonzero(np.isfinite(data.values))
        # Stamp each value so that the day-window selection finds exactly it:
        # interval-end variables at the window end, the others at its start.
        stamps = data.dates[day]
        if variable in _INTERVAL_END_HOURS:
            stamps = stamps + pd.Timedelta(days=1, hours=_INTERVAL_END_HOURS[variable])
        return pd.DataFrame(
            {
                "station_id": data.station_id[station],
                "latitude": data.latitude[station],
                "longitude": data.longitude[station],
                "elevation": data.elevation[station],
                "datetime": stamps,
                variable: data.values[station, day],
            }
        )


def normalise_station_ids(stations: Any) -> list[str] | None:
    """Validate the ``dwd_stations`` setting and return sorted station ids.

    Parameters
    ----------
    stations : list[str], bool or None
        ``None`` or ``True``: all stations.  A list of DWD station ids; ids
        are zero-padded to five digits (``44`` -> ``"00044"``).

    Returns
    -------
    list[str] or None
        Sorted five-digit ids, or ``None`` for all stations.

    Raises
    ------
    ValueError
        If *stations* is a list of dicts (the lat/lon format of the former
        Open-Meteo downloader), ``False``, or another type.
    """
    if stations is None or stations is True:
        return None
    if isinstance(stations, (list, tuple)) and all(
        isinstance(s, (str, int)) and not isinstance(s, bool) for s in stations
    ):
        return sorted(str(s).strip().zfill(5) for s in stations) or None
    raise ValueError(
        "dwd_stations must be True (all DWD stations) or a list of DWD station "
        "ids such as ['00044', '01048']; station dicts with latitude/longitude "
        "(the former Open-Meteo format) are no longer supported, because the "
        "data now come from the DWD station network itself."
    )


def _as_date(value: date | str | None) -> date | None:
    """Parse an ISO date string (or pass a date through)."""
    if value is None:
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
