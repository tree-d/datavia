"""Tests of :mod:`datavia.weather.vapour_pressure` (FAO-56 formulas).

Covers the saturation vapour pressure against FAO-56 reference values, the
three deficit forms (measured vapour pressure, relative humidity, dewpoint),
clipping of negative deficits, NaN propagation, and that numpy arrays and
(dask-backed) xarray DataArrays keep their type.
"""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from datavia.weather.vapour_pressure import (
    saturation_vapour_pressure,
    vapour_pressure_deficit,
    vapour_pressure_deficit_from_dewpoint,
    vapour_pressure_deficit_from_relative_humidity,
)


class TestSaturationVapourPressure:
    """FAO-56 eq. 11 against the values of FAO-56 Annex 2, table 2.3."""

    @pytest.mark.parametrize(
        ("temperature_c", "expected_kpa"),
        [(0.0, 0.611), (15.0, 1.705), (20.0, 2.338), (24.5, 3.075), (35.0, 5.623)],
    )
    def test_reference_values(self, temperature_c: float, expected_kpa: float) -> None:
        assert saturation_vapour_pressure(temperature_c) == pytest.approx(
            expected_kpa, abs=1e-3
        )

    def test_increases_with_temperature(self) -> None:
        values = saturation_vapour_pressure(np.array([-10.0, 0.0, 10.0, 30.0]))
        assert np.all(np.diff(values) > 0)


class TestDeficitFromVapourPressure:
    """Daily deficit from Tmax, Tmin and a measured actual vapour pressure."""

    def test_fao56_worked_example(self) -> None:
        # FAO-56 examples 3 and 5: Tmax 24.5, Tmin 15 degC -> es = 2.39 kPa;
        # with ea = 1.409 kPa the deficit is 0.98 kPa.
        assert vapour_pressure_deficit(24.5, 15.0, 1.409) == pytest.approx(
            0.98, abs=0.005
        )

    def test_extremes_give_more_than_the_mean_temperature(self) -> None:
        # e is convex: es from the extremes exceeds e(Tmean).
        deficit = vapour_pressure_deficit(30.0, 10.0, 0.0)
        assert deficit > saturation_vapour_pressure(20.0)

    def test_supersaturation_is_clipped_to_zero(self) -> None:
        assert vapour_pressure_deficit(10.0, 5.0, 5.0) == 0.0

    def test_nan_propagates(self) -> None:
        result = vapour_pressure_deficit(
            np.array([20.0, np.nan]), np.array([10.0, 10.0]), np.array([1.0, 1.0])
        )
        assert np.isfinite(result[0]) and np.isnan(result[1])


class TestDeficitFromRelativeHumidity:
    """Daily deficit with ea = RHmean / 100 * es (FAO-56 eq. 19)."""

    def test_formula(self) -> None:
        es = (saturation_vapour_pressure(24.5) + saturation_vapour_pressure(15.0)) / 2
        assert vapour_pressure_deficit_from_relative_humidity(
            24.5, 15.0, 60.0
        ) == pytest.approx(0.4 * es)

    def test_saturated_and_supersaturated_air_have_no_deficit(self) -> None:
        result = vapour_pressure_deficit_from_relative_humidity(
            np.array([20.0, 20.0]), np.array([10.0, 10.0]), np.array([100.0, 104.0])
        )
        np.testing.assert_array_equal(result, [0.0, 0.0])

    def test_dask_backed_dataarray_stays_lazy(self) -> None:
        days = xr.DataArray(np.array([20.0, 25.0, np.nan]), dims="time").chunk(1)
        result = vapour_pressure_deficit_from_relative_humidity(days, days - 10.0, 50.0)
        assert isinstance(result, xr.DataArray)
        assert result.chunks is not None
        computed = result.values
        assert np.all(computed[:2] > 0) and np.isnan(computed[2])


class TestDeficitFromDewpoint:
    """Instantaneous deficit e(T) - e(Td)."""

    def test_formula(self) -> None:
        expected = saturation_vapour_pressure(20.0) - saturation_vapour_pressure(10.0)
        assert vapour_pressure_deficit_from_dewpoint(20.0, 10.0) == pytest.approx(
            expected
        )

    def test_dewpoint_at_or_above_temperature_gives_zero(self) -> None:
        result = vapour_pressure_deficit_from_dewpoint(
            np.array([15.0, 15.0]), np.array([15.0, 16.0])
        )
        np.testing.assert_array_equal(result, [0.0, 0.0])
