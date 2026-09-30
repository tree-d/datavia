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


def _random_accumulated(name: str, days: int = 3):
    """Accumulated dataset from random hourly increments; returns (increments, ds).

    Stamps run ``2025-06-01 01:00`` upward (interval end); the accumulation
    resets after each 00:00 stamp, which holds the previous day's total.
    """
    times = pd.date_range("2025-06-01 01:00", periods=days * 24, freq="1h")
    inc = pd.Series(np.random.default_rng(7).random(len(times)) * 1e6, index=times)
    accum = inc.groupby((inc.index - pd.Timedelta(hours=1)).normalize()).cumsum()
    da = xr.DataArray(
        np.broadcast_to(accum.values[:, None, None], (len(times), 3, 3)).copy(),
        dims=["time", "latitude", "longitude"],
        coords={
            "time": times,
            "latitude": [50.0, 50.1, 50.2],
            "longitude": [10.0, 10.1, 10.2],
        },
    )
    return inc, da.to_dataset(name=name)


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

    def test_hourly_returns_increment_at_requested_hour(self):
        inc, ds = _random_accumulated("ssrd")
        for stamp in [
            "2025-06-02 00:00",
            "2025-06-02 01:00",
            "2025-06-02 02:00",
            "2025-06-02 13:00",
        ]:
            got = interpolate_dataset(
                ds,
                50.1,
                10.1,
                "ssrd",
                pd.Timestamp(stamp),
                temporal_resolution="hourly",
                accumulated=True,
            )
            assert got == pytest.approx(inc[pd.Timestamp(stamp)]), stamp

    def test_hourly_list_gives_one_value_per_timestamp(self):
        inc, ds = _random_accumulated("ssrd")
        stamps = ["2025-06-02 00:00", "2025-06-02 05:00"]
        got = interpolate_dataset(
            ds,
            50.1,
            10.1,
            "ssrd",
            [pd.Timestamp(t) for t in stamps],
            temporal_resolution="hourly",
            accumulated=True,
        )
        np.testing.assert_allclose(got, [inc[pd.Timestamp(t)] for t in stamps])

    def test_hourly_first_stamp_without_predecessor_is_nan(self):
        _, ds = _random_accumulated("ssrd")
        got = interpolate_dataset(
            ds.isel(time=slice(23, None)),
            50.1,
            10.1,
            "ssrd",
            pd.Timestamp("2025-06-02 00:00"),
            temporal_resolution="hourly",
            accumulated=True,
        )
        assert np.isnan(got)

    def test_daily_total_matches_sum_of_hourly_increments(self):
        inc, ds = _random_accumulated("ssrd")
        t = pd.Timestamp("2025-06-02 17:30")
        daily = interpolate_dataset(
            ds, 50.1, 10.1, "ssrd", t, temporal_resolution="daily", accumulated=True
        )
        day = inc[(inc.index > "2025-06-02 00:00") & (inc.index <= "2025-06-03 00:00")]
        assert daily == pytest.approx(day.sum())

    def test_daily_precipitation_window_is_06_to_06_utc(self):
        inc, ds = _random_accumulated("tp")
        expected = inc[
            (inc.index > "2025-06-02 06:00") & (inc.index <= "2025-06-03 06:00")
        ].sum()
        # 03:00 UTC on June 3 still belongs to the 06-06 window that started June 2.
        for q in ["2025-06-02 06:30", "2025-06-02 20:00", "2025-06-03 03:00"]:
            got = interpolate_dataset(
                ds,
                50.1,
                10.1,
                "tp",
                pd.Timestamp(q),
                temporal_resolution="daily",
                accumulated=True,
                day_start_hour=6,
            )
            assert got == pytest.approx(expected), q

    def test_daily_window_without_closing_stamps_is_nan(self):
        _, ds = _random_accumulated("tp", days=1)
        got = interpolate_dataset(
            ds,
            50.1,
            10.1,
            "tp",
            pd.Timestamp("2025-06-01 12:00"),
            temporal_resolution="daily",
            accumulated=True,
            day_start_hour=6,
        )
        assert np.isnan(got)


class TestDailyNativeWindow:
    """HYRAS pr style data: one value per day, stamped at the window start (06:00)."""

    def _ds(self):
        times = pd.date_range("2025-06-01 06:00", periods=4, freq="1D")
        return xr.Dataset(
            {
                "pr": (
                    ("time", "latitude", "longitude"),
                    np.arange(4.0).repeat(9).reshape(4, 3, 3),
                )
            },
            coords={
                "time": times,
                "latitude": [50.0, 50.1, 50.2],
                "longitude": [10.0, 10.1, 10.2],
            },
        )

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("2025-06-02 00:00", 0.0),  # before 06:00 -> window that began June 1
            ("2025-06-02 06:00", 1.0),
            ("2025-06-02 12:00", 1.0),
            ("2025-06-02 20:00", 1.0),  # nearest-stamp would give 2.0
            ("2025-06-03 05:59", 1.0),
        ],
    )
    def test_query_resolves_to_containing_window(self, query, expected):
        got = interpolate_dataset(
            self._ds(), 50.1, 10.1, "pr", pd.Timestamp(query), day_start_hour=6
        )
        assert got == expected

    def test_nearest_would_have_picked_next_day_after_18_utc(self):
        naive = interpolate_dataset(
            self._ds(), 50.1, 10.1, "pr", pd.Timestamp("2025-06-02 20:00")
        )
        assert naive == 2.0  # the regression day_start_hour=6 fixes


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


class TestHourlyThroughGetter:
    """Hourly mode returns the same shapes as daily mode (value at the hour)."""

    _VAR = "surface_solar_radiation_downwards"

    def _query(self, tmp_path, when):
        from unittest.mock import patch

        from datavia.weather.getter_weather import GetterWeather

        inc, ds = _random_accumulated(self._VAR)
        ds.to_zarr(tmp_path / "z.zarr", mode="w")
        store = xr.open_zarr(tmp_path / "z.zarr")
        getter = GetterWeather("ERA5_land", temporal_resolution="hourly")
        with (
            patch("datavia.weather.getter_weather.get_weather_paths", return_value=[]),
            patch("datavia.weather.getter_weather._try_open_zarr", return_value=store),
        ):
            got = getter.get_data(
                np.array([[10.1, 50.1], [10.0, 50.0]]),
                variable=self._VAR,
                datetime_utc=when,
            )
        return inc, got

    def test_single_timestamp_gives_one_value_per_coordinate(self, tmp_path):
        inc, got = self._query(tmp_path, "2025-06-02 12:00")
        assert got.shape == (2,)
        np.testing.assert_allclose(
            got, w_m2_to_par(inc[pd.Timestamp("2025-06-02 12:00")] / 3600.0), rtol=1e-5
        )

    def test_timestamp_list_gives_coordinates_by_time(self, tmp_path):
        inc, got = self._query(tmp_path, ["2025-06-02 00:00", "2025-06-02 01:00"])
        assert got.shape == (2, 2)
        expected = [
            w_m2_to_par(inc[pd.Timestamp(t)] / 3600.0)
            for t in ["2025-06-02 00:00", "2025-06-02 01:00"]
        ]
        np.testing.assert_allclose(got[0], expected, rtol=1e-5)


class TestPrecipitationWindowsAgree:
    """ERA5, DWD and HYRAS-style precipitation give the same daily totals."""

    def test_same_rain_same_daily_totals(self, tmp_path):
        rng = np.random.default_rng(3)
        times = pd.date_range("2025-06-01 01:00", periods=72, freq="1h")
        rain = pd.Series(rng.random(72), index=times)  # mm per hour, interval-end

        # ERA5: accumulated metres since 00 UTC.
        accum = (
            (rain / 1000.0)
            .groupby((times - pd.Timedelta(hours=1)).normalize())
            .cumsum()
        )
        era5 = xr.DataArray(
            np.broadcast_to(accum.values[:, None, None], (72, 3, 3)).copy(),
            dims=["time", "latitude", "longitude"],
            coords={
                "time": times,
                "latitude": [50.0, 50.1, 50.2],
                "longitude": [10.0, 10.1, 10.2],
            },
        ).to_dataset(name="tp")

        # DWD: hourly station rows.
        path = tmp_path / "p.parquet"
        pd.DataFrame(
            {
                "station_id": "A",
                "latitude": 50.1,
                "longitude": 10.1,
                "datetime": times,
                "total_precipitation": rain.values,
            }
        ).to_parquet(path)

        # HYRAS: 06-06 totals stamped at 06:00 (only June 2 is complete).
        hyras_total = rain[
            (times > "2025-06-02 06:00") & (times <= "2025-06-03 06:00")
        ].sum()
        hyras = xr.Dataset(
            {
                "pr": (
                    ("time", "latitude", "longitude"),
                    np.full((1, 3, 3), hyras_total),
                )
            },
            coords={
                "time": [pd.Timestamp("2025-06-02 06:00")],
                "latitude": [50.0, 50.1, 50.2],
                "longitude": [10.0, 10.1, 10.2],
            },
        )

        q = pd.Timestamp("2025-06-02 15:00")
        e = (
            interpolate_dataset(
                era5, 50.1, 10.1, "tp", q, accumulated=True, day_start_hour=6
            )
            * 1000.0
        )
        d = interpolate_station_parquet(
            str(path),
            50.1,
            10.1,
            "total_precipitation",
            q,
            daily_aggregation="sum",
            day_start_hour=6,
        )
        h = interpolate_dataset(hyras, 50.1, 10.1, "pr", q, day_start_hour=6)
        assert e == pytest.approx(hyras_total, rel=1e-5)
        assert d == pytest.approx(hyras_total)
        assert h == pytest.approx(hyras_total)
