"""
DWDStationDownloader — fetches hourly time series at DWD station coordinates
from the Open-Meteo historical weather API
(https://open-meteo.com/en/docs/historical-weather-api) and saves them as a
Parquet file.

The values are Open-Meteo **model output** (ERA5/IFS-based reanalysis snapped
to the IFS O1280 grid) at the station coordinates, not DWD observations; see
``docs/development/known_issues.md``.  No API key is required; Open-Meteo is
free for non-commercial use.  For real DWD observations consider the
``wetterdienst`` library instead.
"""

import logging
import tempfile
import time
from datetime import UTC, date, datetime, timedelta
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
    "relative_humidity_2m": "relative_humidity_2m",
}

#: Variables Open-Meteo only provides as daily aggregates.  The downloader
#: requests hourly data only, so these cannot be fetched here.
_DAILY_ONLY_VARIABLES: frozenset[str] = frozenset(
    {"temperature_2m_max", "temperature_2m_min"}
)

#: HTTP status codes worth retrying (rate limit and transient server errors).
_RETRY_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})

#: Attempts per station request, including the first.
_MAX_ATTEMPTS: int = 3

#: Fallback wait in seconds before a retry when no Retry-After is sent.
_RETRY_BACKOFF_S: float = 5.0


def _utc_today() -> date:
    """Return today's date in UTC (Open-Meteo rejects end dates after it)."""
    return datetime.now(UTC).date()


#: Default DWD station locations covering the German climate regions.
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
    """Download hourly data at DWD station coordinates via Open-Meteo.

    Fetches hourly records for all configured stations and variables and
    writes a single Parquet file containing columns ``station_id``,
    ``latitude``, ``longitude``, ``datetime``, and one column per variable.
    Timestamps are naive UTC; fluxes are stamped at interval end.
    """

    #: Pipeline variable names this downloader can fetch.
    SUPPORTED_VARIABLES: frozenset[str] = frozenset(_PIPELINE_TO_OPEN_METEO)

    @classmethod
    def split_supported(cls, variables: list[str]) -> tuple[list[str], list[str]]:
        """Split *variables* into ``(supported, unsupported)``, keeping order."""
        supported = [v for v in variables if v in cls.SUPPORTED_VARIABLES]
        unsupported = [v for v in variables if v not in cls.SUPPORTED_VARIABLES]
        return supported, unsupported

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
            Start date for the download (inclusive). Defaults to today (UTC).
        date_end : date or str, optional
            End date for the download (inclusive). Defaults to today (UTC).
            One extra day is requested, clamped to today (UTC), so the last
            day's interval-end stamps are complete.
        stations : list[dict[str, Any]], optional
            Station dicts, each with keys ``id``, ``latitude``, ``longitude``.
            Defaults to :data:`_DEFAULT_STATIONS`.
        **kwargs : Any
            Additional keyword arguments forwarded to
            :class:`datavia.core.downloader_api.APIDownloader`.
        """
        super().__init__(url=_OPEN_METEO_URL, **kwargs)
        self.variables: list[str] = variables or ["2m_temperature"]
        daily_only = [v for v in self.variables if v in _DAILY_ONLY_VARIABLES]
        if daily_only:
            raise ValueError(
                f"DWD station download is hourly-only; daily-only variables "
                f"are not supported: {daily_only}"
            )
        unknown = [v for v in self.variables if v not in _PIPELINE_TO_OPEN_METEO]
        if unknown:
            raise ValueError(
                f"Unsupported DWD station variables {unknown}. "
                f"Supported: {sorted(_PIPELINE_TO_OPEN_METEO)}"
            )
        # Translate pipeline names to Open-Meteo API names for the HTTP request.
        self._open_meteo_vars: list[str] = [
            _PIPELINE_TO_OPEN_METEO[v] for v in self.variables
        ]
        self.date_start: str = str(date_start or _utc_today())
        self.date_end: str = str(date_end or _utc_today())
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
            If the API request for any station fails: a non-200 status that
            persists after retries, or a network error.
        """
        records: list[dict[str, Any]] = []
        # The day after date_end holds the 00:00 stamp (and for 06-06 UTC
        # precipitation the 01:00-06:00 stamps) that close date_end's day.
        end_date = min(
            date.fromisoformat(self.date_end) + timedelta(days=1), _utc_today()
        )

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
                "end_date": str(end_date),
                "timezone": "UTC",
            }
            logger.info(
                "Requesting DWD data for station '%s' (%s to %s)",
                station["id"],
                self.date_start,
                self.date_end,
            )
            payload = self._fetch(params, station["id"])
            hourly = payload.get("hourly", {})
            timestamps = hourly.get("time", [])
            for i, ts in enumerate(timestamps):
                row: dict[str, Any] = {
                    "station_id": station["id"],
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
        with tempfile.NamedTemporaryFile(
            suffix=".parquet", prefix="dwd_stations_", delete=False
        ) as output_file:
            output_path = output_file.name
        df.to_parquet(output_path, engine="pyarrow", compression="snappy", index=False)
        logger.info(
            "DWD download complete: %d records written to %s",
            len(records),
            output_path,
        )
        return output_path

    def _fetch(self, params: dict[str, Any], station_id: str) -> dict[str, Any]:
        """GET one station's data, retrying rate limits and server errors.

        Raises
        ------
        RuntimeError
            On a network error, or a non-200 status after all retries.
        """
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = requests.get(self.url, params=params, timeout=60)
            except requests.RequestException as exc:
                raise RuntimeError(
                    f"Open-Meteo request failed for station '{station_id}': {exc}"
                ) from exc
            if response.status_code == 200:
                return response.json()
            if response.status_code not in _RETRY_STATUS or attempt == _MAX_ATTEMPTS:
                break
            retry_after = response.headers.get("Retry-After", "")
            wait = float(retry_after) if retry_after.isdigit() else _RETRY_BACKOFF_S
            logger.warning(
                "Open-Meteo returned %d for station '%s'; retrying in %.0f s",
                response.status_code,
                station_id,
                wait,
            )
            time.sleep(wait)
        raise RuntimeError(
            f"Open-Meteo API returned {response.status_code} for "
            f"station '{station_id}': {response.text[:200]}"
        )
