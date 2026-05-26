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

from __future__ import annotations

import datetime
import logging
from typing import NamedTuple

from shapely import box as shapely_box
from shapely.wkt import loads as wkt_loads

from datavia.library.database.query import get_weather_metadata

logger = logging.getLogger(__name__)

_ONE_DAY: datetime.timedelta = datetime.timedelta(days=1)


# ---------------------------------------------------------------------------
# Public data classes
# ---------------------------------------------------------------------------


class CoverageCell(NamedTuple):
    """A single ``(bbox, date_range)`` download unit returned by :class:`CoverageManager`.

    Attributes
    ----------
    bbox : tuple[float, float, float, float]
        Bounding box as ``(west, south, east, north)`` in EPSG:4326 degrees.
    date_start : str
        Inclusive start of the required date range (ISO date, ``YYYY-MM-DD``).
    date_end : str
        Inclusive end of the required date range (ISO date, ``YYYY-MM-DD``).
    """

    bbox: tuple[float, float, float, float]
    date_start: str
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

    def __init__(self, source_name: str, variables: list[str]) -> None:
        """Load existing coverage cells from ``weather_layers`` for all variables.

        Parameters
        ----------
        source_name : str
            Pipeline source name used to filter ``weather_layers`` rows.
        variables : list[str]
            Variables to check.  Coverage is computed per variable and unioned.

        Raises
        ------
        ValueError
            If *variables* is empty.
        """
        if not variables:
            raise ValueError(
                "CoverageManager requires at least one variable in 'variables'."
            )

        self._source_name: str = source_name
        self._variables: list[str] = list(variables)
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
                f"date_start ({date_start!r}) must not be after date_end ({date_end!r})."
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

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_existing_cells(self) -> None:
        """Populate ``_cells_per_variable`` from ``weather_layers``.

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
                        "Skipping incomplete coverage row for %s/%s (missing bbox or dates).",
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
