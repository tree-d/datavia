"""Tests of :class:`~datavia.weather.variogram_cache.VariogramCache`."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock

import numpy as np
import pytest

kriging = pytest.importorskip("datavia.kriging")

from datavia.weather.variogram_cache import (  # noqa: E402
    CACHE_FILE_VERSION,
    VariogramCache,
)

SETTINGS = {
    "model": "exponential",
    "bin_width": 1000.0,
    "max_lag": 80000.0,
    "max_elevation_gradient": 0.2,
    "min_pairs_per_bin": 30,
    "crs": "EPSG:3035",
}
JUNE = date(2025, 6, 1)


def _fit_result(nugget: float = 0.1) -> MagicMock:
    """A fit result like the one ``datavia.kriging.krige`` returns."""
    return MagicMock(
        variogram=kriging.Variogram(nugget, 0.9, 40123.456789, kriging.EXPONENTIAL),
        bins=kriging.EmpiricalBins(
            np.array([500.0, 1500.0]), np.array([0.3, 0.6]), np.array([40, 55])
        ),
        converged=True,
    )


def _get(cache, fit, n_days=30, complete=True, settings=SETTINGS):
    return cache.get_or_fit(
        "total_precipitation",
        JUNE,
        settings,
        n_days=n_days,
        n_stations=1987,
        complete=complete,
        fit=fit,
    )


def test_fit_is_written_and_read_back_exactly(tmp_path):
    """A fit is cached under its month and settings hash, exactly."""
    cache = VariogramCache(tmp_path)
    fitted = _get(cache, MagicMock(return_value=_fit_result()))
    assert not fitted.from_cache
    assert fitted.path == tmp_path / "total_precipitation" / (
        f"2025-06_{VariogramCache.settings_hash(SETTINGS)}.json"
    )
    again = cache.load(fitted.path)
    assert again.from_cache
    assert again.variogram == fitted.variogram
    np.testing.assert_array_equal(again.bins.lag, fitted.bins.lag)
    assert again.meta["n_days"] == 30
    assert again.meta["n_stations"] == 1987
    assert again.meta["settings"] == SETTINGS
    assert json.loads(fitted.path.read_text())["version"] == CACHE_FILE_VERSION


def test_complete_month_is_never_refitted(tmp_path):
    """A month cached as complete is reused, never refitted."""
    cache = VariogramCache(tmp_path)
    _get(cache, MagicMock(return_value=_fit_result()))
    fit = MagicMock(return_value=_fit_result(nugget=0.5))
    reused = _get(cache, fit, n_days=31)
    fit.assert_not_called()
    assert reused.from_cache
    assert reused.variogram.nugget == 0.1


def test_running_month_is_refitted_when_more_days_arrive(tmp_path):
    """The running month is refitted only when more days arrive."""
    cache = VariogramCache(tmp_path)
    _get(cache, MagicMock(return_value=_fit_result()), n_days=10, complete=False)

    same_days = MagicMock(return_value=_fit_result(nugget=0.5))
    assert _get(cache, same_days, n_days=10, complete=False).from_cache
    same_days.assert_not_called()

    more_days = MagicMock(return_value=_fit_result(nugget=0.5))
    refitted = _get(cache, more_days, n_days=12, complete=False)
    more_days.assert_called_once()
    assert refitted.variogram.nugget == 0.5
    assert cache.load(refitted.path).meta["n_days"] == 12


def test_other_settings_use_another_file(tmp_path):
    """Other fit settings give another cache file."""
    cache = VariogramCache(tmp_path)
    first = _get(cache, MagicMock(return_value=_fit_result()))
    fit = MagicMock(return_value=_fit_result())
    second = _get(cache, fit, settings={**SETTINGS, "model": "spherical"})
    fit.assert_called_once()
    assert first.path != second.path
    assert VariogramCache.settings_hash(SETTINGS) == VariogramCache.settings_hash(
        dict(reversed(SETTINGS.items()))
    )


def test_unreadable_file_is_refitted(tmp_path):
    """An unreadable cache file is replaced by a new fit."""
    cache = VariogramCache(tmp_path)
    path = cache.path("total_precipitation", JUNE, SETTINGS)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 99}))
    fit = MagicMock(return_value=_fit_result())
    assert not _get(cache, fit).from_cache
    fit.assert_called_once()


def test_load_rejects_other_versions(tmp_path):
    """Loading a file of another version raises."""
    path = tmp_path / "v.json"
    path.write_text(json.dumps({"version": 99}))
    with pytest.raises(ValueError, match="unsupported variogram cache version"):
        VariogramCache(tmp_path).load(path)


def test_cached_variogram_drives_kriging(tmp_path):
    """A variogram read from the cache kriges exactly like the fitted one."""
    rng = np.random.default_rng(1)
    sx, sy = rng.uniform(0, 5e4, 40), rng.uniform(0, 5e4, 40)
    sh = rng.uniform(0, 500, 40)
    z = 5 + 0.01 * sh[:, None] + rng.normal(0, 1, (40, 6))
    empty = np.empty(0)
    cache = VariogramCache(tmp_path)

    fitted = _get(
        cache,
        lambda: kriging.krige(
            empty, empty, empty, sx, sy, sh, z, nodata=-999.0, max_dist=1e5
        ),
    )
    cached = cache.load(fitted.path)
    x, y, h = np.array([1e4, 3e4]), np.array([2e4, 1e4]), np.array([100.0, 300.0])
    a = kriging.krige(
        x, y, h, sx, sy, sh, z, nodata=-999.0, max_dist=1e5, variogram=fitted.variogram
    )
    b = kriging.krige(
        x, y, h, sx, sy, sh, z, nodata=-999.0, max_dist=1e5, variogram=cached.variogram
    )
    np.testing.assert_array_equal(a.estimates, b.estimates)
