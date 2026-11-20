"""External drift kriging (EDK) of station data onto grid cells.

Ordinary kriging (ordinary_kriging.mojo) assumes the mean of the field is constant
around each cell. External drift kriging lets the mean follow a second variable
that is known everywhere, here the elevation of the DEM: the weights must not
only sum to 1, they must also reproduce the cell's elevation,
sum_i w_i h_i = h_0. That is one more constraint, so the OK system is bordered
by one more row and column:

    | Gamma  1  h | | w   |   | gamma_0 |
    | 1^T    0  0 | | mu1 | = |    1    |     h_i = station elevation
    | h^T    0  0 | | mu2 |   |   h_0   |     h_0 = cell elevation

Everything else, the neighbourhood, the estimator choice, the weight cache, the
solve, the estimate and the parallel cell loop, is ordinary kriging's: this file
only defines the bordered system and runs `ordinary_kriging.krige_cells` with it.

It contains:

  * `ExternalDriftSystem`     the bordered system, built on `OrdinaryKrigingSystem`,
  * `external_drift_kriging`  `krige_cells` with that system, from raw pointers.
"""

from std.memory import Pointer

from kernels.ordinary_kriging import (
    Cells,
    KrigingCounts,
    KrigingSettings,
    KrigingSystem,
    OrdinaryKrigingSystem,
    SelectedStations,
    Stations,
    _untracked,
    krige_cells,
)
from kernels.variogram import Variogram


@fieldwise_init
struct ExternalDriftSystem(KrigingSystem):
    """The EDK system: the OK system with the
    station elevations as one more row and column, and the cell elevation as one
    more entry of the right-hand side."""

    def constraint_count(self) -> Int:
        return OrdinaryKrigingSystem().constraint_count() + 1

    def assemble_matrix(self, selected: SelectedStations, model: Variogram, mut matrix: List[Float64], size: Int):
        """The OK matrix, then the elevations into the last row and column."""
        OrdinaryKrigingSystem().assemble_matrix(selected, model, matrix, size)
        var n = selected.count()
        var drift = n + 1
        for i in range(n):
            matrix[i * size + drift] = selected.elevation[i]
            matrix[drift * size + i] = selected.elevation[i]

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
        """The OK right-hand side, then the cell elevation."""
        OrdinaryKrigingSystem().assemble_right_hand_side(
            selected, cell_x, cell_y, cell_elevation, model, right_hand_side, size
        )
        right_hand_side[selected.count() + 1] = cell_elevation


def external_drift_kriging(
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
    """External drift kriging of every cell on every day, with the elevation as the
    drift, on all CPUs the process may use (see `ordinary_kriging.krige_cells`).

    The bordered system needs at least as many reporting stations as it has
    unknowns besides the multipliers, and their elevations must differ: where all
    stations of a day share one elevation, the drift row is a multiple of the row of
    ones, the system is singular, and the cell-day gets `OUT_NODATA` (counted in
    `KrigingCounts.singular`).

    Args:
        cell_x: Cell eastings [m], `cell_count` entries.
        cell_y: Cell northings [m], `cell_count` entries.
        cell_elevation: Cell elevations [m]: the drift value of each cell, and
            `settings.dem_nodata` for cells without DEM value.
        cell_count: Number of cells.
        station_x: Station eastings [m], `station_count` entries.
        station_y: Station northings [m], `station_count` entries.
        station_elevation: Station elevations [m]: the drift value of each station.
        station_values: C-ordered `(station_count, day_count)`; missing values equal `settings.nodata`.
        station_count: Number of stations.
        day_count: Number of days.
        model: The fitted variogram.
        settings: Search radius, nodata values and the precipitation options.
        estimates: Receives the estimates, C-ordered `(cell_count, day_count)` Float32.

    Returns:
        How many kriging systems were singular or had weights not summing to 1
        (see `ordinary_kriging.ordinary_kriging`).
    """
    return krige_cells(
        system=ExternalDriftSystem(),
        cells=Cells(_untracked(cell_x), _untracked(cell_y), _untracked(cell_elevation), cell_count),
        stations=Stations(
            _untracked(station_x), _untracked(station_y), _untracked(station_elevation),
            _untracked(station_values), station_count, day_count,
        ),
        model=model, settings=settings, estimates=_untracked(estimates),
    )
