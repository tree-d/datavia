"""Checks ordinary_kriging.mojo and external_drift_kriging.mojo against a plain scalar
reference implementation of the kriging matrix, right-hand side, LU solve and cell loop
(defined below; it is the single-threaded scalar version of the same algorithm).

    pixi run mojo run -I packages/kriging/datavia/kriging packages/kriging/tests/mojo/test_kriging.mojo
    (or, from the datavia root: pixi run test-kriging-mojo)
"""

from std.math import sqrt
from std.random import random_float64, seed

from kernels.external_drift_kriging import ExternalDriftSystem, external_drift_kriging
from kernels.ordinary_kriging import (
    OUT_NODATA,
    KrigingSettings,
    OrdinaryKrigingSystem,
    SelectedStations,
    Stations,
    _kriging_weights,
    _lu_solve_kernel,
    _SOLVE_OK,
    _SOLVE_SINGULAR,
    _SOLVE_SUM_WARNING,
    ordinary_kriging,
)
from kernels.variogram import EXPONENTIAL_MODEL, SPHERICAL_MODEL, Variogram, variogram_value
from testing_helpers import close

comptime NODATA = -9.0
"""Marks a missing station value."""
comptime DEM_NODATA = -9999.0
"""Marks a cell without DEM value."""


# ---------------------------------------------------------------------------
# Scalar reference: the same algorithm, one entry at a time, single-threaded
# ---------------------------------------------------------------------------


def distance(x1: Float64, y1: Float64, x2: Float64, y2: Float64) -> Float64:
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


struct Data(Movable):
    """Random stations, cells and station values, owned here."""

    var station_x: List[Float64]
    """Station eastings [m]."""
    var station_y: List[Float64]
    """Station northings [m]."""
    var station_elevation: List[Float64]
    """Station elevations [m]."""
    var station_values: List[Float64]
    """C-ordered `(station_count, day_count)`; missing values equal `NODATA`."""
    var station_count: Int
    """Number of stations."""
    var day_count: Int
    """Number of days."""
    var cell_x: List[Float64]
    """Cell eastings [m]."""
    var cell_y: List[Float64]
    """Cell northings [m]."""
    var cell_elevation: List[Float64]
    """Cell elevations [m]; `DEM_NODATA` for one cell."""
    var cell_count: Int
    """Number of cells."""

    def __init__(out self, station_count: Int, day_count: Int, cell_count: Int, span: Float64):
        """Creates random, precipitation-like data in a square of side `span`.

        About 15 % of the values are missing and 25 % are 0. On day 0 every station
        reports; on the last day only the first two do. With more than 2 cells, cell 1
        has no DEM value and cell 2 lies exactly on station 0.

        Args:
            station_count: Number of stations.
            day_count: Number of days.
            cell_count: Number of cells.
            span: Side length [m] of the square the stations and cells lie in.
        """
        self.station_count = station_count
        self.day_count = day_count
        self.cell_count = cell_count
        self.station_x = List[Float64]()
        self.station_y = List[Float64]()
        self.station_elevation = List[Float64]()
        self.station_values = List[Float64](length=station_count * day_count, fill=0.0)
        self.cell_x = List[Float64]()
        self.cell_y = List[Float64]()
        self.cell_elevation = List[Float64]()
        for _ in range(station_count):
            self.station_x.append(random_float64() * span)
            self.station_y.append(random_float64() * span)
            self.station_elevation.append(50.0 + random_float64() * 900.0)
        for station in range(station_count):
            for day in range(day_count):
                var draw = random_float64()
                # some missing values, many zeros (precipitation-like), the rest positive
                var value = NODATA if draw < 0.15 else (
                    0.0 if draw < 0.4 else Float64(Int(random_float64() * 300.0)) * 0.1
                )
                self.station_values[station * day_count + day] = value
        # day 0: every station reports, so whole neighbourhoods are kriged; last day: almost none report
        for station in range(station_count):
            self.station_values[station * day_count] = 1.0 + Float64(station % 7)
            if day_count > 1 and station > 1:
                self.station_values[station * day_count + day_count - 1] = NODATA
        for _ in range(cell_count):
            self.cell_x.append(random_float64() * span)
            self.cell_y.append(random_float64() * span)
            self.cell_elevation.append(50.0 + random_float64() * 900.0)
        if cell_count > 2 and station_count > 0:
            self.cell_elevation[1] = DEM_NODATA  # a cell without DEM value
            self.cell_x[2] = self.station_x[0]  # a cell on a station
            self.cell_y[2] = self.station_y[0]

    def value(self, station: Int, day: Int) -> Float64:
        """Returns the value of one station on one day.

        Args:
            station: Station index.
            day: Day index.

        Returns:
            The value, or `NODATA`.
        """
        return self.station_values[station * self.day_count + day]


def reference_matrix(
    selected: List[Int], data: Data, model: Variogram, use_drift: Bool, mut matrix: List[Float64]
) -> Int:
    """Assembles the OK or EDK matrix entry by entry.

    Args:
        selected: Indices of the stations in the system.
        data: The stations.
        model: The variogram.
        use_drift: True for external drift kriging, False for ordinary kriging.
        matrix: Replaced by the matrix, row-major.

    Returns:
        The size of the system (number of rows).
    """
    var n = len(selected)
    var size = n + 2 if use_drift else n + 1
    matrix = List[Float64](length=size * size, fill=0.0)
    for i in range(n):
        var station_i = selected[i]
        for j in range(i + 1, n):
            var station_j = selected[j]
            var semivariance = variogram_value(
                distance(
                    data.station_x[station_i], data.station_y[station_i],
                    data.station_x[station_j], data.station_y[station_j],
                ),
                model,
            )
            matrix[i * size + j] = semivariance
            matrix[j * size + i] = semivariance
        matrix[i * size + i] = variogram_value(0.0, model)
        matrix[i * size + n] = 1.0
        matrix[n * size + i] = 1.0
        if use_drift:
            matrix[i * size + n + 1] = data.station_elevation[station_i]
            matrix[(n + 1) * size + i] = data.station_elevation[station_i]
    return size


def reference_right_hand_side(
    selected: List[Int],
    cell_x: Float64,
    cell_y: Float64,
    cell_elevation: Float64,
    data: Data,
    model: Variogram,
    use_drift: Bool,
    mut right_hand_side: List[Float64],
):
    """Assembles the OK or EDK right-hand side entry by entry.

    Args:
        selected: Indices of the stations in the system.
        cell_x: Easting [m] of the cell.
        cell_y: Northing [m] of the cell.
        cell_elevation: Elevation [m] of the cell (used with `use_drift`).
        data: The stations.
        model: The variogram.
        use_drift: True for external drift kriging, False for ordinary kriging.
        right_hand_side: Replaced by the right-hand side.
    """
    var n = len(selected)
    var size = n + 2 if use_drift else n + 1
    right_hand_side = List[Float64](length=size, fill=0.0)
    for i in range(n):
        right_hand_side[i] = variogram_value(
            distance(cell_x, cell_y, data.station_x[selected[i]], data.station_y[selected[i]]), model
        )
    right_hand_side[n] = 1.0
    if use_drift:
        right_hand_side[n + 1] = cell_elevation


def reference_lu_solve(mut a: List[Float64], mut b: List[Float64], size: Int) -> Bool:
    """Solves A x = b by Gaussian elimination with partial pivoting, scalar.

    Args:
        a: The matrix A, row-major `(size, size)`; destroyed.
        b: The right-hand side b; replaced by the solution x.
        size: Number of unknowns.

    Returns:
        False if the matrix is singular or the solution is not finite, else True.
    """
    for col in range(size):
        var pivot = col
        var largest = abs(a[col * size + col])
        for row in range(col + 1, size):
            if abs(a[row * size + col]) > largest:
                largest = abs(a[row * size + col])
                pivot = row
        if largest == 0.0:
            return False
        if pivot != col:
            for k in range(size):
                var swapped = a[col * size + k]
                a[col * size + k] = a[pivot * size + k]
                a[pivot * size + k] = swapped
            var swapped = b[col]
            b[col] = b[pivot]
            b[pivot] = swapped
        var diagonal = a[col * size + col]
        for row in range(col + 1, size):
            var factor = a[row * size + col] / diagonal
            if factor != 0.0:
                for k in range(col + 1, size):
                    a[row * size + k] -= factor * a[col * size + k]
                b[row] -= factor * b[col]
            a[row * size + col] = 0.0
    for row in range(size - 1, -1, -1):
        var total = b[row]
        for k in range(row + 1, size):
            total -= a[row * size + k] * b[k]
        b[row] = total / a[row * size + row]
        if not (abs(b[row]) < Float64.MAX):
            return False
    return True


def reference_weights(
    selected: List[Int],
    cell_x: Float64,
    cell_y: Float64,
    cell_elevation: Float64,
    data: Data,
    model: Variogram,
    use_drift: Bool,
    mut weights: List[Float64],
) -> Int:
    """Assembles and solves the kriging system of one cell with the reference functions.

    Args:
        selected: Indices of the stations in the system.
        cell_x: Easting [m] of the cell.
        cell_y: Northing [m] of the cell.
        cell_elevation: Elevation [m] of the cell.
        data: The stations.
        model: The variogram.
        use_drift: True for external drift kriging, False for ordinary kriging.
        weights: Replaced by the weights, followed by the Lagrange multipliers.

    Returns:
        `_SOLVE_OK`, `_SOLVE_SINGULAR`, or `_SOLVE_SUM_WARNING` if the weights do
        not sum to 1 within 1e-4.
    """
    var matrix = List[Float64]()
    var size = reference_matrix(selected, data, model, use_drift, matrix)
    reference_right_hand_side(selected, cell_x, cell_y, cell_elevation, data, model, use_drift, weights)
    if not reference_lu_solve(matrix, weights, size):
        return _SOLVE_SINGULAR
    var weight_sum: Float64 = 0.0
    for i in range(len(selected)):
        weight_sum += weights[i]
    if abs(weight_sum - 1.0) > 1.0e-4:
        return _SOLVE_SUM_WARNING
    return _SOLVE_OK


def reference_krige(
    data: Data, model: Variogram, settings: KrigingSettings, use_drift: Bool, mut estimates: List[Float32]
) -> Tuple[Int, Int]:
    """Runs the reference cell loop for every cell.

    Args:
        data: The stations and cells.
        model: The variogram.
        settings: Search radius, nodata values and the precipitation options.
        use_drift: True for external drift kriging, False for ordinary kriging.
        estimates: Receives the estimates, C-ordered `(cell_count, day_count)`.

    Returns:
        The number of singular systems and of weight sets not summing to 1.
    """
    var singular_count = 0
    var sum_warning_count = 0
    for cell in range(data.cell_count):
        var cell_x = data.cell_x[cell]
        var cell_y = data.cell_y[cell]
        var cell_elevation = data.cell_elevation[cell]
        var rounded_elevation = Float64(
            Int(cell_elevation + 0.5 if cell_elevation >= 0.0 else cell_elevation - 0.5)
        )
        if rounded_elevation == settings.dem_nodata:
            for day in range(data.day_count):
                estimates[cell * data.day_count + day] = OUT_NODATA
            continue
        var neighbours = List[Int]()
        for station in range(data.station_count):
            if distance(
                cell_x, cell_y, data.station_x[station], data.station_y[station]
            ) <= settings.max_dist:
                neighbours.append(station)
        var have_weights = False
        var cached_selection = List[Int]()
        var weights = List[Float64]()
        for day in range(data.day_count):
            var selected = List[Int]()
            var zero_count = 0
            for station in neighbours:
                if data.value(station, day) != settings.nodata:
                    if data.value(station, day) == 0.0:
                        zero_count += 1
                    selected.append(station)
            var estimate: Float32
            if zero_count == len(selected):
                estimate = 0.0
            elif len(selected) == 1:
                var station = selected[0]
                var station_distance = distance(cell_x, cell_y, data.station_x[station], data.station_y[station])
                if settings.dist_zero and station_distance > settings.threshold_dist:
                    estimate = 0.0
                else:
                    estimate = Float32(data.value(station, day))
            elif len(selected) == 2:
                var idw_weights = List[Float64](length=2, fill=0.0)
                var on_station = -1
                for i in range(2):
                    var station_distance = distance(
                        cell_x, cell_y, data.station_x[selected[i]], data.station_y[selected[i]]
                    )
                    if station_distance == 0.0:
                        on_station = i
                        break
                    idw_weights[i] = 1.0 / (station_distance * station_distance)
                if on_station >= 0:
                    idw_weights[0] = 0.0
                    idw_weights[1] = 0.0
                    idw_weights[on_station] = 1.0
                else:
                    var weight_sum = idw_weights[0] + idw_weights[1]
                    idw_weights[0] /= weight_sum
                    idw_weights[1] /= weight_sum
                estimate = 0.0
                for i in range(2):
                    estimate += Float32(idw_weights[i] * data.value(selected[i], day))
            else:
                var same = have_weights and len(selected) == len(cached_selection)
                if same:
                    for i in range(len(selected)):
                        if selected[i] != cached_selection[i]:
                            same = False
                if not same:
                    var code = reference_weights(
                        selected, cell_x, cell_y, cell_elevation, data, model, use_drift, weights
                    )
                    have_weights = code != _SOLVE_SINGULAR
                    if code == _SOLVE_SINGULAR:
                        singular_count += 1
                    elif code == _SOLVE_SUM_WARNING:
                        sum_warning_count += 1
                    cached_selection = selected.copy()
                if have_weights:
                    estimate = 0.0
                    for i in range(len(selected)):
                        estimate += Float32(weights[i] * data.value(selected[i], day))
                else:
                    estimate = OUT_NODATA
            if settings.correct_negative and estimate > OUT_NODATA and estimate < 0.0:
                estimate = 0.0
            estimates[cell * data.day_count + day] = estimate
    return (singular_count, sum_warning_count)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def stations_of(data: Data) -> Stations:
    """Returns the kernel's view of the stations of `data`.

    Args:
        data: The data; must outlive the returned value.

    Returns:
        `Stations` reading the lists of `data` in place.
    """
    return Stations.from_pointers(
        data.station_x.unsafe_ptr(), data.station_y.unsafe_ptr(), data.station_elevation.unsafe_ptr(),
        data.station_values.unsafe_ptr(), data.station_count, data.day_count,
    )


def check_parts() raises:
    """Checks matrix, right-hand side and solve, each on its own, against the reference.

    Raises:
        Error: If an assembled entry differs by more than 1e-14 relative.
    """
    var assembly_differs = 0
    var solve_differs = 0
    var total = 0
    for n in [3, 5, 17, 64, 160]:
        var data = Data(n, 1, 0, 100_000.0)
        var selected = SelectedStations()
        for i in range(n):
            selected.index.append(i)
        selected.gather(stations_of(data))
        for model_type in [SPHERICAL_MODEL, EXPONENTIAL_MODEL]:
            var model = Variogram(0.05, 0.8, 30_000.0, model_type)
            for use_drift in [False, True]:
                var size = n + 2 if use_drift else n + 1
                var matrix = List[Float64](length=size * size, fill=-1.0)
                var right_hand_side = List[Float64](length=size, fill=-1.0)
                if use_drift:
                    ExternalDriftSystem().assemble_matrix(selected, model, matrix, size)
                    ExternalDriftSystem().assemble_right_hand_side(
                        selected, 1234.5, 6789.0, 300.0, model, right_hand_side, size
                    )
                else:
                    OrdinaryKrigingSystem().assemble_matrix(selected, model, matrix, size)
                    OrdinaryKrigingSystem().assemble_right_hand_side(
                        selected, 1234.5, 6789.0, 300.0, model, right_hand_side, size
                    )
                var expected_matrix = List[Float64]()
                var expected_right_hand_side = List[Float64]()
                _ = reference_matrix(selected.index, data, model, use_drift, expected_matrix)
                reference_right_hand_side(
                    selected.index, 1234.5, 6789.0, 300.0, data, model, use_drift, expected_right_hand_side
                )
                var label = String("n=", n, " model_type=", model_type, " drift=", use_drift)
                for k in range(size * size):
                    if not close(matrix[k], expected_matrix[k], 1e-14):
                        raise Error(String("matrix ", label, " entry ", k))
                    if matrix[k] != expected_matrix[k]:
                        assembly_differs += 1
                for k in range(size):
                    if not close(right_hand_side[k], expected_right_hand_side[k], 1e-14):
                        raise Error(String("right-hand side ", label, " entry ", k))
                    if right_hand_side[k] != expected_right_hand_side[k]:
                        assembly_differs += 1
                # the same system through both solvers
                var solved_matrix = expected_matrix.copy()
                var solution = expected_right_hand_side.copy()
                _ = _lu_solve_kernel[4](solved_matrix, solution, size)
                _ = reference_lu_solve(expected_matrix, expected_right_hand_side, size)
                for k in range(size):
                    if solution[k] != expected_right_hand_side[k]:
                        solve_differs += 1
                total += 1
    print("parts ok,", assembly_differs, "assembled entries and", solve_differs,
          "solution entries differ in the last bits, over", total, "systems")


def check_weights() raises:
    """Checks the weights of single systems, OK and EDK, both model types, against the reference.

    Raises:
        Error: If the solve outcome differs, or a weight differs by more than 1e-9
            of the largest weight.
    """
    var identical = 0
    var total = 0
    for n in [3, 4, 5, 6, 17, 64, 160]:
        var data = Data(n, 1, 0, 100_000.0)
        var stations = stations_of(data)
        for model_type in [SPHERICAL_MODEL, EXPONENTIAL_MODEL]:
            var model = Variogram(0.05, 0.8, 30_000.0, model_type)
            for use_drift in [False, True]:
                var selected = SelectedStations()
                for i in range(n):
                    selected.index.append(i)
                selected.gather(stations)
                var cell_x = 50_000.0 * random_float64()
                var cell_y = 50_000.0 * random_float64()
                var cell_elevation = 500.0
                var matrix = List[Float64]()
                var weights = List[Float64]()
                var code: Int
                if use_drift:
                    code = _kriging_weights(
                        ExternalDriftSystem(), selected, cell_x, cell_y, cell_elevation, model, matrix, weights
                    )
                else:
                    code = _kriging_weights(
                        OrdinaryKrigingSystem(), selected, cell_x, cell_y, cell_elevation, model, matrix, weights
                    )
                var expected = List[Float64]()
                var expected_code = reference_weights(
                    selected.index, cell_x, cell_y, cell_elevation, data, model, use_drift, expected
                )
                var label = String("weights n=", n, " model_type=", model_type, " drift=", use_drift)
                if code != expected_code:
                    raise Error(label + ": code " + String(code) + " vs " + String(expected_code))
                # The assembly rounds differently in the last bit (see check_parts), which moves
                # near-zero weights by more than their own size: compare to the largest weight.
                var largest: Float64 = 0.0
                for i in range(len(selected.index)):
                    largest = max(largest, abs(expected[i]))
                var same = True
                for i in range(len(expected)):
                    if weights[i] != expected[i]:
                        same = False
                    var scale = largest if i < len(selected.index) else abs(expected[i])
                    if abs(weights[i] - expected[i]) > 1e-9 * scale + 1e-300:
                        raise Error(
                            label + ": weight " + String(i) + ": " + String(weights[i]) + " vs " + String(expected[i])
                        )
                total += 1
                identical += 1 if same else 0
    print("weights ok,", identical, "of", total, "systems bit-identical to the reference")


def check_edk_extends_ok() raises:
    """Checks that the EDK matrix is the OK matrix bordered by the elevations.

    Raises:
        Error: If the OK block, the drift row and column, or the zero block differ.
    """
    var n = 9
    var data = Data(n, 1, 0, 50_000.0)
    var selected = SelectedStations()
    for i in range(n):
        selected.index.append(i)
    selected.gather(stations_of(data))
    var model = Variogram(0.1, 0.6, 20_000.0, EXPONENTIAL_MODEL)
    var ok = List[Float64](length=(n + 1) * (n + 1), fill=-1.0)
    var edk = List[Float64](length=(n + 2) * (n + 2), fill=-1.0)
    OrdinaryKrigingSystem().assemble_matrix(selected, model, ok, n + 1)
    ExternalDriftSystem().assemble_matrix(selected, model, edk, n + 2)
    for i in range(n + 1):
        for j in range(n + 1):
            if ok[i * (n + 1) + j] != edk[i * (n + 2) + j]:
                raise Error(String("EDK block differs from OK at ", i, ",", j))
    for i in range(n):
        var elevation = data.station_elevation[i]
        if edk[i * (n + 2) + n + 1] != elevation or edk[(n + 1) * (n + 2) + i] != elevation:
            raise Error(String("EDK drift row/column wrong at ", i))
    for i in range(n, n + 2):
        for j in range(n, n + 2):
            if edk[i * (n + 2) + j] != 0.0:
                raise Error("EDK constraint block not zero")
    print("edk extends ok: ok")


def check_cells(station_count: Int, day_count: Int, cell_count: Int, model_type: Int, use_drift: Bool) raises:
    """Checks the whole cell loop against the reference cell loop.

    Prints a checksum, so runs with different worker counts can be compared.

    Args:
        station_count: Number of random stations.
        day_count: Number of days.
        cell_count: Number of random cells.
        model_type: `SPHERICAL_MODEL` or `EXPONENTIAL_MODEL`.
        use_drift: True for external drift kriging, False for ordinary kriging.

    Raises:
        Error: If the problem counts differ, or an estimate differs by more than
            1e-5 relative and 1e-6 absolute.
    """
    var data = Data(station_count, day_count, cell_count, 200_000.0)
    var model = Variogram(0.05, 0.9, 40_000.0, model_type)
    var settings = KrigingSettings(
        max_dist=60_000.0, nodata=NODATA, dem_nodata=DEM_NODATA,
        correct_negative=True, dist_zero=True, threshold_dist=20_000.0,
    )
    var estimates = List[Float32](length=cell_count * day_count, fill=123.0)
    var expected = List[Float32](length=cell_count * day_count, fill=456.0)
    var counts = (
        external_drift_kriging(
            cell_x=data.cell_x.unsafe_ptr(), cell_y=data.cell_y.unsafe_ptr(),
            cell_elevation=data.cell_elevation.unsafe_ptr(), cell_count=cell_count,
            station_x=data.station_x.unsafe_ptr(), station_y=data.station_y.unsafe_ptr(),
            station_elevation=data.station_elevation.unsafe_ptr(),
            station_values=data.station_values.unsafe_ptr(), station_count=station_count, day_count=day_count,
            model=model, settings=settings, estimates=estimates.unsafe_ptr(),
        ) if use_drift else ordinary_kriging(
            cell_x=data.cell_x.unsafe_ptr(), cell_y=data.cell_y.unsafe_ptr(),
            cell_elevation=data.cell_elevation.unsafe_ptr(), cell_count=cell_count,
            station_x=data.station_x.unsafe_ptr(), station_y=data.station_y.unsafe_ptr(),
            station_elevation=data.station_elevation.unsafe_ptr(),
            station_values=data.station_values.unsafe_ptr(), station_count=station_count, day_count=day_count,
            model=model, settings=settings, estimates=estimates.unsafe_ptr(),
        )
    )
    var (singular_count, sum_warning_count) = reference_krige(data, model, settings, use_drift, expected)
    var label = String(
        "cells station_count=", station_count, " day_count=", day_count, " cell_count=", cell_count,
        " model_type=", model_type, " drift=", use_drift,
    )
    if counts.singular != singular_count or counts.sum_warning != sum_warning_count:
        raise Error(label + ": counts " + String(counts.singular) + "/" + String(counts.sum_warning)
                    + " vs " + String(singular_count) + "/" + String(sum_warning_count))
    var differing = 0
    var checksum: Float64 = 0.0  # compare runs with different worker counts (e.g. under `taskset -c 0`)
    for k in range(cell_count * day_count):
        checksum += Float64(k % 97 + 1) * Float64(estimates[k])
        if estimates[k] != expected[k]:
            differing += 1
            # relative, or absolute for estimates that cancel to about 0 (values here are 0 .. 30)
            if not close(Float64(estimates[k]), Float64(expected[k]), 1e-5) and abs(estimates[k] - expected[k]) > 1e-6:
                raise Error(
                    label + ": value " + String(k) + ": " + String(estimates[k]) + " vs " + String(expected[k])
                )
    print(label, "ok,", differing, "values differ in the last bits, checksum", repr(checksum))


def main() raises:
    """Runs all checks with a fixed random seed.

    Raises:
        Error: If any check fails.
    """
    seed(11)
    check_parts()
    check_weights()
    check_edk_extends_ok()
    for station_count in [0, 1, 2, 3, 40, 300]:
        for use_drift in [False, True]:
            check_cells(station_count, 7, 200, EXPONENTIAL_MODEL, use_drift)
    check_cells(300, 31, 500, SPHERICAL_MODEL, True)
    check_cells(300, 1, 3, EXPONENTIAL_MODEL, False)
    print("all ok")
