"""Tests for the shared time-base helpers (day labels, de-accumulation, daily means)."""

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from datavia.weather.source_registry import apply_conversion, get_station_aggregation

from datavia.library.interpolation import (
    interpolate_dataset,
    interpolate_station_parquet,
)
from datavia.library.temporal import (
    accumulated_day_total,
    day_window,
    deaccumulate_since_midnight,
    infer_series_type,
    instantaneous_day_mean,
    to_daily,
)
from datavia.library.unit_conversions import ssrd_to_par, w_m2_to_par

# Constant 100 W m-2 => 360 000 J m-2 per hour, 8.64e6 J m-2 per day.
_HOURLY_J = 360_000.0
_LATS = [50.0, 50.1, 50.2]
_LONS = [10.0, 10.1, 10.2]


def _grid(values, times, name: str) -> xr.Dataset:
    """Broadcast a 1-D time series onto a 3x3 grid."""
    values = np.asarray(values, dtype="float64")
    da = xr.DataArray(
        np.broadcast_to(values[:, None, None], (len(times), 3, 3)).copy(),
        dims=["time", "latitude", "longitude"],
        coords={"time": times, "latitude": _LATS, "longitude": _LONS},
    )
    return da.to_dataset(name=name)


def _era5_accumulated(days: int = 3, start: str = "2025-06-01") -> xr.Dataset:
    """ERA5-Land style ssrd: reset after 00:00, which holds the previous day's total."""
    times = pd.date_range(start, periods=days * 24, freq="1h") + pd.Timedelta(hours=1)
    # times run 01:00 D0 .. 00:00 D0+days
    hours = times.hour
    return _grid(np.where(hours == 0, 24, hours) * _HOURLY_J, times, "ssrd")


def _random_accumulated(name: str, days: int = 3):
    """Accumulated dataset from random hourly increments; returns (increments, ds).

    Stamps run ``2025-06-01 01:00`` upward (interval end); the accumulation
    resets after each 00:00 stamp, which holds the previous day's total.
    """
    times = pd.date_range("2025-06-01 01:00", periods=days * 24, freq="1h")
    inc = pd.Series(np.random.default_rng(7).random(len(times)) * 1e6, index=times)
    accum = inc.groupby((inc.index - pd.Timedelta(hours=1)).normalize()).cumsum()
    return inc, _grid(accum.values, times, name)


def _sample(ds, name, when, **kwargs):
    return interpolate_dataset(ds, 50.1, 10.1, name, pd.Timestamp(when), **kwargs)


# ---------------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    """``day_window`` and ``infer_series_type``."""

    @pytest.mark.parametrize(
        "when", ["2025-06-02", "2025-06-02 03:00", "2025-06-02 23:59"]
    )
    def test_day_window_uses_calendar_date(self, when):
        assert day_window(when, 6) == (
            pd.Timestamp("2025-06-02 06:00"),
            pd.Timestamp("2025-06-03 06:00"),
        )

    @pytest.mark.parametrize(
        ("times", "expected"),
        [
            (["2025-06-01"], "daily"),
            (pd.date_range("2025-06-01 06:00", periods=5, freq="1D"), "daily"),
            (pd.date_range("2025-06-01", periods=5, freq="1h"), "instantaneous"),
        ],
    )
    def test_infer_series_type(self, times, expected):
        assert infer_series_type(times) == expected

    def test_unknown_series_type_raises(self):
        ds = _era5_accumulated()
        with pytest.raises(ValueError, match="series_type"):
            _sample(ds, "ssrd", "2025-06-02", series_type="bogus")


# ---------------------------------------------------------------------------
# De-accumulation
# ---------------------------------------------------------------------------


class TestDeaccumulate:
    """``deaccumulate_since_midnight`` edge cases."""

    def test_constant_flux_gives_constant_increments(self):
        inc = deaccumulate_since_midnight(_era5_accumulated()["ssrd"])
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

    def test_negative_differences_are_clipped(self):
        """Real ERA5-Land running totals sometimes dip slightly within a day."""
        da = xr.DataArray(
            [5.0, 4.999],
            dims=["time"],
            coords={"time": pd.date_range("2025-06-01 02:00", periods=2, freq="1h")},
        )
        assert deaccumulate_since_midnight(da).values[1] == 0.0

    @pytest.mark.parametrize(
        ("query", "hour_end"),
        [("2025-06-02 12:30", 13), ("2025-06-02 13:30", 14), ("2025-06-02 12:00", 12)],
    )
    def test_off_hour_targets_use_hour_containing_them(self, query, hour_end):
        """Half hours resolve consistently to the interval that contains them."""
        inc, ds = _random_accumulated("ssrd")
        got = deaccumulate_since_midnight(ds["ssrd"], at=[pd.Timestamp(query)])
        expected = inc[pd.Timestamp(f"2025-06-02 {hour_end:02d}:00")]
        assert got.isel(latitude=0, longitude=0).item() == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Accumulated variables through interpolate_dataset
# ---------------------------------------------------------------------------


class TestAccumulatedSampling:
    """``interpolate_dataset(series_type="accumulated")`` daily and hourly modes."""

    def test_daily_returns_day_total_not_nearest_hour(self):
        # Noon query would hit a 12 h partial total with nearest-stamp selection.
        got = _sample(
            _era5_accumulated(), "ssrd", "2025-06-02 12:00", series_type="accumulated"
        )
        assert got == pytest.approx(24 * _HOURLY_J)
        assert ssrd_to_par(got) == pytest.approx(w_m2_to_par(100.0))

    def test_daily_missing_total_stamp_is_nan(self):
        ds = _era5_accumulated(days=1)  # has D0 01:00 .. D1 00:00 only
        got = _sample(ds, "ssrd", "2025-06-02 12:00", series_type="accumulated")
        assert np.isnan(got)

    def test_hourly_returns_increment_at_requested_hour(self):
        inc, ds = _random_accumulated("ssrd")
        for stamp in [
            "2025-06-02 00:00",
            "2025-06-02 01:00",
            "2025-06-02 02:00",
            "2025-06-02 13:00",
        ]:
            got = _sample(
                ds,
                "ssrd",
                stamp,
                temporal_resolution="hourly",
                series_type="accumulated",
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
            series_type="accumulated",
        )
        np.testing.assert_allclose(got, [inc[pd.Timestamp(t)] for t in stamps])

    def test_hourly_first_stamp_without_predecessor_is_nan(self):
        _, ds = _random_accumulated("ssrd")
        got = _sample(
            ds.isel(time=slice(23, None)),
            "ssrd",
            "2025-06-02 00:00",
            temporal_resolution="hourly",
            series_type="accumulated",
        )
        assert np.isnan(got)

    def test_daily_total_matches_sum_of_hourly_increments(self):
        inc, ds = _random_accumulated("ssrd")
        daily = _sample(ds, "ssrd", "2025-06-02 17:30", series_type="accumulated")
        day = inc[(inc.index > "2025-06-02 00:00") & (inc.index <= "2025-06-03 00:00")]
        assert daily == pytest.approx(day.sum())

    @pytest.mark.parametrize(
        "query",
        ["2025-06-02", "2025-06-02 03:00", "2025-06-02 06:30", "2025-06-02 20:00"],
    )
    def test_daily_precipitation_is_06_to_06_window_of_the_date(self, query):
        """Day D is (D 06:00, D+1 06:00], whatever the time of day queried."""
        inc, ds = _random_accumulated("tp")
        expected = inc[
            (inc.index > "2025-06-02 06:00") & (inc.index <= "2025-06-03 06:00")
        ].sum()
        got = _sample(ds, "tp", query, series_type="accumulated", day_start_hour=6)
        assert got == pytest.approx(expected)

    def test_daily_window_without_closing_stamps_is_nan(self):
        _, ds = _random_accumulated("tp", days=1)
        got = _sample(
            ds, "tp", "2025-06-01 12:00", series_type="accumulated", day_start_hour=6
        )
        assert np.isnan(got)

    def test_negative_day_total_is_clipped(self):
        times = pd.DatetimeIndex(
            ["2025-06-02 06:00", "2025-06-03 00:00", "2025-06-03 06:00"]
        )
        da = xr.DataArray([1.0, 0.9999, 0.0], dims=["time"], coords={"time": times})
        got = accumulated_day_total(da, [pd.Timestamp("2025-06-02")], day_start_hour=6)
        assert got.item() == 0.0


# ---------------------------------------------------------------------------
# Daily-native data (HYRAS)
# ---------------------------------------------------------------------------


class TestDailyNative:
    """HYRAS style data: one stamp per day, dated with the day it describes."""

    @staticmethod
    def _ds(stamp_hour: int) -> xr.Dataset:
        times = pd.date_range("2025-06-01", periods=4, freq="1D") + pd.Timedelta(
            hours=stamp_hour
        )
        return _grid(np.arange(4.0), times, "v")

    @pytest.mark.parametrize("stamp_hour", [0, 6, 12])  # HYRAS tas, pr, rsds
    @pytest.mark.parametrize(
        "query",
        [
            "2025-06-02",
            "2025-06-02 03:00",
            "2025-06-02 12:00",
            "2025-06-02 13:00",
            "2025-06-02 23:59",
        ],
    )
    def test_query_returns_value_of_its_date(self, stamp_hour, query):
        """Regression: 00:00-stamped tas queried after 12:00 used to give D+1."""
        assert _sample(self._ds(stamp_hour), "v", query) == 1.0

    def test_day_outside_the_data_is_nan(self):
        """Regression: nearest-stamp selection silently returned an edge day."""
        assert np.isnan(_sample(self._ds(6), "v", "2025-05-31"))
        assert np.isnan(_sample(self._ds(6), "v", "2025-06-10"))

    def test_first_day_of_data_is_found(self):
        """Jan 1 style edge: the first stamp of a store is its own date's value."""
        assert _sample(self._ds(6), "v", "2025-06-01") == 0.0


# ---------------------------------------------------------------------------
# Instantaneous hourly data (temperature, humidity)
# ---------------------------------------------------------------------------


class TestInstantaneous:
    """Hourly snapshots: daily mode is the mean of the 24 stamps of the date."""

    @staticmethod
    def _ds(days: int = 3) -> xr.Dataset:
        times = pd.date_range("2025-06-01", periods=days * 24, freq="1h")
        # Value = hour of day + 100 * day index, so each day has a distinct mean.
        values = times.hour + 100 * (times.normalize() - times[0]).days
        return _grid(values, times, "t2m")

    @pytest.mark.parametrize(
        "query", ["2025-06-02", "2025-06-02 12:00", "2025-06-02 23:00"]
    )
    def test_daily_mean_of_the_date(self, query):
        # Day index 1: hours 0..23 plus 100 -> mean 111.5; D+1 00:00 excluded.
        assert _sample(self._ds(), "t2m", query) == pytest.approx(111.5)

    def test_one_missing_hour_gives_nan(self):
        ds = self._ds().drop_sel(time=pd.Timestamp("2025-06-02 05:00"))
        assert np.isnan(_sample(ds, "t2m", "2025-06-02"))

    def test_helper_returns_one_value_per_target(self):
        got = instantaneous_day_mean(
            self._ds()["t2m"],
            [pd.Timestamp("2025-06-01"), pd.Timestamp("2025-06-03 08:00")],
        )
        np.testing.assert_allclose(
            got.isel(latitude=0, longitude=0).values, [11.5, 211.5]
        )

    def test_hourly_mode_picks_the_stamp(self):
        got = _sample(
            self._ds(), "t2m", "2025-06-02 05:10", temporal_resolution="hourly"
        )
        assert got == pytest.approx(105.0)

    def test_hourly_mode_outside_data_is_nan(self):
        """Regression: nearest-stamp selection returned the last stamp."""
        got = _sample(
            self._ds(), "t2m", "2025-07-01 05:00", temporal_resolution="hourly"
        )
        assert np.isnan(got)


# ---------------------------------------------------------------------------
# Station daily aggregation
# ---------------------------------------------------------------------------


def _station_parquet(
    tmp_path, stations, column, start="2025-06-02 01:00", hours=24, value=100.0
):
    """Hourly parquet for *stations* ``[(id, lat, lon), ...]`` with a constant value."""
    stamps = pd.date_range(start, periods=hours, freq="1h")
    frames = [
        pd.DataFrame(
            {
                "station_id": sid,
                "latitude": lat,
                "longitude": lon,
                "datetime": stamps,
                column: value,
            }
        )
        for sid, lat, lon in stations
    ]
    path = tmp_path / "s.parquet"
    pd.concat(frames).to_parquet(path)
    return str(path)


class TestStationDaily:
    """Daily aggregation of hourly station data."""

    _SSRD = "surface_solar_radiation_downwards"

    def test_daily_mean_over_interval_end_day(self, tmp_path):
        path = _station_parquet(tmp_path, [("A", 50.0, 10.0)], self._SSRD)
        got = interpolate_station_parquet(
            path,
            50.0,
            10.0,
            self._SSRD,
            pd.Timestamp("2025-06-02 12:00"),
            daily_aggregation="mean",
        )
        assert got == pytest.approx(100.0)

    def test_partial_day_is_rejected(self, tmp_path):
        path = _station_parquet(tmp_path, [("A", 50.0, 10.0)], self._SSRD, hours=12)
        got = interpolate_station_parquet(
            path,
            50.0,
            10.0,
            self._SSRD,
            pd.Timestamp("2025-06-02 12:00"),
            daily_aggregation="mean",
        )
        assert np.isnan(got)

    def test_null_inside_day_drops_station(self, tmp_path):
        """A station with one null hour is dropped; the complete one remains."""
        stamps = pd.date_range("2025-06-02 01:00", periods=24, freq="1h")
        a = np.full(24, 100.0)
        a[3] = np.nan
        path = tmp_path / "s.parquet"
        pd.concat(
            pd.DataFrame(
                {
                    "station_id": s,
                    "latitude": lat,
                    "longitude": 10.0,
                    "datetime": stamps,
                    self._SSRD: v,
                }
            )
            for s, lat, v in [("A", 50.0, a), ("B", 50.05, np.full(24, 50.0))]
        ).to_parquet(path)
        got = interpolate_station_parquet(
            str(path),
            50.0,
            10.0,
            self._SSRD,
            pd.Timestamp("2025-06-02"),
            daily_aggregation="mean",
        )
        assert got == pytest.approx(50.0)

    def test_several_stations_are_inverse_distance_weighted(self, tmp_path):
        """Distances stay aligned with the per-station daily values."""
        path = tmp_path / "s.parquet"
        stamps = pd.date_range("2025-06-02 01:00", periods=24, freq="1h")
        stations = [
            ("A", 50.01, 10.0, 10.0),
            ("B", 50.02, 10.0, 20.0),
            ("C", 50.04, 10.0, 40.0),
        ]
        pd.concat(
            pd.DataFrame(
                {
                    "station_id": s,
                    "latitude": la,
                    "longitude": lo,
                    "datetime": stamps,
                    self._SSRD: v,
                }
            )
            for s, la, lo, v in stations
        ).to_parquet(path)
        got = interpolate_station_parquet(
            str(path),
            50.0,
            10.0,
            self._SSRD,
            pd.Timestamp("2025-06-02"),
            daily_aggregation="mean",
        )
        # Distances are proportional to 1:2:4, so weights are 4:2:1.
        assert got == pytest.approx((4 * 10.0 + 2 * 20.0 + 1 * 40.0) / 7)

    def test_timezone_aware_target(self, tmp_path):
        path = _station_parquet(tmp_path, [("A", 50.0, 10.0)], self._SSRD)
        got = interpolate_station_parquet(
            path,
            50.0,
            10.0,
            self._SSRD,
            "2025-06-02T12:00:00+02:00",
            daily_aggregation="mean",
        )
        assert got == pytest.approx(100.0)

    def test_instantaneous_mean_uses_left_closed_day(self, tmp_path):
        """Temperature day D is [D 00:00, D+1 00:00): D 00:00 in, D+1 00:00 out."""
        stamps = pd.date_range("2025-06-02 00:00", periods=25, freq="1h")
        path = tmp_path / "t.parquet"
        pd.DataFrame(
            {
                "station_id": "A",
                "latitude": 50.0,
                "longitude": 10.0,
                "datetime": stamps,
                "2m_temperature": np.r_[np.full(24, 10.0), 99.0],
            }
        ).to_parquet(path)
        kwargs = get_station_aggregation("2m_temperature", "daily")
        assert kwargs["interval_end"] is False
        got = interpolate_station_parquet(
            str(path),
            50.0,
            10.0,
            "2m_temperature",
            pd.Timestamp("2025-06-02"),
            **kwargs,
        )
        assert got == pytest.approx(10.0)

    def test_to_daily_interval_end_excludes_window_start(self):
        stamps = pd.date_range("2025-06-02 00:00", periods=25, freq="1h")
        df = pd.DataFrame({"g": 1, "datetime": stamps, "x": 1.0})
        out = to_daily(df, "x", "sum", pd.Timestamp("2025-06-02"), ["g"])
        assert out["x"].item() == 24.0


def test_station_daily_precipitation_sum(tmp_path):
    """DWD precipitation in daily mode is the 24 h sum, per the registry."""
    kwargs = get_station_aggregation("total_precipitation", "daily")
    assert kwargs == {
        "daily_aggregation": "sum",
        "day_start_hour": 6,
        "interval_end": True,
    }
    path = _station_parquet(
        tmp_path,
        [("A", 50.0, 10.0)],
        "total_precipitation",
        start="2025-06-02 07:00",
        value=0.5,
    )
    got = interpolate_station_parquet(
        path, 50.0, 10.0, "total_precipitation", pd.Timestamp("2025-06-02"), **kwargs
    )
    assert got == pytest.approx(12.0)


def test_station_aggregation_hourly_mode_selects_nearest():
    """Hourly mode never aggregates station data."""
    assert (
        get_station_aggregation("total_precipitation", "hourly")["daily_aggregation"]
        is None
    )


def test_apply_conversion_period_only_affects_energy_totals():
    """``period_s`` rescales J m-2 totals and leaves other conversions alone."""
    hourly = apply_conversion(
        "ERA5_land", "surface_solar_radiation_downwards", _HOURLY_J, period_s=3600.0
    )
    assert hourly == pytest.approx(w_m2_to_par(100.0))
    assert apply_conversion(
        "ERA5_land", "2m_temperature", 300.0, period_s=3600.0
    ) == pytest.approx(26.85)


def test_apply_conversion_override_with_period():
    """A user override to J_m2 -> PAR still honours ``period_s``."""
    overrides = {"surface_solar_radiation_downwards": {"from": "J_m2", "to": "PAR"}}
    got = apply_conversion(
        "HYRAS",
        "surface_solar_radiation_downwards",
        _HOURLY_J,
        overrides,
        period_s=3600.0,
    )
    assert got == pytest.approx(w_m2_to_par(100.0))


# ---------------------------------------------------------------------------
# GetterWeather wiring
# ---------------------------------------------------------------------------


def _lazy(ds, tmp_path):
    ds.to_zarr(tmp_path / "z.zarr", mode="w")
    return xr.open_zarr(tmp_path / "z.zarr")  # lazy, dask-backed


def _getter_query(store, variable, when, resolution="daily", source="ERA5_land"):
    from datavia.weather.getter_weather import GetterWeather

    getter = GetterWeather(source, temporal_resolution=resolution)
    with (
        patch("datavia.weather.getter_weather.get_weather_paths", return_value=[]),
        patch("datavia.weather.getter_weather._try_open_zarr", return_value=store),
    ):
        return getter.get_data(
            np.array([[10.1, 50.1]]), variable=variable, datetime_utc=when
        )


class TestGetterAccumulated:
    """``GetterWeather`` wiring for accumulated variables."""

    _VAR = "surface_solar_radiation_downwards"

    def _store(self, tmp_path):
        return _lazy(_era5_accumulated().rename({"ssrd": self._VAR}), tmp_path)

    def test_daily_single_time_on_lazy_zarr(self, tmp_path):
        """A noon query returns the full-day PAR from a dask-backed store."""
        got = _getter_query(self._store(tmp_path), self._VAR, "2025-06-02 12:00")
        assert got[0] == pytest.approx(w_m2_to_par(100.0))

    def test_daily_multi_time_on_lazy_zarr(self, tmp_path):
        """Each requested day gets its own total; shape is (N, T)."""
        got = _getter_query(
            self._store(tmp_path), self._VAR, ["2025-06-01 06:00", "2025-06-02 06:00"]
        )
        assert got.shape == (1, 2)
        np.testing.assert_allclose(got, w_m2_to_par(100.0))

    def test_daily_day_without_next_midnight_is_missing(self, tmp_path):
        """A day whose next-00:00 stamp is absent must error, not return a partial."""
        from datavia.weather.getter_weather import MissingWeatherDataError

        with pytest.raises(MissingWeatherDataError, match="next day's first hours"):
            _getter_query(self._store(tmp_path), self._VAR, "2025-06-04 12:00")

    def _mocked(self, variable, resolution):
        from datavia.weather.getter_weather import GetterWeather

        getter = GetterWeather("ERA5_land", temporal_resolution=resolution)
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
                np.array([[10.1, 50.1]]), variable=variable, datetime_utc="2025-06-02"
            )
        return sampler.call_args.kwargs, conv.call_args.kwargs

    def test_accumulated_series_type_and_period_forwarded(self):
        """Hourly mode passes series_type='accumulated' and period_s=3600."""
        sampler_kw, conv_kw = self._mocked(self._VAR, "hourly")
        assert sampler_kw["series_type"] == "accumulated"
        assert conv_kw["period_s"] == 3600.0

    def test_non_accumulated_variable_infers_series_type(self):
        """Temperature lets the sampler infer the series type and gets no period."""
        sampler_kw, conv_kw = self._mocked("2m_temperature", "daily")
        assert sampler_kw["series_type"] is None
        assert conv_kw["period_s"] is None


class TestGetterDailyMean:
    """ERA5 temperature in daily mode is the mean of the date's 24 hours."""

    def test_daily_mean_in_celsius(self, tmp_path):
        times = pd.date_range("2025-06-01", periods=72, freq="1h")
        ds = _grid(273.15 + times.hour, times, "2m_temperature")
        got = _getter_query(_lazy(ds, tmp_path), "2m_temperature", "2025-06-02 15:00")
        assert got[0] == pytest.approx(11.5, abs=1e-4)


class TestTryOpenZarrPadding:
    """Year padding so day totals/increments can cross a year boundary."""

    def _years_opened(self, from_dt, to_dt, pad_day):
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


class TestHourlyThroughGetter:
    """Hourly mode returns the same shapes as daily mode (value at the hour)."""

    _VAR = "surface_solar_radiation_downwards"

    def _query(self, tmp_path, when):
        from datavia.weather.getter_weather import GetterWeather

        inc, ds = _random_accumulated(self._VAR)
        store = _lazy(ds, tmp_path)
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

    @pytest.mark.parametrize(
        "query", ["2025-06-02", "2025-06-02 03:00", "2025-06-02 15:00"]
    )
    def test_same_rain_same_daily_totals(self, tmp_path, query):
        rng = np.random.default_rng(3)
        times = pd.date_range("2025-06-01 01:00", periods=72, freq="1h")
        rain = pd.Series(rng.random(72), index=times)  # mm per hour, interval-end

        # ERA5: accumulated metres since 00 UTC.
        accum = (
            (rain / 1000.0)
            .groupby((times - pd.Timedelta(hours=1)).normalize())
            .cumsum()
        )
        era5 = _grid(accum.values, times, "tp")

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

        # HYRAS: the 06-06 total of June 2, stamped 2025-06-02 06:00.
        hyras_total = rain[
            (times > "2025-06-02 06:00") & (times <= "2025-06-03 06:00")
        ].sum()
        hyras = _grid([hyras_total], pd.DatetimeIndex(["2025-06-02 06:00"]), "pr")

        q = pd.Timestamp(query)
        e = _sample(era5, "tp", q, series_type="accumulated", day_start_hour=6) * 1000.0
        d = interpolate_station_parquet(
            str(path),
            50.1,
            10.1,
            "total_precipitation",
            q,
            **get_station_aggregation("total_precipitation", "daily"),
        )
        h = _sample(hyras, "pr", q)
        assert e == pytest.approx(hyras_total, rel=1e-5)
        assert d == pytest.approx(hyras_total)
        assert h == pytest.approx(hyras_total)
