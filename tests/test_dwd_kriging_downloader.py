"""Tests of :class:`~datavia.weather.dwd_kriging_downloader.DWDKrigingDownloader`.

Covers the year file (layout, stamps, mask, values, provenance), the
variogram cache, reuse of stored months, rejected settings, and the kriged
vapour pressure deficit.  Station data come from the fake CDC of
:mod:`cdc_fake`; the 1 km grid is
shrunk to a 21 x 16 km patch near Leipzig with a synthetic terrain, so the
real Mojo kriging runs in well under a second.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from cdc_fake import FakeCDC

pytest.importorskip("datavia.kriging")
xr = pytest.importorskip("xarray")

from datavia.weather import dwd_kriging_downloader as dkd  # noqa: E402
from datavia.weather.dwd_cdc import CDCClient  # noqa: E402

TODAY = date(2026, 2, 12)  # yesterday = Feb 11; the fake data end Feb 10
GRID_X = np.arange(4500500.0, 4520500.0 + 1, 1000.0)
GRID_Y = np.arange(3120500.0, 3135500.0 + 1, 1000.0)
FIRST, LAST = date(2026, 1, 1), date(2026, 2, 10)


@pytest.fixture(autouse=True)
def small_grid(monkeypatch, tmp_path):
    """Shrink the grid, pin "today", and write temporary files to tmp_path."""
    import tempfile

    monkeypatch.setattr(dkd, "GRID_X", GRID_X)
    monkeypatch.setattr(dkd, "GRID_Y", GRID_Y)
    monkeypatch.setattr("datavia.weather.dwd_cdc._utc_today", lambda: TODAY)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def _elevation() -> np.ndarray:
    """Terrain rising to the east; the westernmost column is outside "Germany"."""
    elevation = np.tile(100.0 + 10.0 * np.arange(len(GRID_X)), (len(GRID_Y), 1))
    elevation[:, 0] = np.nan
    return elevation


def _fake_cdc() -> FakeCDC:
    """30 climate stations around the patch: temperature = 5 - 0.0065 * h + noise.

    The vapour pressure is that of 70 % relative humidity at the mean
    temperature.
    """
    from datavia.library.coordinate_transforms import get_transformer

    rng = np.random.default_rng(3)
    x = rng.uniform(GRID_X[0] - 3e4, GRID_X[-1] + 3e4, 30)
    y = rng.uniform(GRID_Y[0] - 3e4, GRID_Y[-1] + 3e4, 30)
    lon, lat = get_transformer("EPSG:3035", "EPSG:4326").transform(x, y)
    h = rng.uniform(80.0, 400.0, 30)
    n_days = (LAST - FIRST).days + 1
    daily = np.sin(np.arange(n_days) / 5.0) * 3.0
    fake = FakeCDC()
    for i in range(30):
        tmk = 5.0 - 0.0065 * h[i] + daily + rng.normal(0, 0.3, n_days)
        fake.add(
            "kl",
            "recent",
            f"{i + 1:05d}",
            FIRST,
            {
                "TMK": np.round(tmk, 1),
                "TXK": np.round(tmk + 5, 1),
                "TNK": np.round(tmk - 5, 1),
                "UPM": [80.0] * n_days,
                # 70 % of the saturation vapour pressure (FAO-56 eq. 11) in hPa.
                "VPM": np.round(7.0 * 0.6108 * np.exp(17.27 * tmk / (tmk + 237.3)), 1),
            },
            [(round(h[i], 1), round(lat[i], 4), round(lon[i], 4))],
        )
    return fake


def _downloader(tmp_path, fake=None, **kwargs) -> dkd.DWDKrigingDownloader:
    fake = fake or _fake_cdc()
    client = CDCClient(tmp_path / "cdc", session=fake.session())
    kwargs.setdefault("variables", ["2m_temperature"])
    return dkd.DWDKrigingDownloader(
        date_start="2026-01-15",
        date_end="2026-02-10",
        data_dir=tmp_path,
        client=client,
        **kwargs,
    )


class TestNetCDF:
    """The year file: layout, time stamps, mask and values."""

    @pytest.fixture
    def ds(self, tmp_path):
        path = _downloader(tmp_path).krige_year("2m_temperature", 2026, _elevation())
        with xr.open_dataset(path) as ds:
            yield ds.load()

    def test_layout_like_hyras(self, ds):
        assert ds["tas"].dims == ("time", "y", "x")
        assert ds["tas"].attrs["grid_mapping"] == "crs"
        assert ds["tas"].attrs["units"] == "degree_Celsius"
        np.testing.assert_array_equal(ds["x"], GRID_X)
        np.testing.assert_array_equal(ds["y"], GRID_Y)
        assert "lambert_azimuthal_equal_area" in ds["crs"].attrs["grid_mapping_name"]

    def test_whole_year_up_to_last_published_day(self, ds):
        times = pd.DatetimeIndex(ds["time"].values)
        assert times[0] == pd.Timestamp("2026-01-01 00:00")
        assert times[-1] == pd.Timestamp("2026-02-10 00:00")
        assert len(times) == (LAST - FIRST).days + 1

    def test_cells_without_elevation_are_nan(self, ds):
        assert np.isnan(ds["tas"].values[:, :, 0]).all()
        assert np.isfinite(ds["tas"].values[:, :, 1:]).all()

    def test_values_follow_stations_and_drift(self, ds):
        tas = ds["tas"].values
        # Plausible values, and colder towards the higher (eastern) cells.
        assert np.nanmin(tas) > -10.0 and np.nanmax(tas) < 15.0
        west, east = np.nanmean(tas[:, :, 1]), np.nanmean(tas[:, :, -1])
        assert east < west

    def test_provenance(self, ds):
        variograms = json.loads(ds.attrs["variograms"])
        assert set(variograms) == {"2026-01", "2026-02"}
        assert json.loads(ds.attrs["kriging_settings"])["max_dist"] == 1.5e5


class TestVapourPressureDeficit:
    """The kriged vapour pressure deficit: a HYRAS-like ``vpd`` file in kPa."""

    @pytest.fixture
    def ds(self, tmp_path: Path) -> Iterator[xr.Dataset]:
        """The kriged vapour pressure deficit of the fake stations."""
        path = _downloader(tmp_path, variables=["vapour_pressure_deficit"]).krige_year(
            "vapour_pressure_deficit", 2026, _elevation()
        )
        with xr.open_dataset(path) as ds:
            yield ds.load()

    def test_variable_and_attributes(self, ds: xr.Dataset) -> None:
        assert ds["vpd"].dims == ("time", "y", "x")
        assert ds["vpd"].attrs["units"] == "kPa"
        assert (
            ds["vpd"].attrs["standard_name"] == "water_vapor_saturation_deficit_in_air"
        )
        assert ds["vpd"].attrs["grid_mapping"] == "crs"

    def test_values_are_plausible_and_non_negative(self, ds: xr.Dataset) -> None:
        land = ds["vpd"].values[:, :, 1:]
        assert np.isfinite(land).all()
        # TXK/TNK = TMK +- 5 degC, 70 % humidity, winter: a few tenths of a kPa.
        assert land.min() >= 0.0 and land.max() < 1.5

    def test_provenance_names_the_input_columns(self, ds: xr.Dataset) -> None:
        assert "kl/TXK+TNK+VPM" in ds.attrs["source"]
        assert json.loads(ds.attrs["kriging_settings"])["correct_negative"] is True


class TestVariogramCache:
    """One variogram per month, cached and reused."""

    def test_monthly_variograms_are_cached(self, tmp_path):
        _downloader(tmp_path).krige_year("2m_temperature", 2026, _elevation())
        files = sorted(
            (tmp_path / "DWD_stations" / "_variograms" / "2m_temperature").glob(
                "*.json"
            )
        )
        meta = [json.loads(f.read_text())["meta"] for f in files]
        assert [m["month"] for m in meta] == ["2026-01", "2026-02"]
        assert [m["complete"] for m in meta] == [True, False]
        assert meta[1]["n_days"] == 10

    def test_second_run_reuses_the_variograms(self, tmp_path, monkeypatch):
        dl = _downloader(tmp_path)
        dl.krige_year("2m_temperature", 2026, _elevation())
        fits = []
        original = dl.variogram_cache.get_or_fit

        def spy(*args, **kwargs):
            result = original(*args, **kwargs)
            fits.append(result.from_cache)
            return result

        monkeypatch.setattr(dl.variogram_cache, "get_or_fit", spy)
        dl.krige_year("2m_temperature", 2026, _elevation())
        assert fits == [True, True]


class TestStore:
    """Ingestion into the Zarr store and reuse of complete months."""

    def _ingest(self, tmp_path, path):
        from datavia.weather.saver_weather import SaverWeather

        saver = SaverWeather("DWD_stations")
        saver.data_dir = str(tmp_path)
        assert saver.save(str(path))
        return tmp_path / "DWD_stations" / "2m_temperature" / "2026.zarr"

    def test_ingests_like_hyras(self, tmp_path):
        path = _downloader(tmp_path).krige_year("2m_temperature", 2026, _elevation())
        store = self._ingest(tmp_path, path)
        with xr.open_zarr(store, consolidated=False) as ds:
            assert "2m_temperature" in ds
            assert ds.sizes == {"time": 41, "y": len(GRID_Y), "x": len(GRID_X)}
            # Gaps (the column outside "Germany") are filled at write time.
            assert np.isfinite(ds["2m_temperature"].values).all()

    def test_complete_months_are_copied_from_the_store(self, tmp_path, monkeypatch):
        dl = _downloader(tmp_path)
        store = self._ingest(
            tmp_path, dl.krige_year("2m_temperature", 2026, _elevation())
        )
        with xr.open_zarr(store, consolidated=False) as ds:
            january = ds["2m_temperature"].sel(time="2026-01").values

        kriged = []
        original = dl._krige_month
        monkeypatch.setattr(
            dl,
            "_krige_month",
            lambda variable, month, *a: (
                kriged.append(str(month)) or original(variable, month, *a)
            ),
        )
        path = dl.krige_year("2m_temperature", 2026, _elevation())
        assert kriged == ["2026-02"]  # January is complete in the store
        with xr.open_dataset(path) as ds:
            np.testing.assert_array_equal(ds["tas"].sel(time="2026-01").values, january)


class TestErrors:
    """Settings that are rejected."""

    def test_hourly_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="daily values only"):
            _downloader(tmp_path, temporal_resolution="hourly")

    def test_unknown_variable_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="not available from DWD_stations"):
            _downloader(tmp_path, variables=["surface_solar_radiation_downwards"])

    def test_future_year_gives_no_file(self, tmp_path):
        dl = _downloader(tmp_path)
        assert dl.krige_year("2m_temperature", 2027, _elevation()) is None

    def test_download_returns_one_file_per_variable_and_year(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(dkd, "grid_elevation", lambda cache_dir: _elevation())
        dl = _downloader(tmp_path, variables=["2m_temperature", "temperature_2m_max"])
        paths = dl.download().splitlines()
        assert len(paths) == 2
        with xr.open_dataset(paths[1]) as ds:
            assert "tasmax" in ds
