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

    def test_apply_conversion_unknown_variable_passthrough(self) -> None:
        """Variables without a registry entry are returned unchanged."""
        from datavia.weather.source_registry import apply_conversion

        assert apply_conversion("ERA5_land", "no_such_variable", 42.0) == 42.0
        arr = np.array([1.0, 2.0])
        np.testing.assert_array_equal(
            apply_conversion("ERA5_land", "no_such_variable", arr), arr
        )

    def test_apply_conversion_unknown_unit_pair_returns_raw(self) -> None:
        """A spec with no matching (from, to) function returns the raw value."""
        from datavia.weather.source_registry import apply_conversion

        overrides = {"2m_temperature": {"from": "bogus", "to": "other"}}
        assert apply_conversion("ERA5_land", "2m_temperature", 5.0, overrides) == 5.0

    @pytest.mark.parametrize("source", ["HYRAS", "DWD_stations"])
    def test_radiation_w_m2_converted_to_par(self, source: str) -> None:
        """HYRAS and DWD W m⁻² radiation is converted to PAR."""
        from datavia.weather.source_registry import apply_conversion

        result = apply_conversion(source, "surface_solar_radiation_downwards", 100.0)
        assert result == pytest.approx(100.0 * 0.5 * 4.57)

    def test_radiation_era5_still_ssrd_to_par(self) -> None:
        """ERA5 J m⁻² radiation still converts to the same PAR unit."""
        from datavia.weather.source_registry import apply_conversion

        result = apply_conversion(
            "ERA5_land", "surface_solar_radiation_downwards", 86400.0
        )
        assert result == pytest.approx(0.5 * 4.57)


# ---------------------------------------------------------------------------
# interpolate_station_parquet — index alignment fix (issue #4)
