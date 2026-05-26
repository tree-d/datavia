"""
DWDStationDownloader — fetches DWD station observation time series from the
Open-Meteo historical API (https://open-meteo.com/en/docs/dwd-api) and saves
them as a Parquet file.

No API key is required; Open-Meteo is free for non-commercial use.
For high-volume commercial use consider the ``wetterdienst`` library instead.
"""

from __future__ import annotations

import logging
import tempfile
from datetime import date
from typing import Any

import pandas as pd
import requests

from datavia.core.downloader_api import APIDownloader

logger = logging.getLogger(__name__)

#: Open-Meteo historical weather API endpoint.
_OPEN_METEO_URL: str = "https://archive-api.open-meteo.com/v1/archive"

#: Default DWD stations (WMO IDs) covering the German climate regions.
#: A more complete list can be generated at runtime from the Open-Meteo
#: station index, but these representative stations are used as a bootstrap.
_DEFAULT_STATIONS: list[dict[str, Any]] = [
    {"id": "Berlin", "latitude": 52.52, "longitude": 13.41},
    {"id": "Munich", "latitude": 48.14, "longitude": 11.58},
    {"id": "Hamburg", "latitude": 53.55, "longitude": 10.0},
    {"id": "Frankfurt", "latitude": 50.11, "longitude": 8.68},
    {"id": "Cologne", "latitude": 50.94, "longitude": 6.96},
]


class DWDStationDownloader(APIDownloader):
    """Download DWD station observations via the Open-Meteo historical API.

    Fetches hourly records for all configured stations and variables and
    writes a single Parquet file containing columns ``station_id``,
    ``latitude``, ``longitude``, ``datetime``, and one column per variable.
    """

    def __init__(
        self,
        variables: list[str] | None = None,
        date_start: date | str | None = None,
        date_end: date | str | None = None,
        stations: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialise the DWD station downloader.

        Parameters
        ----------
        variables : list[str], optional
            Open-Meteo variable names, e.g.
            ``["temperature_2m", "precipitation"]".``
            Defaults to ``["temperature_2m"]``.
        date_start : date or str, optional
            Start date for the download (inclusive). Defaults to today.
        date_end : date or str, optional
            End date for the download (inclusive). Defaults to today.
        stations : list[dict[str, Any]], optional
            Station dicts, each with keys ``id``, ``latitude``, ``longitude``.
            Defaults to :data:`_DEFAULT_STATIONS`.
        **kwargs : Any
            Additional keyword arguments forwarded to
            :class:`datavia.core.downloader_api.APIDownloader`.
        """
        super().__init__(url=_OPEN_METEO_URL, **kwargs)
        self.variables: list[str] = variables or ["temperature_2m"]
        self.date_start: str = (
            str(date_start) if date_start is not None else str(date.today())
        )
        self.date_end: str = (
            str(date_end) if date_end is not None else str(date.today())
        )
        self.stations: list[dict[str, Any]] = stations or _DEFAULT_STATIONS

    def download(self) -> str:
        """Fetch DWD station observations and return path to a Parquet file.

        Queries the Open-Meteo historical API for each configured station and
        combines all results into a single DataFrame, which is then written to
        a temporary Parquet file.

        Returns
        -------
        str
            Absolute path to the written ``.parquet`` file.

        Raises
        ------
        ImportError
            If ``requests``, ``pandas``, or ``pyarrow`` is not installed.
        RuntimeError
            If the API request for any station fails with a non-200 status.
        """
        records: list[dict[str, Any]] = []

        try:
            from tqdm import tqdm  # type: ignore[import]

            station_iter: Any = tqdm(self.stations, desc="DWD stations", unit="station")
        except ImportError:
            station_iter = self.stations

        for station in station_iter:
            params: dict[str, Any] = {
                "latitude": station["latitude"],
                "longitude": station["longitude"],
                "hourly": ",".join(self.variables),
                "start_date": self.date_start,
                "end_date": self.date_end,
                "timezone": "UTC",
            }
            logger.info(
                "Requesting DWD data for station '%s' (%s to %s)",
                station["id"],
                self.date_start,
                self.date_end,
            )
            response = requests.get(self.url, params=params, timeout=60)
            if response.status_code != 200:
                raise RuntimeError(
                    f"Open-Meteo API returned {response.status_code} for "
                    f"station '{station['id']}': {response.text[:200]}"
                )

            payload = response.json()
            hourly = payload.get("hourly", {})
            timestamps = hourly.get("time", [])
            for i, ts in enumerate(timestamps):
                row: dict[str, Any] = {
                    "station_id": station["id"],
                    "latitude": station["latitude"],
                    "longitude": station["longitude"],
                    "datetime": ts,
                }
                for var in self.variables:
                    row[var] = hourly.get(var, [None] * len(timestamps))[i]
                records.append(row)

        if not records:
            raise RuntimeError("DWD download produced no records.")

        df = pd.DataFrame(records)
        _, output_path = tempfile.mkstemp(suffix=".parquet", prefix="dwd_stations_")
        df.to_parquet(output_path, engine="pyarrow", compression="snappy", index=False)
        logger.info(
            "DWD download complete: %d records written to %s",
            len(records),
            output_path,
        )
        return output_path
