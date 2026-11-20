"""Python extension module `datavia.kriging._core`: the one function through which data passes
between Python and the Mojo library.

    krige(cell_x, cell_y, cell_elevation,
          station_x, station_y, station_elevation, station_values, settings) -> dict

It holds no computation of its own: it checks and reads the arrays, then runs

  1. variogram  (only if `settings` gives none) the empirical variogram of all days
                (kernels/empirical_variogram.mojo) and the model fit (kernels/variogram.mojo),
  2. kriging    every cell on every day: kernels/external_drift_kriging.mojo if
                `settings["drift"]`, else kernels/ordinary_kriging.mojo.

Both steps run on all CPUs the process may use, with the GIL released. The Python
package (`datavia.kriging`, see `_krige.py`) wraps this for points (`krige`) and
grids (`krige_grid`).
"""

from std.memory import Pointer
from std.os import abort
from std.python import Python, PythonObject
from std.python.bindings import PythonModuleBuilder

from kernels.empirical_variogram import EmpiricalVariogram, empirical_variogram
from kernels.external_drift_kriging import external_drift_kriging
from kernels.ordinary_kriging import KrigingCounts, KrigingSettings, ordinary_kriging
from kernels.variogram import Variogram, variogram

comptime _F64Ptr = Pointer[Float64, MutUntrackedOrigin]
comptime _F32Ptr = Pointer[Float32, MutUntrackedOrigin]


def _float64_data(numpy: PythonObject, array: PythonObject, name: String, ndim: Int) raises -> _F64Ptr:
    """The data of `array`, which must be a C-contiguous float64 numpy array with `ndim` dimensions."""
    if not Bool(py=Python.import_module("builtins").isinstance(array, numpy.ndarray)):
        raise Error(String(name, " must be a numpy array"))
    if Int(py=array.ndim) != ndim:
        raise Error(String(name, " must have ", ndim, " dimension(s), not ", Int(py=array.ndim)))
    if not Bool(py=array.dtype == numpy.float64):
        raise Error(String(name, " must be float64, not ", String(py=array.dtype.name)))
    if not Bool(py=array.flags["C_CONTIGUOUS"]):
        raise Error(String(name, " must be C-contiguous"))
    return _F64Ptr(unsafe_from_address=Int(py=array.ctypes.data))


def _require_length(array: PythonObject, name: String, axis: Int, length: Int) raises:
    """Raises unless `array` has `length` entries along `axis`."""
    if Int(py=array.shape[axis]) != length:
        raise Error(String(name, " has ", Int(py=array.shape[axis]), " entries along axis ", axis, ", expected ", length))


def _populated_bins(numpy: PythonObject, bins: EmpiricalVariogram) raises -> PythonObject:
    """(lag [m], semivariance, pair count) of the populated bins, in physical units, as numpy arrays."""
    var lag = Python.list()
    var semivariance = Python.list()
    var pair_count = Python.list()
    for bin in range(bins.bin_count):
        if bins.is_populated(bin):
            lag.append(PythonObject(bins.lag[bin] * bins.lag_scale))
            semivariance.append(PythonObject(bins.semivariance[bin] * bins.semivariance_scale))
            pair_count.append(PythonObject(bins.pair_count[bin]))
    return Python.tuple(
        numpy.array(lag, dtype=numpy.float64),
        numpy.array(semivariance, dtype=numpy.float64),
        numpy.array(pair_count, dtype=numpy.int64),
    )


def krige(
    cell_x: PythonObject,
    cell_y: PythonObject,
    cell_elevation: PythonObject,
    station_x: PythonObject,
    station_y: PythonObject,
    station_elevation: PythonObject,
    station_values: PythonObject,
    settings: PythonObject,
) raises -> PythonObject:
    """Kriging of station values onto cells, estimating the variogram first unless one is given.

    Args:
        cell_x: Cell eastings [m], 1-D float64, C-contiguous; may be empty (then only
            the variogram is computed).
        cell_y: Cell northings [m], like `cell_x`.
        cell_elevation: Cell elevations [m] (the drift), like `cell_x`; cells whose
            rounded elevation equals `dem_nodata` get `OUT_NODATA`.
        station_x: Station eastings [m], 1-D float64, C-contiguous.
        station_y: Station northings [m], like `station_x`.
        station_elevation: Station elevations [m], like `station_x`.
        station_values: 2-D float64, C-contiguous `(station_count, day_count)`;
            missing values equal `nodata`.
        settings: A dict with the keys `nodata`, `dem_nodata`, `max_dist` [m],
            `threshold_dist` [m], `correct_negative`, `dist_zero`, `drift` (True:
            external drift kriging, False: ordinary kriging), `model_type` (1 spherical,
            2 exponential), and either `variogram` = (nugget, sill, range [m]) or, if
            that is missing or None, `bin_width` [m], `max_lag` [m],
            `max_elevation_gradient` and `min_pairs_per_bin` for estimating it.

    Returns:
        A dict: `estimates` (float32 numpy array `(cell_count, day_count)`), `variogram`
        (nugget, sill, range [m], model type; nugget and sill of an estimated variogram
        are in normalised units), `bins` (lag [m],
        semivariance, pair count of the populated bins, or None if the variogram was
        given), `converged` (whether the fit converged, or None), `singular` and
        `sum_warning` (kriging systems that were singular, or whose weights did not
        sum to 1).

    Raises:
        Error: if an array has the wrong type, shape or layout, a setting is missing,
            or no station pair qualifies for the variogram.
    """
    var numpy = Python.import_module("numpy")
    var x = _float64_data(numpy, cell_x, "cell_x", 1)
    var y = _float64_data(numpy, cell_y, "cell_y", 1)
    var elevation = _float64_data(numpy, cell_elevation, "cell_elevation", 1)
    var sx = _float64_data(numpy, station_x, "station_x", 1)
    var sy = _float64_data(numpy, station_y, "station_y", 1)
    var sh = _float64_data(numpy, station_elevation, "station_elevation", 1)
    var values = _float64_data(numpy, station_values, "station_values", 2)
    var cell_count = Int(py=cell_x.shape[0])
    var station_count = Int(py=station_x.shape[0])
    _require_length(cell_y, "cell_y", 0, cell_count)
    _require_length(cell_elevation, "cell_elevation", 0, cell_count)
    _require_length(station_y, "station_y", 0, station_count)
    _require_length(station_elevation, "station_elevation", 0, station_count)
    _require_length(station_values, "station_values", 0, station_count)
    var day_count = Int(py=station_values.shape[1])

    var nodata = Float64(py=settings["nodata"])
    var model_type = Int(py=settings["model_type"])
    var kriging_settings = KrigingSettings(
        max_dist=Float64(py=settings["max_dist"]),
        nodata=nodata,
        dem_nodata=Float64(py=settings["dem_nodata"]),
        correct_negative=Bool(py=settings["correct_negative"]),
        dist_zero=Bool(py=settings["dist_zero"]),
        threshold_dist=Float64(py=settings["threshold_dist"]),
    )
    var drift = Bool(py=settings["drift"])

    var result = Python.dict()
    var model: Variogram
    var given = settings.get("variogram")
    ref cpython = Python().cpython()
    if given is None:
        var bin_width = Float64(py=settings["bin_width"])
        var max_lag = Float64(py=settings["max_lag"])
        var max_elevation_gradient = Float64(py=settings["max_elevation_gradient"])
        var min_pairs_per_bin = Int(py=settings["min_pairs_per_bin"])
        var gil = cpython.PyEval_SaveThread()
        var bins = empirical_variogram(
            x=sx, y=sy, elevation=sh, values=values, station_count=station_count, day_count=day_count,
            nodata=nodata, bin_width=bin_width, max_lag=max_lag,
            max_elevation_gradient=max_elevation_gradient, min_pairs_per_bin=min_pairs_per_bin,
        )
        cpython.PyEval_RestoreThread(gil)
        if not bins.any_populated():
            raise Error("no station pairs within max_lag: cannot estimate a variogram")
        var fit = variogram(empirical_variogram=bins, model_type=model_type)
        model = fit.variogram
        result["bins"] = _populated_bins(numpy, bins)
        result["converged"] = PythonObject(fit.search.converged)
    else:
        model = Variogram(Float64(py=given[0]), Float64(py=given[1]), Float64(py=given[2]), model_type)
        result["bins"] = Python.none()
        result["converged"] = Python.none()

    var estimates = numpy.empty(Python.tuple(cell_count, day_count), dtype=numpy.float32)
    var estimates_data = _F32Ptr(unsafe_from_address=Int(py=estimates.ctypes.data))
    var counts = KrigingCounts(0, 0)
    var gil = cpython.PyEval_SaveThread()
    if drift:
        counts = external_drift_kriging(
            cell_x=x, cell_y=y, cell_elevation=elevation, cell_count=cell_count,
            station_x=sx, station_y=sy, station_elevation=sh, station_values=values,
            station_count=station_count, day_count=day_count,
            model=model, settings=kriging_settings, estimates=estimates_data,
        )
    else:
        counts = ordinary_kriging(
            cell_x=x, cell_y=y, cell_elevation=elevation, cell_count=cell_count,
            station_x=sx, station_y=sy, station_elevation=sh, station_values=values,
            station_count=station_count, day_count=day_count,
            model=model, settings=kriging_settings, estimates=estimates_data,
        )
    cpython.PyEval_RestoreThread(gil)

    result["estimates"] = estimates
    result["variogram"] = Python.tuple(model.nugget, model.sill, model.range, model.model_type)
    result["singular"] = PythonObject(counts.singular)
    result["sum_warning"] = PythonObject(counts.sum_warning)
    return result


@export
def PyInit__core() abi("C") -> PythonObject:
    """Module initialiser called by Python on `import datavia.kriging._core`.

    Returns:
        The module object, with the single function `krige`.
    """
    try:
        var m = PythonModuleBuilder("_core")
        m.def_function[krige]("krige", docstring="Variogram (unless given) and kriging of station values onto cells")
        return m.finalize()
    except e:
        abort(String("error creating Python module datavia.kriging._core: ", e))
