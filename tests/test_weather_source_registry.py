"""Unit tests for :mod:`datavia.weather.source_registry`.

Covers unit conversion functions and apply_conversion(), the DWD_stations
entry, and the vapour pressure deficit entries of every source.
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

    def test_radiation_w_m2_converted_to_par(self) -> None:
        """HYRAS W m⁻² radiation is converted to PAR."""
        from datavia.weather.source_registry import apply_conversion

        result = apply_conversion("HYRAS", "surface_solar_radiation_downwards", 100.0)
        assert result == pytest.approx(100.0 * 0.5 * 4.57)

    def test_dwd_stations_values_pass_through(self) -> None:
        """Kriged DWD values are already in target units: no conversion."""
        from datavia.weather.source_registry import apply_conversion

        assert apply_conversion("DWD_stations", "2m_temperature", 12.5) == 12.5
        assert apply_conversion("DWD_stations", "total_precipitation", 3.0) == 3.0

    def test_radiation_era5_still_ssrd_to_par(self) -> None:
        """ERA5 J m⁻² radiation still converts to the same PAR unit."""
        from datavia.weather.source_registry import apply_conversion

        result = apply_conversion(
            "ERA5_land", "surface_solar_radiation_downwards", 86400.0
        )
        assert result == pytest.approx(0.5 * 4.57)


# ---------------------------------------------------------------------------
# interpolate_station_parquet — index alignment fix (issue #4)


class TestDWDStationsEntry:
    """The kriged DWD_stations source is a gridded source like HYRAS."""

    def test_grid_downloader_is_kriging(self) -> None:
        from datavia.weather.dwd_kriging_downloader import DWDKrigingDownloader
        from datavia.weather.source_registry import get_grid_downloader_class

        assert get_grid_downloader_class("DWD_stations") is DWDKrigingDownloader

    def test_valid_variables(self) -> None:
        from datavia.weather.source_registry import get_valid_variables

        assert get_valid_variables("DWD_stations") == {
            "2m_temperature",
            "temperature_2m_max",
            "temperature_2m_min",
            "total_precipitation",
            "relative_humidity_2m",
            "vapour_pressure_deficit",
        }

    def test_same_grid_envelope_and_store_layout_as_hyras(self) -> None:
        from datavia.weather.source_registry import SOURCE_REGISTRY

        dwd, hyras = SOURCE_REGISTRY["DWD_stations"], SOURCE_REGISTRY["HYRAS"]
        assert dwd["coverage_bbox"] == hyras["coverage_bbox"]
        assert dwd["zarr_grid"]["crs"] == hyras["zarr_grid"]["crs"] == "EPSG:3035"
        assert dwd["zarr_grid"]["time_freq"] == "1D"

    def test_station_data_is_daily_only(self) -> None:
        from datavia.weather.source_registry import station_data_supports

        assert station_data_supports("daily")
        assert not station_data_supports("hourly")


class TestVapourPressureDeficitEntries:
    """Every source offers ``vapour_pressure_deficit``, stored as ``vpd`` in kPa."""

    @pytest.mark.parametrize("source_name", ["ERA5_land", "HYRAS", "DWD_stations"])
    def test_valid_and_stored_as_vpd(self, source_name: str) -> None:
        from datavia.weather.source_registry import (
            get_nc_variable_name,
            get_target_unit,
            get_valid_variables,
        )

        valid = get_valid_variables(source_name)
        assert valid is not None and "vapour_pressure_deficit" in valid
        assert get_nc_variable_name(source_name, "vapour_pressure_deficit") == "vpd"
        # Stored in kPa already: no conversion.
        assert get_target_unit(source_name, "vapour_pressure_deficit") is None

    def test_station_blend_is_a_daily_mean_in_kpa(self) -> None:
        from datavia.weather.source_registry import (
            SOURCE_REGISTRY,
            get_station_aggregation,
            is_accumulated,
        )

        entry = SOURCE_REGISTRY["DWD_stations"]
        assert entry["native_units"]["vapour_pressure_deficit"] == "kPa"
        aggregation = get_station_aggregation("vapour_pressure_deficit", "daily")
        assert aggregation["daily_aggregation"] == "mean"
        assert aggregation["day_start_hour"] == 0
        assert not aggregation["interval_end"]
        assert not is_accumulated("ERA5_land", "vapour_pressure_deficit")

    def test_hyras_grid_downloader_is_registered(self) -> None:
        from datavia.weather.hyras_downloader import HYRASDownloader
        from datavia.weather.source_registry import get_grid_downloader_class

        assert get_grid_downloader_class("HYRAS") is HYRASDownloader
