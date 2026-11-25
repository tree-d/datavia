"""Saving and loading a variogram (JSON) and reusing it for kriging.

pixi run test
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from datavia.kriging import (
    EXPONENTIAL,
    SPHERICAL,
    EmpiricalBins,
    Variogram,
    krige,
    load_variogram,
)

NODATA = -999.0


def stations(n_stations: int = 60, n_days: int = 10):
    """Return synthetic stations with an elevation trend in their values.

    Parameters
    ----------
    n_stations : int
        Number of stations.
    n_days : int
        Number of days.

    Returns
    -------
    tuple of np.ndarray
        ``(station_x, station_y, station_elevation, z)``; ``z`` is
        ``(n_stations, n_days)``.
    """
    rng = np.random.default_rng(1)
    station_x, station_y = (
        rng.uniform(0, 5e4, n_stations),
        rng.uniform(0, 5e4, n_stations),
    )
    station_elevation = rng.uniform(0, 500, n_stations)
    z = 5 + 0.01 * station_elevation[:, None] + rng.normal(0, 1, (n_stations, n_days))
    return station_x, station_y, station_elevation, z


def fit():
    """Fit a variogram to :func:`stations` by kriging on no points.

    Returns
    -------
    KrigingResult
        Empty estimates, the fitted variogram and its bins.
    """
    none = np.empty(0)
    return krige(
        none,
        none,
        none,
        *stations(),
        nodata=NODATA,
        max_dist=3e4,
        max_lag=4e4,
        min_pairs_per_bin=5,
    )


@pytest.mark.parametrize("model_type", [SPHERICAL, EXPONENTIAL])
def test_round_trip_is_exact(tmp_path, model_type):
    """A saved variogram reads back bit-identical, for both models."""
    variogram = Variogram(0.1 + 1e-17, 2 / 3, 12345.678901234567, model_type)
    variogram.save(tmp_path / "v.json")
    assert Variogram.load(tmp_path / "v.json") == variogram
    assert load_variogram(tmp_path / "v.json") == variogram
    assert Variogram.from_dict(variogram.to_dict()) == variogram


def test_file_is_self_describing(tmp_path):
    """The file names the model and the range unit."""
    Variogram(0.2, 0.9, 39900.0, EXPONENTIAL).save(tmp_path / "v.json")
    data = json.loads((tmp_path / "v.json").read_text())
    assert data == {
        "version": 1,
        "model": "exponential",
        "nugget": 0.2,
        "sill": 0.9,
        "range_m": 39900.0,
    }


def test_unknown_model_raises(tmp_path):
    """An unknown model name is rejected on load."""
    path = tmp_path / "v.json"
    path.write_text(
        json.dumps(
            {"version": 1, "model": "gaussian", "nugget": 0, "sill": 1, "range_m": 1e4}
        )
    )
    with pytest.raises(ValueError, match="gaussian"):
        Variogram.load(path)
    with pytest.raises(ValueError, match="model type"):
        Variogram(0, 1, 1e4, 7).to_dict()


def test_unknown_version_raises(tmp_path):
    """A file of another format version is rejected."""
    path = tmp_path / "v.json"
    path.write_text(
        json.dumps(
            {
                "version": 99,
                "model": "exponential",
                "nugget": 0,
                "sill": 1,
                "range_m": 1e4,
            }
        )
    )
    with pytest.raises(ValueError, match="version"):
        Variogram.load(path)


def test_saved_fit_has_bins_and_gives_the_same_kriging(tmp_path):
    """A saved fit keeps its bins and kriges identically."""
    result = fit()
    path = tmp_path / "fit.json"
    result.save_variogram(path)

    data = json.loads(path.read_text())
    assert data["converged"] == result.converged
    loaded = Variogram.load(path)
    assert loaded == result.variogram
    bins = EmpiricalBins.load(path)
    assert np.array_equal(bins.lag, result.bins.lag)
    assert np.array_equal(bins.semivariance, result.bins.semivariance)
    assert np.array_equal(bins.pair_count, result.bins.pair_count)

    station_x, station_y, station_elevation, z = stations()
    points = (station_x[:8] + 500.0, station_y[:8] - 500.0, station_elevation[:8])
    with_fit = krige(
        *points,
        station_x,
        station_y,
        station_elevation,
        z,
        nodata=NODATA,
        max_dist=3e4,
        variogram=result.variogram,
    )
    with_file = krige(
        *points,
        station_x,
        station_y,
        station_elevation,
        z,
        nodata=NODATA,
        max_dist=3e4,
        variogram=loaded,
    )
    assert np.array_equal(with_fit.estimates, with_file.estimates)


def test_given_variogram_saves_without_bins(tmp_path):
    """A given (not fitted) variogram is saved without bins."""
    station_x, station_y, station_elevation, z = stations()
    given = Variogram(0.1, 1.0, 2e4, SPHERICAL)
    result = krige(
        station_x[:3],
        station_y[:3],
        station_elevation[:3],
        station_x,
        station_y,
        station_elevation,
        z,
        nodata=NODATA,
        max_dist=3e4,
        variogram=given,
    )
    path = tmp_path / "given.json"
    result.save_variogram(path)
    data = json.loads(path.read_text())
    assert "bins" not in data and "converged" not in data
    assert Variogram.load(path) == given
    with pytest.raises(ValueError, match="no empirical bins"):
        EmpiricalBins.load(path)
