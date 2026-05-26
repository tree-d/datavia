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
from datetime import date, timedelta
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
        buffer_days: int = 1,
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
            The edges are snapped to the 0.1\u00b0 ERA5-Land grid automatically.
        buffer_days : int, optional
            Number of additional days prepended to *date_start* before
            submitting the CDS request.  Accumulative variables (precipitation,
            SSRD) reset at UTC midnight, so a buffer ensures the first
            local-day total can be reconstructed.  Defaults to ``1``.
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
        raw_bbox = bbox or _GERMANY_BBOX
        self.bbox: list[float] = self._snap_bbox(raw_bbox)
        self.buffer_days: int = max(0, int(buffer_days))

    @staticmethod
    def _snap_bbox(bbox: list[float]) -> list[float]:
        """Snap a bounding box to the ERA5-Land 0.1\u00b0 grid.

        ERA5-Land has a native resolution of 0.1\u00b0.  Rounding request edges
        outward to the nearest 0.1\u00b0 boundary ensures that all grid points
        within the area of interest are included in the download.

        Parameters
        ----------
        bbox : list[float]
            Bounding box ``[north, west, south, east]`` in degrees.

        Returns
        -------
        list[float]
            Snapped bounding box ``[north, west, south, east]`` where north
            and east edges are rounded up (``ceil``) and south and west edges
            are rounded down (``floor``) to the nearest 0.1\u00b0.
        """
        import math

        grid_step = 0.1
        north, west, south, east = bbox
        snapped_north = math.ceil(round(north / grid_step, 10)) * grid_step
        snapped_east = math.ceil(round(east / grid_step, 10)) * grid_step
        snapped_south = math.floor(round(south / grid_step, 10)) * grid_step
        snapped_west = math.floor(round(west / grid_step, 10)) * grid_step
        return [
            round(snapped_north, 10),
            round(snapped_west, 10),
            round(snapped_south, 10),
            round(snapped_east, 10),
        ]

    @staticmethod
    def _build_request_date_fields(
        date_start: str, date_end: str
    ) -> tuple[list[str], list[str], list[str]]:
        """Build the sorted unique year, month and day lists for a CDS request.

        Iterates every calendar day in ``[date_start, date_end]`` (inclusive)
        and collects the distinct years, months-of-year and days-of-month that
        appear.  The resulting lists are sorted and zero-padded to two digits
        for months and days, matching the format expected by the CDS API.

        Parameters
        ----------
        date_start : str
            ISO-8601 date string of the first day, e.g. ``"2024-01-01"``.
        date_end : str
            ISO-8601 date string of the last day (inclusive), e.g.
            ``"2024-03-15"``.

        Returns
        -------
        tuple[list[str], list[str], list[str]]
            A three-tuple ``(years, months, days)`` where each element is a
            sorted list of zero-padded strings, e.g.
            ``(["2024"], ["01", "02", "03"], ["01", …, "15"])``.

        Raises
        ------
        ValueError
            If ``date_end`` is earlier than ``date_start``.
        """
        start = date.fromisoformat(date_start)
        end = date.fromisoformat(date_end)
        if end < start:
            raise ValueError(
                f"date_end ({date_end}) must not be earlier than date_start ({date_start})."
            )
        n_days = (end - start).days + 1
        all_dates = [start + timedelta(days=n) for n in range(n_days)]
        years = sorted({d.strftime("%Y") for d in all_dates})
        months = sorted({d.strftime("%m") for d in all_dates})
        days = sorted({d.strftime("%d") for d in all_dates})
        return years, months, days

    def download(self) -> str:
        """Request ERA5-Land data from CDS and return the path to the downloaded NetCDF.

        Submits a ``reanalysis-era5-land`` CDS request (0.1° land surface
        reanalysis) for all configured variables, hours and dates using the
        CDS API v2 parameter keys.  The result is written to a uniquely named
        temporary file so concurrent downloads do not overwrite each other.

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

        years, months, days = self._build_request_date_fields(
            self.date_start, self.date_end
        )

        # Widen the start date by buffer_days so that accumulative variables
        # (precipitation, SSRD) that reset at UTC midnight include enough
        # context to reconstruct the first local-day total.
        if self.buffer_days > 0:
            buffered_start = date.fromisoformat(self.date_start) - timedelta(
                days=self.buffer_days
            )
            years, months, days = self._build_request_date_fields(
                str(buffered_start), self.date_end
            )

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
                "reanalysis-era5-land",
                {
                    "product_type": "reanalysis",
                    "variable": self.variables,
                    "year": years,
                    "month": months,
                    "day": days,
                    "time": [f"{h:02d}:00" for h in range(24)],
                    "area": self.bbox,
                    "data_format": "netcdf",
                    "download_format": "unarchived",
                    "grid": "0.1/0.1",
                },
                output_path,
            )
        except Exception as exc:
            raise RuntimeError(f"CDS retrieval failed: {exc}") from exc

        logger.info("ERA5 download complete: %s", output_path)
        return output_path
