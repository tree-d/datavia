"""Checks empirical_variogram.mojo against a plain scalar reference loop.

    pixi run mojo run -I packages/kriging/datavia/kriging packages/kriging/tests/mojo/test_empirical_variogram.mojo
    (or, from the datavia root: pixi run test-kriging-mojo)
"""

from std.math import ceil, sqrt
from std.random import random_float64, seed

from kernels.empirical_variogram import EmpiricalVariogram, empirical_variogram
from testing_helpers import close

comptime BIN_WIDTH = 1000.0
"""Width [m] of one distance class."""
comptime MAX_LAG = 30000.0
"""Pairs farther apart than this [m] are left out."""
comptime MAX_ELEVATION_GRADIENT = 0.2
"""Pairs steeper than this (metres of elevation per metre of distance) are left out."""
comptime MIN_PAIRS = 30
"""Bins with fewer pairs are merged into a neighbour."""
comptime NODATA = -999.0
"""Marks a missing station value."""
comptime _EMPTY_MARKER = -9
"""What the reference writes into the pair count of a merged-away bin, and (as -9.0) into the lag of an empty bin."""


struct Reference:
    """Scalar reference: the original accumulate / merge / finalise / normalise, -9 markers included."""

    var lag: List[Float64]
    """Per bin: normalised lag, or -9 if the bin is empty."""
    var semivariance: List[Float64]
    """Per bin: normalised semivariance."""
    var pair_count: List[Int]
    """Per bin: number of pairs, or -9 if the bin was merged away (0 if it never had one)."""

    def __init__(
        out self,
        x: List[Float64],
        y: List[Float64],
        elevation: List[Float64],
        z: List[Float64],
        station_count: Int,
        day_count: Int,
    ):
        """Computes the normalised empirical variogram one pair and one day at a time.

        Args:
            x: Station eastings [m].
            y: Station northings [m].
            elevation: Station elevations [m].
            z: Station values, C-ordered `(station_count, day_count)`; missing values equal `NODATA`.
            station_count: Number of stations.
            day_count: Number of days.
        """
        var bin_count = Int(ceil(MAX_LAG / BIN_WIDTH))
        self.lag = List[Float64](length=bin_count, fill=0.0)
        self.semivariance = List[Float64](length=bin_count, fill=0.0)
        self.pair_count = List[Int](length=bin_count, fill=0)
        for day in range(day_count):
            for i in range(station_count - 1):
                var value_i = z[i * day_count + day]
                if value_i == NODATA:
                    continue
                for j in range(i + 1, station_count):
                    var value_j = z[j * day_count + day]
                    if value_j == NODATA:
                        continue
                    var distance = sqrt((x[i] - x[j]) * (x[i] - x[j]) + (y[i] - y[j]) * (y[i] - y[j]))
                    if distance > MAX_LAG:
                        continue
                    if abs(elevation[i] - elevation[j]) / distance > MAX_ELEVATION_GRADIENT:
                        continue
                    var k = max(1, Int(ceil(distance / BIN_WIDTH))) - 1
                    self.pair_count[k] += 1
                    self.semivariance[k] += (value_i - value_j) * (value_i - value_j)
        # merge sparse bins upwards, then downwards
        for k in range(bin_count - 1):
            var pairs = self.pair_count[k]
            var squared_difference_sum = self.semivariance[k]
            if pairs > 0 and pairs < MIN_PAIRS:
                self.pair_count[k] = _EMPTY_MARKER
                self.semivariance[k] = 0.0
                if self.pair_count[k + 1] > 0:
                    self.pair_count[k + 1] += pairs
                    self.semivariance[k + 1] += squared_difference_sum
                else:
                    self.pair_count[k + 1] = pairs
                    self.semivariance[k + 1] = squared_difference_sum
        for k in range(bin_count - 1, 0, -1):
            var pairs = self.pair_count[k]
            var squared_difference_sum = self.semivariance[k]
            if pairs > 0 and pairs < MIN_PAIRS:
                self.pair_count[k] = _EMPTY_MARKER
                self.semivariance[k] = 0.0
                if self.pair_count[k - 1] > 0:
                    self.pair_count[k - 1] += pairs
                    self.semivariance[k - 1] += squared_difference_sum
                else:
                    self.pair_count[k - 1] = pairs
                    self.semivariance[k - 1] = squared_difference_sum
        # semivariance and lag; empty bins below a populated one widen its lag range
        var covered_bins = 0
        for k in range(bin_count):
            if self.pair_count[k] > 0:
                covered_bins += 1
                self.semivariance[k] = self.semivariance[k] / 2.0 / Float64(self.pair_count[k])
                self.lag[k] = Float64(k + 1) * BIN_WIDTH - Float64(covered_bins) * BIN_WIDTH / 2.0
                covered_bins = 0
            else:
                covered_bins += 1
                self.lag[k] = Float64(_EMPTY_MARKER)
        # normalise
        var largest_lag = self.lag[0]
        var largest_semivariance = self.semivariance[0]
        for k in range(1, bin_count):
            largest_lag = max(largest_lag, self.lag[k])
            largest_semivariance = max(largest_semivariance, self.semivariance[k])
        for k in range(bin_count):
            if self.lag[k] > 0.0:
                self.lag[k] = self.lag[k] / largest_lag
                self.semivariance[k] = self.semivariance[k] / largest_semivariance


def check(station_count: Int, day_count: Int, clustered: Bool) raises:
    """Checks `empirical_variogram` on random stations against the reference.

    Prints a checksum, so runs with different worker counts can be compared.

    Args:
        station_count: Number of random stations.
        day_count: Number of days.
        clustered: True to put the stations close together, so most pairs lie
            within `MAX_LAG` and bins hold many pairs.

    Raises:
        Error: If a pair count differs, a semivariance or lag differs by more than
            1e-12 relative, or a bin that should be empty is not.
    """
    var x = List[Float64](capacity=station_count)
    var y = List[Float64](capacity=station_count)
    var elevation = List[Float64](capacity=station_count)
    var z = List[Float64](length=station_count * day_count, fill=0.0)
    var span = 20000.0 if clustered else 60000.0  # clustered: most pairs inside max_lag, many per bin
    for _ in range(station_count):
        x.append(Float64(Int(random_float64() * span)))  # whole metres: duplicates and exact bin edges occur
        y.append(Float64(Int(random_float64() * span)))
        elevation.append(Float64(Int(random_float64() * 800.0)))
    if station_count > 3:  # a duplicate position, which makes the elevation gradient infinite or 0/0
        x[2] = x[1]
        y[2] = y[1]
        elevation[2] = elevation[1] + (0.0 if station_count % 2 == 0 else 50.0)
    for station in range(station_count):
        for day in range(day_count):
            z[station * day_count + day] = (
                NODATA if random_float64() < 0.2 else Float64(Int(random_float64() * 300.0)) * 0.1
            )

    var reference = Reference(x, y, elevation, z, station_count, day_count)
    var result = empirical_variogram(
        x=x.unsafe_ptr(), y=y.unsafe_ptr(), elevation=elevation.unsafe_ptr(), values=z.unsafe_ptr(),
        station_count=station_count, day_count=day_count, nodata=NODATA,
        bin_width=BIN_WIDTH, max_lag=MAX_LAG, max_elevation_gradient=MAX_ELEVATION_GRADIENT,
        min_pairs_per_bin=MIN_PAIRS)

    var label = String("station_count=", station_count, " day_count=", day_count, " clustered=", clustered)
    var populated = 0
    var checksum: Float64 = 0.0  # compare runs with different worker counts (e.g. under `taskset -c 0`)
    for k in range(result.bin_count):
        checksum += Float64(k + 1) * (Float64(result.pair_count[k]) + result.semivariance[k] + result.lag[k])
        # equal to the reference; the reference marks empty bins with pair_count <= 0 and lag = -9
        if reference.pair_count[k] > 0:
            populated += 1
            if result.pair_count[k] != reference.pair_count[k]:
                raise Error(
                    label + ": pair_count in bin " + String(k) + ": " + String(result.pair_count[k])
                    + " vs " + String(reference.pair_count[k])
                )
            if not close(result.semivariance[k], reference.semivariance[k]) or not close(
                result.lag[k], reference.lag[k]
            ):
                raise Error(label + ": semivariance/lag in bin " + String(k))
        elif result.is_populated(k) or result.lag[k] != 0.0 or result.semivariance[k] != 0.0:
            raise Error(label + ": bin " + String(k) + " should be empty")
    print(label, "ok,", populated, "populated bins, checksum", repr(checksum))


def main() raises:
    """Runs `check` over station counts, day counts and both layouts, with a fixed random seed.

    Raises:
        Error: If any check fails.
    """
    seed(7)
    for station_count in [0, 1, 2, 3, 5, 17, 100, 300]:
        for day_count in [1, 3, 30]:
            check(station_count, day_count, False)
            check(station_count, day_count, True)
    print("all ok")
