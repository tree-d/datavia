"""
ERA5Downloader — fetches hourly ERA5 reanalysis data from the
Copernicus Climate Data Store (CDS) for a configurable Germany bounding box
and date range.

Authentication requires a valid ``~/.cdsapirc`` credentials file. Refer to
https://cds.climate.copernicus.eu/how-to-api for setup instructions.

Configurable chunking
---------------------
The date range is split into CDS jobs according to the ``chunk_by``
parameter.  Smaller chunks keep individual requests short (1-5 min queue
time) and allow resuming after a failure; larger chunks reduce API overhead
when the CDS queue is fast.  Supported values:

- ``"monthly"`` (default) — one job per calendar month.
- ``"quarterly"`` — one job per calendar quarter (Q1 Jan-Mar, Q2 Apr-Jun,
  Q3 Jul-Sep, Q4 Oct-Dec).  Reduces the job count by 3x vs monthly.
- ``"yearly"`` — one job per calendar year.  Best when the CDS queue is
  fast and the date range is short.
- ``"none"`` — a single job for the entire date range.  Matches the
  pre-Step-9 behaviour and is useful for short ranges (e.g. one week).

:meth:`ERA5Downloader.download` returns a newline-joined string of all
produced paths so that the caller can save them individually.

Queue-wait logging
------------------
While a CDS job is queued, the downloader logs the job ID and elapsed wait
time every 60 s, including per-job wall time.  Optionally a
``cds_queue_timeout`` (seconds) cancels the job and raises
:exc:`TimeoutError` if the queue wait exceeds the limit.

Progress bar
------------
A ``tqdm`` outer bar shows ``[N/total chunks]`` progress.  The inner byte
transfer bar is provided by the ``cdsapi`` client itself (unchanged).
"""

import calendar
import contextlib
import datetime
import logging
import math
import tempfile
import time
from datetime import date, timedelta
from typing import Any

from datavia.core.downloader_api import APIDownloader

logger = logging.getLogger(__name__)

# ``cdsapi`` is an optional dependency (installed via the ``era5`` extra).
# Import it at module level so that ``unittest.mock.patch`` can replace it
# in tests.  The module attribute is ``None`` when cdsapi is not installed;
# the :meth:`ERA5Downloader.download` method checks and raises ImportError.
try:
    import cdsapi  # type: ignore[import]
except ImportError:
    cdsapi = None  # type: ignore[assignment]

#: Default bounding box covering Germany (NWSE order as required by cdsapi).
_GERMANY_BBOX: list[float] = [55.1, 5.9, 47.3, 15.0]

#: ERA5 CDS API endpoint.
_CDS_URL: str = "https://cds.climate.copernicus.eu/api"

#: Valid values for the ``chunk_by`` parameter.
_VALID_CHUNK_BY: frozenset[str] = frozenset({"monthly", "quarterly", "yearly", "none"})


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
        cds_queue_timeout: int | None = None,
        chunk_by: str = "monthly",
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
        cds_queue_timeout : int, optional
            Maximum number of seconds to wait while a single CDS job is
            queued.  When the limit is exceeded the job is cancelled and
            :exc:`TimeoutError` is raised with the job ID.  ``None`` (default)
            means no timeout — the downloader waits indefinitely.
        chunk_by : str, optional
            Granularity of CDS job splitting.  One of:

            - ``"monthly"`` (default) — one job per calendar month.
            - ``"quarterly"`` — one job per calendar quarter.
            - ``"yearly"`` — one job per calendar year.
            - ``"none"`` — a single job for the entire date range.
        **kwargs : Any
            Additional keyword arguments forwarded to
            :class:`datavia.core.downloader_api.APIDownloader`.

        Raises
        ------
        ValueError
            If *chunk_by* is not one of the valid values.
        """
        if chunk_by not in _VALID_CHUNK_BY:
            raise ValueError(
                f"Invalid chunk_by value {chunk_by!r}. "
                f"Must be one of: {sorted(_VALID_CHUNK_BY)}."
            )
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
        self.cds_queue_timeout: int | None = (
            int(cds_queue_timeout) if cds_queue_timeout is not None else None
        )
        self.chunk_by: str = chunk_by

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
            are rounded down (``floor``) to the nearest 0.1°.
        """
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
                f"date_end ({date_end}) must not be earlier "
                f"than date_start ({date_start})."
            )
        n_days = (end - start).days + 1
        all_dates = [start + timedelta(days=n) for n in range(n_days)]
        years = sorted({d.strftime("%Y") for d in all_dates})
        months = sorted({d.strftime("%m") for d in all_dates})
        days = sorted({d.strftime("%d") for d in all_dates})
        return years, months, days

    @staticmethod
    def _iter_monthly_chunks(
        date_start: str,
        date_end: str,
    ) -> list[tuple[str, str]]:
        """Split a range into one chunk per calendar month.

        Each element covers exactly the days within that calendar month that
        fall inside ``[date_start, date_end]``.  The list is sorted by start
        date.

        Parameters
        ----------
        date_start : str
            ISO-8601 date string of the first day, e.g. ``"2024-01-15"``.
        date_end : str
            ISO-8601 date string of the last day (inclusive), e.g.
            ``"2024-03-10"``.

        Returns
        -------
        list[tuple[str, str]]
            Sorted list of ``(chunk_start, chunk_end)`` ISO date string pairs,
            one per calendar month that the range spans.

        Raises
        ------
        ValueError
            If ``date_end`` is earlier than ``date_start``.
        """
        start = date.fromisoformat(date_start)
        end = date.fromisoformat(date_end)
        if end < start:
            raise ValueError(
                f"date_end ({date_end}) must not be earlier "
                f"than date_start ({date_start})."
            )

        chunks: list[tuple[str, str]] = []
        cursor = date(start.year, start.month, 1)
        while cursor <= end:
            _, last_day = calendar.monthrange(cursor.year, cursor.month)
            month_end = date(cursor.year, cursor.month, last_day)
            chunk_start = max(cursor, start)
            chunk_end = min(month_end, end)
            chunks.append((str(chunk_start), str(chunk_end)))
            # Advance to the first day of the next month.
            if cursor.month == 12:
                cursor = date(cursor.year + 1, 1, 1)
            else:
                cursor = date(cursor.year, cursor.month + 1, 1)
        return chunks

    @staticmethod
    def _iter_quarterly_chunks(
        date_start: str,
        date_end: str,
    ) -> list[tuple[str, str]]:
        """Split a range into one chunk per calendar quarter.

        Quarter boundaries are fixed: Q1 = Jan-Mar, Q2 = Apr-Jun,
        Q3 = Jul-Sep, Q4 = Oct-Dec.  The first and last quarters are clipped
        to ``[date_start, date_end]``.

        Parameters
        ----------
        date_start : str
            ISO-8601 date string of the first day.
        date_end : str
            ISO-8601 date string of the last day (inclusive).

        Returns
        -------
        list[tuple[str, str]]
            Sorted list of ``(chunk_start, chunk_end)`` pairs, one per
            calendar quarter that the range spans.

        Raises
        ------
        ValueError
            If ``date_end`` is earlier than ``date_start``.
        """
        start = date.fromisoformat(date_start)
        end = date.fromisoformat(date_end)
        if end < start:
            raise ValueError(
                f"date_end ({date_end}) must not be earlier "
                f"than date_start ({date_start})."
            )

        # (first_month, last_month) for each quarter.
        quarter_ranges: list[tuple[int, int]] = [(1, 3), (4, 6), (7, 9), (10, 12)]

        chunks: list[tuple[str, str]] = []
        for year in range(start.year, end.year + 1):
            for q_start_month, q_end_month in quarter_ranges:
                _, q_end_day = calendar.monthrange(year, q_end_month)
                q_start = date(year, q_start_month, 1)
                q_end = date(year, q_end_month, q_end_day)
                chunk_start = max(q_start, start)
                chunk_end = min(q_end, end)
                if chunk_start <= chunk_end:
                    chunks.append((str(chunk_start), str(chunk_end)))
        return chunks

    @staticmethod
    def _iter_yearly_chunks(
        date_start: str,
        date_end: str,
    ) -> list[tuple[str, str]]:
        """Split a range into one chunk per calendar year.

        The first and last years are clipped to ``[date_start, date_end]``
        so partial years at the boundaries are handled correctly.

        Parameters
        ----------
        date_start : str
            ISO-8601 date string of the first day.
        date_end : str
            ISO-8601 date string of the last day (inclusive).

        Returns
        -------
        list[tuple[str, str]]
            Sorted list of ``(chunk_start, chunk_end)`` pairs, one per
            calendar year that the range spans.

        Raises
        ------
        ValueError
            If ``date_end`` is earlier than ``date_start``.
        """
        start = date.fromisoformat(date_start)
        end = date.fromisoformat(date_end)
        if end < start:
            raise ValueError(
                f"date_end ({date_end}) must not be earlier "
                f"than date_start ({date_start})."
            )

        chunks: list[tuple[str, str]] = []
        for year in range(start.year, end.year + 1):
            year_start = date(year, 1, 1)
            year_end = date(year, 12, 31)
            chunk_start = max(year_start, start)
            chunk_end = min(year_end, end)
            chunks.append((str(chunk_start), str(chunk_end)))
        return chunks

    def _get_chunks(self, effective_start: str) -> list[tuple[str, str]]:
        """Return the list of date-range chunks for the configured ``chunk_by`` mode.

        Dispatches to :meth:`_iter_monthly_chunks`, :meth:`_iter_quarterly_chunks`,
        :meth:`_iter_yearly_chunks`, or a single-chunk list depending on
        :attr:`chunk_by`.

        Parameters
        ----------
        effective_start : str
            Actual start date after applying ``buffer_days`` offset.

        Returns
        -------
        list[tuple[str, str]]
            Sorted list of ``(chunk_start, chunk_end)`` date-range pairs.
        """
        if self.chunk_by == "monthly":
            return self._iter_monthly_chunks(effective_start, self.date_end)
        if self.chunk_by == "quarterly":
            return self._iter_quarterly_chunks(effective_start, self.date_end)
        if self.chunk_by == "yearly":
            return self._iter_yearly_chunks(effective_start, self.date_end)
        # chunk_by == "none" — single request for the whole range.
        return [(effective_start, self.date_end)]

    def _wait_for_cds_job(self, job: Any) -> None:
        """Poll a queued CDS job, logging progress every 60 s.

        The job is polled until its status is no longer ``"queued"`` or
        ``"running"``; control is returned to ``cdsapi`` to perform the
        actual file download.  This method only handles the queue-wait phase.

        Parameters
        ----------
        job : Any
            A ``cdsapi`` result/job object that exposes a ``reply`` dict with
            ``"status"`` and ``"request_id"`` keys.

        Raises
        ------
        TimeoutError
            If ``cds_queue_timeout`` is set and the job remains queued for
            longer than that many seconds.
        """
        poll_interval: int = 60
        elapsed: int = 0
        job_id: str = job.reply.get("request_id", "unknown")

        while True:
            status: str = job.reply.get("status", "")
            if status not in ("queued", "running"):
                break
            if status == "queued":
                logger.info(
                    "ERA5Downloader: job %s queued — waiting %d s (total %s)",
                    job_id,
                    poll_interval,
                    datetime.timedelta(seconds=elapsed),
                )
                if (
                    self.cds_queue_timeout is not None
                    and elapsed >= self.cds_queue_timeout
                ):
                    with contextlib.suppress(Exception):
                        job.delete()
                    raise TimeoutError(
                        f"ERA5Downloader: CDS job {job_id} exceeded queue timeout "
                        f"of {self.cds_queue_timeout} s."
                    )
            elif status == "running":
                logger.info("ERA5Downloader: job %s running", job_id)
            time.sleep(poll_interval)
            elapsed += poll_interval
            job.update()

    def download(self) -> str:
        """Request ERA5-Land data from CDS one month at a time and return all paths.

        Splits the configured date range into one calendar-month CDS job per
        chunk.  Each chunk is downloaded to its own temporary NetCDF file.
        All produced file paths are returned as a newline-joined string so
        that :class:`~datavia.weather.composite_downloader.CompositeWeatherDownloader`
        and :class:`~datavia.weather.pipeline.WeatherPipeline` can save them
        individually via ``SaverWeather.save()``.

        A ``tqdm`` progress bar shows ``[current/total months]`` if ``tqdm``
        is installed.  The inner byte-transfer bar is provided by the
        ``cdsapi`` client itself.

        Returns
        -------
        str
            Newline-joined absolute paths of the downloaded ``.nc`` files
            (one per calendar month chunk).

        Raises
        ------
        ImportError
            If the ``cdsapi`` package is not installed.
        RuntimeError
            If a CDS request fails or the credentials file is missing.
        TimeoutError
            If ``cds_queue_timeout`` is set and a single job exceeds it
            while in the queued state.
        """
        if cdsapi is None:
            raise ImportError(
                "cdsapi is required for ERA5 downloads. "
                "Install it with `pip install cdsapi` and set up ~/.cdsapirc."
            )

        # Apply buffer_days to the start date for accumulative variables
        # (precipitation, SSRD) so the first local-day total can be reconstructed.
        effective_start: str = self.date_start
        if self.buffer_days > 0:
            buffered = date.fromisoformat(self.date_start) - timedelta(
                days=self.buffer_days
            )
            effective_start = str(buffered)

        chunks = self._get_chunks(effective_start)
        total_chunks = len(chunks)
        logger.info(
            "ERA5Downloader: %d chunk(s) [chunk_by=%s] for variables=%s, %s to %s",
            total_chunks,
            self.chunk_by,
            self.variables,
            self.date_start,
            self.date_end,
        )

        # Optional outer tqdm progress bar.
        try:
            from tqdm import tqdm  # type: ignore[import]

            chunk_iter = tqdm(chunks, desc=f"ERA5 ({self.chunk_by})", unit="chunk")
        except ImportError:
            chunk_iter = iter(chunks)  # type: ignore[assignment]

        client = cdsapi.Client()
        output_paths: list[str] = []

        for chunk_index, (chunk_start, chunk_end) in enumerate(chunk_iter, start=1):
            years, months, days = self._build_request_date_fields(
                chunk_start, chunk_end
            )
            _, output_path = tempfile.mkstemp(suffix=".nc", prefix="era5_")
            logger.info(
                "ERA5Downloader: submitting chunk %d/%d [%s] — %s to %s",
                chunk_index,
                total_chunks,
                self.chunk_by,
                chunk_start,
                chunk_end,
            )

            job_wall_start = time.monotonic()
            job = None
            try:
                job = client.retrieve(
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
                )
                # Poll for queue/running status while logging progress.
                if hasattr(job, "reply"):
                    self._wait_for_cds_job(job)
                # Download the result to the temp file.
                job.download(output_path)
            except KeyboardInterrupt:
                if job is not None:
                    with contextlib.suppress(Exception):
                        job.delete()
                        logger.info(
                            "ERA5Downloader: cancelled CDS job for chunk %s to %s",
                            chunk_start,
                            chunk_end,
                        )
            except TimeoutError:
                raise
            except Exception as exc:
                exc_msg = str(exc)
                if "403" in exc_msg and (
                    "too large" in exc_msg.lower() or "cost limits" in exc_msg.lower()
                ):
                    raise RuntimeError(
                        f"CDS request too large for chunk {chunk_start} to {chunk_end} "
                        f"({len(self.variables)} variable(s), "
                        f"{chunk_start} to {chunk_end}). "
                        "The Copernicus CDS enforces a per-request size limit. "
                        "Solutions: use chunk_by='monthly' (default), reduce the "
                        "number of variables per request, or shrink the bounding box "
                        "(era5_bbox)."
                    ) from exc
                raise RuntimeError(
                    "CDS retrieval failed for chunk "
                    f"{chunk_start} to {chunk_end}: {exc}"
                ) from exc

            job_elapsed = time.monotonic() - job_wall_start
            logger.info(
                "ERA5Downloader: chunk %d/%d complete in %.1f s -> %s",
                chunk_index,
                total_chunks,
                job_elapsed,
                output_path,
            )
            output_paths.append(output_path)

        return "\n".join(output_paths)
