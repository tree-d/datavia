"""Checks the Python API of datavia.kriging (krige, krige_grid) on synthetic data.

pixi run test
"""

from __future__ import annotations

import datavia.kriging as kriging
import numpy as np
import pytest
from datavia.kriging._krige import _extension

NODATA = -999.0
DEM_NODATA = -9999.0


def synthetic(n_stations: int = 60, n_days: int = 10):
    """Synthetic stations (with one missing value) and a grid with one DEM gap."""
    rng = np.random.default_rng(0)
    sx, sy = rng.uniform(0, 5e4, n_stations), rng.uniform(0, 5e4, n_stations)
    sh = rng.uniform(0, 500, n_stations)
    z = 5 + 0.01 * sh[:, None] + rng.normal(0, 1, (n_stations, n_days))
    z[0, 2] = NODATA
    easting, northing = np.meshgrid(np.arange(0, 5e4, 1e3), np.arange(0, 4e4, 1e3))
    dem = rng.uniform(0, 500, easting.shape)
    dem[0, 0] = DEM_NODATA
    return (sx, sy, sh, z), (easting, northing, dem)


def test_grid_and_points_agree():
    """krige_grid and krige give the same estimates for the same cells."""
    stations, (easting, northing, dem) = synthetic()
    grid = kriging.krige_grid(
        easting,
        northing,
        dem,
        *stations,
        nodata=NODATA,
        dem_nodata=DEM_NODATA,
        max_dist=3e4,
        max_lag=4e4,
        min_pairs_per_bin=5,
    )
    assert grid.estimates.shape == (10, *easting.shape), grid.estimates.shape
    assert grid.bins is not None and grid.converged is not None
    assert np.all(grid.estimates[:, 0, 0] == kriging.OUT_NODATA)
    assert np.all(np.isfinite(grid.estimates)) and grid.singular_count == 0

    # The same cells as points, with the grid's variogram given: the same values
    # (but without the DEM gap).
    points = kriging.krige(
        easting.ravel(),
        northing.ravel(),
        dem.ravel(),
        *stations,
        nodata=NODATA,
        max_dist=3e4,
        variogram=grid.variogram,
        threshold_dist=0.5 * np.hypot(1e3, 1e3),
    )
    assert points.bins is None and points.converged is None
    assert points.estimates.shape == (easting.size, 10)
    as_grid = np.moveaxis(points.estimates.reshape(*easting.shape, 10), -1, 0)
    has_dem = dem != DEM_NODATA
    assert np.array_equal(as_grid[:, has_dem], grid.estimates[:, has_dem])


def test_single_day_and_ordinary_kriging():
    """Single-day input drops the day axis; ordinary kriging runs too."""
    (sx, sy, sh, z), (easting, northing, dem) = synthetic()
    vario = kriging.Variogram(0.1, 1.0, 2e4, kriging.SPHERICAL)
    one_day = kriging.krige_grid(
        easting,
        northing,
        dem,
        sx,
        sy,
        sh,
        z[:, 3],
        nodata=NODATA,
        dem_nodata=DEM_NODATA,
        max_dist=3e4,
        variogram=vario,
    )
    all_days = kriging.krige_grid(
        easting,
        northing,
        dem,
        sx,
        sy,
        sh,
        z,
        nodata=NODATA,
        dem_nodata=DEM_NODATA,
        max_dist=3e4,
        variogram=vario,
    )
    assert one_day.estimates.shape == easting.shape
    assert np.array_equal(one_day.estimates, all_days.estimates[3])
    ok = kriging.krige(
        easting.ravel()[:7],
        northing.ravel()[:7],
        dem.ravel()[:7],
        sx,
        sy,
        sh,
        z[:, 3],
        nodata=NODATA,
        max_dist=3e4,
        variogram=vario,
        drift=False,
    )
    assert ok.estimates.shape == (7,)
    assert not np.array_equal(
        ok.estimates[1:], one_day.estimates.ravel()[1:7]
    )  # OK differs from EDK


@pytest.mark.parametrize(
    "bad, message",
    [
        ("float32", "float64"),
        ("short_cell_y", "cell_y"),
        ("short_values", "station_values"),
        ("fortran_order", "C-contiguous"),
    ],
)
def test_bad_input_raises(bad, message):
    """The extension rejects wrong dtypes, lengths and memory layouts."""
    (sx, sy, sh, z), _ = synthetic()
    settings = {
        "nodata": NODATA,
        "dem_nodata": DEM_NODATA,
        "max_dist": 3e4,
        "threshold_dist": 0.0,
        "correct_negative": False,
        "dist_zero": False,
        "drift": True,
        "model_type": 2,
        "variogram": (0.1, 1.0, 2e4),
    }
    cells = np.zeros(3)
    args = {
        "float32": (cells.astype(np.float32), cells, cells, sx, sy, sh, z),
        "short_cell_y": (cells, cells[:2], cells, sx, sy, sh, z),
        "short_values": (cells, cells, cells, sx, sy, sh, z[:-1]),
        "fortran_order": (cells, cells, cells, sx, sy, sh, np.asfortranarray(z)),
    }[bad]
    with pytest.raises(Exception, match=message):  # Mojo raises a plain Exception
        _extension().krige(*args, settings)
