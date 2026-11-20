"""Checks empirical_variogram.mojo against a plain scalar reference loop.

    pixi run mojo run -I packages/kriging/datavia/kriging packages/kriging/tests/mojo/test_empirical_variogram.mojo
    (or, from the datavia root: pixi run test-kriging-mojo)
"""

from std.math import ceil, sqrt
from std.random import random_float64, seed

from kernels.empirical_variogram import EmpiricalVariogram, empirical_variogram

comptime BIN_WIDTH = 1000.0
comptime MAX_LAG = 30000.0
comptime GRADIENT = 0.2
comptime MIN_PAIRS = 30
comptime NODATA = -999.0


struct Reference:
    """Scalar reference: the original accumulate / merge / finalise / normalise, -9 markers included."""

    var h: List[Float64]
    var gamma: List[Float64]
    var n: List[Int]

    def __init__(out self, x: List[Float64], y: List[Float64], elevation: List[Float64],
                 z: List[Float64], nsta: Int, ntime: Int):
        var nb = Int(ceil(MAX_LAG / BIN_WIDTH))
        self.h = List[Float64](length=nb, fill=0.0)
        self.gamma = List[Float64](length=nb, fill=0.0)
        self.n = List[Int](length=nb, fill=0)
        for day in range(ntime):
            for i in range(nsta - 1):
                var zi = z[i * ntime + day]
                if zi == NODATA:
                    continue
                for j in range(i + 1, nsta):
                    var zj = z[j * ntime + day]
                    if zj == NODATA:
                        continue
                    var d = sqrt((x[i] - x[j]) * (x[i] - x[j]) + (y[i] - y[j]) * (y[i] - y[j]))
                    if d > MAX_LAG:
                        continue
                    if abs(elevation[i] - elevation[j]) / d > GRADIENT:
                        continue
                    var k = max(1, Int(ceil(d / BIN_WIDTH))) - 1
                    self.n[k] += 1
                    self.gamma[k] += (zi - zj) * (zi - zj)
        for k in range(nb - 1):
            var nhk = self.n[k]
            var gk = self.gamma[k]
            if nhk > 0 and nhk < MIN_PAIRS:
                self.n[k] = -9
                self.gamma[k] = 0.0
                if self.n[k + 1] > 0:
                    self.n[k + 1] += nhk
                    self.gamma[k + 1] += gk
                else:
                    self.n[k + 1] = nhk
                    self.gamma[k + 1] = gk
        for k in range(nb - 1, 0, -1):
            var nhk = self.n[k]
            var gk = self.gamma[k]
            if nhk > 0 and nhk < MIN_PAIRS:
                self.n[k] = -9
                self.gamma[k] = 0.0
                if self.n[k - 1] > 0:
                    self.n[k - 1] += nhk
                    self.gamma[k - 1] += gk
                else:
                    self.n[k - 1] = nhk
                    self.gamma[k - 1] = gk
        var ni = 0
        for k in range(nb):
            if self.n[k] > 0:
                ni += 1
                self.gamma[k] = self.gamma[k] / 2.0 / Float64(self.n[k])
                self.h[k] = Float64(k + 1) * BIN_WIDTH - Float64(ni) * BIN_WIDTH / 2.0
                ni = 0
            else:
                ni += 1
                self.h[k] = -9.0
        var gh = self.h[0]
        var gg = self.gamma[0]
        for k in range(1, nb):
            gh = max(gh, self.h[k])
            gg = max(gg, self.gamma[k])
        for k in range(nb):
            if self.h[k] > 0.0:
                self.h[k] = self.h[k] / gh
                self.gamma[k] = self.gamma[k] / gg


def close(a: Float64, b: Float64, rel: Float64 = 1e-12) -> Bool:
    return abs(a - b) <= rel * max(abs(a), abs(b)) + 1e-300


def check(nsta: Int, ntime: Int, clustered: Bool) raises:
    var x = List[Float64](capacity=nsta)
    var y = List[Float64](capacity=nsta)
    var elevation = List[Float64](capacity=nsta)
    var z = List[Float64](length=nsta * ntime, fill=0.0)
    var span = 20000.0 if clustered else 60000.0  # clustered: most pairs inside max_lag, many per bin
    for i in range(nsta):
        x.append(Float64(Int(random_float64() * span)))  # whole metres: duplicates and exact bin edges occur
        y.append(Float64(Int(random_float64() * span)))
        elevation.append(Float64(Int(random_float64() * 800.0)))
    if nsta > 3:  # a duplicate position, which makes the elevation gradient infinite or 0/0
        x[2] = x[1]
        y[2] = y[1]
        elevation[2] = elevation[1] + (0.0 if nsta % 2 == 0 else 50.0)
    for station in range(nsta):
        for t in range(ntime):
            z[station * ntime + t] = NODATA if random_float64() < 0.2 else Float64(Int(random_float64() * 300.0)) * 0.1

    var ref_ = Reference(x, y, elevation, z, nsta, ntime)
    var result = empirical_variogram(
        x=x.unsafe_ptr(), y=y.unsafe_ptr(), elevation=elevation.unsafe_ptr(), values=z.unsafe_ptr(),
        station_count=nsta, day_count=ntime, nodata=NODATA,
        bin_width=BIN_WIDTH, max_lag=MAX_LAG, max_elevation_gradient=GRADIENT, min_pairs_per_bin=MIN_PAIRS)

    var label = String("nsta=", nsta, " ntime=", ntime, " clustered=", clustered)
    var populated = 0
    var checksum: Float64 = 0.0  # compare runs with different worker counts (e.g. under `taskset -c 0`)
    for k in range(result.bin_count):
        checksum += Float64(k + 1) * (Float64(result.pair_count[k]) + result.semivariance[k] + result.lag[k])
        # equal to the reference; the reference marks empty bins with n <= 0 and h = -9
        if ref_.n[k] > 0:
            populated += 1
            if result.pair_count[k] != ref_.n[k]:
                raise Error(label + ": pair_count in bin " + String(k) + ": " + String(result.pair_count[k]) + " vs " + String(ref_.n[k]))
            if not close(result.semivariance[k], ref_.gamma[k]) or not close(result.lag[k], ref_.h[k]):
                raise Error(label + ": semivariance/lag in bin " + String(k))
        elif result.is_populated(k) or result.lag[k] != 0.0 or result.semivariance[k] != 0.0:
            raise Error(label + ": bin " + String(k) + " should be empty")
    print(label, "ok,", populated, "populated bins, checksum", repr(checksum))


def main() raises:
    seed(7)
    for nsta in [0, 1, 2, 3, 5, 17, 100, 300]:
        for ntime in [1, 3, 30]:
            check(nsta, ntime, False)
            check(nsta, ntime, True)
    print("all ok")
