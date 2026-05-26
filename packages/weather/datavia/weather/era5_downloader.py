"""
ERA5Downloader — fetches hourly ERA5 reanalysis data from the
Copernicus Climate Data Store (CDS) for a configurable Germany bounding box
and date range.

Authentication requires a valid ``~/.cdsapirc`` credentials file. Refer to
https://cds.climate.copernicus.eu/how-to-api for setup instructions.
"""

from __future__ import annotations

import logging
import tempfile
from datetime import date
from typing import Any

from datavia.core.downloader_api import APIDownloader

logger = logging.getLogger(__name__)

#: Default bounding box covering Germany (NWSE order as required by cdsapi).
_GERMANY_BBOX: list[float] = [55.1, 5.9, 47.3, 15.0]

#: ERA5 CDS API endpoint.
_CDS_URL: str = "https://cds.climate.copernicus.eu/api"


class ERA5Downloader(APIDownloader):
    """Download ERA5 hourly reanalysis data from the Copernicus CDS.

    Uses the ``cdsapi`` Python library to submit an asynchronous retrieval
    request for the specified variables and date range.  The resulting NetCDF
    file is written to a system temporary file and its path is returned.

    Authentication is read from ``~/.cdsapirc``; the file must contain a
    valid UID and API key (see https://cds.climate.copernicus.eu/how-to-api).
    """

    def __init__(
        self,
        variables: list[str] | None = None,
        date_start: date | str | None = None,
        date_end: date | str | None = None,
        bbox: list[float] | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialise the ERA5 downloader.

        Parameters
        ----------
        variables : list[str], optional
            ERA5 variable names as accepted by cdsapi, e.g.
            ``["2m_temperature", "total_precipitation"]``.
            Defaults to ``["2m_temperature"]``.
        date_start : date or str, optional
            First day of the download range (inclusive). Defaults to today.
        date_end : date or str, optional
            Last day of the download range (inclusive). Defaults to today.
        bbox : list[float], optional
            Bounding box ``[north, west, south, east]`` in degrees.  Defaults
            to the Germany bounding box ``[55.1, 5.9, 47.3, 15.0]``.
        **kwargs : Any
            Additional keyword arguments forwarded to
            :class:`datavia.core.downloader_api.APIDownloader`.
        """
        super().__init__(url=_CDS_URL, **kwargs)
        self.variables: list[str] = variables or ["2m_temperature"]
        self.date_start: str = (
            str(date_start) if date_start is not None else str(date.today())
        )
        self.date_end: str = (
            str(date_end) if date_end is not None else str(date.today())
        )
        self.bbox: list[float] = bbox or _GERMANY_BBOX

    def download(self) -> str:
        """Request ERA5 data from CDS and return the path to the downloaded NetCDF.

        Submits a ``reanalysis-era5-single-levels`` CDS request for all
        configured variables, hours and dates.  The result is written to a
        uniquely named temporary file so concurrent downloads do not
        overwrite each other.

        Returns
        -------
        str
            Absolute path to the downloaded ``.nc`` file.

        Raises
        ------
        ImportError
            If the ``cdsapi`` package is not installed.
        RuntimeError
            If the CDS request fails or the credentials file is missing.
        """
        try:
            import cdsapi  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "cdsapi is required for ERA5 downloads. "
                "Install it with `pip install cdsapi` and set up ~/.cdsapirc."
            ) from exc

        _, output_path = tempfile.mkstemp(suffix=".nc", prefix="era5_")
        logger.info(
            "Requesting ERA5 data: variables=%s, %s to %s",
            self.variables,
            self.date_start,
            self.date_end,
        )

        client = cdsapi.Client()
        try:
            client.retrieve(
                "reanalysis-era5-single-levels",
                {
                    "product_type": "reanalysis",
                    "variable": self.variables,
                    "year": list(
                        dict.fromkeys(
                            str(self.date_start)[:4],
                            str(self.date_end)[:4],
                        )
                    ),
                    "month": [f"{m:02d}" for m in range(1, 13)],
                    "day": [f"{d:02d}" for d in range(1, 32)],
                    "time": [f"{h:02d}:00" for h in range(24)],
                    "area": self.bbox,
                    "format": "netcdf",
                    "date": f"{self.date_start}/{self.date_end}",
                },
                output_path,
            )
        except Exception as exc:
            raise RuntimeError(f"CDS retrieval failed: {exc}") from exc

        logger.info("ERA5 download complete: %s", output_path)
        return output_path
