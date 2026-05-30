r"""
CoverageManager — incremental download coverage tracker for weather data.

Determines which ``(bbox, date_start, date_end)`` cells are already present
in ``weather_layers`` and returns only the uncovered portions so that
:class:`~datavia.weather.pipeline.WeatherPipeline` can issue the minimum
number of downloader calls.

Coverage is treated as a 2-D problem (spatial x temporal).  Spatial
operations use :mod:`shapely` for correctness; the *4-strip coordinate
decomposition* converts the spatial remainder back into exact non-overlapping
axis-aligned bounding boxes, avoiding any polygon-approximation error.

Algorithm overview
------------------
:meth:`CoverageManager.missing_spatiotemporal` maintains a list of pending
``(bbox, date_range)`` pieces, starting from the full requested cell.  For
each existing coverage cell it subtracts the covered portion:

1. **Before temporal overlap** — pieces with dates entirely before the
   existing cell starts are kept untouched.
2. **After temporal overlap** — pieces with dates entirely after the
   existing cell ends are kept untouched.
3. **Spatial remainder during temporal overlap** — the pending bbox minus the
   existing cell's bbox is expressed as up to four axis-aligned strips
   (left, right, bottom-centre, top-centre) via :func:`_four_strip_bboxes`.
   Together these strips cover ``pending_bbox \ existing_bbox`` exactly.

Each returned :class:`CoverageCell` maps to one downloader call.
"""

import datetime
import logging
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    import xarray as xr

    from .zarr_store_manager import ZarrStoreManager

import pandas as pd
from shapely import box as shapely_box
from shapely.wkt import loads as wkt_loads
from sqlalchemy import text

from datavia.library.database.connection import session_local
from datavia.library.database.query import get_weather_metadata

logger = logging.getLogger(__name__)

_ONE_DAY: datetime.timedelta = datetime.timedelta(days=1)


# ---------------------------------------------------------------------------
# Public data classes
# ---------------------------------------------------------------------------


class CoverageCell(NamedTuple):
    """A single ``(bbox, date_range)`` unit from :class:`CoverageManager`.

    Each instance corresponds to one downloader call and covers a contiguous
    spatial bounding box and date range that is missing from the Zarr store.
    """

    #: Bounding box as ``(west, south, east, north)`` in EPSG:4326 degrees.
    bbox: tuple[float, float, float, float]
    #: Inclusive start of the required date range (ISO date, ``YYYY-MM-DD``).
    date_start: str
    #: Inclusive end of the required date range (ISO date, ``YYYY-MM-DD``).
    date_end: str


# ---------------------------------------------------------------------------
# Private data classes
# ---------------------------------------------------------------------------


class _ExistingCell(NamedTuple):
    """An existing coverage cell loaded from ``weather_layers``.

    All fields are normalised at load time.

    Attributes
    ----------
    bbox : tuple[float, float, float, float]
        Bounding box as ``(west, south, east, north)`` in EPSG:4326 degrees.
    date_start : datetime.date
        Inclusive start of the covered date range.
    date_end : datetime.date
        Inclusive end of the covered date range.
    """

    bbox: tuple[float, float, float, float]
    date_start: datetime.date
    date_end: datetime.date


# ---------------------------------------------------------------------------
# CoverageManager
# ---------------------------------------------------------------------------


class CoverageManager:
    """Compute uncovered ``(bbox, date_range)`` cells for incremental downloads.

    Loads existing coverage from ``weather_layers`` on construction and answers:
    *"Given what is already registered in the database, which cells still
    need to be downloaded?"*

    A ``(bbox, date_range)`` piece is **missing** if it is uncovered for *at
    least one* configured variable.  Per-variable missing sets are unioned and
    deduplicated before being returned.

    Parameters
    ----------
    source_name : str
        Pipeline source name, e.g. ``"ERA5_land"`` or ``"HYRAS"``.
    variables : list[str]
        Variable names to track coverage for.
    """

    def __init__(
        self,
        source_name: str,
        variables: list[str],
        data_dir: str | None = None,
    ) -> None:
        """Load existing coverage cells from ``weather_layers`` for all variables.

        When *data_dir* is provided (or discoverable from ``get_config()``),
        the constructor also performs an auto-rebuild: if a Zarr store exists
        on disk for any ``(source_name, variable)`` but has no corresponding
        DB rows, :meth:`rebuild_from_store` is called automatically to
        repopulate the DB before any missing-cell computation is done.

        Parameters
        ----------
        source_name : str
            Pipeline source name used to filter ``weather_layers`` rows.
        variables : list[str]
            Variables to check.  Coverage is computed per variable and unioned.
        data_dir : str, optional
            Root data directory.  When ``None``, resolved from
            ``get_config().data_directory``.

        Raises
        ------
        ValueError
            If *variables* is empty.
        """
        if not variables:
            raise ValueError(
                "CoverageManager requires at least one variable in 'variables'."
            )

        if data_dir is None:
            try:
                from datavia.config import get_config

                data_dir = str(get_config().data_directory)
            except Exception:
                data_dir = None

        self._source_name: str = source_name
        self._variables: list[str] = list(variables)
        self._data_dir: str | None = data_dir
        self._cells_per_variable: dict[str, list[_ExistingCell]] = {}
        self._load_existing_cells()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def missing_spatiotemporal(
        self,
        bbox: tuple[float, float, float, float],
        date_start: str,
        date_end: str,
    ) -> list[CoverageCell]:
        """Return cells not yet covered for any configured variable.

        Each returned :class:`CoverageCell` should produce exactly one
        downloader call.  The 2-D subtraction algorithm guarantees that no
        already-registered ``(bbox, date_range)`` area is included in the
        result, so no redundant downloads are triggered for data that is
        already present.

        For the spatial remainder, :mod:`shapely` is used to compute the
        intersection bounding box; the *4-strip decomposition* then converts
        the remainder into at most four axis-aligned bbox strips with exact
        coordinate arithmetic (no polygon approximation).

        Parameters
        ----------
        bbox : tuple[float, float, float, float]
            Requested area as ``(west, south, east, north)`` in EPSG:4326
            degrees.
        date_start : str
            Start of the requested date range.  Accepts ISO date
            (``YYYY-MM-DD``) or ISO-8601 datetime; only the date part is used.
        date_end : str
            End of the requested date range.  Must be >= *date_start*.

        Returns
        -------
        list[CoverageCell]
            Deduplicated list of cells that still need to be fetched.  Returns
            an empty list when the full request is already covered for all
            configured variables.

        Raises
        ------
        ValueError
            If *date_start* is later than *date_end*.
        """
        req_start = _parse_date(date_start)
        req_end = _parse_date(date_end)
        if req_start > req_end:
            raise ValueError(
                f"date_start ({date_start!r}) must not be "
                f"after date_end ({date_end!r})."
            )

        seen: set[CoverageCell] = set()
        result: list[CoverageCell] = []

        for variable in self._variables:
            existing_cells = self._cells_per_variable.get(variable, [])
            for cell in _compute_missing(bbox, req_start, req_end, existing_cells):
                if cell not in seen:
                    seen.add(cell)
                    result.append(cell)

        logger.debug(
            "CoverageManager: %d missing cell(s) for '%s' in [%s, %s].",
            len(result),
            self._source_name,
            date_start,
            date_end,
        )
        return result

    def rebuild_from_store(self, variable: str) -> int:
        """Repopulate ``weather_layers`` from the Zarr store contents.

        Scans the Zarr store(s) for *variable* month by month.  For each
        calendar month that contains at least one non-NaN value, a
        ``weather_layers`` row is inserted recording the covered bounding box
        and time range.  Months with only fill-value data are skipped.

        If the store directory carries a ``.write_in_progress`` sentinel (left
        by a previous interrupted write), the sentinel is removed after the
        scan completes, regardless of how many months were successfully
        registered.  This implements the OQ-3 Option (b) recovery strategy
        without discarding any already-good data.

        Safe to call multiple times — duplicate rows are deleted before
        insertion so the result is always consistent with the actual store
        contents.

        Parameters
        ----------
        variable : str
            Datavia variable name to rebuild coverage for.

        Returns
        -------
        int
            Number of ``weather_layers`` rows inserted.

        Raises
        ------
        KeyError
            If *variable*'s source has no ``zarr_grid`` entry.
        """
        if self._data_dir is None:
            logger.warning(
                "rebuild_from_store: data_dir is not set; skipping rebuild for %s/%s.",
                self._source_name,
                variable,
            )
            return 0

        from .zarr_store_manager import _SENTINEL, ZarrStoreManager

        mgr = ZarrStoreManager(self._data_dir, self._source_name)
        source_root = Path(self._data_dir) / self._source_name / variable
        if not source_root.exists():
            return 0

        rows_inserted = 0
        for store_dir in sorted(source_root.glob("*.zarr")):
            try:
                year = int(store_dir.stem)
            except ValueError:
                logger.warning("Skipping unexpected store directory: %s", store_dir)
                continue

            sentinel = store_dir / _SENTINEL
            try:
                rows_inserted += self._rebuild_year(mgr, variable, year)
            except Exception as exc:
                logger.warning(
                    "rebuild_from_store: error scanning %s/%s/%d: %s",
                    self._source_name,
                    variable,
                    year,
                    exc,
                )
            finally:
                # Remove sentinel regardless of scan outcome so the store is
                # no longer blocked for reads (OQ-3 Option b behaviour).
                if sentinel.exists():
                    try:
                        sentinel.unlink()
                        logger.info(
                            "Removed stale .write_in_progress sentinel from %s.",
                            store_dir,
                        )
                    except OSError as err:
                        logger.warning(
                            "Could not remove sentinel from %s: %s", store_dir, err
                        )

        if rows_inserted:
            # Reload cells so subsequent missing_spatiotemporal calls reflect
            # the newly inserted rows without requiring a new CoverageManager.
            self._load_existing_cells()

        logger.info(
            "rebuild_from_store: inserted %d row(s) for %s/%s.",
            rows_inserted,
            self._source_name,
            variable,
        )
        return rows_inserted

    def _rebuild_year(self, mgr: ZarrStoreManager, variable: str, year: int) -> int:
        """Scan one year store month by month and insert DB rows for covered months.

        Parameters
        ----------
        mgr : ZarrStoreManager
            Store manager configured for this source and data directory.
        variable : str
            Datavia variable name.
        year : int
            Calendar year.

        Returns
        -------
        int
            Number of rows inserted for this year.
        """
        import xarray as xr

        path = mgr.store_path(variable, year)
        ds = xr.open_zarr(str(path), consolidated=False)
        rows = 0

        try:
            for month in range(1, 13):
                month_start = f"{year}-{month:02d}-01"
                last_day = (
                    pd.Timestamp(f"{year}-{month:02d}-01") + pd.offsets.MonthEnd(0)
                ).strftime("%Y-%m-%d")
                month_end = last_day

                da_month = ds[variable].sel(time=slice(month_start, month_end))
                if da_month.sizes.get("time", 0) == 0:
                    continue

                has_data = bool(da_month.notnull().any().compute())
                if not has_data:
                    continue

                bbox = self._bbox_from_notnull(da_month)
                self._insert_coverage_row(
                    variable=variable,
                    year=year,
                    valid_from=month_start + "T00:00:00",
                    valid_until=month_end + "T23:00:00",
                    bbox=bbox,
                    store_uri=str(mgr.store_path(variable, year)),
                )
                rows += 1
        finally:
            ds.close()

        return rows

    def _bbox_from_notnull(self, da: xr.DataArray) -> str:
        """Return a WKT POLYGON bbox for all lat/lon cells
        with at least one non-NaN value.

        Parameters
        ----------
        da : xr.DataArray
            Subset DataArray with ``latitude`` and ``longitude`` dimensions.

        Returns
        -------
        str
            WKT POLYGON in EPSG:4326, or the full grid extent as fallback.
        """
        has_data = da.notnull().any(dim="time").compute()

        if "latitude" in da.dims and "longitude" in da.dims:
            # Geographic store (EPSG:4326): dimensions are lat/lon in degrees.
            lat_mask = has_data.any(dim="longitude")
            lon_mask = has_data.any(dim="latitude")
            lats = has_data.latitude.values[lat_mask.values]
            lons = has_data.longitude.values[lon_mask.values]
            if len(lats) == 0 or len(lons) == 0:
                lats = da.latitude.values
                lons = da.longitude.values
            west = float(lons.min())
            east = float(lons.max())
            south = float(lats.min())
            north = float(lats.max())
            return (
                f"POLYGON (({west} {south}, {east} {south}, "
                f"{east} {north}, {west} {north}, {west} {south}))"
            )

        # Projected store (e.g. HYRAS EPSG:3035): dimensions are x/y in metres.
        # Reproject the bbox corners to WGS84 for database storage.
        from .source_registry import SOURCE_REGISTRY
        from .zarr_store_manager import _projected_bbox_to_wgs84

        zarr_crs = (
            SOURCE_REGISTRY.get(self._source_name, {})
            .get("zarr_grid", {})
            .get("crs", "EPSG:3035")
        )
        return _projected_bbox_to_wgs84(has_data, crs=zarr_crs, as_tuple=False)

    def _insert_coverage_row(
        self,
        variable: str,
        year: int,
        valid_from: str,
        valid_until: str,
        bbox: str,
        store_uri: str,
    ) -> None:
        """Insert or replace a ``weather_layers`` row for a recovered Zarr month.

        Parameters
        ----------
        variable : str
            Datavia variable name.
        year : int
            Calendar year.
        valid_from : str
            ISO-8601 start of the covered period.
        valid_until : str
            ISO-8601 end of the covered period.
        bbox : str
            WKT POLYGON bounding box in EPSG:4326.
        store_uri : str
            Absolute path to the Zarr store directory.
        """
        layer_name = f"{self._source_name}_{variable}_{year}_rebuilt"
        acquisition_time = datetime.datetime.now(datetime.UTC).isoformat()

        session = session_local()
        try:
            session.execute(
                text(
                    "DELETE FROM weather_layers "
                    "WHERE layer_name = :layer_name AND source_name = :source_name"
                ),
                {"layer_name": layer_name, "source_name": self._source_name},
            )
            session.execute(
                text(
                    """
                    INSERT INTO weather_layers
                        (layer_name, source_name, variable, file_format,
                         valid_from, valid_until, uri,
                         acquisition_time, bbox, crs, metadata)
                    VALUES
                        (:layer_name, :source_name, :variable, :file_format,
                         :valid_from, :valid_until, :uri,
                         :acquisition_time, :bbox, :crs, :metadata)
                    """
                ),
                {
                    "layer_name": layer_name,
                    "source_name": self._source_name,
                    "variable": variable,
                    "file_format": "zarr",
                    "valid_from": valid_from,
                    "valid_until": valid_until,
                    "uri": store_uri,
                    "acquisition_time": acquisition_time,
                    "bbox": bbox,
                    "crs": "EPSG:4326",
                    "metadata": (
                        '{"file_format": "zarr", "source": "rebuild_from_store"}'
                    ),
                },
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_existing_cells(self) -> None:
        """Populate ``_cells_per_variable`` from ``weather_layers``.

        After loading, checks whether any configured variable has Zarr stores
        on disk but zero DB rows.  When this is detected (e.g. after a DB
        loss or a fresh checkout), :meth:`rebuild_from_store` is called
        automatically so that subsequent missing-cell computations are correct
        and no redundant CDS downloads are issued.

        Skips rows with missing ``bbox``, ``valid_from``, or ``valid_until``.
        Logs a warning for rows whose fields cannot be parsed rather than
        raising, so that a single malformed DB row does not block all downloads.
        """
        for variable in self._variables:
            rows = get_weather_metadata(self._source_name, variable)
            cells: list[_ExistingCell] = []
            for row in rows:
                if not (
                    row.get("bbox") and row.get("valid_from") and row.get("valid_until")
                ):
                    logger.debug(
                        "Skipping incomplete coverage row for %s/%s "
                        "(missing bbox or dates).",
                        self._source_name,
                        variable,
                    )
                    continue
                try:
                    bbox = _parse_bbox_wkt(row["bbox"])
                    date_start = _parse_date(row["valid_from"])
                    date_end = _parse_date(row["valid_until"])
                except Exception as exc:
                    logger.warning(
                        "Skipping malformed coverage row for %s/%s: %s",
                        self._source_name,
                        variable,
                        exc,
                    )
                    continue
                cells.append(
                    _ExistingCell(bbox=bbox, date_start=date_start, date_end=date_end)
                )
            self._cells_per_variable[variable] = cells
            logger.debug(
                "CoverageManager: loaded %d existing cell(s) for %s/%s.",
                len(cells),
                self._source_name,
                variable,
            )

        # Auto-rebuild: if any variable has Zarr stores on disk but zero DB
        # rows, repopulate from the store before the first coverage query.
        if self._data_dir is not None:
            for variable in self._variables:
                if self._cells_per_variable.get(variable):
                    continue  # DB rows present — no rebuild needed.
                var_root = Path(self._data_dir) / self._source_name / variable
                if var_root.exists() and any(var_root.glob("*.zarr")):
                    logger.info(
                        "CoverageManager: Zarr store found for %s/%s with no DB rows; "
                        "triggering rebuild_from_store.",
                        self._source_name,
                        variable,
                    )
                    try:
                        self.rebuild_from_store(variable)
                    except Exception as exc:
                        logger.warning(
                            "Auto-rebuild failed for %s/%s: %s",
                            self._source_name,
                            variable,
                            exc,
                        )


# ---------------------------------------------------------------------------
# Private module-level helpers
# ---------------------------------------------------------------------------


def _parse_date(date_str: str) -> datetime.date:
    """Parse an ISO date or ISO-8601 datetime string to a :class:`datetime.date`.

    Only the first ten characters (``YYYY-MM-DD``) are used, so both bare
    date strings and full datetime strings (e.g. ``"2024-12-31T23:59:59"``)
    are accepted.

    Parameters
    ----------
    date_str : str
        ISO date (``YYYY-MM-DD``) or ISO-8601 datetime string.

    Returns
    -------
    datetime.date
        Parsed date.

    Raises
    ------
    ValueError
        If the first 10 characters cannot be parsed as a date.
    """
    return datetime.date.fromisoformat(date_str[:10])


def _parse_bbox_wkt(wkt: str) -> tuple[float, float, float, float]:
    """Parse a WKT POLYGON string to a ``(west, south, east, north)`` tuple.

    Parameters
    ----------
    wkt : str
        WKT POLYGON bounding box in EPSG:4326 as stored in ``weather_layers``,
        e.g. ``"POLYGON ((5.9 47.3, 15.0 47.3, 15.0 55.1, 5.9 55.1, 5.9 47.3))"``.

    Returns
    -------
    tuple[float, float, float, float]
        Bounding box as ``(west, south, east, north)``,
        i.e. ``(min_lon, min_lat, max_lon, max_lat)``.

    Raises
    ------
    shapely.errors.WKTReadingError
        If *wkt* is not a valid WKT geometry string.
    """
    poly = wkt_loads(wkt)
    west, south, east, north = poly.bounds
    return (west, south, east, north)


def _compute_missing(
    bbox: tuple[float, float, float, float],
    date_start: datetime.date,
    date_end: datetime.date,
    existing_cells: list[_ExistingCell],
) -> list[CoverageCell]:
    """Compute ``(bbox, date_range)`` pieces not covered by *existing_cells*.

    Uses an iterative 2-D subtraction algorithm:

    1. Start with one pending piece: ``(bbox, date_start, date_end)``.
    2. For each existing cell that overlaps the pending piece both spatially
       and temporally, split the pending piece into up to six sub-pieces:

       - *Before temporal overlap*: full pending bbox, dates before the
         existing cell starts.
       - *After temporal overlap*: full pending bbox, dates after the
         existing cell ends.
       - *Spatial strips during temporal overlap*: up to four axis-aligned
         bounding-box strips (see :func:`_four_strip_bboxes`) covering the
         pending bbox minus the existing cell's bbox.

    Repeats step 2 for all existing cells until no pending pieces remain or
    all existing cells have been processed.

    :mod:`shapely` is used to check spatial intersection and to compute the
    intersection bounding box; all strip arithmetic then uses exact coordinate
    operations.

    Parameters
    ----------
    bbox : tuple[float, float, float, float]
        Requested bounding box as ``(west, south, east, north)`` in EPSG:4326.
    date_start : datetime.date
        Inclusive start of the requested date range.
    date_end : datetime.date
        Inclusive end of the requested date range.
    existing_cells : list[_ExistingCell]
        Existing coverage cells to subtract from the request.

    Returns
    -------
    list[CoverageCell]
        Uncovered cells.  Each cell's ``bbox`` is ``(west, south, east, north)``
        and dates are ISO date strings (``YYYY-MM-DD``).
    """
    # Each pending piece is a 6-tuple: (west, south, east, north, t_start, t_end)
    pending: list[tuple[float, float, float, float, datetime.date, datetime.date]] = [
        (*bbox, date_start, date_end)  # type: ignore[misc]
    ]

    for existing in existing_cells:
        if not pending:
            break

        ex_bbox = existing.bbox
        ex_start = existing.date_start
        ex_end = existing.date_end
        existing_box = shapely_box(*ex_bbox)

        new_pending: list[
            tuple[float, float, float, float, datetime.date, datetime.date]
        ] = []

        for piece in pending:
            p_w, p_s, p_e, p_n, p_start, p_end = piece

            # --- 1. Check temporal overlap ---
            t_overlap_start = max(p_start, ex_start)
            t_overlap_end = min(p_end, ex_end)
            if t_overlap_start > t_overlap_end:
                # No temporal overlap: piece is entirely outside this cell.
                new_pending.append(piece)
                continue

            # --- 2. Check spatial overlap (Shapely for robustness) ---
            piece_box = shapely_box(p_w, p_s, p_e, p_n)
            if not piece_box.intersects(existing_box):
                # No spatial overlap: piece is entirely outside this cell.
                new_pending.append(piece)
                continue

            # --- 3. Both axes overlap — decompose the pending piece ---

            # 3a. Before temporal overlap: full bbox, dates before existing starts.
            if p_start < t_overlap_start:
                new_pending.append(
                    (p_w, p_s, p_e, p_n, p_start, t_overlap_start - _ONE_DAY)
                )

            # 3b. After temporal overlap: full bbox, dates after existing ends.
            if p_end > t_overlap_end:
                new_pending.append(
                    (p_w, p_s, p_e, p_n, t_overlap_end + _ONE_DAY, p_end)
                )

            # 3c. Spatial remainder during temporal overlap.
            #     Compute the intersection bounds via Shapely, then apply the
            #     4-strip coordinate decomposition for exact axis-aligned strips.
            intersection = piece_box.intersection(existing_box)
            if intersection.is_empty:
                # Touching polygons: no actual overlap area.
                new_pending.append((p_w, p_s, p_e, p_n, t_overlap_start, t_overlap_end))
                continue

            xi1, yi1, xi2, yi2 = intersection.bounds
            for strip in _four_strip_bboxes(p_w, p_s, p_e, p_n, xi1, yi1, xi2, yi2):
                new_pending.append((*strip, t_overlap_start, t_overlap_end))  # type: ignore[misc]

        pending = new_pending

    return [
        CoverageCell(
            bbox=(p_w, p_s, p_e, p_n),
            date_start=str(p_start),
            date_end=str(p_end),
        )
        for p_w, p_s, p_e, p_n, p_start, p_end in pending
    ]


def _four_strip_bboxes(
    p_w: float,
    p_s: float,
    p_e: float,
    p_n: float,
    xi1: float,
    yi1: float,
    xi2: float,
    yi2: float,
) -> list[tuple[float, float, float, float]]:
    """Decompose a bbox minus an interior intersection into up to four strips.

    Given a pending bounding box ``(p_w, p_s, p_e, p_n)`` and the bounds of
    its intersection with an existing cell ``(xi1, yi1, xi2, yi2)``, return
    the axis-aligned strips that cover the pending box outside the intersection
    with no gaps and no overlaps.

    The four candidate strips are:

    - **Left**: ``p_w`` to ``xi1``, full height ``p_s`` to ``p_n``.
    - **Right**: ``xi2`` to ``p_e``, full height ``p_s`` to ``p_n``.
    - **Bottom-centre**: ``xi1`` to ``xi2``, from ``p_s`` to ``yi1``.
    - **Top-centre**: ``xi1`` to ``xi2``, from ``yi2`` to ``p_n``.

    A strip is omitted when its width or height is zero (i.e. the existing
    cell reaches the boundary of the pending piece in that direction).

    Parameters
    ----------
    p_w, p_s, p_e, p_n : float
        Bounds of the pending piece ``(west, south, east, north)``.
    xi1, yi1, xi2, yi2 : float
        Bounds of the intersection with the existing cell
        ``(west, south, east, north)``.

    Returns
    -------
    list[tuple[float, float, float, float]]
        List of ``(west, south, east, north)`` strip bboxes.  Empty when the
        intersection fully covers the pending piece (the existing cell contains
        the pending piece).
    """
    strips: list[tuple[float, float, float, float]] = []

    if p_w < xi1:  # Left strip: pending's west edge to intersection's west edge.
        strips.append((p_w, p_s, xi1, p_n))
    if xi2 < p_e:  # Right strip: intersection's east edge to pending's east edge.
        strips.append((xi2, p_s, p_e, p_n))
    if p_s < yi1:  # Bottom-centre: below intersection, between its east/west edges.
        strips.append((xi1, p_s, xi2, yi1))
    if yi2 < p_n:  # Top-centre: above intersection, between its east/west edges.
        strips.append((xi1, yi2, xi2, p_n))

    return strips
