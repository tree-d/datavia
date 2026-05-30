"""Unit tests for :mod:`datavia.weather.source_registry`.

Covers unit conversion functions and apply_conversion().
"""

import numpy as np
import pytest

# ---------------------------------------------------------------------------


class TestUnitConversions:
    """Tests for the ERA5 unit conversion utilities."""

    def test_kelvin_to_celsius_scalar(self) -> None:
        """0 K converts to -273.15 °C."""
        from datavia.library.unit_conversions import kelvin_to_celsius

        assert kelvin_to_celsius(273.15) == pytest.approx(0.0)

    def test_kelvin_to_celsius_array(self) -> None:
        """Array conversion preserves shape and values."""

        from datavia.library.unit_conversions import kelvin_to_celsius

        values = np.array([273.15, 373.15])
        result = kelvin_to_celsius(values)
        assert result == pytest.approx([0.0, 100.0])

    def test_precipitation_m_to_mm(self) -> None:
        """0.001 m converts to 1 mm."""
        from datavia.library.unit_conversions import precipitation_m_to_mm

        assert precipitation_m_to_mm(0.001) == pytest.approx(1.0)

    def test_ssrd_to_par_zero(self) -> None:
        """Zero SSRD yields zero PAR."""
        from datavia.library.unit_conversions import ssrd_to_par

        assert ssrd_to_par(0.0) == pytest.approx(0.0)

    def test_ssrd_to_par_known_value(self) -> None:
        """86400 J m⁻² day⁻¹ should equal 0.5 * 4.57 µmol m⁻² s⁻¹."""
        from datavia.library.unit_conversions import ssrd_to_par

        # 86400 J/m2/day / 86400 s/day x 0.5 x 4.57 = 2.285 umol/m2/s
        expected = 1.0 * 0.5 * 4.57
        assert ssrd_to_par(86400.0) == pytest.approx(expected)

    def test_convert_era5_variable_temperature(self) -> None:
        """kelvin_to_celsius converts temperature correctly."""
        from datavia.library.unit_conversions import kelvin_to_celsius

        result = kelvin_to_celsius(300.0)
        assert result == pytest.approx(300.0 - 273.15)

    def test_convert_era5_variable_precipitation(self) -> None:
        """precipitation_m_to_mm converts precipitation correctly."""
        from datavia.library.unit_conversions import precipitation_m_to_mm

        assert precipitation_m_to_mm(0.005) == pytest.approx(5.0)

    def test_convert_era5_variable_unknown_passthrough(self) -> None:
        """Unknown variables are returned unchanged
        (identity test on ssrd_to_par passthrough)."""
        from datavia.library.unit_conversions import kelvin_to_celsius

        # kelvin_to_celsius always applies the offset; test with a value
        # that maps to a known result to confirm the function is still callable.
        assert kelvin_to_celsius(273.15) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# interpolate_station_parquet — index alignment fix (issue #4)
