"""Theoretical variogram fitted to an empirical variogram.

Kriging needs the semivariance at any distance, not only at the bin distances of
the empirical variogram. A model (nugget + spherical or nugget + exponential) is
therefore fitted to the bins, and kriging uses the model.

It contains:

  * `Variogram`             the model (nugget, sill, range, model type) and its
                            semivariance at a distance, scalar or vectorised,
  * `_misfit_kernel`        a vectorised kernel: how badly a model fits the bins,
  * `_NelderMeadSearch`     the state of the optimiser, one method per simplex
                            operation (reflect, expand, contract, shrink, ...),
  * `nelder_mead_bounded`   finds the model parameters with the smallest misfit,
  * `variogram`             fits a model to an `EmpiricalVariogram` with fixed
                            start point, bounds and stopping rule.

Pipeline (each step is one function or struct):

  1. collect bins   the populated, normalised bins are copied into contiguous,
                    vector-padded arrays (`_FitBins`)
  2. minimise       Nelder-Mead searches (nugget, sill, range) for the smallest
                    pair-weighted misfit, evaluating `_misfit_kernel` at every step
  3. scale          the fitted range is converted back from normalised lags to metres

The misfit is vectorised over the bins. It is not spread over threads: every
Nelder-Mead step depends on the previous one, and one misfit evaluation covers
only a few dozen bins, less work than handing it to another thread costs.
"""

from std.math import exp
from std.sys import simd_width_of

from kernels.empirical_variogram import EmpiricalVariogram

comptime _VECTOR_SIZE = simd_width_of[DType.float64]()

# Model types. The numbers are part of the Python API (`SPHERICAL`, `EXPONENTIAL`).
comptime SPHERICAL_MODEL = 1
"""Nugget + spherical model."""
comptime EXPONENTIAL_MODEL = 2
"""Nugget + exponential model."""

comptime VariogramParameters = SIMD[DType.float64, 4]
"""The parameters the fit searches, as one vector: (nugget, sill, range, unused).
The fourth lane only pads the vector to a power of two; it is kept at 0."""


def _parameters(nugget: Float64, sill: Float64, range: Float64) -> VariogramParameters:
    """`VariogramParameters` from its three parameters."""
    return VariogramParameters(nugget, sill, range, 0.0)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


def _semivariance_of_model[model_type: Int, width: Int](
    model: Variogram, distance: SIMD[DType.float64, width]
) -> SIMD[DType.float64, width]:
    """Semivariance of the model at `distance` > 0.

    Spherical: rises as 1.5 r - 0.5 r^3 with r = distance / range and stays at
    nugget + sill from the range on. Exponential: approaches nugget + sill as
    1 - exp(-distance / range). A range of 0 gives nugget + sill everywhere.
    """
    var plateau = SIMD[DType.float64, width](model.nugget + model.sill)
    comptime if model_type == SPHERICAL_MODEL:
        var relative_distance = distance / model.range
        var rising = model.nugget + model.sill * (
            1.5 * relative_distance - 0.5 * relative_distance * relative_distance * relative_distance
        )
        return distance.lt(model.range).select(rising, plateau)
    else:
        if model.range > 0.0:
            return model.nugget + model.sill * (1.0 - exp(-distance / model.range))
        return plateau


@fieldwise_init
struct Variogram(Copyable, ImplicitlyCopyable, Movable):
    """Theoretical variogram: semivariance as a function of distance.

    At distance 0 the semivariance is the nugget, not 0, so the kriging matrix has the
    nugget on its diagonal and measurement noise is not reproduced exactly. With
    distance it rises by up to `sill` more, reaching (spherical) or approaching
    (exponential) `nugget + sill` at about `range`.
    """

    var nugget: Float64
    """Semivariance at distance 0: measurement noise and small-scale variation."""
    var sill: Float64
    """Partial sill: how much the semivariance rises above the nugget."""
    var range: Float64
    """Distance [m] at which the rise ends (spherical) or its scale (exponential)."""
    var model_type: Int
    """`SPHERICAL_MODEL` or `EXPONENTIAL_MODEL`; any other value means exponential."""

    def value[width: Int](self, distance: SIMD[DType.float64, width]) -> SIMD[DType.float64, width]:
        """Semivariance at `distance` [m], for `width` distances at once.

        Parameters:
            width: Number of distances (SIMD lanes); 1 for a single distance.

        Args:
            distance: Distances [m], each >= 0.

        Returns:
            The semivariance at each distance: the nugget at distance 0, otherwise the
            model (see `_semivariance_of_model`).
        """
        var at_distance: SIMD[DType.float64, width]
        if self.model_type == SPHERICAL_MODEL:
            at_distance = _semivariance_of_model[SPHERICAL_MODEL, width](self, distance)
        else:
            at_distance = _semivariance_of_model[EXPONENTIAL_MODEL, width](self, distance)
        return distance.eq(0.0).select(SIMD[DType.float64, width](self.nugget), at_distance)


def variogram_value(distance: Float64, model: Variogram) -> Float64:
    """Semivariance of `model` at `distance` [m].

    Args:
        distance: Distance [m], >= 0.
        model: The variogram.

    Returns:
        `model.value[1](distance)`, as a scalar.
    """
    return model.value[1](distance)


# ---------------------------------------------------------------------------
# Kernel
# ---------------------------------------------------------------------------


struct _FitBins(Movable):
    """The bins the fit uses, contiguous and padded to a whole number of vectors.

    Only bins with a normalised lag in (0, 1] are kept: empty bins have
    lag 0. They keep their order. The padding has weight 0, so it adds nothing to
    the misfit; its lag is 1, where every model is finite.
    """

    var lag: List[Float64]
    """Normalised lag of each bin."""
    var semivariance: List[Float64]
    """Normalised semivariance of each bin."""
    var pair_weight: List[Float64]
    """Number of station pairs in each bin: bins with more pairs count more."""
    var padded_count: Int
    """Number of entries, padding included: a multiple of the vector size."""

    def __init__(out self, bins: EmpiricalVariogram):
        self.lag = List[Float64]()
        self.semivariance = List[Float64]()
        self.pair_weight = List[Float64]()
        for bin in range(bins.bin_count):
            var lag = bins.lag[bin]
            if lag > 0.0 and lag <= 1.0:
                self.lag.append(lag)
                self.semivariance.append(bins.semivariance[bin])
                self.pair_weight.append(Float64(bins.pair_count[bin]))
        self.padded_count = ((len(self.lag) + _VECTOR_SIZE - 1) // _VECTOR_SIZE) * _VECTOR_SIZE
        while len(self.lag) < self.padded_count:
            self.lag.append(1.0)
            self.semivariance.append(0.0)
            self.pair_weight.append(0.0)


def _misfit_kernel[model_type: Int, vector_size: Int](fit_bins: _FitBins, model: Variogram) -> Float64:
    """Pair-weighted L1 misfit: sum over the bins of
    pairs * |empirical semivariance - model semivariance at the bin's lag|.

    The model semivariances and differences are computed for a whole vector of bins
    at once. The model type is a compile-time parameter, so the choice of model is
    made once, outside the loop.

    The weighted differences are added to the total one at a time, in bin order, each
    as a fused multiply-add: this is exactly what the scalar loop compiles to, so the
    misfit is bit-identical to it. Summing per vector lane changes the misfit in the
    last bits, and on flat stretches of the misfit that is enough to send Nelder-Mead
    down a different path (in tests, to a range up to 30% different).
    """
    var lags = fit_bins.lag.unsafe_ptr()
    var semivariances = fit_bins.semivariance.unsafe_ptr()
    var pair_weights = fit_bins.pair_weight.unsafe_ptr()
    var misfit: Float64 = 0.0
    for first in range(0, fit_bins.padded_count, vector_size):
        var modelled = _semivariance_of_model[model_type, vector_size](
            model, lags.unsafe_load[width=vector_size](offset=first)
        )
        var observed = semivariances.unsafe_load[width=vector_size](offset=first)
        var differences = abs(observed - modelled)
        var weights = pair_weights.unsafe_load[width=vector_size](offset=first)
        comptime for lane in range(vector_size):
            misfit = differences[lane].fma(weights[lane], misfit)
    return misfit


def _misfit(fit_bins: _FitBins, parameters: VariogramParameters, model_type: Int) -> Float64:
    """Misfit of the model with `parameters` to the bins (runs `_misfit_kernel`)."""
    var model = Variogram(parameters[0], parameters[1], parameters[2], model_type)
    if model_type == SPHERICAL_MODEL:
        return _misfit_kernel[SPHERICAL_MODEL, _VECTOR_SIZE](fit_bins, model)
    return _misfit_kernel[EXPONENTIAL_MODEL, _VECTOR_SIZE](fit_bins, model)


# ---------------------------------------------------------------------------
# Optimiser: bounded Nelder-Mead
# ---------------------------------------------------------------------------


# Fixed settings of the optimiser.
comptime _PARAMETER_COUNT = 3
"""Parameters searched: nugget, sill, range."""
comptime _VERTEX_COUNT = _PARAMETER_COUNT + 1
"""Points of the simplex."""
comptime _REFLECTION_FACTOR = 1.0
comptime _EXPANSION_FACTOR = 2.0
comptime _CONTRACTION_FACTOR = 0.5
comptime _LOCAL_MINIMUM_PROBE = 0.001
"""Probe step of the local-minimum test, and simplex size after a restart, as a
fraction of `initial_step`."""


@fieldwise_init
struct NelderMeadResult(Copyable, ImplicitlyCopyable, Movable):
    """Outcome of `nelder_mead_bounded`."""

    var best_point: VariogramParameters
    """Parameters with the smallest misfit found."""
    var misfit: Float64
    """Misfit at `best_point`."""
    var evaluation_count: Int
    """Number of misfit evaluations used."""
    var restart_count: Int
    """How often the search was restarted because the result was not a local minimum."""
    var converged: Bool
    """False if the evaluation budget ran out, or the last result was not a local minimum."""


def _clamp(
    point: VariogramParameters, lower_bound: VariogramParameters, upper_bound: VariogramParameters
) -> VariogramParameters:
    """`point` moved into the box [lower_bound, upper_bound], parameter by parameter."""
    return min(upper_bound, max(lower_bound, point))


def _index_of_smallest(values: List[Float64]) -> Int:
    """Index of the first smallest entry."""
    var smallest = 0
    for index in range(1, len(values)):
        if values[index] < values[smallest]:
            smallest = index
    return smallest


def _index_of_largest(values: List[Float64]) -> Int:
    """Index of the first largest entry."""
    var largest = 0
    for index in range(1, len(values)):
        if values[index] > values[largest]:
            largest = index
    return largest


def _centroid_without(simplex: List[VariogramParameters], left_out: Int) -> VariogramParameters:
    """Mean of all vertices of the simplex except `left_out`."""
    var total = VariogramParameters(0.0)
    for vertex in range(len(simplex)):
        total += simplex[vertex]
    return (total - simplex[left_out]) / Float64(_PARAMETER_COUNT)


struct _NelderMeadSearch(Movable):
    """State of a bounded Nelder-Mead search: the simplex, the misfit at each of its
    points, and how many misfit evaluations were spent.

    Each method is one simplex operation. Every point the search
    evaluates is first moved into [lower_bound, upper_bound].
    """

    var fit_bins: _FitBins
    """The bins whose misfit is minimised."""
    var model_type: Int
    """`SPHERICAL_MODEL` or `EXPONENTIAL_MODEL`."""
    var lower_bound: VariogramParameters
    """Smallest allowed value of each parameter."""
    var upper_bound: VariogramParameters
    """Largest allowed value of each parameter."""
    var simplex: List[VariogramParameters]
    """The `_VERTEX_COUNT` points of the simplex."""
    var vertex_misfit: List[Float64]
    """Misfit at each point of the simplex."""
    var best_vertex: Int
    """The point with the smallest misfit."""
    var best_misfit: Float64
    """The smallest misfit of the simplex, as last updated. Normally the misfit at
    `best_vertex`; it stays at the old value if the best point is also the worst
    one (all misfits equal) and is replaced by a worse one."""
    var evaluation_count: Int
    """Misfit evaluations so far, over all restarts."""
    var steps_until_convergence_check: Int
    """Counts down to the next convergence check; carried over restarts."""

    def __init__(
        out self,
        *,
        bins: EmpiricalVariogram,
        model_type: Int,
        lower_bound: VariogramParameters,
        upper_bound: VariogramParameters,
        convergence_check_interval: Int,
    ):
        self.fit_bins = _FitBins(bins)
        self.model_type = model_type
        self.lower_bound = lower_bound
        self.upper_bound = upper_bound
        self.simplex = List[VariogramParameters](length=_VERTEX_COUNT, fill=VariogramParameters(0.0))
        self.vertex_misfit = List[Float64](length=_VERTEX_COUNT, fill=0.0)
        self.best_vertex = 0
        self.best_misfit = 0.0
        self.evaluation_count = 0
        self.steps_until_convergence_check = convergence_check_interval

    # --- small helpers ---

    def _within_bounds(self, point: VariogramParameters) -> VariogramParameters:
        """`point` moved into [lower_bound, upper_bound]."""
        return _clamp(point, self.lower_bound, self.upper_bound)

    def _evaluate(mut self, point: VariogramParameters) -> Float64:
        """Misfit at `point`, counted as one evaluation."""
        self.evaluation_count += 1
        return _misfit(self.fit_bins, point, self.model_type)

    def _replace_vertex(mut self, vertex: Int, point: VariogramParameters, misfit: Float64):
        """Puts `point`, whose misfit is `misfit`, in place of `vertex`."""
        self.simplex[vertex] = point
        self.vertex_misfit[vertex] = misfit

    def _find_best_vertex(mut self):
        """Sets `best_vertex` and `best_misfit` from all points of the simplex."""
        self.best_vertex = _index_of_smallest(self.vertex_misfit)
        self.best_misfit = self.vertex_misfit[self.best_vertex]

    def _misfit_spread(self) -> Float64:
        """Sum of squared deviations of the vertex misfits from their mean."""
        var mean_misfit: Float64 = 0.0
        for vertex in range(_VERTEX_COUNT):
            mean_misfit += self.vertex_misfit[vertex]
        mean_misfit /= Float64(_VERTEX_COUNT)
        var spread: Float64 = 0.0
        for vertex in range(_VERTEX_COUNT):
            spread += (self.vertex_misfit[vertex] - mean_misfit) * (self.vertex_misfit[vertex] - mean_misfit)
        return spread

    # --- simplex operations ---

    def start_simplex(mut self, *, centre: VariogramParameters, step: VariogramParameters):
        """A new simplex: `centre` itself, plus one point per parameter, moved by
        `step` along that parameter (only that parameter is moved into the bounds)."""
        var centre_misfit = self._evaluate(centre)
        self._replace_vertex(_PARAMETER_COUNT, centre, centre_misfit)
        for parameter in range(_PARAMETER_COUNT):
            var stepped = centre
            stepped[parameter] = min(
                self.upper_bound[parameter], max(self.lower_bound[parameter], centre[parameter] + step[parameter])
            )
            var stepped_misfit = self._evaluate(stepped)
            self._replace_vertex(parameter, stepped, stepped_misfit)
        self._find_best_vertex()

    def _expand_or_reflect(
        mut self, worst_vertex: Int, centroid: VariogramParameters,
        reflected: VariogramParameters, reflected_misfit: Float64,
    ):
        """The reflected point beats the best point: try going twice as far, and keep
        whichever of the two is better."""
        var expanded = self._within_bounds(centroid + _EXPANSION_FACTOR * (reflected - centroid))
        var expanded_misfit = self._evaluate(expanded)
        if reflected_misfit < expanded_misfit:
            self._replace_vertex(worst_vertex, reflected, reflected_misfit)
        else:
            self._replace_vertex(worst_vertex, expanded, expanded_misfit)

    def _contract_from_reflected_side(
        mut self, worst_vertex: Int, centroid: VariogramParameters,
        reflected: VariogramParameters, reflected_misfit: Float64,
    ):
        """The reflected point beats only the worst point: try halfway between the
        centroid and the reflected point, and keep whichever of the two is better."""
        var contracted = self._within_bounds(centroid + _CONTRACTION_FACTOR * (reflected - centroid))
        var contracted_misfit = self._evaluate(contracted)
        if contracted_misfit <= reflected_misfit:
            self._replace_vertex(worst_vertex, contracted, contracted_misfit)
        else:
            self._replace_vertex(worst_vertex, reflected, reflected_misfit)

    def _contract_from_worst_side(mut self, worst_vertex: Int, centroid: VariogramParameters) -> Bool:
        """The reflected point is worse than every point: try halfway between the
        centroid and the worst point. If that is worse still, shrink the whole simplex.

        Returns True if the simplex was shrunk.
        """
        var contracted = self._within_bounds(centroid + _CONTRACTION_FACTOR * (self.simplex[worst_vertex] - centroid))
        var contracted_misfit = self._evaluate(contracted)
        if self.vertex_misfit[worst_vertex] < contracted_misfit:
            self._shrink_towards_best()
            return True
        self._replace_vertex(worst_vertex, contracted, contracted_misfit)
        return False

    def _shrink_towards_best(mut self):
        """Moves every point halfway towards the best point, then finds the new best."""
        for vertex in range(_VERTEX_COUNT):
            var shrunk = self._within_bounds((self.simplex[vertex] + self.simplex[self.best_vertex]) * 0.5)
            var shrunk_misfit = self._evaluate(shrunk)
            self._replace_vertex(vertex, shrunk, shrunk_misfit)
        self._find_best_vertex()

    def _step(mut self, worst_vertex: Int) -> Bool:
        """One Nelder-Mead step: reflect the worst point through the centroid of the
        others, then expand, contract or shrink depending on how good that is.

        Returns True if the simplex was shrunk (which ends the step at once).
        """
        var centroid = _centroid_without(self.simplex, worst_vertex)
        var reflected = self._within_bounds(
            centroid + _REFLECTION_FACTOR * (centroid - self.simplex[worst_vertex])
        )
        var reflected_misfit = self._evaluate(reflected)

        if reflected_misfit < self.best_misfit:
            self._expand_or_reflect(worst_vertex, centroid, reflected, reflected_misfit)
            return False

        var vertices_worse_than_reflected = 0
        for vertex in range(_VERTEX_COUNT):
            if reflected_misfit < self.vertex_misfit[vertex]:
                vertices_worse_than_reflected += 1
        if vertices_worse_than_reflected > 1:
            self._replace_vertex(worst_vertex, reflected, reflected_misfit)
            return False
        if vertices_worse_than_reflected == 0:
            return self._contract_from_worst_side(worst_vertex, centroid)
        self._contract_from_reflected_side(worst_vertex, centroid, reflected, reflected_misfit)
        return False

    def run_until_converged(
        mut self, *, convergence_check_interval: Int, convergence_threshold: Float64, max_evaluations: Int
    ):
        """Steps until the vertex misfits spread by no more than `convergence_threshold`
        (checked every `convergence_check_interval` steps) or the budget is used up."""
        while self.evaluation_count < max_evaluations:
            var worst_vertex = _index_of_largest(self.vertex_misfit)
            if self._step(worst_vertex):
                continue
            if self.vertex_misfit[worst_vertex] < self.best_misfit:
                self.best_misfit = self.vertex_misfit[worst_vertex]
                self.best_vertex = worst_vertex

            self.steps_until_convergence_check -= 1
            if self.steps_until_convergence_check > 0:
                continue
            if self.evaluation_count <= max_evaluations:
                self.steps_until_convergence_check = convergence_check_interval
                if self._misfit_spread() <= convergence_threshold:
                    return

    # --- result ---

    def best_point(self) -> VariogramParameters:
        """The best point of the simplex."""
        return self._within_bounds(self.simplex[self.best_vertex])

    def misfit_at_best_point(self) -> Float64:
        """The misfit at the best point of the simplex."""
        return self.vertex_misfit[self.best_vertex]

    def is_local_minimum(
        mut self, mut point: VariogramParameters, misfit: Float64, initial_step: VariogramParameters
    ) -> Bool:
        """Local-minimum test: a small step up or down in any parameter must not give
        a misfit below `misfit`. The steps are made on `point` itself: on failure it
        holds the better probe point; on success each parameter went up one step,
        down two and up one again (all within the bounds)."""
        for parameter in range(_PARAMETER_COUNT):
            var probe = initial_step[parameter] * _LOCAL_MINIMUM_PROBE
            point[parameter] = point[parameter] + probe
            point = self._within_bounds(point)
            if self._evaluate(point) < misfit:
                return False
            point[parameter] = point[parameter] - probe - probe
            point = self._within_bounds(point)
            if self._evaluate(point) < misfit:
                return False
            point[parameter] = point[parameter] + probe
            point = self._within_bounds(point)
        return True


def nelder_mead_bounded(
    *,
    bins: EmpiricalVariogram,
    model_type: Int,
    start: VariogramParameters,
    lower_bound: VariogramParameters,
    upper_bound: VariogramParameters,
    initial_step: VariogramParameters,
    min_misfit_variance: Float64,
    convergence_check_interval: Int,
    max_evaluations: Int,
) -> NelderMeadResult:
    """Finds the (nugget, sill, range) whose model fits the normalised bins best.

    A Nelder-Mead simplex search: a simplex of 4 points (one more than there are
    parameters) moves through the parameter space by replacing its worst point with
    a reflected, expanded or contracted one, or shrinks towards its best point. Every
    trial point is moved into [lower_bound, upper_bound]. Every
    `convergence_check_interval` steps, the search stops if the misfits at the
    simplex points vary by less than `min_misfit_variance`. The result must then
    pass a local-minimum test (a small step up and down in each parameter must not
    improve the misfit); if it fails, the search restarts there with a small simplex.
    The search is deterministic: the same bins always give the same path and result.

    Args:
        bins: The normalised empirical variogram (after `normalise`).
        model_type: `SPHERICAL_MODEL` or `EXPONENTIAL_MODEL`.
        start: Where the search starts.
        lower_bound: Smallest allowed value of each parameter.
        upper_bound: Largest allowed value of each parameter.
        initial_step: Size of the first simplex along each parameter.
        min_misfit_variance: Convergence threshold, per parameter.
        convergence_check_interval: Steps between two convergence checks.
        max_evaluations: Budget of misfit evaluations.
    """
    var search = _NelderMeadSearch(
        bins=bins, model_type=model_type, lower_bound=lower_bound, upper_bound=upper_bound,
        convergence_check_interval=convergence_check_interval,
    )
    var convergence_threshold = min_misfit_variance * Float64(_PARAMETER_COUNT)
    var restart_point = start
    var step_scale: Float64 = 1.0
    var restart_count = 0

    while True:
        search.start_simplex(centre=restart_point, step=initial_step * step_scale)
        search.run_until_converged(
            convergence_check_interval=convergence_check_interval,
            convergence_threshold=convergence_threshold,
            max_evaluations=max_evaluations,
        )
        var best_point = search.best_point()
        var best_misfit = search.misfit_at_best_point()

        if search.evaluation_count > max_evaluations:
            return NelderMeadResult(best_point, best_misfit, search.evaluation_count, restart_count, False)
        if search.is_local_minimum(best_point, best_misfit, initial_step):
            return NelderMeadResult(best_point, best_misfit, search.evaluation_count, restart_count, True)

        # Restart from the point that failed the test, with a small simplex.
        restart_point = _clamp(best_point, lower_bound, upper_bound)
        step_scale = _LOCAL_MINIMUM_PROBE
        restart_count += 1


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


@fieldwise_init
struct VariogramFit(Copyable, ImplicitlyCopyable, Movable):
    """A fitted model and how the fit went."""

    var variogram: Variogram
    """The fitted model. Its range is in metres; nugget and sill stay in normalised
    semivariance units (kriging weights do not depend on that scale)."""
    var search: NelderMeadResult
    """The optimiser's result, in normalised units."""


def variogram(*, empirical_variogram: EmpiricalVariogram, model_type: Int) -> VariogramFit:
    """Fits a model of `model_type` to the normalised empirical variogram and
    converts its range to metres.

    The search starts at (0, 1, 0.5) and stays within nugget [0, 0.3], sill [0, 5]
    and range [0, 2] (in units of the largest lag). Because the semivariances are
    normalised to (0, 1], a nugget of at most 0.3 means at most 30 % of the largest
    binned semivariance is unexplained small-scale noise. Some of these settings are
    rounded through Float32 (0.15 -> 0.15000000596...); changing that would move the
    search path and with it the fitted parameters in their last digits.

    Args:
        empirical_variogram: The binned variogram after `normalise` (as returned by
            `empirical_variogram.empirical_variogram`); at least one bin populated.
        model_type: `SPHERICAL_MODEL` or `EXPONENTIAL_MODEL`.

    Returns:
        The model (range in metres, nugget and sill in normalised units) and the
        optimiser's result (`search.converged` is False if the evaluation budget of
        2000 ran out before the convergence and local-minimum tests passed).
    """
    var search = nelder_mead_bounded(
        bins=empirical_variogram,
        model_type=model_type,
        start=_parameters(0.0, 1.0, 0.5),
        lower_bound=_parameters(0.0, 0.0, 0.0),
        upper_bound=_parameters(Float64(Float32(0.3)), 5.0, 2.0),
        initial_step=_parameters(Float64(Float32(0.15)), 2.5, 1.0),
        min_misfit_variance=Float64(Float32(0.001)),
        convergence_check_interval=100,
        max_evaluations=2000,
    )
    var best = search.best_point
    var model = Variogram(best[0], best[1], best[2] * empirical_variogram.lag_scale, model_type)
    return VariogramFit(model, search)
