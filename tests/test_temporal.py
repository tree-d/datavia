"""Tests for the shared time-base helpers (de-accumulation, daily aggregation)."""

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from datavia.weather.source_registry import apply_conversion

from datavia.library.interpolation import (
    interpolate_dataset,
    interpolate_station_parquet,
)
from datavia.library.temporal import deaccumulate_since_midnight
from datavia.library.unit_conversions import ssrd_to_par, w_m2_to_par

# Constant 100 W m-2 => 360 000 J m-2 per hour, 8.64e6 J m-2 per day.
_HOURLY_J = 360_000.0


def _era5_accumulated(days: int = 3, start: str = "2025-06-01") -> xr.Dataset:
    """ERA5-Land style ssrd: reset after 00:00, which holds the previous day's total."""
    times = pd.date_range(start, periods=days * 24, freq="1h") + pd.Timedelta(hours=1)
    # times run 01:00 D0 .. 00:00 D0+days
    hours = times.hour
    accum = np.where(hours == 0, 24, hours) * _HOURLY_J
    da = xr.DataArray(
        np.broadcast_to(accum[:, None, None], (len(times), 3, 3)).astype("float64"),
        dims=["time", "latitude", "longitude"],
        coords={
            "time": times,
            "latitude": [50.0, 50.1, 50.2],
            "longitude": [10.0, 10.1, 10.2],
        },
    )
    return da.to_dataset(name="ssrd")


class TestDeaccumulate:
    """``deaccumulate_since_midnight`` edge cases."""

    def test_constant_flux_gives_constant_increments(self):
        ds = _era5_accumulated()
        inc = deaccumulate_since_midnight(ds["ssrd"])
        # First stamp is 01:00 -> equals raw; everything else differences.
        np.testing.assert_allclose(inc.values, _HOURLY_J)

    def test_hour_01_needs_no_predecessor(self):
        da = xr.DataArray(
            [_HOURLY_J],
            dims=["time"],
            coords={"time": pd.DatetimeIndex(["2025-06-01 01:00"])},
        )
        assert deaccumulate_since_midnight(da).item() == _HOURLY_J

    def test_hour_00_without_predecessor_is_nan(self):
        da = xr.DataArray(
            [24 * _HOURLY_J],
            dims=["time"],
            coords={"time": pd.DatetimeIndex(["2025-06-02 00:00"])},
        )
        assert np.isnan(deaccumulate_since_midnight(da).item())


class TestAccumulatedSampling:
    """``interpolate_dataset(accumulated=True)`` daily and hourly modes."""

    def test_daily_returns_day_total_not_nearest_hour(self):
        ds = _era5_accumulated()
        # Noon query would hit a 12 h partial total with nearest-stamp selection.
        got = interpolate_dataset(
            ds,
            50.1,
            10.1,
            "ssrd",
            pd.Timestamp("2025-06-02 12:00"),
            temporal_resolution="daily",
            accumulated=True,
        )
        assert got == pytest.approx(24 * _HOURLY_J)
        assert ssrd_to_par(got) == pytest.approx(w_m2_to_par(100.0))

    def test_daily_missing_total_stamp_is_nan(self):
        ds = _era5_accumulated(days=1)  # has D0 01:00 .. D1 00:00 only
        got = interpolate_dataset(
            ds,
            50.1,
            10.1,
            "ssrd",
            pd.Timestamp("2025-06-02 12:00"),
            temporal_resolution="daily",
            accumulated=True,
        )
        assert np.isnan(got)

    def test_hourly_returns_increments_for_whole_day(self):
        ds = _era5_accumulated()
        got = interpolate_dataset(
            ds,
            50.1,
            10.1,
            "ssrd",
            pd.Timestamp("2025-06-02"),
            temporal_resolution="hourly",
            accumulated=True,
        )
        assert got.shape == (24,)
        np.testing.assert_allclose(got, _HOURLY_J)

    def test_hourly_and_daily_agree_in_par(self):
        ds = _era5_accumulated()
        t = pd.Timestamp("2025-06-02")
        hourly = interpolate_dataset(
            ds, 50.1, 10.1, "ssrd", t, "EPSG:4326", "hourly", True
        )
        daily = interpolate_dataset(
            ds, 50.1, 10.1, "ssrd", t, "EPSG:4326", "daily", True
        )
        assert ssrd_to_par(hourly, period_s=3600).mean() == pytest.approx(
            ssrd_to_par(daily)
        )


class TestStationDaily:
    """Daily aggregation of interval-end hourly station data."""

    def _parquet(self, tmp_path, hours: int = 24):
        stamps = pd.date_range("2025-06-02 01:00", periods=hours, freq="1h")
        df = pd.DataFrame(
            {
                "station_id": "A",
                "latitude": 50.0,
                "longitude": 10.0,
                "datetime": stamps,
                "surface_solar_radiation_downwards": 100.0,
            }
        )
        path = tmp_path / "s.parquet"
        df.to_parquet(path)
        return str(path)

    def test_daily_mean_over_interval_end_day(self, tmp_path):
        got = interpolate_station_parquet(
            self._parquet(tmp_path),
            50.0,
            10.0,
            "surface_solar_radiation_downwards",
            pd.Timestamp("2025-06-02 12:00"),
            daily_aggregation="mean",
        )
        assert got == pytest.approx(100.0)

    def test_partial_day_is_rejected(self, tmp_path):
        got = interpolate_station_parquet(
            self._parquet(tmp_path, hours=12),
            50.0,
            10.0,
            "surface_solar_radiation_downwards",
            pd.Timestamp("2025-06-02 12:00"),
            daily_aggregation="mean",
        )
        assert np.isnan(got)


def test_apply_conversion_period_only_affects_energy_totals():
    """``period_s`` rescales J m-2 totals and leaves other conversions alone."""
    hourly = apply_conversion(
        "ERA5_land", "surface_solar_radiation_downwards", _HOURLY_J, period_s=3600.0
    )
    assert hourly == pytest.approx(w_m2_to_par(100.0))
    assert apply_conversion(
        "ERA5_land", "2m_temperature", 300.0, period_s=3600.0
    ) == pytest.approx(26.85)


class TestGetterAccumulated:
    """``GetterWeather`` wiring for accumulated variables."""

    _VAR = "surface_solar_radiation_downwards"

    def _lazy_store(self, tmp_path):
        ds = _era5_accumulated().rename({"ssrd": self._VAR})
        ds.to_zarr(tmp_path / "z.zarr", mode="w")
        return xr.open_zarr(tmp_path / "z.zarr")  # lazy, dask-backed

    def _query(self, store, when, resolution="daily"):
        from unittest.mock import patch

        from datavia.weather.getter_weather import GetterWeather

        getter = GetterWeather("ERA5_land", temporal_resolution=resolution)
        with (
            patch("datavia.weather.getter_weather.get_weather_paths", return_value=[]),
            patch("datavia.weather.getter_weather._try_open_zarr", return_value=store),
        ):
            return getter.get_data(
                np.array([[10.1, 50.1]]), variable=self._VAR, datetime_utc=when
            )

    def test_daily_single_time_on_lazy_zarr(self, tmp_path):
        """A noon query returns the full-day PAR from a dask-backed store."""
        got = self._query(self._lazy_store(tmp_path), "2025-06-02 12:00")
        assert got[0] == pytest.approx(w_m2_to_par(100.0))

    def test_daily_multi_time_on_lazy_zarr(self, tmp_path):
        """Each requested day gets its own total; shape is (N, T)."""
        got = self._query(
            self._lazy_store(tmp_path), ["2025-06-01 06:00", "2025-06-02 06:00"]
        )
        assert got.shape == (1, 2)
        np.testing.assert_allclose(got, w_m2_to_par(100.0))

    def test_daily_day_without_next_midnight_is_missing(self, tmp_path):
        """A day whose next-00:00 stamp is absent must error, not return a partial."""
        with pytest.raises(RuntimeError):
            self._query(self._lazy_store(tmp_path), "2025-06-04 12:00")

    def test_accumulated_flag_and_period_forwarded(self):
        """Hourly mode passes accumulated=True to the sampler and period_s=3600."""
        from unittest.mock import MagicMock, patch

        from datavia.weather.getter_weather import GetterWeather

        getter = GetterWeather("ERA5_land", temporal_resolution="hourly")
        with (
            patch("datavia.weather.getter_weather.get_weather_paths", return_value=[]),
            patch(
                "datavia.weather.getter_weather._try_open_zarr",
                return_value=MagicMock(),
            ),
            patch(
                "datavia.weather.getter_weather.interpolate_dataset",
                return_value=np.array([_HOURLY_J]),
            ) as sampler,
            patch(
                "datavia.weather.getter_weather.apply_conversion",
                side_effect=lambda s, v, val, u, **kw: val,
            ) as conv,
        ):
            getter.get_data(
                np.array([[10.1, 50.1]]), variable=self._VAR, datetime_utc="2025-06-02"
            )
        assert sampler.call_args.kwargs["accumulated"] is True
        assert conv.call_args.kwargs["period_s"] == 3600.0

    def test_non_accumulated_variable_unchanged(self):
        """Temperature is not flagged accumulated and gets no period_s."""
        from unittest.mock import MagicMock, patch

        from datavia.weather.getter_weather import GetterWeather

        getter = GetterWeather("ERA5_land")
        with (
            patch("datavia.weather.getter_weather.get_weather_paths", return_value=[]),
            patch(
                "datavia.weather.getter_weather._try_open_zarr",
                return_value=MagicMock(),
            ),
            patch(
                "datavia.weather.getter_weather.interpolate_dataset",
                return_value=np.array([300.0]),
            ) as sampler,
            patch(
                "datavia.weather.getter_weather.apply_conversion",
                side_effect=lambda s, v, val, u, **kw: val,
            ) as conv,
        ):
            getter.get_data(
                np.array([[10.1, 50.1]]),
                variable="2m_temperature",
                datetime_utc="2025-06-02",
            )
        assert sampler.call_args.kwargs["accumulated"] is False
        assert "period_s" not in conv.call_args.kwargs


class TestTryOpenZarrPadding:
    """Year padding so day totals/increments can cross a year boundary."""

    def _years_opened(self, from_dt, to_dt, pad_day):
        from unittest.mock import MagicMock, patch

        from datavia.weather.getter_weather import _try_open_zarr

        manager = MagicMock()
        with (
            patch("datavia.config.get_config", return_value=MagicMock()),
            patch(
                "datavia.weather.getter_weather.ZarrStoreManager", return_value=manager
            ),
        ):
            _try_open_zarr(
                "ERA5_land", "2m_temperature", from_dt, to_dt, pad_day=pad_day
            )
        return manager.open_multi_year.call_args.args[1]

    def test_no_padding_by_default(self):
        """Without pad_day only the requested years are opened."""
        assert self._years_opened("2025-01-01", "2025-12-31", False) == [2025]

    def test_padding_reaches_adjacent_years(self):
        """Jan 1 needs 23:00 of the previous year; Dec 31 needs next 00:00."""
        assert self._years_opened("2025-01-01", "2025-12-31", True) == [
            2024,
            2025,
            2026,
        ]


def test_station_daily_precipitation_sum(tmp_path):
    """DWD precipitation in daily mode is the 24 h sum, per the registry."""
    from datavia.weather.source_registry import SOURCE_REGISTRY

    agg = SOURCE_REGISTRY["DWD_stations"]["daily_aggregation"]["total_precipitation"]
    stamps = pd.date_range("2025-06-02 01:00", periods=24, freq="1h")
    path = tmp_path / "p.parquet"
    pd.DataFrame(
        {
            "station_id": "A",
            "latitude": 50.0,
            "longitude": 10.0,
            "datetime": stamps,
            "total_precipitation": 0.5,
        }
    ).to_parquet(path)
    got = interpolate_station_parquet(
        str(path),
        50.0,
        10.0,
        "total_precipitation",
        pd.Timestamp("2025-06-02 12:00"),
        daily_aggregation=agg,
    )
    assert agg == "sum"
    assert got == pytest.approx(12.0)
