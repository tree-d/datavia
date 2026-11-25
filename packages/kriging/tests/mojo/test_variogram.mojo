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


def reference_value(
    distance: Float64, nugget: Float64, sill: Float64, range_m: Float64, model_type: Int
) -> Float64:
    """Returns the model semivariance, computed scalar from the formulas.

    Args:
        distance: Distance, >= 0, in the unit of `range_m`.
        nugget: The nugget.
        sill: The partial sill.
        range_m: The range.
        model_type: `SPHERICAL_MODEL`, or any other value for exponential.

    Returns:
        The semivariance.
    """
    if distance == 0.0:
        return nugget
    if model_type == SPHERICAL_MODEL:
        if distance < range_m:
            var relative_distance = distance / range_m
            # One expression, as in the kernel, so the compiler fuses it the same way (bit-identical).
            return nugget + sill * (
                1.5 * relative_distance - 0.5 * relative_distance * relative_distance * relative_distance
            )
        return nugget + sill
    if range_m > 0.0:
        return nugget + sill * (1.0 - exp(-distance / range_m))
    return nugget + sill


def reference_misfit(
    bins: EmpiricalVariogram, nugget: Float64, sill: Float64, range_m: Float64, model_type: Int
) -> Float64:
    """Returns the misfit, computed scalar: sum of N_k |gamma_k - gamma(h_k)| over the bins with 0 < h_k <= 1.

    Args:
        bins: The normalised empirical variogram.
        nugget: The nugget.
        sill: The partial sill.
        range_m: The range, in normalised lag units.
        model_type: `SPHERICAL_MODEL` or `EXPONENTIAL_MODEL`.

    Returns:
        The pair-weighted L1 misfit.
    """
    var misfit: Float64 = 0.0
    for k in range(bins.bin_count):
        var lag = bins.lag[k]
        if lag > 0.0 and lag <= 1.0:
            misfit += abs(
                bins.semivariance[k] - reference_value(lag, nugget, sill, range_m, model_type)
            ) * Float64(bins.pair_count[k])
    return misfit


def random_bins(bin_count: Int) -> EmpiricalVariogram:
    """Returns random normalised bins, about a third of them empty (lag 0, no pairs).

    Args:
        bin_count: Number of bins.

    Returns:
        The bins, with `lag_scale` set to 1000 m per bin.
    """
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
    """Checks the scalar and vectorised model value against the reference, bit for bit.

    Covers both models, an unknown model type (treated as exponential), a range of
    0, and distances at and around the range.

    Raises:
        Error: If a value differs from the reference.
    """
    var distances: List[Float64] = [0.0, 1.0, 500.0, 24_999.0, 25_000.0, 25_001.0, 1.0e6]
    for model_type in [SPHERICAL_MODEL, EXPONENTIAL_MODEL, 7]:
        for range_m in [0.0, 25_000.0]:
            var model = Variogram(0.1, 0.6, range_m, model_type)
            for distance in distances:
                var expected = reference_value(distance, 0.1, 0.6, range_m, model_type)
                var label = String("model_type=", model_type, " range=", range_m, " distance=", distance)
                if variogram_value(distance, model) != expected:
                    raise Error(String("value: ", label))
                var vector = model.value[4](SIMD[DType.float64, 4](distance, distance, distance, distance))
                for lane in range(4):
                    if vector[lane] != expected:
                        raise Error(String("vectorised value: ", label))
    print("value ok")


def check_misfit() raises:
    """Checks that the vectorised misfit is bit-identical to the scalar reference.

    Bit-identical, so the optimiser takes the same path as a scalar implementation.

    Raises:
        Error: If a misfit differs from the reference.
    """
    for bin_count in [1, 3, 4, 5, 17, 80, 101]:
        var bins = random_bins(bin_count)
        var fit_bins = _FitBins(bins)
        for model_type in [SPHERICAL_MODEL, EXPONENTIAL_MODEL]:
            for _ in range(20):
                var nugget = 0.3 * random_float64()
                var sill = 5.0 * random_float64()
                var range_m = 0.0 if random_float64() < 0.1 else 2.0 * random_float64()
                var got = _misfit(fit_bins, _parameters(nugget, sill, range_m), model_type)
                var expected = reference_misfit(bins, nugget, sill, range_m, model_type)
                if got != expected:
                    raise Error(
                        String("misfit: bins=", bin_count, " model_type=", model_type, ": ", got, " vs ", expected)
                    )
    print("misfit ok")


def check_fit() raises:
    """Checks that the fit lies in the bounds, has its range in metres and improves on the start point.

    Raises:
        Error: If any of the three fails.
    """
    for model_type in [SPHERICAL_MODEL, EXPONENTIAL_MODEL]:
        var bins = random_bins(80)
        var fit = variogram(empirical_variogram=bins, model_type=model_type)
        var best = fit.search.best_point
        if not (0.0 <= best[0] <= Float64(Float32(0.3)) and 0.0 <= best[1] <= 5.0 and 0.0 <= best[2] <= 2.0):
            raise Error(String("fit out of bounds: ", best))
        if fit.variogram.range != best[2] * bins.lag_scale or fit.variogram.model_type != model_type:
            raise Error("fit: range not scaled to metres")
        if not fit.search.misfit < reference_misfit(bins, 0.0, 1.0, 0.5, model_type):
            raise Error("fit: no better than the start point")
        print("fit model_type=", model_type, ": ", best, " misfit ", fit.search.misfit, ", ",
              fit.search.evaluation_count, " evaluations, ", fit.search.restart_count, " restarts, converged ",
              fit.search.converged, sep="")


def main() raises:
    """Runs all checks with a fixed random seed.

    Raises:
        Error: If any check fails.
    """
    seed(7)
    check_value()
    check_misfit()
    check_fit()
    print("all ok")
