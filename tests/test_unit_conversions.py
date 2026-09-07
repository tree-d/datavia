"""Unit tests for datavia.library.unit_conversions.

Derived from the public API signatures only — no implementation files were
consulted.  All tests are pure arithmetic and require no mocks or I/O.

Functions under test:
- kelvin_to_celsius(values)
- precipitation_m_to_mm(values)
- ssrd_to_par(ssrd_daily_j_m2)
"""

from __future__ import annotations

import numpy as np
import pytest

from datavia.library.unit_conversions import (
    kelvin_to_celsius,
    precipitation_m_to_mm,
    ssrd_to_par,
)

# ---------------------------------------------------------------------------
# kelvin_to_celsius
# ---------------------------------------------------------------------------


class TestKelvinToCelsius:
    """Tests for the kelvin_to_celsius conversion function.

    Verifies the scalar, array, edge-case, and type-contract behaviours
    promised by the function signature.
    """

    def test_freezing_point_of_water_scalar(self) -> None:
        """Test that 273.15 K converts exactly to 0.0 °C.

        Returns:
            None
        """
        result = kelvin_to_celsius(273.15)
        assert abs(float(result) - 0.0) < 1e-9

    def test_boiling_point_of_water_scalar(self) -> None:
        """Test that 373.15 K converts exactly to 100.0 °C.

        Returns:
            None
        """
        result = kelvin_to_celsius(373.15)
        assert abs(float(result) - 100.0) < 1e-9

    def test_absolute_zero(self) -> None:
        """Test that 0 K maps to -273.15 °C (the absolute zero of temperature).

        Returns:
            None
        """
        result = kelvin_to_celsius(0.0)
        assert abs(float(result) - (-273.15)) < 1e-9

    @pytest.mark.parametrize(
        "k_val, expected_c",
        [
            (273.15, 0.0),
            (373.15, 100.0),
            (0.0, -273.15),
            (1000.0, 726.85),
            (300.0, 26.85),
        ],
    )
    def test_parametrized_scalar_values(self, k_val: float, expected_c: float) -> None:
        """Test kelvin_to_celsius against a table of known scalar pairs.

        Args:
            k_val: Input temperature in Kelvin.
            expected_c: Expected output in degrees Celsius.

        Returns:
            None
        """
        result = kelvin_to_celsius(k_val)
        assert abs(float(result) - expected_c) < 1e-9

    def test_array_element_wise_conversion(self) -> None:
        """Test that array input is converted element-wise.

        Returns:
            None
        """
        k_arr = np.array([273.15, 373.15, 0.0])
        result = kelvin_to_celsius(k_arr)
        expected = np.array([0.0, 100.0, -273.15])
        np.testing.assert_allclose(result, expected, atol=1e-9)

    def test_array_shape_preserved(self) -> None:
        """Test that a 2-D array output has the same shape as the input.

        Returns:
            None
        """
        k_arr = np.array([[273.15, 300.0], [310.0, 373.15]])
        result = kelvin_to_celsius(k_arr)
        assert np.asarray(result).shape == k_arr.shape

    def test_large_values_do_not_overflow(self) -> None:
        """Test that very large Kelvin inputs convert without arithmetic overflow.

        Returns:
            None
        """
        result = kelvin_to_celsius(1_000_000.0)
        assert abs(float(result) - (1_000_000.0 - 273.15)) < 1e-3

    def test_scalar_return_type_is_numeric(self) -> None:
        """Test that a scalar float input produces a numeric scalar output.

        Returns:
            None
        """
        result = kelvin_to_celsius(300.0)
        assert isinstance(result, (float, int, np.floating, np.integer))

    def test_array_return_type_is_ndarray(self) -> None:
        """Test that an ndarray input produces an ndarray output.

        Returns:
            None
        """
        result = kelvin_to_celsius(np.array([300.0, 310.0]))
        assert isinstance(result, np.ndarray)


# ---------------------------------------------------------------------------
# precipitation_m_to_mm
# ---------------------------------------------------------------------------


class TestPrecipitationMToMm:
    """Tests for the precipitation_m_to_mm conversion function.

    Verifies that the x 1000 conversion factor is applied correctly to both
    scalar and array inputs.
    """

    def test_one_metre_equals_1000_mm(self) -> None:
        """Test that exactly 1 m of precipitation converts to 1000 mm.

        Returns:
            None
        """
        result = precipitation_m_to_mm(1.0)
        assert abs(float(result) - 1000.0) < 1e-9

    def test_zero_metres_equals_zero_mm(self) -> None:
        """Test that 0 m converts to 0 mm without artefact.

        Returns:
            None
        """
        result = precipitation_m_to_mm(0.0)
        assert abs(float(result) - 0.0) < 1e-9

    @pytest.mark.parametrize(
        "m_val, expected_mm",
        [
            (0.001, 1.0),
            (0.01, 10.0),
            (0.1, 100.0),
            (1.0, 1000.0),
            (2.5, 2500.0),
        ],
    )
    def test_parametrized_conversion_factor(
        self, m_val: float, expected_mm: float
    ) -> None:
        """Test the x 1000 factor against a table of metre/mm pairs.

        Args:
            m_val: Input precipitation in metres.
            expected_mm: Expected output in millimetres.

        Returns:
            None
        """
        result = precipitation_m_to_mm(m_val)
        assert abs(float(result) - expected_mm) < 1e-9

    def test_array_element_wise_conversion(self) -> None:
        """Test element-wise conversion for a 1-D array input.

        Returns:
            None
        """
        m_arr = np.array([0.0, 0.001, 0.01, 0.1])
        result = precipitation_m_to_mm(m_arr)
        expected = np.array([0.0, 1.0, 10.0, 100.0])
        np.testing.assert_allclose(result, expected, atol=1e-9)

    def test_array_shape_preserved(self) -> None:
        """Test that a 2-D array output has the same shape as the 2-D input.

        Returns:
            None
        """
        m_arr = np.zeros((4, 3))
        result = precipitation_m_to_mm(m_arr)
        assert np.asarray(result).shape == m_arr.shape


# ---------------------------------------------------------------------------
# ssrd_to_par
# ---------------------------------------------------------------------------


class TestSsrdToPar:
    """Tests for the ssrd_to_par conversion function.

    Verifies return type, non-negativity for physical inputs, and the
    monotonicity expected of a linear conversion.
    """

    def test_zero_input_returns_zero(self) -> None:
        """Test that zero daily SSRD accumulation produces zero PAR flux.

        Returns:
            None
        """
        result = ssrd_to_par(0.0)
        assert float(result) == pytest.approx(0.0, abs=1e-9)

    def test_scalar_non_negative_for_positive_input(self) -> None:
        """Test that a positive SSRD scalar yields a non-negative PAR value.

        Returns:
            None
        """
        result = ssrd_to_par(1_000_000.0)
        assert float(result) >= 0.0

    def test_array_non_negative_for_non_negative_input(self) -> None:
        """Test that an array of non-negative SSRD values yields non-negative PAR.

        Returns:
            None
        """
        ssrd = np.array([0.0, 1e6, 5e6, 20e6])
        result = np.asarray(ssrd_to_par(ssrd))
        assert np.all(result >= 0.0)

    def test_scalar_return_type_is_numeric(self) -> None:
        """Test that a float scalar input returns a numeric scalar.

        Returns:
            None
        """
        result = ssrd_to_par(1_000_000.0)
        assert isinstance(result, (float, int, np.floating, np.integer))

    def test_array_return_type_is_ndarray(self) -> None:
        """Test that an ndarray input returns an ndarray.

        Returns:
            None
        """
        result = ssrd_to_par(np.array([1e6, 2e6]))
        assert isinstance(result, np.ndarray)

    def test_monotonically_increases_with_input(self) -> None:
        """Test that PAR increases monotonically as SSRD increases.

        This is a physical sanity check: more solar radiation → more PAR.

        Returns:
            None
        """
        ssrd = np.array([0.0, 1e6, 2e6, 5e6])
        result = np.asarray(ssrd_to_par(ssrd))
        diffs = np.diff(result)
        assert np.all(diffs >= 0.0)
