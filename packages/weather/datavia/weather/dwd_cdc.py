"""Daily DWD station observations from the Climate Data Center (CDC).

The DWD publishes the daily observations of its station network on its
OpenData server as one zip archive per station::

    https://opendata.dwd.de/climate_environment/CDC/observations_germany/
        climate/daily/<group>/<branch>/tageswerte_<TAG>_<id>_....zip

- ``<group>`` is the product group: ``kl`` (climate stations: temperature,
  humidity, ...) or ``more_precip`` (the much denser precipitation network).
- ``<branch>`` is ``historical`` (quality controlled, up to the end of the
  previous year; one file per station named
  ``tageswerte_<TAG>_<id>_<first>_<last>_hist.zip``) or ``recent`` (about the
  last 550 days up to yesterday, not yet fully checked; named
  ``tageswerte_<TAG>_<id>_akt.zip``).

Each archive holds a ``produkt_*.txt`` table (``;``-separated, one row per
day, ``-999`` for missing values) and a ``Metadaten_Geographie_*.txt`` table
with the station's position history.

:class:`CDCClient` downloads the archives a date range needs, caches them,
and reads one variable into a :class:`StationData` block of shape
``(n_stations, n_days)``.  Most variables are one table column
(:class:`CDCProduct`); a few are computed per station and day from several
columns of the same archive (:class:`DerivedCDCProduct`, e.g. the vapour
pressure deficit from ``TXK``, ``TNK`` and ``VPM``).  This is step 1 of the
kriged ``DWD_stations`` source; step 2 (interpolation onto a grid) is
:mod:`datavia.weather.dwd_kriging_downloader`.

Reading rules (those of the operational German Drought Monitor):

- Values outside a plausible range (:class:`CDCProduct`) are treated as
  missing, as is ``-999``.  A derived value is missing if any of its inputs
  is, and its result is range-checked again.  Quality flags (``QN_*``) are not used.
- A station's position and elevation are its *current* ones (the last row of
  the geography metadata), for the whole period.
- Where the historical and the recent archive both have a value for a day,
  the historical (quality-controlled) one is used.
- Where several stations share one position, only the one with the most
  valid values in the requested period is kept; the kriging system would be
  singular otherwise.
- Station archives known to be corrupt (:data:`CORRUPT_STATIONS`) are skipped.

Archive cache layout (under ``cache_dir``, by default
``<data_dir>/DWD_stations/_cdc``)::

    <group>/<branch>/tageswerte_*.zip

Historical archives are kept until the DWD replaces them (the file name then
changes, and the old file is removed); recent archives are downloaded again
when they are older than ``recent_max_age`` (default one day).
"""

from __future__ import annotations

import io
import logging
import re
import time
import zipfile
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .vapour_pressure import vapour_pressure_deficit

logger = logging.getLogger(__name__)

#: Root URL of the daily station observations on the DWD OpenData server.
CDC_DAILY_URL: str = (
    "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/daily"
)

#: The two archive branches: quality-controlled history and the recent period.
BRANCHES: tuple[str, str] = ("historical", "recent")

#: Station archives that are known to be corrupt and break the interpolation
#: (excluded in the operational German Drought Monitor), per product group.
CORRUPT_STATIONS: dict[str, frozenset[str]] = {
    "more_precip": frozenset({"05127", "14317"}),
}

#: How far back the ``recent`` branch reaches.  The DWD keeps about 550 days
#: there; the margin makes sure a range at the edge still reads both branches.
_RECENT_SPAN: timedelta = timedelta(days=600)

#: HTTP status codes worth retrying (rate limit and transient server errors).
_RETRY_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})

#: Attempts per HTTP request, including the first.
_MAX_ATTEMPTS: int = 5

#: Wait before retry *n* is ``n * _RETRY_BACKOFF_S`` seconds.
_RETRY_BACKOFF_S: float = 2.0

#: Missing-value marker of the CDC product tables.
_CDC_MISSING: int = -999

#: Archive names in a directory listing.  Groups: tag, station id, and for
#: historical archives the first and last day (``YYYYMMDD``).
_ARCHIVE_RE = re.compile(
    r'href="(tageswerte_([A-Za-z]+)_(\d{5})_(?:(\d{8})_(\d{8})_hist|akt)\.zip)"'
)


@dataclass(frozen=True)
class CDCProduct:
    """Where a pipeline variable is found in the CDC daily archives.

    Attributes
    ----------
    group : str
        Product group directory, ``"kl"`` or ``"more_precip"``.
    column : str
        Column of the ``produkt_*.txt`` table holding the daily value, e.g.
        ``"TMK"`` (daily mean temperature).
    low, high : float
        Plausible range in physical units; values outside it are treated as
        missing.
    units : str
        Units of the values, e.g. ``"degC"`` or ``"mm"``.
    description : str
        What the daily value is, including its time window.
    """

    group: str
    column: str
    low: float
    high: float
    units: str
    description: str

    @property
    def columns(self) -> tuple[str, ...]:
        """The table columns read: just :attr:`column`."""
        return (self.column,)


@dataclass(frozen=True)
class DerivedCDCProduct:
    """A pipeline variable computed from several columns of one archive.

    Each input column is read and range-checked like a :class:`CDCProduct`;
    *compute* then combines them, and its result is range-checked again.

    Attributes
    ----------
    group : str
        Product group directory of all inputs, e.g. ``"kl"``.
    inputs : tuple[CDCProduct, ...]
        The input columns, in the order *compute* takes them.
    compute : Callable[..., np.ndarray]
        Takes one ``(n_days,)`` array per input (physical units, NaN where
        missing) and returns the ``(n_days,)`` values of the variable.
    low, high : float
        Plausible range of the result; values outside it are treated as
        missing.
    units : str
        Units of the result, e.g. ``"kPa"``.
    description : str
        What the daily value is, including its time window.
    """

    group: str
    inputs: tuple[CDCProduct, ...]
    compute: Callable[..., np.ndarray]
    low: float
    high: float
    units: str
    description: str

    @property
    def columns(self) -> tuple[str, ...]:
        """The table columns read: those of the inputs, in order."""
        return tuple(product.column for product in self.inputs)


def _station_vapour_pressure_deficit(
    temperature_max_c: np.ndarray,
    temperature_min_c: np.ndarray,
    vapour_pressure_hpa: np.ndarray,
) -> np.ndarray:
    """Return the FAO-56 vapour pressure deficit [kPa] of station days.

    ``es`` from the daily extremes, ``ea`` the measured daily mean vapour
    pressure; see :func:`~datavia.weather.vapour_pressure.vapour_pressure_deficit`.
    """
    # VPM is in hPa; the FAO-56 formulas work in kPa.
    return vapour_pressure_deficit(
        temperature_max_c, temperature_min_c, vapour_pressure_hpa / 10.0
    )


#: Daily maximum temperature (an input of the vapour pressure deficit too).
_TEMPERATURE_MAX: CDCProduct = CDCProduct(
    "kl", "TXK", -40.0, 50.0, "degC", "Daily maximum air temperature, 00-24 UTC"
)

#: Daily minimum temperature (an input of the vapour pressure deficit too).
_TEMPERATURE_MIN: CDCProduct = CDCProduct(
    "kl", "TNK", -45.0, 45.0, "degC", "Daily minimum air temperature, 00-24 UTC"
)

#: Daily mean vapour pressure.  Saturation at the highest German
#: temperatures is about 60 hPa, so larger values are errors.
_VAPOUR_PRESSURE: CDCProduct = CDCProduct(
    "kl", "VPM", 0.0, 60.0, "hPa", "Daily mean vapour pressure, 00-24 UTC"
)

#: The CDC product of each supported pipeline variable.
CDC_PRODUCTS: dict[str, CDCProduct | DerivedCDCProduct] = {
    "total_precipitation": CDCProduct(
        "more_precip",
        "RS",
        0.0,
        311.0,
        "mm",
        "Precipitation of day D: 05:51 UTC on D to 05:50 UTC on D+1",
    ),
    "2m_temperature": CDCProduct(
        "kl", "TMK", -38.0, 45.0, "degC", "Daily mean air temperature, 00-24 UTC"
    ),
    "temperature_2m_max": _TEMPERATURE_MAX,
    "temperature_2m_min": _TEMPERATURE_MIN,
    "relative_humidity_2m": CDCProduct(
        "kl", "UPM", 0.0, 100.0, "%", "Daily mean relative humidity, 00-24 UTC"
    ),
    # A German heat-wave day stays below about 5 kPa; 10 kPa leaves a margin.
    "vapour_pressure_deficit": DerivedCDCProduct(
        "kl",
        (_TEMPERATURE_MAX, _TEMPERATURE_MIN, _VAPOUR_PRESSURE),
        _station_vapour_pressure_deficit,
        0.0,
        10.0,
        "kPa",
        "Daily vapour pressure deficit (FAO-56): es from TXK and TNK minus the "
        "mean vapour pressure VPM, 00-24 UTC",
    ),
}


@dataclass(frozen=True)
class StationData:
    """Daily values of one variable at many stations over one date range.

    Attributes
    ----------
    station_id : np.ndarray
        Five-digit DWD station ids (``str``), shape ``(n_stations,)``.
    longitude, latitude : np.ndarray
        Current station position in degrees (WGS84), shape ``(n_stations,)``.
    elevation : np.ndarray
        Current station elevation in metres, shape ``(n_stations,)``.
    dates : pd.DatetimeIndex
        The days, consecutive, length ``n_days``.
    values : np.ndarray
        Daily values in physical units (see :class:`CDCProduct`), float64,
        shape ``(n_stations, n_days)``; NaN where a station has no valid value.
    """

    station_id: np.ndarray
    longitude: np.ndarray
    latitude: np.ndarray
    elevation: np.ndarray
    dates: pd.DatetimeIndex
    values: np.ndarray

    @property
    def n_stations(self) -> int:
        """Number of stations."""
        return len(self.station_id)

    def subset(self, keep: np.ndarray) -> StationData:
        """Return only the stations selected by *keep*.

        Parameters
        ----------
        keep : np.ndarray
            Boolean mask of shape ``(n_stations,)`` or integer indices.

        Returns
        -------
        StationData
            The selected stations, all days.
        """
        return StationData(
            self.station_id[keep],
            self.longitude[keep],
            self.latitude[keep],
            self.elevation[keep],
            self.dates,
            self.values[keep],
        )

    def between(self, start: date, end: date) -> StationData:
        """Return the days from *start* to *end* (both inclusive).

        Parameters
        ----------
        start, end : date
            First and last day; must lie within :attr:`dates`.

        Returns
        -------
        StationData
            All stations, the selected days only.
        """
        days = (self.dates >= pd.Timestamp(start)) & (self.dates <= pd.Timestamp(end))
        return StationData(
            self.station_id,
            self.longitude,
            self.latitude,
            self.elevation,
            self.dates[days],
            self.values[:, days],
        )


@dataclass(frozen=True)
class _Archive:
    """One station archive listed on the server."""

    name: str
    station_id: str
    branch: str
    first: date | None  # historical only: first and last day in the file
    last: date | None

    def overlaps(self, start: date, end: date) -> bool:
        """Whether the archive can hold days in ``[start, end]``."""
        if self.first is None or self.last is None:
            return True  # recent archive: the caller decides by date
        return self.first <= end and self.last >= start


class CDCClient:
    """Download, cache and read daily DWD station observations.

    Parameters
    ----------
    cache_dir : str or Path
        Directory for the downloaded archives (``<group>/<branch>/`` below
        it).  Created when needed.
    workers : int, optional
        Parallel downloads and archive reads.  Defaults to 8.
    recent_max_age : timedelta, optional
        A cached ``recent`` archive older than this is downloaded again, so
        new days and corrections arrive.  Defaults to one day.
    session : requests.Session, optional
        HTTP session to use; a new one by default.

    Examples
    --------
    >>> from datetime import date
    >>> client = CDCClient("/tmp/cdc")  # doctest: +SKIP
    >>> data = client.station_data(  # doctest: +SKIP
    ...     "total_precipitation", date(2025, 6, 1), date(2025, 6, 30)
    ... )
    >>> data.values.shape  # doctest: +SKIP
    (1987, 30)
    """

    def __init__(
        self,
        cache_dir: str | Path,
        workers: int = 8,
        recent_max_age: timedelta = timedelta(days=1),
        session: requests.Session | None = None,
    ) -> None:
        """Initialise the client; see the class docstring for the parameters."""
        self.cache_dir = Path(cache_dir)
        self.workers = workers
        self.recent_max_age = recent_max_age
        self._session = session or requests.Session()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def station_data(
        self,
        variable: str,
        start: date,
        end: date,
        station_ids: Iterable[str] | None = None,
    ) -> StationData:
        """Read the daily values of *variable* at all stations for a date range.

        Downloads the archives that are missing or outdated in the cache,
        reads the variable from them and applies the reading rules of the
        module docstring.

        Parameters
        ----------
        variable : str
            Pipeline variable name, a key of :data:`CDC_PRODUCTS`.
        start, end : date
            First and last day (both inclusive).
        station_ids : iterable of str, optional
            Only these five-digit DWD station ids (only their archives are
            downloaded).  Default: every station.

        Returns
        -------
        StationData
            Every station with at least one valid value in the range.  The
            days span ``start`` to ``end`` even where no station reports
            (e.g. today, or the future); those columns are all NaN.

        Raises
        ------
        ValueError
            If *variable* is not supported, or *end* is before *start*.
        RuntimeError
            If a directory listing or an archive cannot be downloaded.
        """
        if variable not in CDC_PRODUCTS:
            raise ValueError(
                f"Variable {variable!r} is not available from the DWD CDC. "
                f"Supported: {sorted(CDC_PRODUCTS)}"
            )
        if end < start:
            raise ValueError(f"end ({end}) must not be before start ({start})")
        product = CDC_PRODUCTS[variable]
        branches = self._branches_for(start, end)
        wanted = None if station_ids is None else set(station_ids)
        archives = [
            archive
            for branch in branches
            for archive in self._list_archives(product.group, branch)
            if archive.overlaps(start, end)
            and (wanted is None or archive.station_id in wanted)
        ]
        logger.info(
            "DWD CDC %s/%s: %d station archives (%s) for %s .. %s",
            product.group,
            "+".join(product.columns),
            len(archives),
            "+".join(branches),
            start,
            end,
        )
        paths = self._fetch_archives(product.group, archives)

        dates = pd.date_range(start, end, freq="D")
        with ThreadPoolExecutor(self.workers) as pool:
            parsed = list(
                pool.map(lambda p: _read_archive(p, product, dates), paths.values())
            )
        stations = _merge_branches(
            [(archive, result) for archive, result in zip(paths, parsed, strict=True)]
        )
        data = _to_station_data(stations, dates)
        data = data.subset(_unique_positions(data))
        logger.info(
            "DWD CDC %s: %d stations with data for %s .. %s",
            variable,
            data.n_stations,
            start,
            end,
        )
        return data

    def last_available_day(self, variable: str) -> date:
        """Return the newest day the ``recent`` archives can hold for *variable*.

        The DWD publishes the previous day's values during the morning (UTC),
        so this is yesterday (UTC).  The actual last day of a station can be
        earlier.

        Parameters
        ----------
        variable : str
            Pipeline variable name, a key of :data:`CDC_PRODUCTS`.

        Returns
        -------
        date
            Yesterday's date in UTC.
        """
        del variable  # the same for every product
        return _utc_today() - timedelta(days=1)

    # ------------------------------------------------------------------
    # Downloading
    # ------------------------------------------------------------------

    @staticmethod
    def _branches_for(start: date, end: date) -> list[str]:
        """Return the branches that can hold days of ``[start, end]``.

        The historical branch reaches to the end of the previous year at most;
        the recent branch about 550 days back from today.
        """
        today = _utc_today()
        branches = []
        if start.year < today.year:
            branches.append("historical")
        if end >= today - _RECENT_SPAN:
            branches.append("recent")
        return branches

    def _list_archives(self, group: str, branch: str) -> list[_Archive]:
        """Return the station archives listed in one group/branch directory.

        Corrupt stations (:data:`CORRUPT_STATIONS`) are left out.

        Raises
        ------
        RuntimeError
            If the listing cannot be downloaded or contains no archives.
        """
        url = f"{CDC_DAILY_URL}/{group}/{branch}/"
        listing = self._http_get(url).decode("utf-8", errors="replace")
        corrupt = CORRUPT_STATIONS.get(group, frozenset())
        archives = []
        for name, _tag, station, first, last in sorted(
            set(_ARCHIVE_RE.findall(listing))
        ):
            if station in corrupt:
                continue
            archives.append(
                _Archive(
                    name,
                    station,
                    branch,
                    _parse_day(first) if first else None,
                    _parse_day(last) if last else None,
                )
            )
        if not archives:
            raise RuntimeError(f"No station archives found at {url}")
        return archives

    def _fetch_archives(
        self, group: str, archives: Iterable[_Archive]
    ) -> dict[_Archive, Path]:
        """Make sure every archive is in the cache; return the local paths.

        Historical archives that the server no longer lists (replaced by a
        newer file) are deleted from the cache.  Recent archives older than
        :attr:`recent_max_age` are downloaded again.
        """
        archives = list(archives)
        wanted: dict[_Archive, Path] = {}
        for archive in archives:
            directory = self.cache_dir / group / archive.branch
            directory.mkdir(parents=True, exist_ok=True)
            wanted[archive] = directory / archive.name
        self._remove_replaced(group, archives)

        stale_before = time.time() - self.recent_max_age.total_seconds()
        todo = [
            archive
            for archive, path in wanted.items()
            if not path.exists()
            or (archive.branch == "recent" and path.stat().st_mtime < stale_before)
        ]
        if todo:
            logger.info("DWD CDC %s: downloading %d archives", group, len(todo))

        def fetch(archive: _Archive) -> None:
            url = f"{CDC_DAILY_URL}/{group}/{archive.branch}/{archive.name}"
            path = wanted[archive]
            tmp = path.with_suffix(".part")
            tmp.write_bytes(self._http_get(url))
            tmp.replace(path)  # atomic: an interrupted download leaves no zip

        with ThreadPoolExecutor(self.workers) as pool:
            list(pool.map(fetch, todo))
        return wanted

    def _remove_replaced(self, group: str, archives: list[_Archive]) -> None:
        """Delete cached historical archives superseded by a listed newer file."""
        listed = {a.name for a in archives if a.branch == "historical"}
        stations = {a.station_id for a in archives if a.branch == "historical"}
        directory = self.cache_dir / group / "historical"
        if not directory.is_dir():
            return
        for path in directory.glob("tageswerte_*_hist.zip"):
            station = path.name.split("_")[2]
            if station in stations and path.name not in listed:
                path.unlink()

    def _http_get(self, url: str) -> bytes:
        """GET *url*, retrying network errors, rate limits and server errors.

        Raises
        ------
        RuntimeError
            If every attempt fails or the server answers with another error.
        """
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = self._session.get(url, timeout=60)
            except requests.RequestException as exc:
                if attempt == _MAX_ATTEMPTS:
                    raise RuntimeError(
                        f"DWD CDC request failed for {url}: {exc}"
                    ) from exc
            else:
                if response.status_code == 200:
                    return response.content
                if (
                    response.status_code not in _RETRY_STATUS
                    or attempt == _MAX_ATTEMPTS
                ):
                    raise RuntimeError(
                        f"DWD CDC returned HTTP {response.status_code} for {url}"
                    )
            time.sleep(_RETRY_BACKOFF_S * attempt)
        raise AssertionError("unreachable")


# ----------------------------------------------------------------------
# Reading archives
# ----------------------------------------------------------------------


@dataclass
class _StationSeries:
    """One station's metadata and its values on the requested days."""

    longitude: float
    latitude: float
    elevation: float
    values: np.ndarray  # (n_days,), NaN where missing


def _read_archive(
    path: Path, product: CDCProduct | DerivedCDCProduct, dates: pd.DatetimeIndex
) -> _StationSeries | None:
    """Read one station archive.

    Parameters
    ----------
    path : Path
        A ``tageswerte_*.zip`` archive.
    product : CDCProduct or DerivedCDCProduct
        Which column(s) to read and the plausible range.
    dates : pd.DatetimeIndex
        The requested days.

    Returns
    -------
    _StationSeries or None
        Position, elevation and values on *dates* (NaN where missing);
        ``None`` if the archive has no product or geography table, or its
        table lacks a column the product needs.
    """
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        product_name = next((n for n in names if n.startswith("produkt_")), None)
        geo_name = next(
            (n for n in names if n.startswith("Metadaten_Geographie_")), None
        )
        if product_name is None or geo_name is None:
            logger.warning("Skipping %s: no product or geography table", path.name)
            return None
        product_bytes = archive.read(product_name)
        geo_bytes = archive.read(geo_name)

    table = pd.read_csv(
        io.BytesIO(product_bytes),
        sep=";",
        skipinitialspace=True,
        dtype={"MESS_DATUM": str},
    )
    table.columns = [c.strip() for c in table.columns]
    raw = _read_product_values(table, product)
    if raw is None:
        return None
    days = pd.to_datetime(table["MESS_DATUM"], format="%Y%m%d")

    # Day offsets by integer arithmetic: archives are read in threads, and the
    # lazily built lookup engine of a shared pandas index is not thread-safe.
    index = (
        (days.to_numpy() - dates[0].to_datetime64()) // np.timedelta64(1, "D")
    ).astype(np.int64)
    inside = (index >= 0) & (index < len(dates))
    values = np.full(len(dates), np.nan)
    values[index[inside]] = raw[inside]

    longitude, latitude, elevation = _current_position(geo_bytes)
    return _StationSeries(longitude, latitude, elevation, values)


def _read_product_values(
    table: pd.DataFrame, product: CDCProduct | DerivedCDCProduct
) -> np.ndarray | None:
    """Return the product's value on each row of a product table.

    Parameters
    ----------
    table : pd.DataFrame
        The ``produkt_*.txt`` table, column names stripped.
    product : CDCProduct or DerivedCDCProduct
        A single column, or several combined by ``product.compute``.

    Returns
    -------
    np.ndarray or None
        float64 values, one per row, NaN where missing or implausible;
        ``None`` if a needed column is absent.
    """
    if isinstance(product, CDCProduct):
        return _read_column(table, product)
    inputs = [_read_column(table, column) for column in product.inputs]
    if any(values is None for values in inputs):
        return None
    values = np.asarray(product.compute(*inputs), dtype=float)
    implausible = (values < product.low) | (values > product.high)
    return np.where(implausible, np.nan, values)


def _read_column(table: pd.DataFrame, product: CDCProduct) -> np.ndarray | None:
    """Return one column as float64, NaN where missing (``-999``) or implausible.

    ``None`` if the table has no such column.
    """
    if product.column not in table.columns:
        return None
    raw = pd.to_numeric(table[product.column], errors="coerce").to_numpy(float)
    invalid = (raw == _CDC_MISSING) | (raw < product.low) | (raw > product.high)
    return np.where(invalid, np.nan, raw)  # a new array: never write into pandas


def _current_position(geo_bytes: bytes) -> tuple[float, float, float]:
    """Return ``(longitude, latitude, elevation)`` from the last geography row.

    The table (latin-1, ``;``-separated) lists the station's positions over
    time; the last row is the current one.
    """
    rows = [
        line
        for line in geo_bytes.decode("latin-1").replace("\r", "").split("\n")
        if line.strip()
    ]
    fields = [f.strip() for f in rows[-1].split(";")]
    # Columns: Stations_id; Stationshoehe; Geogr.Breite; Geogr.Laenge; ...
    return float(fields[3]), float(fields[2]), float(fields[1])


def _merge_branches(
    parsed: list[tuple[_Archive, _StationSeries | None]],
) -> dict[str, _StationSeries]:
    """Combine the historical and recent series of each station.

    Days with a historical value keep it; the recent archive fills the rest.
    The position is taken from the recent archive when there is one (it is
    the more current).
    """
    by_station: dict[str, dict[str, _StationSeries]] = {}
    for archive, series in parsed:
        if series is not None:
            by_station.setdefault(archive.station_id, {})[archive.branch] = series
    merged: dict[str, _StationSeries] = {}
    for station, branches in by_station.items():
        historical, recent = branches.get("historical"), branches.get("recent")
        if historical is not None and recent is not None:
            values = np.where(
                np.isnan(historical.values), recent.values, historical.values
            )
            merged[station] = _StationSeries(
                recent.longitude, recent.latitude, recent.elevation, values
            )
        else:
            merged[station] = historical or recent  # type: ignore[assignment]
    return merged


def _to_station_data(
    stations: dict[str, _StationSeries], dates: pd.DatetimeIndex
) -> StationData:
    """Stack the stations that have at least one valid value into a block."""
    ids = sorted(
        s for s, series in stations.items() if np.isfinite(series.values).any()
    )
    if not ids:
        empty = np.empty(0)
        return StationData(
            np.empty(0, dtype=str),
            empty,
            empty,
            empty,
            dates,
            np.empty((0, len(dates))),
        )
    series = [stations[s] for s in ids]
    return StationData(
        np.array(ids),
        np.array([s.longitude for s in series]),
        np.array([s.latitude for s in series]),
        np.array([s.elevation for s in series]),
        dates,
        np.vstack([s.values for s in series]),
    )


def _unique_positions(data: StationData) -> np.ndarray:
    """Mask that keeps one station per position (the one with most valid days).

    Two stations at the same position make the kriging system singular.
    Positions are compared to 1e-4 degrees (about 10 m).
    """
    keep = np.ones(data.n_stations, dtype=bool)
    valid_days = np.isfinite(data.values).sum(axis=1)
    groups: dict[tuple[float, float], list[int]] = {}
    positions = zip(
        np.round(data.longitude, 4).tolist(),
        np.round(data.latitude, 4).tolist(),
        strict=True,
    )
    for i, position in enumerate(positions):
        groups.setdefault(position, []).append(i)
    for members in groups.values():
        if len(members) > 1:
            # Most valid days wins; ties go to the higher station id (newer).
            best = max(members, key=lambda i: (valid_days[i], data.station_id[i]))
            keep[[i for i in members if i != best]] = False
    return keep


def _parse_day(text: str) -> date:
    """Parse a ``YYYYMMDD`` day."""
    return datetime.strptime(text, "%Y%m%d").date()


def _utc_today() -> date:
    """Return today's date in UTC."""
    return datetime.now(UTC).date()
