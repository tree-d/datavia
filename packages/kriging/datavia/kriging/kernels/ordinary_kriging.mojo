"""Ordinary kriging (OK) of station data onto grid cells.

Kriging estimates the value at a cell as a weighted sum of the station values,
z0 = sum_i w_i z_i. The weights come from the variogram (see variogram.mojo):
they are the ones that make the estimate unbiased and its error variance
smallest. For ordinary kriging they solve the linear system

    | Gamma  1 | | w  |   | gamma_0 |     Gamma_ij = gamma(|s_i - s_j|)
    | 1^T    0 | | mu | = |    1    |     gamma_0,i = gamma(|s_i - s_0|)

The row of ones makes the weights sum to 1 (mu is its Lagrange multiplier).
External drift kriging (external_drift_kriging.mojo) borders this system with
one more constraint; everything else in this file serves both.

It contains:

  * `KrigingSystem`          the trait of a kriging system: how many constraint
                             rows it has and how its matrix and right-hand side
                             are assembled,
  * `OrdinaryKrigingSystem`  the OK system: variogram block plus a row of ones,
                             vectorised over the station pairs,
  * `_lu_solve_kernel`       solves the system, its row updates vectorised,
  * `_krige_cell_kernel`     one cell for every day: neighbourhood, estimator
                             choice, weights (cached from day to day), estimate,
  * `krige_cells`            runs the cell kernel for all cells on all CPUs the
                             process may use, for any `KrigingSystem`,
  * `ordinary_kriging`       `krige_cells` with the OK system, from raw pointers.

Pipeline per cell (each step is one function):

  1. neighbourhood  the stations within max_dist of the cell (vectorised over stations)
  2. select         per day: the neighbours that have a value, and the estimator
                    this calls for (zero, a single station, IDW, or kriging)
  3. weights        assemble the system and solve it; kept while the day's
                    selection is the same as the previous day's
  4. estimate       sum_i w_i z_i, accumulated in Float32 in station order
  5. correct        optionally, negative estimates are set to 0

Cells are independent: each writes only its own days of the output, so the
result does not depend on the number of workers.

Edge cases:
  - The estimators only ever write the current day of a cell.
  - `threshold_dist` is a setting, so the single-station rule works for any grid.
  - A singular kriging system gives `OUT_NODATA` for that cell and day.
  - IDW with a station exactly at the cell centre returns that station's value.
"""

from std.math import sqrt
from std.memory import Pointer
from max.algorithm.backend.cpu.parallelize import parallelize
from max.runtime.asyncrt import parallelism_level

from kernels.memory_layout import F32Ptr, F64Ptr, VECTOR_SIZE, untracked
from kernels.variogram import Variogram

comptime OUT_NODATA: Float32 = -9999.0
"""Output for cells without a valid DEM value and for failed solves."""

# Estimator chosen per cell and day (see `_choose_estimator`).
comptime _ESTIMATE_KRIGING = 0
comptime _ESTIMATE_SINGLE = 1
comptime _ESTIMATE_IDW = 2
comptime _ESTIMATE_ZERO = 3

# Outcome of `_kriging_weights`.
comptime _SOLVE_OK = 0
comptime _SOLVE_SINGULAR = 1
comptime _SOLVE_SUM_WARNING = 2

comptime _WEIGHT_SUM_TOLERANCE = 1.0e-4
"""Weights whose sum differs from 1 by more than this are counted. They are still used: the
deviation is a rounding symptom of a nearly singular system, not a reason to drop the cell."""

comptime _CHUNKS_PER_WORKER = 16
"""Cells are cut into this many contiguous chunks per worker, taken cyclically (see `krige_cells`)."""


def _distance(x1: Float64, y1: Float64, x2: Float64, y2: Float64) -> Float64:
    """Returns the Euclidean distance between two points.

    Args:
        x1: Easting [m] of the first point.
        y1: Northing [m] of the first point.
        x2: Easting [m] of the second point.
        y2: Northing [m] of the second point.

    Returns:
        The distance [m].
    """
    var dx = x1 - x2
    var dy = y1 - y2
    return sqrt(dx * dx + dy * dy)


# ---------------------------------------------------------------------------
# Inputs and settings
# ---------------------------------------------------------------------------


@fieldwise_init
struct Cells(Copyable, ImplicitlyCopyable, Movable):
    """The grid cells to estimate: `count` entries each."""

    var x: F64Ptr
    """Easting [m]."""
    var y: F64Ptr
    """Northing [m]."""
    var elevation: F64Ptr
    """Elevation [m] (the drift of external drift kriging); `dem_nodata` where the DEM has no value."""
    var count: Int
    """Number of cells."""

    @staticmethod
    def from_pointers(
        x: Pointer[Float64, _], y: Pointer[Float64, _], elevation: Pointer[Float64, _], count: Int
    ) -> Self:
        """Returns the cells behind the caller's pointers, which must stay valid for the whole call.

        Args:
            x: Cell eastings [m], `count` entries.
            y: Cell northings [m], `count` entries.
            elevation: Cell elevations [m], `count` entries.
            count: Number of cells.

        Returns:
            The cells, reading the caller's memory in place.
        """
        return Self(untracked(x), untracked(y), untracked(elevation), count)


@fieldwise_init
struct Stations(Copyable, ImplicitlyCopyable, Movable):
    """The stations and their values: `count` entries each, `day_count` values per station."""

    var x: F64Ptr
    """Easting [m]."""
    var y: F64Ptr
    """Northing [m]."""
    var elevation: F64Ptr
    """Elevation [m]."""
    var values: F64Ptr
    """C-ordered `(count, day_count)`: station `i` on day `t` is `values[i * day_count + t]`."""
    var count: Int
    """Number of stations."""
    var day_count: Int
    """Number of days."""

    @staticmethod
    def from_pointers(
        x: Pointer[Float64, _],
        y: Pointer[Float64, _],
        elevation: Pointer[Float64, _],
        values: Pointer[Float64, _],
        count: Int,
        day_count: Int,
    ) -> Self:
        """Returns the stations behind the caller's pointers, which must stay valid for the whole call.

        Args:
            x: Station eastings [m], `count` entries.
            y: Station northings [m], `count` entries.
            elevation: Station elevations [m], `count` entries.
            values: Station values, C-ordered `(count, day_count)`.
            count: Number of stations.
            day_count: Number of days.

        Returns:
            The stations, reading the caller's memory in place.
        """
        return Self(untracked(x), untracked(y), untracked(elevation), untracked(values), count, day_count)

    def value(self, station: Int, day: Int) -> Float64:
        """Returns the value of one station on one day.

        Args:
            station: Station index, `0 <= station < count`.
            day: Day index, `0 <= day < day_count`.

        Returns:
            The value; equals the run's `nodata` if the station has none that day.
        """
        return self.values[unsafe_offset=station * self.day_count + day]


@fieldwise_init
struct KrigingSettings(Copyable, ImplicitlyCopyable, Movable):
    """Run settings of the interpolation."""

    var max_dist: Float64
    """Search radius [m]: only stations within it take part."""
    var nodata: Float64
    """Marks a missing station value."""
    var dem_nodata: Float64
    """Marks a cell without DEM value; such cells get `OUT_NODATA` on every day."""
    var correct_negative: Bool
    """Set negative estimates to 0."""
    var dist_zero: Bool
    """With a single station: estimate 0 if it is farther than `threshold_dist`."""
    var threshold_dist: Float64
    """Distance [m] for `dist_zero`, typically half the cell diagonal."""


@fieldwise_init
struct KrigingCounts(Copyable, ImplicitlyCopyable, Movable):
    """How many kriging systems needed attention, over all cells and days."""

    var singular: Int
    """Singular systems: the cell-day got `OUT_NODATA`."""
    var sum_warning: Int
    """Weight sets that do not sum to 1 within 1e-4 (used anyway)."""


# ---------------------------------------------------------------------------
# Kriging system
# ---------------------------------------------------------------------------


struct SelectedStations(Movable):
    """The stations that report on a day, with their positions gathered into
    contiguous arrays, so the system can be assembled a vector at a time."""

    var index: List[Int]
    """Station numbers, in increasing order."""
    var x: List[Float64]
    """Easting [m] of each selected station (filled by `gather`)."""
    var y: List[Float64]
    """Northing [m] of each selected station (filled by `gather`)."""
    var elevation: List[Float64]
    """Elevation [m] of each selected station (filled by `gather`)."""

    def __init__(out self):
        """Creates an empty selection."""
        self.index = List[Int]()
        self.x = List[Float64]()
        self.y = List[Float64]()
        self.elevation = List[Float64]()

    def count(self) -> Int:
        """Returns the number of selected stations.

        Returns:
            The length of `index`.
        """
        return len(self.index)

    def gather(mut self, stations: Stations):
        """Copies the position of every selected station into `x`, `y` and `elevation`.

        Args:
            stations: All stations; `index` refers to them.
        """
        self.x.clear()
        self.y.clear()
        self.elevation.clear()
        for i in range(self.count()):
            var station = self.index[i]
            self.x.append(stations.x[unsafe_offset=station])
            self.y.append(stations.y[unsafe_offset=station])
            self.elevation.append(stations.elevation[unsafe_offset=station])


trait KrigingSystem(Copyable, Deinitable, ImplicitlyCopyable, Movable):
    """A kriging system: `n` weights plus `constraint_count()` Lagrange multipliers.

    The matrix is stored row-major with `size = n + constraint_count()` entries per
    row. The first `n` rows and columns belong to the selected stations, in order.
    """

    def constraint_count(self) -> Int:
        """Returns the number of constraint rows below the `n` station rows.

        Returns:
            The number of Lagrange multipliers of the system.
        """
        ...

    def assemble_matrix(self, selected: SelectedStations, model: Variogram, mut matrix: List[Float64], size: Int):
        """Writes all `size * size` entries of the system matrix.

        Args:
            selected: The reporting stations; must be gathered (`SelectedStations.gather`).
            model: The variogram.
            matrix: Receives the matrix, row-major; must hold at least `size * size` entries.
            size: `selected.count() + constraint_count()`.
        """
        ...

    def assemble_right_hand_side(
        self,
        selected: SelectedStations,
        cell_x: Float64,
        cell_y: Float64,
        cell_elevation: Float64,
        model: Variogram,
        mut right_hand_side: List[Float64],
        size: Int,
    ):
        """Writes the `size` entries of the right-hand side for the cell.

        Args:
            selected: The reporting stations; must be gathered (`SelectedStations.gather`).
            cell_x: Easting [m] of the cell.
            cell_y: Northing [m] of the cell.
            cell_elevation: Elevation [m] of the cell (the drift value, if the system has one).
            model: The variogram.
            right_hand_side: Receives the right-hand side; must hold at least `size` entries.
            size: `selected.count() + constraint_count()`.
        """
        ...


def _store_semivariances[vector_size: Int](
    x: Float64, y: Float64, selected: SelectedStations, model: Variogram, mut target: List[Float64], offset: Int
):
    """Sets `target[offset + j] = gamma(|(x, y) - s_j|)` for every selected station `j`.

    Vectorised over the stations; the stations that do not fill a whole vector are
    handled one at a time. Compared with a plain scalar loop, some entries differ
    in the last bit (tests/mojo/test_kriging.mojo counts them), so the weights
    agree with a scalar implementation to rounding, not bit for bit.

    Parameters:
        vector_size: Number of stations processed per SIMD instruction.

    Args:
        x: Easting [m] of the point the distances are measured from.
        y: Northing [m] of that point.
        selected: The stations; must be gathered (`SelectedStations.gather`).
        model: The variogram.
        target: Receives the semivariances; must hold `offset + selected.count()` entries.
        offset: Index of `target` that receives the first station's semivariance.
    """
    var station_x = selected.x.unsafe_ptr()
    var station_y = selected.y.unsafe_ptr()
    var destination = target.unsafe_ptr()
    var n = selected.count()
    var j = 0
    while j + vector_size <= n:
        var dx = x - station_x.unsafe_load[width=vector_size](offset=j)
        var dy = y - station_y.unsafe_load[width=vector_size](offset=j)
        destination.unsafe_store[width=vector_size](
            offset=offset + j, val=model.value[vector_size](sqrt(dx * dx + dy * dy))
        )
        j += vector_size
    while j < n:
        target[offset + j] = model.value[1](_distance(x, y, selected.x[j], selected.y[j]))[0]
        j += 1


@fieldwise_init
struct OrdinaryKrigingSystem(KrigingSystem):
    """The OK system.

        | Gamma  1 |       right-hand side  | gamma_0 |
        | 1^T    0 |                        |    1    |

    Written so that a larger system can border it: the columns and rows from `n + 1`
    up to `size` are set to 0 and left for the caller to fill.
    """

    def constraint_count(self) -> Int:
        """Returns the number of constraint rows: 1, the row of ones.

        Returns:
            1.
        """
        return 1

    def assemble_matrix(self, selected: SelectedStations, model: Variogram, mut matrix: List[Float64], size: Int):
        """Writes Gamma_ij = gamma(|s_i - s_j|) and the row and column of ones, whole rows at a time.

        The diagonal is the nugget, as gamma(0) is (see `Variogram`). Entries beyond
        row and column `n` are set to 0, for a bordering system to fill.

        Args:
            selected: The reporting stations; must be gathered (`SelectedStations.gather`).
            model: The variogram.
            matrix: Receives the matrix, row-major; must hold at least `size * size` entries.
            size: At least `selected.count() + 1`.
        """
        var n = selected.count()
        for i in range(n):
            _store_semivariances[VECTOR_SIZE](selected.x[i], selected.y[i], selected, model, matrix, i * size)
            matrix[i * size + n] = 1.0
            for k in range(n + 1, size):
                matrix[i * size + k] = 0.0
        for k in range(n):
            matrix[n * size + k] = 1.0
        for row in range(n, size):
            for k in range(0 if row > n else n, size):
                matrix[row * size + k] = 0.0

    def assemble_right_hand_side(
        self,
        selected: SelectedStations,
        cell_x: Float64,
        cell_y: Float64,
        cell_elevation: Float64,
        model: Variogram,
        mut right_hand_side: List[Float64],
        size: Int,
    ):
        """Writes gamma_0,i = gamma(|s_i - s_0|), then 1; entries from `n + 1` on are set to 0.

        Args:
            selected: The reporting stations; must be gathered (`SelectedStations.gather`).
            cell_x: Easting [m] of the cell.
            cell_y: Northing [m] of the cell.
            cell_elevation: Unused by ordinary kriging.
            model: The variogram.
            right_hand_side: Receives the right-hand side; must hold at least `size` entries.
            size: At least `selected.count() + 1`.
        """
        var n = selected.count()
        _store_semivariances[VECTOR_SIZE](cell_x, cell_y, selected, model, right_hand_side, 0)
        right_hand_side[n] = 1.0
        for k in range(n + 1, size):
            right_hand_side[k] = 0.0


# ---------------------------------------------------------------------------
# Kernels
# ---------------------------------------------------------------------------


def _lu_solve_kernel[vector_size: Int](
    mut matrix: List[Float64], mut right_hand_side: List[Float64], size: Int
) -> Bool:
    """Solves `matrix * x = right_hand_side` in place by Gaussian elimination with partial pivoting.

    This is the algorithm of LAPACK dgesv. The solution x goes into
    `right_hand_side`, and `matrix` is destroyed.

    The row updates, where almost all the time goes, are vectorised: each entry is
    updated on its own, so the result is the same as the scalar loop. The pivot
    search (a strided column) and the back substitution (a sum in fixed order) stay
    scalar; both are O(size^2) against the O(size^3) of the updates.

    Parameters:
        vector_size: Number of matrix entries updated per SIMD instruction.

    Args:
        matrix: The system matrix, row-major `(size, size)`; overwritten.
        right_hand_side: The right-hand side, `size` entries; replaced by the solution.
        size: Number of unknowns.

    Returns:
        False if the matrix is singular or the solution is not finite, else True.
    """
    # The usual linear-algebra names: solve A x = b.
    var a = matrix.unsafe_ptr()
    var b = right_hand_side.unsafe_ptr()
    for col in range(size):
        # pivot: the largest |a[unsafe_offset=row, col]| at or below the diagonal
        var pivot = col
        var largest = abs(a[unsafe_offset=col * size + col])
        for row in range(col + 1, size):
            var candidate = abs(a[unsafe_offset=row * size + col])
            if candidate > largest:
                largest = candidate
                pivot = row
        if largest == 0.0:
            return False
        if pivot != col:
            var k = 0
            while k + vector_size <= size:
                var upper = a.unsafe_load[width=vector_size](offset=col * size + k)
                var lower = a.unsafe_load[width=vector_size](offset=pivot * size + k)
                a.unsafe_store[width=vector_size](offset=col * size + k, val=lower)
                a.unsafe_store[width=vector_size](offset=pivot * size + k, val=upper)
                k += vector_size
            while k < size:
                var swapped = a[unsafe_offset=col * size + k]
                a[unsafe_offset=col * size + k] = a[unsafe_offset=pivot * size + k]
                a[unsafe_offset=pivot * size + k] = swapped
                k += 1
            var swapped = b[unsafe_offset=col]
            b[unsafe_offset=col] = b[unsafe_offset=pivot]
            b[unsafe_offset=pivot] = swapped

        var diagonal = a[unsafe_offset=col * size + col]
        for row in range(col + 1, size):
            var factor = a[unsafe_offset=row * size + col] / diagonal
            if factor != 0.0:
                # a[unsafe_offset=row, col+1:] -= factor * a[unsafe_offset=col, col+1:]
                var k = col + 1
                while k + vector_size <= size:
                    var target = a.unsafe_load[width=vector_size](offset=row * size + k)
                    var source = a.unsafe_load[width=vector_size](offset=col * size + k)
                    a.unsafe_store[width=vector_size](offset=row * size + k, val=target - factor * source)
                    k += vector_size
                while k < size:
                    a[unsafe_offset=row * size + k] -= factor * a[unsafe_offset=col * size + k]
                    k += 1
                b[unsafe_offset=row] -= factor * b[unsafe_offset=col]
            a[unsafe_offset=row * size + col] = 0.0

    # back substitution
    for row in range(size - 1, -1, -1):
        var total = b[unsafe_offset=row]
        for k in range(row + 1, size):
            total -= a[unsafe_offset=row * size + k] * b[unsafe_offset=k]
        b[unsafe_offset=row] = total / a[unsafe_offset=row * size + row]
        if not (abs(b[unsafe_offset=row]) < Float64.MAX):
            return False
    return True


def _kriging_weights[System: KrigingSystem](
    system: System,
    selected: SelectedStations,
    cell_x: Float64,
    cell_y: Float64,
    cell_elevation: Float64,
    model: Variogram,
    mut matrix: List[Float64],
    mut weights: List[Float64],
) -> Int:
    """Step 3: assembles and solves the kriging system of one cell.

    Parameters:
        System: The kriging system type (ordinary or external drift).

    Args:
        system: The kriging system.
        selected: The reporting stations; must be gathered (`SelectedStations.gather`).
        cell_x: Easting [m] of the cell.
        cell_y: Northing [m] of the cell.
        cell_elevation: Elevation [m] of the cell.
        model: The variogram.
        matrix: Scratch space for the system matrix; grown as needed, destroyed by the solve.
        weights: Receives the weights w_1..n followed by the Lagrange multipliers; grown as needed.

    Returns:
        `_SOLVE_OK`, `_SOLVE_SINGULAR` (the weights are unusable), or
        `_SOLVE_SUM_WARNING` if the weights do not sum to 1 within
        `_WEIGHT_SUM_TOLERANCE` (they are usable).
    """
    var n = selected.count()
    var size = n + system.constraint_count()
    if len(matrix) < size * size:
        matrix.resize(size * size, 0.0)
    if len(weights) < size:
        weights.resize(size, 0.0)
    system.assemble_matrix(selected, model, matrix, size)
    system.assemble_right_hand_side(selected, cell_x, cell_y, cell_elevation, model, weights, size)
    if not _lu_solve_kernel[VECTOR_SIZE](matrix, weights, size):
        return _SOLVE_SINGULAR
    var weight_sum: Float64 = 0.0
    for i in range(n):
        weight_sum += weights[i]
    if abs(weight_sum - 1.0) > _WEIGHT_SUM_TOLERANCE:
        return _SOLVE_SUM_WARNING
    return _SOLVE_OK


def _find_neighbourhood_kernel[vector_size: Int](
    cell_x: Float64, cell_y: Float64, stations: Stations, max_dist: Float64, mut neighbours: List[Int]
):
    """Step 1: finds the stations within `max_dist` of the cell, vectorised over the stations.

    Parameters:
        vector_size: Number of stations tested per SIMD instruction.

    Args:
        cell_x: Easting [m] of the cell.
        cell_y: Northing [m] of the cell.
        stations: All stations.
        max_dist: Search radius [m].
        neighbours: Receives the station indices, in increasing order (cleared first).
    """
    neighbours.clear()
    var station = 0
    while station + vector_size <= stations.count:
        var dx = cell_x - stations.x.unsafe_load[width=vector_size](offset=station)
        var dy = cell_y - stations.y.unsafe_load[width=vector_size](offset=station)
        var is_near = sqrt(dx * dx + dy * dy).le(max_dist)
        if is_near.reduce_or():
            comptime for lane in range(vector_size):
                if is_near[lane]:
                    neighbours.append(station + lane)
        station += vector_size
    while station < stations.count:
        if _distance(cell_x, cell_y, stations.x[unsafe_offset=station], stations.y[unsafe_offset=station]) <= max_dist:
            neighbours.append(station)
        station += 1


def _select_valid_stations(
    neighbours: List[Int], stations: Stations, day: Int, nodata: Float64, mut selected: SelectedStations
) -> Int:
    """Step 2: selects the neighbours that have a value on `day`.

    Args:
        neighbours: Indices of the stations within the search radius.
        stations: All stations.
        day: Day index.
        nodata: Marks a missing value.
        selected: Its `index` receives the reporting neighbours, in the order of `neighbours`.

    Returns:
        How many of the selected stations report exactly 0.
    """
    selected.index.clear()
    var zero_count = 0
    for i in range(len(neighbours)):
        var station = neighbours[i]
        var value = stations.value(station, day)
        if value != nodata:
            if value == 0.0:
                zero_count += 1
            selected.index.append(station)
    return zero_count


def _choose_estimator(selected_count: Int, zero_count: Int) -> Int:
    """Step 2: chooses the estimator for the day from the number of reporting stations.

    All values 0 (or no station) -> 0; one station -> its value; two -> IDW;
    more -> kriging.

    Args:
        selected_count: Number of reporting stations within the search radius.
        zero_count: How many of them report exactly 0.

    Returns:
        `_ESTIMATE_ZERO`, `_ESTIMATE_SINGLE`, `_ESTIMATE_IDW` or `_ESTIMATE_KRIGING`.
    """
    if zero_count == selected_count:
        return _ESTIMATE_ZERO
    if selected_count == 1:
        return _ESTIMATE_SINGLE
    if selected_count == 2:
        return _ESTIMATE_IDW
    return _ESTIMATE_KRIGING


def _same_selection(a: List[Int], b: List[Int]) -> Bool:
    """Tests whether two selections hold the same stations (test of the weight cache).

    Args:
        a: Station indices.
        b: Station indices.

    Returns:
        True if both lists are equal entry by entry.
    """
    if len(a) != len(b):
        return False
    for i in range(len(a)):
        if a[i] != b[i]:
            return False
    return True


def _idw_weights(
    selected: SelectedStations, cell_x: Float64, cell_y: Float64, stations: Stations, mut weights: List[Float64]
):
    """Computes inverse squared distance weights w_i = d_i^-2 / sum_j d_j^-2.

    A station at distance 0 gets all the weight.

    Args:
        selected: The reporting stations (only `index` is used).
        cell_x: Easting [m] of the cell.
        cell_y: Northing [m] of the cell.
        stations: All stations.
        weights: Receives one weight per selected station; grown as needed.
    """
    var n = selected.count()
    if len(weights) < n:
        weights.resize(n, 0.0)
    for i in range(n):
        var station = selected.index[i]
        var d = _distance(cell_x, cell_y, stations.x[unsafe_offset=station], stations.y[unsafe_offset=station])
        if d == 0.0:
            for k in range(n):
                weights[k] = 0.0
            weights[i] = 1.0
            return
        weights[i] = 1.0 / (d * d)
    var weight_sum: Float64 = 0.0
    for i in range(n):
        weight_sum += weights[i]
    for i in range(n):
        weights[i] /= weight_sum


def _single_station_estimate(value: Float64, distance: Float64, settings: KrigingSettings) -> Float32:
    """Estimates a cell from the only station that has a value.

    Args:
        value: The station's value.
        distance: Distance [m] from the cell to the station.
        settings: Its `dist_zero` and `threshold_dist` decide whether a distant station counts.

    Returns:
        0 if `dist_zero` is on and the station is farther than `threshold_dist`,
        else the station's value.
    """
    if settings.dist_zero and distance > settings.threshold_dist:
        return 0.0
    return Float32(value)


def _weighted_estimate(weights: List[Float64], selected: SelectedStations, stations: Stations, day: Int) -> Float32:
    """Step 4: computes z0 = sum_i w_i z_i (kriging or IDW).

    The sum is accumulated in single precision in station order. The fixed order
    makes the result independent of vector width and thread count.

    Args:
        weights: One weight per selected station (further entries are ignored).
        selected: The reporting stations (only `index` is used).
        stations: All stations.
        day: Day index.

    Returns:
        The estimate.
    """
    var estimate: Float32 = 0.0
    for i in range(selected.count()):
        estimate += Float32(weights[i] * stations.value(selected.index[i], day))
    return estimate


def _correct_negative(estimate: Float32) -> Float32:
    """Step 5: sets a negative estimate to 0, leaving `OUT_NODATA` alone.

    Args:
        estimate: The estimate.

    Returns:
        0 if the estimate is negative but not `OUT_NODATA`, else the estimate.
    """
    if estimate > OUT_NODATA and estimate < 0.0:
        return 0.0
    return estimate


def _is_dem_nodata(elevation: Float64, dem_nodata: Float64) -> Bool:
    """Tests whether a cell has no DEM value.

    The elevation is rounded half away from zero to an integer first, so an
    integer nodata marker matches a DEM stored as floating point.

    Args:
        elevation: The cell's elevation [m].
        dem_nodata: The DEM's nodata marker.

    Returns:
        True if the rounded elevation equals `dem_nodata`.
    """
    return Float64(Int(elevation + 0.5 if elevation >= 0.0 else elevation - 0.5)) == dem_nodata


struct _Workspace(Movable):
    """Scratch space of one worker, reused for all its cells."""

    var neighbours: List[Int]
    """Stations within `max_dist` of the current cell."""
    var selected: SelectedStations
    """The neighbours with a value on the current day."""
    var cached_selection: List[Int]
    """The stations `kriging_weights` was last solved for."""
    var matrix: List[Float64]
    """The kriging matrix (destroyed by the solve)."""
    var kriging_weights: List[Float64]
    """The weights (and multipliers) solved for `cached_selection`."""
    var idw_weights: List[Float64]
    """IDW weights, kept apart so that an IDW day does not spoil the kriging cache."""
    var counts: KrigingCounts
    """Counts of this worker's cells."""

    def __init__(out self):
        """Creates an empty workspace with zero counts."""
        self.neighbours = List[Int]()
        self.selected = SelectedStations()
        self.cached_selection = List[Int]()
        self.matrix = List[Float64]()
        self.kriging_weights = List[Float64]()
        self.idw_weights = List[Float64]()
        self.counts = KrigingCounts(0, 0)


def _krige_cell_kernel[System: KrigingSystem](
    *,
    system: System,
    cells: Cells,
    cell: Int,
    stations: Stations,
    model: Variogram,
    settings: KrigingSettings,
    estimates: F32Ptr,
    mut workspace: _Workspace,
):
    """Estimates one cell on every day into `estimates[cell * day_count + day]`.

    The weights depend only on which stations report, not on their values, so they
    are reused as long as the selection stays the same from one day to the next.

    Parameters:
        System: The kriging system type (ordinary or external drift).

    Args:
        system: The kriging system.
        cells: All cells.
        cell: Index of the cell to estimate.
        stations: All stations and their values.
        model: The variogram.
        settings: Search radius, nodata values and the precipitation options.
        estimates: Output, C-ordered `(cells.count, stations.day_count)`; only this cell's row is written.
        workspace: The calling worker's scratch space; its `counts` are increased.
    """
    var cell_x = cells.x[unsafe_offset=cell]
    var cell_y = cells.y[unsafe_offset=cell]
    var cell_elevation = cells.elevation[unsafe_offset=cell]
    var first_output = cell * stations.day_count
    if _is_dem_nodata(cell_elevation, settings.dem_nodata):
        for day in range(stations.day_count):
            estimates[unsafe_offset=first_output + day] = OUT_NODATA
        return

    _find_neighbourhood_kernel[VECTOR_SIZE](cell_x, cell_y, stations, settings.max_dist, workspace.neighbours)
    var have_weights = False
    workspace.cached_selection.clear()

    for day in range(stations.day_count):
        var zero_count = _select_valid_stations(
            workspace.neighbours, stations, day, settings.nodata, workspace.selected
        )
        var estimator = _choose_estimator(workspace.selected.count(), zero_count)
        var estimate: Float32

        if estimator == _ESTIMATE_KRIGING:
            if not (have_weights and _same_selection(workspace.selected.index, workspace.cached_selection)):
                workspace.selected.gather(stations)
                var code = _kriging_weights(
                    system, workspace.selected, cell_x, cell_y, cell_elevation, model,
                    workspace.matrix, workspace.kriging_weights,
                )
                have_weights = code != _SOLVE_SINGULAR
                if code == _SOLVE_SINGULAR:
                    workspace.counts.singular += 1
                elif code == _SOLVE_SUM_WARNING:
                    workspace.counts.sum_warning += 1
                workspace.cached_selection = workspace.selected.index.copy()
            if have_weights:
                estimate = _weighted_estimate(workspace.kriging_weights, workspace.selected, stations, day)
            else:
                estimate = OUT_NODATA
        elif estimator == _ESTIMATE_SINGLE:
            var station = workspace.selected.index[0]
            var distance = _distance(
                cell_x, cell_y, stations.x[unsafe_offset=station], stations.y[unsafe_offset=station]
            )
            estimate = _single_station_estimate(stations.value(station, day), distance, settings)
        elif estimator == _ESTIMATE_IDW:
            _idw_weights(workspace.selected, cell_x, cell_y, stations, workspace.idw_weights)
            estimate = _weighted_estimate(workspace.idw_weights, workspace.selected, stations, day)
        else:
            estimate = 0.0

        if settings.correct_negative:
            estimate = _correct_negative(estimate)
        estimates[unsafe_offset=first_output + day] = estimate


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def krige_cells[System: KrigingSystem](
    *, system: System, cells: Cells, stations: Stations, model: Variogram, settings: KrigingSettings, estimates: F32Ptr
) -> KrigingCounts:
    """Estimates every cell on every day with the kriging system `system`.

    The work is spread over `parallelism_level()` worker threads: the number of CPUs
    this process is allowed to use (restrict the process before the Mojo runtime
    starts to use fewer). The cost of a cell varies a lot with the station density,
    and neighbouring cells cost about the same, so the cells are cut into
    `_CHUNKS_PER_WORKER` contiguous chunks per worker and worker `w` takes the chunks
    `w, w + n_workers, ...`: every worker gets a share of every region, dense and
    sparse alike, so all workers finish at about the same time. Each cell writes only
    its own days of `estimates`, so the result does not depend on the number of workers.

    Does not touch Python: callers from Python release the GIL around it.

    Parameters:
        System: The kriging system type (ordinary or external drift).

    Args:
        system: The kriging system (`OrdinaryKrigingSystem`, or the external drift one).
        cells: The cells to estimate.
        stations: The stations and their values.
        model: The fitted variogram.
        settings: Search radius, nodata values and the precipitation options.
        estimates: Receives the estimates, C-ordered `(cells.count, stations.day_count)` Float32.

    Returns:
        How many systems were singular or had weights not summing to 1.
    """
    if cells.count < 1:
        return KrigingCounts(0, 0)
    var n_workers = min(parallelism_level(None), cells.count)
    var n_chunks = min(_CHUNKS_PER_WORKER * n_workers, cells.count)
    var cell_count = cells.count

    # Every worker counts into its own entry; they are added in worker order.
    var singular = List[Int](length=n_workers, fill=0)
    var sum_warning = List[Int](length=n_workers, fill=0)

    def work(worker: Int) {var system, var cells, var stations, var model, var settings, var estimates,
                           ref singular, ref sum_warning, var cell_count, var n_chunks, var n_workers}:
        """Estimates the cells of the chunks `worker, worker + n_workers, ...` and stores its counts.

        Args:
            worker: Index of this worker, `0 <= worker < n_workers`.
        """
        var workspace = _Workspace()
        for chunk in range(worker, n_chunks, n_workers):
            for cell in range(chunk * cell_count // n_chunks, (chunk + 1) * cell_count // n_chunks):
                _krige_cell_kernel(
                    system=system, cells=cells, cell=cell, stations=stations, model=model,
                    settings=settings, estimates=estimates, workspace=workspace,
                )
        singular[worker] = workspace.counts.singular
        sum_warning[worker] = workspace.counts.sum_warning

    if n_workers > 1:
        parallelize(work, n_workers, n_workers)
    else:
        work(0)

    var counts = KrigingCounts(0, 0)
    for worker in range(n_workers):
        counts.singular += singular[worker]
        counts.sum_warning += sum_warning[worker]
    return counts


def ordinary_kriging(
    *,
    cell_x: Pointer[Float64, _],
    cell_y: Pointer[Float64, _],
    cell_elevation: Pointer[Float64, _],
    cell_count: Int,
    station_x: Pointer[Float64, _],
    station_y: Pointer[Float64, _],
    station_elevation: Pointer[Float64, _],
    station_values: Pointer[Float64, _],
    station_count: Int,
    day_count: Int,
    model: Variogram,
    settings: KrigingSettings,
    estimates: Pointer[Float32, _],
) -> KrigingCounts:
    """Ordinary kriging of every cell on every day, on all CPUs the process may use.

    See `krige_cells` for how the work is spread.

    Args:
        cell_x: Cell eastings [m], `cell_count` entries.
        cell_y: Cell northings [m], `cell_count` entries.
        cell_elevation: Cell elevations [m]; only used to recognise cells without DEM value.
        cell_count: Number of cells.
        station_x: Station eastings [m], `station_count` entries.
        station_y: Station northings [m], `station_count` entries.
        station_elevation: Station elevations [m], `station_count` entries.
        station_values: C-ordered `(station_count, day_count)`; missing values equal `settings.nodata`.
        station_count: Number of stations.
        day_count: Number of days.
        model: The fitted variogram.
        settings: Search radius, nodata values and the precipitation options.
        estimates: Receives the estimates, C-ordered `(cell_count, day_count)` Float32.

    Returns:
        How many kriging systems were singular (their cell-days got `OUT_NODATA`) or
        had weights not summing to 1 within 1e-4 (used anyway).
    """
    return krige_cells(
        system=OrdinaryKrigingSystem(),
        cells=Cells.from_pointers(cell_x, cell_y, cell_elevation, cell_count),
        stations=Stations.from_pointers(
            station_x, station_y, station_elevation, station_values, station_count, day_count
        ),
        model=model, settings=settings, estimates=untracked(estimates),
    )
