"""Unit tests for :class:`~datavia.weather.dwd_downloader.DWDStationDownloader`.

The downloader writes the daily DWD station observations (from the fake CDC
of :mod:`cdc_fake`) into a long-format Parquet file whose time stamps the
daily selection of the station blend can use.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
from cdc_fake import FakeCDC
from datavia.weather.dwd_cdc import CDCClient
from datavia.weather.dwd_downloader import DWDStationDownloader, normalise_station_ids
from datavia.weather.source_registry import get_station_aggregation

from datavia.library.interpolation import interpolate_station_parquet

TODAY = date(2026, 11, 20)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch, tmp_path):
    """Pin "today" and write the temporary parquet into *tmp_path*."""
    import tempfile

    monkeypatch.setattr("datavia.weather.dwd_cdc._utc_today", lambda: TODAY)
    monkeypatch.setattr("datavia.weather.dwd_downloader._utc_today", lambda: TODAY)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


@pytest.fixture
def fake() -> FakeCDC:
    """Two climate stations and one rain gauge, June 1-3 2026."""
    fake = FakeCDC()
    kl = {
        "TMK": [10.0, 11.0, 12.0],
        "TXK": [15.0, 16.0, 17.0],
        "TNK": [5.0, 6.0, 7.0],
        "UPM": [70.0, 71.0, 72.0],
    }
    fake.add("kl", "recent", "00044", date(2026, 6, 1), kl, [(44.0, 52.9, 8.2)])
    fake.add("kl", "recent", "00073", date(2026, 6, 1), kl, [(374.0, 48.6, 13.1)])
    fake.add(
        "more_precip",
        "recent",
        "00006",
        date(2026, 6, 1),
        {"RS": [0.0, 4.2, -999]},
        [(470.0, 48.8, 10.1)],
    )
    return fake


def _downloader(fake: FakeCDC, tmp_path, **kwargs) -> DWDStationDownloader:
    kwargs.setdefault("date_start", "2026-06-01")
    kwargs.setdefault("date_end", "2026-06-03")
    client = CDCClient(tmp_path / "cdc", session=fake.session())
    return DWDStationDownloader(client=client, data_dir=tmp_path, **kwargs)


class TestParquet:
    """Layout and time stamps of the written Parquet."""

    def test_columns_and_rows(self, fake, tmp_path):
        path = _downloader(fake, tmp_path, variables=["2m_temperature"]).download()
        df = pd.read_parquet(path)
        assert list(df.columns) == [
            "station_id",
            "latitude",
            "longitude",
            "elevation",
            "datetime",
            "2m_temperature",
        ]
        assert len(df) == 6  # two stations x three days
        assert set(df["station_id"]) == {"00044", "00073"}

    def test_temperature_stamped_at_day_start(self, fake, tmp_path):
        path = _downloader(fake, tmp_path, variables=["2m_temperature"]).download()
        df = pd.read_parquet(path)
        row = df[(df.station_id == "00044") & (df["2m_temperature"] == 11.0)]
        assert row["datetime"].item() == pd.Timestamp("2026-06-02 00:00")

    def test_precipitation_stamped_at_window_end(self, fake, tmp_path):
        path = _downloader(fake, tmp_path, variables=["total_precipitation"]).download()
        df = pd.read_parquet(path)
        # Day June 2 (06 UTC June 2 to 06 UTC June 3) -> stamped June 3 06:00;
        # the missing June 3 value is not written.
        assert df["datetime"].tolist() == [
            pd.Timestamp("2026-06-02 06:00"),
            pd.Timestamp("2026-06-03 06:00"),
        ]
        assert df["total_precipitation"].tolist() == [0.0, 4.2]

    def test_mixed_variables_keep_their_own_stamps(self, fake, tmp_path):
        dl = _downloader(
            fake, tmp_path, variables=["2m_temperature", "total_precipitation"]
        )
        df = pd.read_parquet(dl.download())
        temperature = df.dropna(subset=["2m_temperature"])
        precipitation = df.dropna(subset=["total_precipitation"])
        assert (temperature["datetime"].dt.hour == 0).all()
        assert (precipitation["datetime"].dt.hour == 6).all()

    @pytest.mark.parametrize(
        ("variable", "day", "expected"),
        [
            ("2m_temperature", "2026-06-02", 11.0),
            ("total_precipitation", "2026-06-02", 4.2),
        ],
    )
    def test_daily_selection_finds_each_day(
        self, fake, tmp_path, variable, day, expected
    ):
        """The registry's daily selection reads exactly the value of the day."""
        path = _downloader(fake, tmp_path, variables=[variable]).download()
        station = {"2m_temperature": (52.9, 8.2), "total_precipitation": (48.8, 10.1)}
        lat, lon = station[variable]
        got = interpolate_station_parquet(
            path,
            lat,
            lon,
            variable,
            pd.Timestamp(day),
            radius_km=1.0,
            **get_station_aggregation(variable, "daily"),
        )
        assert got == pytest.approx(expected)

    def test_station_filter(self, fake, tmp_path):
        dl = _downloader(fake, tmp_path, variables=["2m_temperature"], stations=["73"])
        df = pd.read_parquet(dl.download())
        assert set(df["station_id"]) == {"00073"}


class TestSettings:
    """Variables, dates and the station setting."""

    def test_unsupported_variable_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Unsupported DWD station variables"):
            DWDStationDownloader(
                variables=["surface_solar_radiation_downwards"], data_dir=tmp_path
            )

    def test_split_supported_keeps_order(self):
        supported, unsupported = DWDStationDownloader.split_supported(
            [
                "total_precipitation",
                "surface_solar_radiation_downwards",
                "temperature_2m_max",
            ]
        )
        assert supported == ["total_precipitation", "temperature_2m_max"]
        assert unsupported == ["surface_solar_radiation_downwards"]

    def test_end_clamped_to_yesterday(self, tmp_path):
        dl = DWDStationDownloader(
            date_start="2026-11-01", date_end="2026-12-31", data_dir=tmp_path
        )
        assert dl.date_end == date(2026, 11, 19)

    def test_default_dates_are_yesterday(self, tmp_path):
        dl = DWDStationDownloader(data_dir=tmp_path)
        assert dl.date_start == dl.date_end == date(2026, 11, 19)

    @pytest.mark.parametrize(
        ("stations", "expected"),
        [(None, None), (True, None), ([], None), (["44", 73], ["00044", "00073"])],
    )
    def test_normalise_station_ids(self, stations, expected):
        assert normalise_station_ids(stations) == expected

    @pytest.mark.parametrize(
        "stations", [[{"id": "S1", "latitude": 52.5, "longitude": 13.4}], False, "44"]
    )
    def test_invalid_station_setting_raises(self, stations):
        with pytest.raises(ValueError, match="dwd_stations must be"):
            normalise_station_ids(stations)

    def test_no_published_day_raises(self, fake, tmp_path):
        dl = _downloader(fake, tmp_path, date_start="2026-11-20", date_end="2026-11-25")
        with pytest.raises(RuntimeError, match="No published DWD station day"):
            dl.download()

    def test_values_are_floats(self, fake, tmp_path):
        df = pd.read_parquet(_downloader(fake, tmp_path).download())
        assert np.issubdtype(df["2m_temperature"].dtype, np.floating)
