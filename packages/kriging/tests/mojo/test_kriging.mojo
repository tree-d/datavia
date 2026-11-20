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
    _SOLVE_SINGULAR,
    _SOLVE_SUM_WARNING,
    ordinary_kriging,
)
from kernels.variogram import EXPONENTIAL_MODEL, SPHERICAL_MODEL, Variogram, variogram_value

comptime NODATA = -9.0
comptime DEM_NODATA = -9999.0


# ---------------------------------------------------------------------------
# Scalar reference: the same algorithm, one entry at a time, single-threaded
# ---------------------------------------------------------------------------


def distance(x1: Float64, y1: Float64, x2: Float64, y2: Float64) -> Float64:
    var dx = x1 - x2
    var dy = y1 - y2
    return sqrt(dx * dx + dy * dy)


struct Data(Movable):
    """Stations, cells and their values, owned here."""

    var sx: List[Float64]
    var sy: List[Float64]
    var sh: List[Float64]
    var sz: List[Float64]
    var nsta: Int
    var ntime: Int
    var cx: List[Float64]
    var cy: List[Float64]
    var ch: List[Float64]
    var ncell: Int

    def __init__(out self, nsta: Int, ntime: Int, ncell: Int, span: Float64):
        self.nsta = nsta
        self.ntime = ntime
        self.ncell = ncell
        self.sx = List[Float64]()
        self.sy = List[Float64]()
        self.sh = List[Float64]()
        self.sz = List[Float64](length=nsta * ntime, fill=0.0)
        self.cx = List[Float64]()
        self.cy = List[Float64]()
        self.ch = List[Float64]()
        for _ in range(nsta):
            self.sx.append(random_float64() * span)
            self.sy.append(random_float64() * span)
            self.sh.append(50.0 + random_float64() * 900.0)
        for i in range(nsta):
            for t in range(ntime):
                var u = random_float64()
                # some missing values, many zeros (precipitation-like), the rest positive
                var value = NODATA if u < 0.15 else (0.0 if u < 0.4 else Float64(Int(random_float64() * 300.0)) * 0.1)
                self.sz[i * ntime + t] = value
        # day 0: every station reports, so whole neighbourhoods are kriged; last day: almost none report
        for i in range(nsta):
            self.sz[i * ntime] = 1.0 + Float64(i % 7)
            if ntime > 1 and i > 1:
                self.sz[i * ntime + ntime - 1] = NODATA
        for _ in range(ncell):
            self.cx.append(random_float64() * span)
            self.cy.append(random_float64() * span)
            self.ch.append(50.0 + random_float64() * 900.0)
        if ncell > 2 and nsta > 0:
            self.ch[1] = DEM_NODATA  # a cell without DEM value
            self.cx[2] = self.sx[0]  # a cell on a station
            self.cy[2] = self.sy[0]

    def value(self, i: Int, day: Int) -> Float64:
        return self.sz[i * self.ntime + day]


def reference_matrix(sel: List[Int], d: Data, v: Variogram, use_drift: Bool, mut a: List[Float64]) -> Int:
    var n = len(sel)
    var m = n + 2 if use_drift else n + 1
    a = List[Float64](length=m * m, fill=0.0)
    for i in range(n):
        var ii = sel[i]
        for j in range(i + 1, n):
            var jj = sel[j]
            var g = variogram_value(distance(d.sx[ii], d.sy[ii], d.sx[jj], d.sy[jj]), v)
            a[i * m + j] = g
            a[j * m + i] = g
        a[i * m + i] = variogram_value(0.0, v)
        a[i * m + n] = 1.0
        a[n * m + i] = 1.0
        if use_drift:
            a[i * m + n + 1] = d.sh[ii]
            a[(n + 1) * m + i] = d.sh[ii]
    return m


def reference_rhs(sel: List[Int], cx: Float64, cy: Float64, ch: Float64, d: Data, v: Variogram, use_drift: Bool, mut b: List[Float64]):
    var n = len(sel)
    var m = n + 2 if use_drift else n + 1
    b = List[Float64](length=m, fill=0.0)
    for i in range(n):
        b[i] = variogram_value(distance(cx, cy, d.sx[sel[i]], d.sy[sel[i]]), v)
    b[n] = 1.0
    if use_drift:
        b[n + 1] = ch


def reference_lu_solve(mut a: List[Float64], mut b: List[Float64], m: Int) -> Bool:
    for col in range(m):
        var piv = col
        var best = abs(a[col * m + col])
        for row in range(col + 1, m):
            if abs(a[row * m + col]) > best:
                best = abs(a[row * m + col])
                piv = row
        if best == 0.0:
            return False
        if piv != col:
            for k in range(m):
                var t = a[col * m + k]
                a[col * m + k] = a[piv * m + k]
                a[piv * m + k] = t
            var tb = b[col]
            b[col] = b[piv]
            b[piv] = tb
        var dg = a[col * m + col]
        for row in range(col + 1, m):
            var f = a[row * m + col] / dg
            if f != 0.0:
                for k in range(col + 1, m):
                    a[row * m + k] -= f * a[col * m + k]
                b[row] -= f * b[col]
            a[row * m + col] = 0.0
    for row in range(m - 1, -1, -1):
        var s = b[row]
        for k in range(row + 1, m):
            s -= a[row * m + k] * b[k]
        b[row] = s / a[row * m + row]
        if not (abs(b[row]) < Float64.MAX):
            return False
    return True


def reference_weights(
    sel: List[Int], cx: Float64, cy: Float64, ch: Float64, d: Data, v: Variogram, use_drift: Bool, mut w: List[Float64]
) -> Int:
    var a = List[Float64]()
    var m = reference_matrix(sel, d, v, use_drift, a)
    reference_rhs(sel, cx, cy, ch, d, v, use_drift, w)
    if not reference_lu_solve(a, w, m):
        return 1
    var s: Float64 = 0.0
    for i in range(len(sel)):
        s += w[i]
    if abs(s - 1.0) > 1.0e-4:
        return 2
    return 0


def reference_krige(d: Data, v: Variogram, p: KrigingSettings, use_drift: Bool, mut out: List[Float32]) -> Tuple[Int, Int]:
    """The krige_cell of the reference for every cell."""
    var n_singular = 0
    var n_sum_warning = 0
    for k in range(d.ncell):
        var cx = d.cx[k]
        var cy = d.cy[k]
        var ch = d.ch[k]
        if Float64(Int(ch + 0.5 if ch >= 0.0 else ch - 0.5)) == p.dem_nodata:
            for day in range(d.ntime):
                out[k * d.ntime + day] = OUT_NODATA
            continue
        var neigh = List[Int]()
        for j in range(d.nsta):
            if distance(cx, cy, d.sx[j], d.sy[j]) <= p.max_dist:
                neigh.append(j)
        var have_weights = False
        var sel_cached = List[Int]()
        var w = List[Float64]()
        for day in range(d.ntime):
            var sel = List[Int]()
            var n_zero = 0
            for j in neigh:
                if d.value(j, day) != p.nodata:
                    if d.value(j, day) == 0.0:
                        n_zero += 1
                    sel.append(j)
            var z: Float32
            if n_zero == len(sel):
                z = 0.0
            elif len(sel) == 1:
                var j = sel[0]
                if p.dist_zero and distance(cx, cy, d.sx[j], d.sy[j]) > p.threshold_dist:
                    z = 0.0
                else:
                    z = Float32(d.value(j, day))
            elif len(sel) == 2:
                var wi = List[Float64](length=2, fill=0.0)
                var on_station = -1
                for i in range(2):
                    var dd = distance(cx, cy, d.sx[sel[i]], d.sy[sel[i]])
                    if dd == 0.0:
                        on_station = i
                        break
                    wi[i] = 1.0 / (dd * dd)
                if on_station >= 0:
                    wi[0] = 0.0
                    wi[1] = 0.0
                    wi[on_station] = 1.0
                else:
                    var s = wi[0] + wi[1]
                    wi[0] /= s
                    wi[1] /= s
                z = 0.0
                for i in range(2):
                    z += Float32(wi[i] * d.value(sel[i], day))
            else:
                var same = have_weights and len(sel) == len(sel_cached)
                if same:
                    for i in range(len(sel)):
                        if sel[i] != sel_cached[i]:
                            same = False
                if not same:
                    var code = reference_weights(sel, cx, cy, ch, d, v, use_drift, w)
                    have_weights = code != 1
                    if code == 1:
                        n_singular += 1
                    elif code == 2:
                        n_sum_warning += 1
                    sel_cached = sel.copy()
                if have_weights:
                    z = 0.0
                    for i in range(len(sel)):
                        z += Float32(w[i] * d.value(sel[i], day))
                else:
                    z = OUT_NODATA
            if p.correct_negative and z > OUT_NODATA and z < 0.0:
                z = 0.0
            out[k * d.ntime + day] = z
    return (n_singular, n_sum_warning)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def stations_of(d: Data) -> Stations:
    return Stations(
        _ptr(d.sx), _ptr(d.sy), _ptr(d.sh), _ptr(d.sz), d.nsta, d.ntime
    )


def _ptr(list: List[Float64]) -> Pointer[Float64, MutUntrackedOrigin]:
    return Pointer[Float64, MutUntrackedOrigin](unsafe_from_address=Int(list.unsafe_ptr()))


def close(a: Float64, b: Float64, rel: Float64) -> Bool:
    return abs(a - b) <= rel * max(abs(a), abs(b)) + 1e-300


def check_parts() raises:
    """Matrix, right-hand side and solve, each on its own, against the reference."""
    var assembly_differs = 0
    var solve_differs = 0
    var total = 0
    for n in [3, 5, 17, 64, 160]:
        var d = Data(n, 1, 0, 100_000.0)
        var selected = SelectedStations()
        for i in range(n):
            selected.index.append(i)
        selected.gather(stations_of(d))
        for vtype in [SPHERICAL_MODEL, EXPONENTIAL_MODEL]:
            var v = Variogram(0.05, 0.8, 30_000.0, vtype)
            for use_drift in [False, True]:
                var m = n + 2 if use_drift else n + 1
                var matrix = List[Float64](length=m * m, fill=-1.0)
                var rhs = List[Float64](length=m, fill=-1.0)
                if use_drift:
                    ExternalDriftSystem().assemble_matrix(selected, v, matrix, m)
                    ExternalDriftSystem().assemble_right_hand_side(selected, 1234.5, 6789.0, 300.0, v, rhs, m)
                else:
                    OrdinaryKrigingSystem().assemble_matrix(selected, v, matrix, m)
                    OrdinaryKrigingSystem().assemble_right_hand_side(selected, 1234.5, 6789.0, 300.0, v, rhs, m)
                var expected_matrix = List[Float64]()
                var expected_rhs = List[Float64]()
                _ = reference_matrix(selected.index, d, v, use_drift, expected_matrix)
                reference_rhs(selected.index, 1234.5, 6789.0, 300.0, d, v, use_drift, expected_rhs)
                for k in range(m * m):
                    if not close(matrix[k], expected_matrix[k], 1e-14):
                        raise Error(String("matrix n=", n, " vtype=", vtype, " drift=", use_drift, " entry ", k))
                    if matrix[k] != expected_matrix[k]:
                        assembly_differs += 1
                for k in range(m):
                    if not close(rhs[k], expected_rhs[k], 1e-14):
                        raise Error(String("rhs n=", n, " vtype=", vtype, " drift=", use_drift, " entry ", k))
                    if rhs[k] != expected_rhs[k]:
                        assembly_differs += 1
                # the same system through both solvers
                var a = expected_matrix.copy()
                var b = expected_rhs.copy()
                _ = _lu_solve_kernel[4](a, b, m)
                _ = reference_lu_solve(expected_matrix, expected_rhs, m)
                for k in range(m):
                    if b[k] != expected_rhs[k]:
                        solve_differs += 1
                total += 1
    print("parts ok,", assembly_differs, "assembled entries and", solve_differs,
          "solution entries differ in the last bits, over", total, "systems")


def check_weights() raises:
    """Weights of single systems, OK and EDK, both model types, against the reference."""
    var identical = 0
    var total = 0
    for n in [3, 4, 5, 6, 17, 64, 160]:
        var d = Data(n, 1, 0, 100_000.0)
        var stations = stations_of(d)
        for vtype in [SPHERICAL_MODEL, EXPONENTIAL_MODEL]:
            var v = Variogram(0.05, 0.8, 30_000.0, vtype)
            for use_drift in [False, True]:
                var selected = SelectedStations()
                for i in range(n):
                    selected.index.append(i)
                selected.gather(stations)
                var cx = 50_000.0 * random_float64()
                var cy = 50_000.0 * random_float64()
                var ch = 500.0
                var matrix = List[Float64]()
                var weights = List[Float64]()
                var code: Int
                if use_drift:
                    code = _kriging_weights(ExternalDriftSystem(), selected, cx, cy, ch, v, matrix, weights)
                else:
                    code = _kriging_weights(OrdinaryKrigingSystem(), selected, cx, cy, ch, v, matrix, weights)
                var expected = List[Float64]()
                var expected_code = reference_weights(selected.index, cx, cy, ch, d, v, use_drift, expected)
                var label = String("weights n=", n, " vtype=", vtype, " drift=", use_drift)
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
                        raise Error(label + ": weight " + String(i) + ": " + String(weights[i]) + " vs " + String(expected[i]))
                total += 1
                identical += 1 if same else 0
    print("weights ok,", identical, "of", total, "systems bit-identical to the reference")


def check_edk_extends_ok() raises:
    """The EDK matrix is the OK matrix bordered by the elevations."""
    var n = 9
    var d = Data(n, 1, 0, 50_000.0)
    var selected = SelectedStations()
    for i in range(n):
        selected.index.append(i)
    selected.gather(stations_of(d))
    var v = Variogram(0.1, 0.6, 20_000.0, EXPONENTIAL_MODEL)
    var ok = List[Float64](length=(n + 1) * (n + 1), fill=-1.0)
    var edk = List[Float64](length=(n + 2) * (n + 2), fill=-1.0)
    OrdinaryKrigingSystem().assemble_matrix(selected, v, ok, n + 1)
    ExternalDriftSystem().assemble_matrix(selected, v, edk, n + 2)
    for i in range(n + 1):
        for j in range(n + 1):
            if ok[i * (n + 1) + j] != edk[i * (n + 2) + j]:
                raise Error(String("EDK block differs from OK at ", i, ",", j))
    for i in range(n):
        if edk[i * (n + 2) + n + 1] != d.sh[i] or edk[(n + 1) * (n + 2) + i] != d.sh[i]:
            raise Error(String("EDK drift row/column wrong at ", i))
    for i in range(n, n + 2):
        for j in range(n, n + 2):
            if edk[i * (n + 2) + j] != 0.0:
                raise Error("EDK constraint block not zero")
    print("edk extends ok: ok")


def check_cells(nsta: Int, ntime: Int, ncell: Int, vtype: Int, use_drift: Bool) raises:
    """Whole cell loop against the reference cell loop."""
    var d = Data(nsta, ntime, ncell, 200_000.0)
    var v = Variogram(0.05, 0.9, 40_000.0, vtype)
    var p = KrigingSettings(
        max_dist=60_000.0, nodata=NODATA, dem_nodata=DEM_NODATA,
        correct_negative=True, dist_zero=True, threshold_dist=20_000.0,
    )
    var out = List[Float32](length=ncell * ntime, fill=123.0)
    var expected = List[Float32](length=ncell * ntime, fill=456.0)
    var counts = (
        external_drift_kriging(
            cell_x=d.cx.unsafe_ptr(), cell_y=d.cy.unsafe_ptr(), cell_elevation=d.ch.unsafe_ptr(), cell_count=ncell,
            station_x=d.sx.unsafe_ptr(), station_y=d.sy.unsafe_ptr(), station_elevation=d.sh.unsafe_ptr(),
            station_values=d.sz.unsafe_ptr(), station_count=nsta, day_count=ntime,
            model=v, settings=p, estimates=out.unsafe_ptr(),
        ) if use_drift else ordinary_kriging(
            cell_x=d.cx.unsafe_ptr(), cell_y=d.cy.unsafe_ptr(), cell_elevation=d.ch.unsafe_ptr(), cell_count=ncell,
            station_x=d.sx.unsafe_ptr(), station_y=d.sy.unsafe_ptr(), station_elevation=d.sh.unsafe_ptr(),
            station_values=d.sz.unsafe_ptr(), station_count=nsta, day_count=ntime,
            model=v, settings=p, estimates=out.unsafe_ptr(),
        )
    )
    var (n_singular, n_sum_warning) = reference_krige(d, v, p, use_drift, expected)
    var label = String("cells nsta=", nsta, " ntime=", ntime, " ncell=", ncell, " vtype=", vtype, " drift=", use_drift)
    if counts.singular != n_singular or counts.sum_warning != n_sum_warning:
        raise Error(label + ": counts " + String(counts.singular) + "/" + String(counts.sum_warning)
                    + " vs " + String(n_singular) + "/" + String(n_sum_warning))
    var differing = 0
    var checksum: Float64 = 0.0  # compare runs with different worker counts (e.g. under `taskset -c 0`)
    for k in range(ncell * ntime):
        checksum += Float64(k % 97 + 1) * Float64(out[k])
        if out[k] != expected[k]:
            differing += 1
            # relative, or absolute for estimates that cancel to about 0 (values here are 0 .. 30)
            if not close(Float64(out[k]), Float64(expected[k]), 1e-5) and abs(out[k] - expected[k]) > 1e-6:
                raise Error(label + ": value " + String(k) + ": " + String(out[k]) + " vs " + String(expected[k]))
    print(label, "ok,", differing, "values differ in the last bits, checksum", repr(checksum))


def main() raises:
    seed(11)
    check_parts()
    check_weights()
    check_edk_extends_ok()
    for nsta in [0, 1, 2, 3, 40, 300]:
        for use_drift in [False, True]:
            check_cells(nsta, 7, 200, EXPONENTIAL_MODEL, use_drift)
    check_cells(300, 31, 500, SPHERICAL_MODEL, True)
    check_cells(300, 1, 3, EXPONENTIAL_MODEL, False)
    print("all ok")
