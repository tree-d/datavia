"""
DWDStationDownloader — fetches DWD station observation time series from the
Open-Meteo historical API (https://open-meteo.com/en/docs/dwd-api) and saves
them as a Parquet file.

No API key is required; Open-Meteo is free for non-commercial use.
For high-volume commercial use consider the ``wetterdienst`` library instead.
"""

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

#: Maps pipeline-level variable names (used throughout the datavia API) to the
#: corresponding Open-Meteo hourly parameter names accepted by the archive API.
#: Extend this table when additional variables are added to the registry.
_PIPELINE_TO_OPEN_METEO: dict[str, str] = {
    "2m_temperature": "temperature_2m",
    "total_precipitation": "precipitation",
    "surface_solar_radiation_downwards": "shortwave_radiation",
    "relative_humidity_2m": "relativehumidity_2m",
    "temperature_2m_max": "temperature_2m_max",
    "temperature_2m_min": "temperature_2m_min",
}

#: Default DWD station locations covering the German climate regions.
#: A more complete list can be generated at runtime from the Open-Meteo
#: station index, but these representative stations are used as a bootstrap.
_DEFAULT_STATIONS: list[dict[str, Any]] = [
    {"name": "Berlin", "latitude": 52.52, "longitude": 13.41},
    {"name": "Munich", "latitude": 48.14, "longitude": 11.58},
    {"name": "Hamburg", "latitude": 53.55, "longitude": 10.0},
    {"name": "Frankfurt", "latitude": 50.11, "longitude": 8.68},
    {"name": "Cologne", "latitude": 50.94, "longitude": 6.96},
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
            Pipeline-level variable names, e.g.
            ``["2m_temperature", "total_precipitation"]``.
            Defaults to ``["2m_temperature"]``.  Each name is translated to
            the corresponding Open-Meteo API parameter name automatically.
        date_start : date or str, optional
            Start date for the download (inclusive). Defaults to today.
        date_end : date or str, optional
            End date for the download (inclusive). Defaults to today.
        stations : list[dict[str, Any]], optional
            Station dicts, each with keys ``name``, ``latitude``, ``longitude``.
            Defaults to :data:`_DEFAULT_STATIONS`.
        **kwargs : Any
            Additional keyword arguments forwarded to
            :class:`datavia.core.downloader_api.APIDownloader`.
        """
        super().__init__(url=_OPEN_METEO_URL, **kwargs)
        self.variables: list[str] = variables or ["2m_temperature"]
        # Translate pipeline names to Open-Meteo API names for the HTTP request.
        self._open_meteo_vars: list[str] = [
            _PIPELINE_TO_OPEN_METEO.get(v, v) for v in self.variables
        ]
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
                "hourly": ",".join(self._open_meteo_vars),
                "start_date": self.date_start,
                "end_date": self.date_end,
                "timezone": "UTC",
            }
            logger.info(
                "Requesting DWD data for station '%s' (%s to %s)",
                station["name"],
                self.date_start,
                self.date_end,
            )
            response = requests.get(self.url, params=params, timeout=60)
            if response.status_code != 200:
                raise RuntimeError(
                    f"Open-Meteo API returned {response.status_code} for "
                    f"station '{station['name']}': {response.text[:200]}"
                )

            payload = response.json()
            hourly = payload.get("hourly", {})
            timestamps = hourly.get("time", [])
            for i, ts in enumerate(timestamps):
                row: dict[str, Any] = {
                    "station_id": station["name"],
                    "latitude": station["latitude"],
                    "longitude": station["longitude"],
                    "datetime": ts,
                }
                for pipeline_var, om_var in zip(
                    self.variables, self._open_meteo_vars, strict=False
                ):
                    row[pipeline_var] = hourly.get(om_var, [None] * len(timestamps))[i]
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
