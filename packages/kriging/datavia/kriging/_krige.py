"""Kriging at points (:func:`krige`) and onto grids (:func:`krige_grid`).

Both functions are thin wrappers around the single function of the Mojo
extension ``_core`` (``_core.mojo``).  They

1. convert the inputs to contiguous float64 arrays, the only layout the
   extension accepts (it reads the numpy buffers in place, without copying);
2. collect the settings into the dict the extension expects, with a given
   variogram as ``(nugget, sill, range)``;
3. call the extension, which estimates the variogram (unless given) and
   kriges every target on every day on all CPU cores, with the GIL released;
4. wrap the returned dict in a :class:`~datavia.kriging.KrigingResult` and
   reshape the estimates: ``(n_points, n_days)`` for points,
   ``(n_days, ny, nx)`` for grids, without the day axis for single-day input.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np

from ._types import EXPONENTIAL, EmpiricalBins, KrigingResult, Variogram


def _extension() -> Any:
    """Return the Mojo extension module ``datavia.kriging._core``.

    On first use, ``mojo.importer`` compiles ``_core.mojo`` (and the kernels
    it imports) and caches the result in ``__mojocache__/`` next to it; later
    imports load the cached build.

    Returns
    -------
    module
        The extension module, whose ``krige`` function does the work.

    Raises
    ------
    ImportError
        If Mojo is not installed (``mojo.importer`` is missing).
    """
    try:
        import mojo.importer  # noqa: F401  (installs the .mojo import hook)
    except ImportError as exc:
        raise ImportError(
            "datavia.kriging needs Mojo and MAX: pip install 'datavia-kriging[mojo]',"
            " or install the conda packages 'mojo' and 'max' from the channel "
            "https://conda.modular.com/max"
        ) from exc

    from . import _core  # type: ignore[attr-defined]

    return _core


def _as_f64(array: Any) -> np.ndarray:
    """Return *array* as a contiguous float64 vector (not copied if it is one)."""
    return np.ascontiguousarray(array, dtype=np.float64).ravel()


def _run(
    cell_x: Any,
    cell_y: Any,
    cell_elevation: Any,
    station_x: Any,
    station_y: Any,
    station_elevation: Any,
    station_values: Any,
    *,
    variogram: Variogram | None,
    model_type: int,
    dem_nodata: float,
    **settings: Any,
) -> tuple[KrigingResult, bool]:
    """Call the Mojo extension and wrap its result.

    Parameters
    ----------
    cell_x, cell_y, cell_elevation : array_like
        Targets: easting, northing [m] and elevation [m]; flattened.
    station_x, station_y, station_elevation : array_like
        Stations: easting, northing [m] and elevation [m], 1-D.
    station_values : array_like
        ``(n_stations, n_days)`` or ``(n_stations,)`` station values.
    variogram : Variogram or None
        Variogram to use; ``None`` to estimate one with *model_type*.
    model_type : int
        Model to fit when *variogram* is ``None``.
    dem_nodata : float
        Targets whose rounded elevation equals this get ``OUT_NODATA``.
    **settings
        The remaining settings, as built by :func:`_settings`.

    Returns
    -------
    result : KrigingResult
        Estimates ``(n_targets, n_days)``, variogram, bins and counts.
    single_day : bool
        Whether *station_values* was 1-D (a single day).

    Raises
    ------
    ValueError
        If the arrays have inconsistent lengths (raised by the extension).
    ImportError
        If Mojo is not installed.
    """
    single_day = np.ndim(station_values) == 1
    values = np.asarray(station_values, dtype=np.float64)
    settings.update(dem_nodata=float(dem_nodata), model_type=int(model_type))
    if variogram is not None:
        settings.update(
            variogram=(variogram.nugget, variogram.sill, variogram.range),
            model_type=variogram.model_type,
        )
    r = _extension().krige(
        _as_f64(cell_x),
        _as_f64(cell_y),
        _as_f64(cell_elevation),
        _as_f64(station_x),
        _as_f64(station_y),
        _as_f64(station_elevation),
        np.ascontiguousarray(values.reshape(len(values), -1)),
        settings,
    )
    bins = None if r["bins"] is None else EmpiricalBins(*r["bins"])
    result = KrigingResult(
        r["estimates"],
        Variogram(*r["variogram"]),
        bins,
        r["converged"],
        r["singular"],
        r["sum_warning"],
    )
    return result, single_day


def _settings(
    nodata: float,
    max_dist: float,
    drift: bool,
    correct_negative: bool,
    dist_zero: bool,
    threshold_dist: float,
    bin_width: float,
    max_lag: float,
    max_elevation_gradient: float,
    min_pairs_per_bin: int,
) -> dict[str, Any]:
    """Return the settings dict of the extension, with every value typed.

    The parameters are those of :func:`krige`.

    Returns
    -------
    dict[str, Any]
        Plain Python floats, bools and ints, keyed as ``_core.krige`` expects.
    """
    return {
        "nodata": float(nodata),
        "max_dist": float(max_dist),
        "drift": bool(drift),
        "correct_negative": bool(correct_negative),
        "dist_zero": bool(dist_zero),
        "threshold_dist": float(threshold_dist),
        "bin_width": float(bin_width),
        "max_lag": float(max_lag),
        "max_elevation_gradient": float(max_elevation_gradient),
        "min_pairs_per_bin": int(min_pairs_per_bin),
    }


def krige(
    x: Any,
    y: Any,
    elevation: Any,
    station_x: Any,
    station_y: Any,
    station_elevation: Any,
    station_values: Any,
    *,
    nodata: float,
    max_dist: float,
    drift: bool = True,
    variogram: Variogram | None = None,
    model_type: int = EXPONENTIAL,
    correct_negative: bool = False,
    dist_zero: bool = False,
    threshold_dist: float = 0.0,
    bin_width: float = 1.0e3,
    max_lag: float = 8.0e4,
    max_elevation_gradient: float = 0.2,
    min_pairs_per_bin: int = 30,
) -> KrigingResult:
    """Krige station values at points.

    Without *variogram*, the empirical variogram is computed from all days of
    *station_values* pooled together and a model is fitted to it first; see
    the :mod:`datavia.kriging` module docstring for the whole method.

    All coordinates must be in one projected CRS with metres as unit (e.g.
    EPSG:3035 or a UTM zone); distances are Euclidean.

    Parameters
    ----------
    x, y : array_like
        Easting and northing [m] of the target points, 1-D, length
        ``n_points``.  May be empty: then only the variogram is estimated.
    elevation : array_like
        Elevation [m] of each target point: the drift value for external
        drift kriging.  Unused (but required) for ordinary kriging.
    station_x, station_y, station_elevation : array_like
        Easting, northing [m] and elevation [m] of the stations, 1-D, length
        ``n_stations``.  Stations must not share a position.
    station_values : array_like
        Station values, ``(n_stations, n_days)``, or ``(n_stations,)`` for a
        single day; missing values equal *nodata*.
    nodata : float
        Marks a missing station value.  Must be a number no real value can
        take (NaN is not supported).
    max_dist : float
        Search radius [m]: only stations within it of a target take part.
        Larger radii smooth more and cost more (the work per target grows
        with the cube of the number of stations in range).
    drift : bool, optional
        ``True`` (default): external drift kriging with the elevation as
        drift.  ``False``: ordinary kriging.
    variogram : Variogram, optional
        Use this variogram (e.g. :meth:`Variogram.load`) instead of estimating
        one.  Its ``model_type`` overrides *model_type*.
    model_type : int, optional
        Model to fit: :data:`~datavia.kriging.EXPONENTIAL` (default) or
        :data:`~datavia.kriging.SPHERICAL`.
    correct_negative : bool, optional
        Set negative estimates to 0, for non-negative variables like
        precipitation.  Default ``False``.
    dist_zero : bool, optional
        When a single station reports within *max_dist* and it is farther
        than *threshold_dist*, estimate 0 instead of copying its value (a lone
        distant rain gauge says little about whether it rained here).
        Default ``False``.
    threshold_dist : float, optional
        Distance [m] for *dist_zero*; typically half a grid cell diagonal.
        Default 0.
    bin_width : float, optional
        Width [m] of the distance bins of the empirical variogram.
        Default 1000.
    max_lag : float, optional
        Largest station separation [m] used for the empirical variogram.
        Default 80 000.
    max_elevation_gradient : float, optional
        Station pairs with ``|h_i - h_j| / distance`` above this are left out
        of the empirical variogram (their difference is dominated by the
        terrain, not by distance).  Default 0.2.
    min_pairs_per_bin : int, optional
        Bins with fewer station pairs are merged into a neighbouring bin, as
        their semivariance is too noisy to fit.  Default 30.

    Returns
    -------
    KrigingResult
        ``estimates`` is float32 ``(n_points, n_days)``, or ``(n_points,)``
        for single-day input; :data:`~datavia.kriging.OUT_NODATA` where a
        kriging system was singular.  ``variogram``, ``bins`` and
        ``converged`` describe the variogram; ``singular_count`` and
        ``sum_warning_count`` count problem systems.

    Raises
    ------
    ValueError
        If the array lengths do not match, or no station pair lies within
        *max_lag* (no variogram can be estimated).
    ImportError
        If Mojo is not installed.

    See Also
    --------
    krige_grid : The same onto a 2-D grid.

    Notes
    -----
    Every target is independent; the work is spread over all CPU cores the
    process may use, so restrict the process (e.g. ``taskset``) to use fewer.
    The result does not depend on the number of cores.

    Examples
    --------
    >>> import numpy as np
    >>> rng = np.random.default_rng(0)
    >>> sx, sy = rng.uniform(0, 5e4, 40), rng.uniform(0, 5e4, 40)
    >>> sh = rng.uniform(0, 500, 40)
    >>> z = 5 - 0.0065 * sh + rng.normal(0, 0.3, 40)  # one day
    >>> r = krige([2.5e4], [2.5e4], [250.0], sx, sy, sh, z,
    ...           nodata=-9999.0, max_dist=1e5)  # doctest: +SKIP
    >>> r.estimates.shape  # doctest: +SKIP
    (1,)
    """
    settings = _settings(
        nodata,
        max_dist,
        drift,
        correct_negative,
        dist_zero,
        threshold_dist,
        bin_width,
        max_lag,
        max_elevation_gradient,
        min_pairs_per_bin,
    )
    result, single_day = _run(
        x,
        y,
        elevation,
        station_x,
        station_y,
        station_elevation,
        station_values,
        variogram=variogram,
        model_type=model_type,
        dem_nodata=np.nan,  # points carry no DEM nodata marker
        **settings,
    )
    estimates = result.estimates[:, 0] if single_day else result.estimates
    return replace(result, estimates=estimates)


def krige_grid(
    easting: Any,
    northing: Any,
    dem: Any,
    station_x: Any,
    station_y: Any,
    station_elevation: Any,
    station_values: Any,
    *,
    nodata: float,
    dem_nodata: float,
    max_dist: float,
    drift: bool = True,
    variogram: Variogram | None = None,
    model_type: int = EXPONENTIAL,
    correct_negative: bool = False,
    dist_zero: bool = False,
    threshold_dist: float | None = None,
    bin_width: float = 1.0e3,
    max_lag: float = 8.0e4,
    max_elevation_gradient: float = 0.2,
    min_pairs_per_bin: int = 30,
) -> KrigingResult:
    """Krige station values onto every cell of a 2-D grid.

    The same as :func:`krige` with the grid's cells as targets, plus a DEM
    nodata value and a default single-station threshold from the grid.

    Parameters
    ----------
    easting, northing : array_like
        Cell-centre coordinates [m], 2-D ``(ny, nx)`` (the grid may be
        curvilinear; e.g. from ``np.meshgrid``).
    dem : array_like
        Elevation [m] of each cell, ``(ny, nx)``: the drift.
    station_x, station_y, station_elevation, station_values
        As for :func:`krige`.
    nodata : float
        Marks a missing station value.
    dem_nodata : float
        Cells whose elevation, rounded to an integer, equals this get
        :data:`~datavia.kriging.OUT_NODATA` on every day (outside the DEM).
        Use a finite marker such as -9999; NaN never matches.
    max_dist : float
        Search radius [m]; see :func:`krige`.
    threshold_dist : float, optional
        Distance [m] for *dist_zero*.  Default: half the cell diagonal, from
        the median cell spacing along each axis.
    drift, variogram, model_type, correct_negative, dist_zero, bin_width, \
max_lag, max_elevation_gradient, min_pairs_per_bin
        As for :func:`krige`.

    Returns
    -------
    KrigingResult
        ``estimates`` is float32 ``(n_days, ny, nx)``, or ``(ny, nx)`` for
        single-day input.

    Raises
    ------
    ValueError
        If *easting*, *northing* and *dem* are not 2-D arrays of one shape, or
        as for :func:`krige`.
    ImportError
        If Mojo is not installed.

    See Also
    --------
    krige : Kriging at points.
    """
    easting = np.asarray(easting, dtype=np.float64)
    northing = np.asarray(northing, dtype=np.float64)
    if (
        easting.ndim != 2
        or easting.shape != northing.shape
        or easting.shape != np.shape(dem)
    ):
        raise ValueError(
            "easting, northing and dem must be 2-D of one shape, got "
            f"{easting.shape}, {northing.shape}, {np.shape(dem)}"
        )
    if threshold_dist is None:
        threshold_dist = _half_cell_diagonal(easting, northing)
    settings = _settings(
        nodata,
        max_dist,
        drift,
        correct_negative,
        dist_zero,
        threshold_dist,
        bin_width,
        max_lag,
        max_elevation_gradient,
        min_pairs_per_bin,
    )
    result, single_day = _run(
        easting,
        northing,
        dem,
        station_x,
        station_y,
        station_elevation,
        station_values,
        variogram=variogram,
        model_type=model_type,
        dem_nodata=dem_nodata,
        **settings,
    )
    grid = result.estimates.reshape(*easting.shape, -1)
    estimates = grid[:, :, 0] if single_day else np.moveaxis(grid, -1, 0)
    return replace(result, estimates=estimates)


def _half_cell_diagonal(easting: np.ndarray, northing: np.ndarray) -> float:
    """Return half the diagonal of a grid cell.

    Uses the median distance between neighbouring cell centres along each
    axis, so a slightly irregular grid works too.

    Parameters
    ----------
    easting, northing : np.ndarray
        Cell-centre coordinates [m], 2-D ``(ny, nx)``.

    Returns
    -------
    float
        ``0.5 * hypot(dx, dy)`` in metres; a dimension with a single cell
        contributes 0.
    """
    dx = (
        np.median(np.hypot(np.diff(easting, axis=1), np.diff(northing, axis=1)))
        if easting.shape[1] > 1
        else 0.0
    )
    dy = (
        np.median(np.hypot(np.diff(easting, axis=0), np.diff(northing, axis=0)))
        if easting.shape[0] > 1
        else 0.0
    )
    return 0.5 * float(np.hypot(dx, dy))
