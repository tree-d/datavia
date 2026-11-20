"""Empirical (experimental) variogram of station data.

An empirical variogram answers: how different are the values of two stations,
on average, as a function of the distance between them? It is the input of the
variogram fit that decides how kriging weighs near against far stations.

It contains:

  * `EmpiricalVariogram`       the output data structure (documented below),
  * `_accumulate_row_kernel`   a vectorised kernel that fills the structure,
  * `empirical_variogram`      computes the variogram from raw pointers: runs the
                               kernel on all CPUs the process may use, then merges
                               sparse bins, computes the semivariance and normalises.

Pipeline (each step is one method or function):

  1. accumulate     every valid station pair (within max_lag, not too steep)
                    adds (z_i - z_j)^2 to the bin of its distance, on every day
  2. merge sparse   bins with too few pairs are merged into a neighbour bin
  3. semivariance   gamma = sum / (2 * pairs), plus the distance of each bin
  4. normalise      distances and semivariances are divided by their maxima

The binned distances are rescaled exactly once, in step 4.
"""

from std.math import ceil, sqrt
from std.memory import Pointer
from std.sys import simd_width_of
from max.algorithm.backend.cpu.parallelize import parallelize
from max.runtime.asyncrt import parallelism_level

comptime _F64Ptr = Pointer[Float64, MutUntrackedOrigin]

# Marks a pair that is left out of the variogram (too far apart, or too steep).
comptime _PAIR_EXCLUDED: Int64 = -1


def _untracked(pointer: Pointer[Float64, _]) -> _F64Ptr:
    """`pointer` without its origin, so it can be stored in a struct field.

    Only used for the arguments of `empirical_variogram`: the caller's pointer is in
    use for the whole call, so the memory behind it outlives every use here."""
    return _F64Ptr(unsafe_from_address=Int(pointer))


@fieldwise_init
struct _StationGeometry(Copyable, ImplicitlyCopyable, Movable):
    """Where the stations are: `station_count` entries each."""

    var x: _F64Ptr
    """Easting [m]."""
    var y: _F64Ptr
    """Northing [m]."""
    var elevation: _F64Ptr
    """Elevation [m]."""


@fieldwise_init
struct _PairRules(Copyable, ImplicitlyCopyable, Movable):
    """Which station pairs count, and into which bin they go."""

    var bin_width: Float64
    """Width of one distance class [m]."""
    var max_lag: Float64
    """Pairs farther apart than this [m] are left out."""
    var max_elevation_gradient: Float64
    """Pairs whose elevation differs by more than this per metre of distance are left out."""


# ---------------------------------------------------------------------------
# Output data structure
# ---------------------------------------------------------------------------


struct EmpiricalVariogram(Movable):
    """Binned empirical semivariogram of station data.

    Station pairs are sorted into distance classes ("bins") of equal width. All
    pairs of all days that fall into a bin are summarised by one number, the
    semivariance of that bin:

        semivariance = sum of (z_i - z_j)^2 over the pairs / (2 * number of pairs)

    (the Matheron estimator, with z the station values). Bin `k` (counting from
    0) covers the distances `k * bin_width < d <= (k + 1) * bin_width`; pairs
    closer than one `bin_width` go into bin 0.

    The fields are filled step by step, and the same instance is passed from
    one step to the next:

        after `empirical_variogram` accumulated the pairs:
            `pair_count` and `squared_difference_sum` hold the raw totals.
        after `merge_sparse_bins`:
            bins with fewer than `min_pairs` pairs were moved into a neighbour
            and now read `pair_count == 0`, like bins that never had any pair.
        after `compute_semivariance`:
            `semivariance` and `lag` are set for every populated bin.
        after `normalise`:
            `lag` and `semivariance` lie in (0, 1], and `lag_scale` and
            `semivariance_scale` hold the factors that undo this.

    A bin with `pair_count == 0` is "empty": its `lag` and `semivariance` are 0
    and must be ignored. Use `is_populated(bin)` to test for this.
    """

    var bin_width: Float64
    """Width of one distance class [m]."""
    var bin_count: Int
    """Number of distance classes; together they cover distances 0 .. max_lag."""

    var pair_count: List[Int]
    """Per bin: number of valid station pairs, summed over all days."""
    var squared_difference_sum: List[Float64]
    """Per bin: sum of (z_i - z_j)^2 over those pairs, in the unit of z squared."""
    var semivariance: List[Float64]
    """Per bin: semivariance of the pairs in the bin, in the unit of z squared
    (divided by `semivariance_scale` once `normalise` has run)."""
    var lag: List[Float64]
    """Per bin: the distance that represents the bin [m] (divided by `lag_scale`
    once `normalise` has run). Normally the middle of the bin; if empty bins
    were merged into it, the middle of the whole merged range."""

    var lag_scale: Float64
    """Factor that turns a normalised `lag` back into metres. 1.0 until `normalise` has run."""
    var semivariance_scale: Float64
    """Factor that turns a normalised `semivariance` back into its unit. 1.0 until `normalise` has run."""

    def __init__(out self, bin_width: Float64, max_lag: Float64):
        """Creates an empty variogram with enough bins to cover distances up to `max_lag` [m]."""
        self.bin_width = bin_width
        self.bin_count = Int(ceil(max_lag / bin_width))
        self.pair_count = List[Int](length=self.bin_count, fill=0)
        self.squared_difference_sum = List[Float64](length=self.bin_count, fill=0.0)
        self.semivariance = List[Float64](length=self.bin_count, fill=0.0)
        self.lag = List[Float64](length=self.bin_count, fill=0.0)
        self.lag_scale = 1.0
        self.semivariance_scale = 1.0

    def is_populated(self, bin: Int) -> Bool:
        """True if at least one station pair lies in the bin."""
        return self.pair_count[bin] > 0

    def any_populated(self) -> Bool:
        """True if at least one bin has a station pair."""
        for bin in range(self.bin_count):
            if self.is_populated(bin):
                return True
        return False

    def merge_sparse_bins(mut self, min_pairs: Int):
        """Merges every bin that has fewer than `min_pairs` pairs into a neighbour.

        A semivariance from few pairs is too noisy to fit. Two passes are made:
        going up in distance, a sparse bin is added to the next bin; then, going
        down, a sparse bin is added to the previous one (the last bin, which has
        no next neighbour, is handled by the second pass). The bin that was merged
        away is left empty. If the receiving bin is empty, it simply takes over
        the counts and sums.
        """
        for k in range(self.bin_count - 1):
            self._move_into(source=k, target=k + 1, min_pairs=min_pairs)
        for k in range(self.bin_count - 1, 0, -1):
            self._move_into(source=k, target=k - 1, min_pairs=min_pairs)

    def _move_into(mut self, source: Int, target: Int, min_pairs: Int):
        """Moves the pairs of `source` into `target` if `source` has fewer than `min_pairs`.

        An empty target holds exactly 0 pairs and a sum of 0.0, so adding to it is
        the same as taking over the values."""
        var pairs = self.pair_count[source]
        if pairs > 0 and pairs < min_pairs:
            self.pair_count[target] += pairs
            self.squared_difference_sum[target] += self.squared_difference_sum[source]
            self.pair_count[source] = 0
            self.squared_difference_sum[source] = 0.0

    def compute_semivariance(mut self):
        """Sets `semivariance` and `lag` of every populated bin.

        The semivariance is `squared_difference_sum / (2 * pair_count)`.
        The lag is the middle of the bin, `(k + 1) * bin_width - bin_width / 2`.
        If empty bins directly below were merged into this bin, they now belong to
        it, so the lag is the middle of the whole range:
        `(k + 1) * bin_width - covered_bins * bin_width / 2`.
        """
        var covered_bins = 0  # this bin plus the empty bins directly below it
        for k in range(self.bin_count):
            covered_bins += 1
            if self.is_populated(k):
                self.semivariance[k] = self.squared_difference_sum[k] / 2.0 / Float64(self.pair_count[k])
                self.lag[k] = Float64(k + 1) * self.bin_width - Float64(covered_bins) * self.bin_width / 2.0
                covered_bins = 0
            else:
                self.semivariance[k] = 0.0
                self.lag[k] = 0.0

    def normalise(mut self):
        """Divides `lag` and `semivariance` by their largest values, so both lie
        in (0, 1]. The fit works on these dimensionless values;
        `lag_scale` and `semivariance_scale` convert results back to metres and
        to the unit of the data. Empty bins stay 0."""
        var largest_lag = self.lag[0]
        var largest_semivariance = self.semivariance[0]
        for k in range(1, self.bin_count):
            largest_lag = max(largest_lag, self.lag[k])
            largest_semivariance = max(largest_semivariance, self.semivariance[k])
        self.lag_scale = largest_lag
        self.semivariance_scale = largest_semivariance
        for k in range(self.bin_count):
            if self.is_populated(k):
                self.lag[k] = self.lag[k] / largest_lag
                self.semivariance[k] = self.semivariance[k] / largest_semivariance


# ---------------------------------------------------------------------------
# Kernel
# ---------------------------------------------------------------------------


def _bins_of_pairs[width: Int](
    stations: _StationGeometry, rules: _PairRules, row: Int, first_column: Int
) -> SIMD[DType.int64, width]:
    """For `width` consecutive stations starting at `first_column`, the bin of their pair
    with station `row`, or `_PAIR_EXCLUDED` if the pair is left out.

    A pair is left out if the stations are farther apart than `max_lag`, or if their
    elevation differs by more than `max_elevation_gradient` metres per metre of
    distance: such pairs mostly measure the terrain, not the weather.
    All these quantities depend on the two stations only, not on the day.
    """
    var row_x = stations.x.unsafe_load(offset=row)
    var row_y = stations.y.unsafe_load(offset=row)
    var row_elevation = stations.elevation.unsafe_load(offset=row)

    var dx = row_x - stations.x.unsafe_load[width=width](offset=first_column)
    var dy = row_y - stations.y.unsafe_load[width=width](offset=first_column)
    var distance = sqrt(dx * dx + dy * dy)
    var elevation_difference = abs(row_elevation - stations.elevation.unsafe_load[width=width](offset=first_column))

    var is_too_far = distance.gt(rules.max_lag)
    var is_too_steep = (elevation_difference / distance).gt(rules.max_elevation_gradient)

    # 0-based distance class max(1, ceil(d / bin_width)) - 1: pairs closer than one bin
    # width go into the first bin.
    var bin = (max(ceil(distance / rules.bin_width), SIMD[DType.float64, width](1.0)) - 1.0).cast[DType.int64]()
    return (is_too_far | is_too_steep).select(SIMD[DType.int64, width](_PAIR_EXCLUDED), bin)


def _find_bins_of_row[vector_size: Int](
    *,
    stations: _StationGeometry,
    rules: _PairRules,
    station_count: Int,
    row: Int,
    mut bin_of_column: List[Int64],
) -> None:
    """Step 1: the bin of every pair (row, column) with column > row, or `_PAIR_EXCLUDED`.

    Vectorised over the columns. The result does not depend on the day, so it is
    computed once per pair, not once per pair and day. The columns that do not fill
    a whole vector are handled one at a time.
    """
    var column = row + 1
    while column + vector_size <= station_count:
        bin_of_column.unsafe_ptr().unsafe_store[width=vector_size](
            offset=column, val=_bins_of_pairs[vector_size](stations, rules, row, column)
        )
        column += vector_size
    while column < station_count:
        bin_of_column[column] = _bins_of_pairs[1](stations, rules, row, column)[0]
        column += 1


def _add_pairs_of_row[vector_size: Int](
    *,
    padded_values: List[Float64],
    padded_day_count: Int,
    station_count: Int,
    nodata: Float64,
    row: Int,
    bin_count: Int,
    bin_of_column: List[Int64],
    mut row_pair_count: List[Int64],
    mut row_squared_difference_sum: List[Float64],
) -> None:
    """Step 2: for every kept pair (row, column), add (z_row - z_column)^2 and a count
    to the pair's bin, for each day on which both stations have a value.

    Vectorised over the days: the days of a station are contiguous in memory, so each
    pair is processed `vector_size` days at a time. Padded days equal `nodata` and are
    skipped. `bin_of_column` is the result of `_find_bins_of_row` for this row.
    """
    var values = padded_values.unsafe_ptr()
    var first_bin_of_row = row * bin_count
    for column in range(row + 1, station_count):
        var bin = Int(bin_of_column[column])
        if bin == Int(_PAIR_EXCLUDED):
            continue

        var squared_differences = SIMD[DType.float64, vector_size](0.0)
        var valid_days = SIMD[DType.int64, vector_size](0)
        for day in range(0, padded_day_count, vector_size):
            var value_row = values.unsafe_load[width=vector_size](offset=row * padded_day_count + day)
            var value_column = values.unsafe_load[width=vector_size](offset=column * padded_day_count + day)
            var has_both_values = value_row.ne(nodata) & value_column.ne(nodata)
            var difference = value_row - value_column
            squared_differences += has_both_values.select(difference * difference, SIMD[DType.float64, vector_size](0.0))
            valid_days += has_both_values.cast[DType.int64]()

        row_pair_count[first_bin_of_row + bin] += valid_days.reduce_add()
        row_squared_difference_sum[first_bin_of_row + bin] += squared_differences.reduce_add()


def _accumulate_row_kernel[vector_size: Int](
    *,
    stations: _StationGeometry,
    rules: _PairRules,
    padded_values: List[Float64],
    padded_day_count: Int,
    station_count: Int,
    nodata: Float64,
    row: Int,
    bin_count: Int,
    mut bin_of_column: List[Int64],
    mut row_pair_count: List[Int64],
    mut row_squared_difference_sum: List[Float64],
) -> None:
    """Accumulates all pairs (row, column) with column > row into the row's own bins:
    step 1 (`_find_bins_of_row`) finds each pair's bin, step 2 (`_add_pairs_of_row`)
    adds the pairs' squared differences to those bins.

    `row_pair_count` and `row_squared_difference_sum` hold `bin_count` bins per row;
    this row writes only its own, `row * bin_count` onwards, so rows can run on
    different threads without sharing memory. `bin_of_column` is scratch space for
    `station_count` entries, owned by the calling worker.
    """
    _find_bins_of_row[vector_size](
        stations=stations, rules=rules, station_count=station_count, row=row, bin_of_column=bin_of_column
    )
    _add_pairs_of_row[vector_size](
        padded_values=padded_values, padded_day_count=padded_day_count, station_count=station_count,
        nodata=nodata, row=row, bin_count=bin_count, bin_of_column=bin_of_column,
        row_pair_count=row_pair_count, row_squared_difference_sum=row_squared_difference_sum,
    )


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def empirical_variogram(
    *,
    x: Pointer[Float64, _],
    y: Pointer[Float64, _],
    elevation: Pointer[Float64, _],
    values: Pointer[Float64, _],
    station_count: Int,
    day_count: Int,
    nodata: Float64,
    bin_width: Float64,
    max_lag: Float64,
    max_elevation_gradient: Float64,
    min_pairs_per_bin: Int,
) -> EmpiricalVariogram:
    """Computes the normalised empirical variogram of station data pooled over all days.

    The work is spread over `parallelism_level()` worker threads: the number of CPUs
    this process is allowed to use. To run on fewer CPUs, restrict the process before
    the Mojo runtime starts (for example with CPU affinity: `taskset`). The result
    does not depend on the number of workers, because
    every station row has its own partial bins and they are added in row order.

    Args:
        x: Station eastings [m], `station_count` entries.
        y: Station northings [m], `station_count` entries.
        elevation: Station elevations [m], `station_count` entries.
        values: Station values, C-ordered `(station_count, day_count)`: the value of
            station `i` on day `t` is `values[i * day_count + t]`. Missing values equal `nodata`.
        station_count: Number of stations.
        day_count: Number of days.
        nodata: Marks a missing value.
        bin_width: Width of one distance class [m].
        max_lag: Pairs farther apart than this [m] are ignored.
        max_elevation_gradient: Pairs whose elevation difference per metre of
            distance exceeds this are ignored.
        min_pairs_per_bin: Bins with fewer pairs are merged into a neighbour.

    Returns:
        The variogram after all four steps (see the module docstring), so `lag` and
        `semivariance` are normalised. If no pair qualified, no bin is populated.
    """
    comptime vector_size = simd_width_of[DType.float64]()
    var bins = EmpiricalVariogram(bin_width, max_lag)
    if station_count < 2 or bins.bin_count < 1:
        return bins^
    var bin_count = bins.bin_count
    var stations = _StationGeometry(_untracked(x), _untracked(y), _untracked(elevation))
    var rules = _PairRules(bin_width, max_lag, max_elevation_gradient)

    # Copy the values into rows padded to a whole number of vectors, filled with
    # `nodata`, so the kernel can read full vectors and ignore the padding.
    var padded_day_count = ((day_count + vector_size - 1) // vector_size) * vector_size
    var padded_values = List[Float64](length=station_count * padded_day_count, fill=nodata)
    for station in range(station_count):
        for day in range(day_count):
            padded_values[station * padded_day_count + day] = values[unsafe_offset=station * day_count + day]

    # Every row gets its own bins, so rows are independent of each other.
    var row_pair_count = List[Int64](length=station_count * bin_count, fill=0)
    var row_squared_difference_sum = List[Float64](length=station_count * bin_count, fill=0.0)

    # The worker closure captures the lists by reference, so they stay alive for
    # as long as the workers use them.
    # The work per row shrinks with the row number, so each worker takes rows
    # cyclically (worker w: rows w, w + n_workers, ...) to balance the load.
    var n_workers = min(parallelism_level(None), station_count)

    def work(worker: Int) {var stations, var rules, ref padded_values, var padded_day_count,
                           var station_count, var nodata, ref row_pair_count, ref row_squared_difference_sum,
                           var bin_count, var n_workers}:
        var bin_of_column = List[Int64](length=station_count, fill=_PAIR_EXCLUDED)
        for row in range(worker, station_count - 1, n_workers):
            _accumulate_row_kernel[vector_size](
                stations=stations, rules=rules, padded_values=padded_values,
                padded_day_count=padded_day_count, station_count=station_count, nodata=nodata, row=row,
                bin_count=bin_count, bin_of_column=bin_of_column,
                row_pair_count=row_pair_count, row_squared_difference_sum=row_squared_difference_sum,
            )

    if n_workers > 1:
        parallelize(work, n_workers, n_workers)
    else:
        work(0)

    # Add the rows' bins in row order (fixed, so the result does not depend on the worker count).
    for row in range(station_count - 1):
        for bin in range(bin_count):
            bins.pair_count[bin] += Int(row_pair_count[row * bin_count + bin])
            bins.squared_difference_sum[bin] += row_squared_difference_sum[row * bin_count + bin]

    bins.merge_sparse_bins(min_pairs_per_bin)
    bins.compute_semivariance()
    bins.normalise()
    return bins^
