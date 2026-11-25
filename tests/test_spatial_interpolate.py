"""Tests for raster point sampling in :func:`spatial_interpolate`.

The test raster has 200 m pixels with value ``10 * col + 100 * row``, so an
error in x shows in the tens digit and an error in y in the hundreds digit.
"""

from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from datavia.library.interpolation import spatial_interpolate

_RES = 200.0
_X0, _Y0 = 500_000.0, 5_600_000.0  # upper-left corner, EPSG:25832
_N = 4
_CRS = "EPSG:25832"


@pytest.fixture
def grid_tiff(tmp_path: Path) -> str:
    """Write the 4 x 4 test raster and return its path."""
    data = np.array(
        [[10 * c + 100 * r for c in range(_N)] for r in range(_N)], dtype="float32"
    )
    path = tmp_path / "grid.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=_N,
        width=_N,
        count=1,
        dtype="float32",
        crs=_CRS,
        transform=from_origin(_X0, _Y0, _RES, _RES),
    ) as dst:
        dst.write(data, 1)
    return str(path)


def _at(col: float, row: float) -> tuple[float, float]:
    """World (x, y) of fractional pixel position (col, row), corner-based."""
    return _X0 + col * _RES, _Y0 - row * _RES


@pytest.mark.parametrize("order", [0, 1, 3])
def test_pixel_centre_returns_pixel_value(grid_tiff: str, order: int) -> None:
    """A query at a pixel's centre returns that pixel's value (no half-pixel shift)."""
    cells = [(c, r) for r in range(_N) for c in range(_N)]
    coords = np.array([_at(c + 0.5, r + 0.5) for c, r in cells])

    got = spatial_interpolate(
        grid_tiff, coords, coords_crs=_CRS, interpolation_order=order
    )

    expected = np.array([10 * c + 100 * r for c, r in cells], dtype=float)
    np.testing.assert_allclose(got, expected, atol=1e-6)


def test_linear_between_centres(grid_tiff: str) -> None:
    """Linear interpolation at a shared pixel corner is the mean of the four pixels."""
    # Corner shared by pixels (1,1), (2,1), (1,2), (2,2): (110+120+210+220)/4.
    got = spatial_interpolate(
        grid_tiff, np.array([_at(2.0, 2.0)]), coords_crs=_CRS, interpolation_order=1
    )
    assert got[0] == pytest.approx(165.0)


@pytest.mark.parametrize("order", [0, 1, 3])
def test_outer_half_of_border_pixels_takes_border_value(
    grid_tiff: str, order: int
) -> None:
    """Points between the last pixel centre and the raster edge are not NaN."""
    coords = np.array(
        [
            _at(0.1, 1.5),  # west edge, row 1  -> pixel (0, 1)
            _at(3.9, 1.5),  # east edge, row 1  -> pixel (3, 1)
            _at(1.5, 0.1),  # north edge, col 1 -> pixel (1, 0)
            _at(1.5, 3.9),  # south edge, col 1 -> pixel (1, 3)
        ]
    )

    got = spatial_interpolate(
        grid_tiff, coords, coords_crs=_CRS, interpolation_order=order
    )

    np.testing.assert_allclose(got, [100.0, 130.0, 10.0, 310.0], atol=1e-6)


@pytest.mark.parametrize("order", [0, 1, 3])
def test_points_outside_raster_are_nan(grid_tiff: str, order: int) -> None:
    """Points outside the raster footprint return NaN."""
    coords = np.array([_at(-0.1, 1.5), _at(4.1, 1.5), _at(1.5, -0.1), _at(1.5, 4.1)])

    got = spatial_interpolate(
        grid_tiff, coords, coords_crs=_CRS, interpolation_order=order
    )

    assert np.isnan(got).all()
