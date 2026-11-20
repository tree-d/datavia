"""Checks variogram.mojo against plain scalar references of the model value and the misfit.

    pixi run mojo run -I packages/kriging/datavia/kriging packages/kriging/tests/mojo/test_variogram.mojo
    (or, from the datavia root: pixi run test-kriging-mojo)
"""

from std.math import exp
from std.random import random_float64, seed

from kernels.empirical_variogram import EmpiricalVariogram
from kernels.variogram import (
    EXPONENTIAL_MODEL,
    SPHERICAL_MODEL,
    Variogram,
    _FitBins,
    _misfit,
    _parameters,
    variogram,
    variogram_value,
)


def reference_value(h: Float64, nugget: Float64, sill: Float64, a: Float64, vtype: Int) -> Float64:
    """Scalar model semivariance."""
    if h == 0.0:
        return nugget
    if vtype == 1:
        if h < a:
            var r = h / a
            return nugget + sill * (1.5 * r - 0.5 * r * r * r)
        return nugget + sill
    if a > 0.0:
        return nugget + sill * (1.0 - exp(-h / a))
    return nugget + sill


def reference_misfit(bins: EmpiricalVariogram, nugget: Float64, sill: Float64, a: Float64, vtype: Int) -> Float64:
    """Scalar misfit: sum of N_k |gamma_k - gamma(h_k)| over the bins with 0 < h <= 1."""
    var f: Float64 = 0.0
    for k in range(bins.bin_count):
        var lag = bins.lag[k]
        if lag > 0.0 and lag <= 1.0:
            f += abs(bins.semivariance[k] - reference_value(lag, nugget, sill, a, vtype)) * Float64(bins.pair_count[k])
    return f


def close(a: Float64, b: Float64, rel: Float64 = 1e-12) -> Bool:
    return abs(a - b) <= rel * max(abs(a), abs(b)) + 1e-300


def random_bins(bin_count: Int) -> EmpiricalVariogram:
    """Normalised bins, about a third of them empty (lag 0, no pairs)."""
    var bins = EmpiricalVariogram(bin_width=1.0, max_lag=Float64(bin_count))
    for k in range(bin_count):
        if random_float64() < 0.3:
            continue
        bins.pair_count[k] = 1 + Int(random_float64() * 500.0)
        bins.lag[k] = Float64(k + 1) / Float64(bin_count)
        bins.semivariance[k] = 0.1 + 0.9 * random_float64()
    bins.lag_scale = 1000.0 * Float64(bin_count)
    return bins^


def check_value() raises:
    var distances: List[Float64] = [0.0, 1.0, 500.0, 24_999.0, 25_000.0, 25_001.0, 1.0e6]
    for vtype in [SPHERICAL_MODEL, EXPONENTIAL_MODEL, 7]:
        for a in [0.0, 25_000.0]:
            var model = Variogram(0.1, 0.6, a, vtype)
            for h in distances:
                var expected = reference_value(h, 0.1, 0.6, a, vtype)
                if variogram_value(h, model) != expected:
                    raise Error(String("value: vtype=", vtype, " range=", a, " h=", h))
                var vector = model.value[4](SIMD[DType.float64, 4](h, h, h, h))
                for lane in range(4):
                    if vector[lane] != expected:
                        raise Error(String("vectorised value: vtype=", vtype, " range=", a, " h=", h))
    print("value ok")


def check_misfit() raises:
    for bin_count in [1, 3, 4, 5, 17, 80, 101]:
        var bins = random_bins(bin_count)
        var fit_bins = _FitBins(bins)
        for vtype in [SPHERICAL_MODEL, EXPONENTIAL_MODEL]:
            for _ in range(20):
                var nugget = 0.3 * random_float64()
                var sill = 5.0 * random_float64()
                var a = 0.0 if random_float64() < 0.1 else 2.0 * random_float64()
                var got = _misfit(fit_bins, _parameters(nugget, sill, a), vtype)
                var expected = reference_misfit(bins, nugget, sill, a, vtype)
                if got != expected:  # bit-identical, so the optimiser takes the same path
                    raise Error(String("misfit: bins=", bin_count, " vtype=", vtype, ": ", got, " vs ", expected))
    print("misfit ok")


def check_fit() raises:
    """The fit lies in the bounds, has its range in metres and improves on the start point."""
    for vtype in [SPHERICAL_MODEL, EXPONENTIAL_MODEL]:
        var bins = random_bins(80)
        var fit = variogram(empirical_variogram=bins, model_type=vtype)
        var best = fit.search.best_point
        if not (0.0 <= best[0] <= Float64(Float32(0.3)) and 0.0 <= best[1] <= 5.0 and 0.0 <= best[2] <= 2.0):
            raise Error(String("fit out of bounds: ", best))
        if fit.variogram.range != best[2] * bins.lag_scale or fit.variogram.model_type != vtype:
            raise Error("fit: range not scaled to metres")
        if not fit.search.misfit < reference_misfit(bins, 0.0, 1.0, 0.5, vtype):
            raise Error("fit: no better than the start point")
        print("fit vtype=", vtype, ": ", best, " misfit ", fit.search.misfit, ", ", fit.search.evaluation_count,
              " evaluations, ", fit.search.restart_count, " restarts, converged ", fit.search.converged, sep="")


def main() raises:
    seed(7)
    check_value()
    check_misfit()
    check_fit()
    print("all ok")
