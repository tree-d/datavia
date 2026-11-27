"""Tests of :mod:`datavia.weather.dwd_cdc`: download, cache and read of DWD
Climate Data Center daily station archives, including derived products (the
vapour pressure deficit from ``TXK``, ``TNK`` and ``VPM``).

All network access goes to the in-memory :class:`cdc_fake.FakeCDC`.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from cdc_fake import FakeCDC
from datavia.weather.dwd_cdc import CDCClient

TODAY = date(2026, 11, 20)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin "today" so the choice of archive branches is deterministic."""
    monkeypatch.setattr("datavia.weather.dwd_cdc._utc_today", lambda: TODAY)
    monkeypatch.setattr("datavia.weather.dwd_cdc.time.sleep", lambda s: None)


def _client(fake: FakeCDC, tmp_path: Path, **kwargs: Any) -> CDCClient:
    return CDCClient(tmp_path / "cdc", session=fake.session(), **kwargs)


def _tmk(values: list[float]) -> dict[str, Iterable[float]]:
    """kl product columns with the given TMK values (others plausible)."""
    n = len(values)
    return {
        "QN_4": [10] * n,
        "TMK": values,
        "TXK": [20.0] * n,
        "TNK": [0.0] * n,
        "UPM": [80.0] * n,
    }


class TestReading:
    """Values, missing values and positions."""

    def test_values_missing_and_implausible(self, tmp_path: Path) -> None:
        fake = FakeCDC()
        fake.add(
            "kl", "recent", "00044", date(2026, 1, 1), _tmk([1.5, -999, 99.0, -2.0])
        )
        data = _client(fake, tmp_path).station_data(
            "2m_temperature", date(2026, 1, 1), date(2026, 1, 5)
        )
        assert list(data.station_id) == ["00044"]
        # -999 is missing, 99 degC is outside the plausible range, Jan 5 has no row.
        np.testing.assert_array_equal(
            data.values[0], [1.5, np.nan, np.nan, -2.0, np.nan]
        )
        assert len(data.dates) == 5

    def test_position_is_the_last_geography_row(self, tmp_path: Path) -> None:
        fake = FakeCDC()
        fake.add(
            "kl",
            "recent",
            "00044",
            date(2026, 1, 1),
            _tmk([1.0]),
            positions=[(10.0, 50.0, 8.0), (44.0, 52.9336, 8.237)],
        )
        data = _client(fake, tmp_path).station_data(
            "2m_temperature", date(2026, 1, 1), date(2026, 1, 1)
        )
        assert (data.longitude[0], data.latitude[0], data.elevation[0]) == (
            8.237,
            52.9336,
            44.0,
        )

    def test_other_columns(self, tmp_path: Path) -> None:
        fake = FakeCDC()
        fake.add("kl", "recent", "00044", date(2026, 1, 1), _tmk([1.0]))
        client = _client(fake, tmp_path)
        day = date(2026, 1, 1)
        assert client.station_data("temperature_2m_max", day, day).values[0, 0] == 20.0
        assert client.station_data("temperature_2m_min", day, day).values[0, 0] == 0.0
        assert (
            client.station_data("relative_humidity_2m", day, day).values[0, 0] == 80.0
        )

    def test_precipitation_comes_from_more_precip(self, tmp_path: Path) -> None:
        fake = FakeCDC()
        fake.add(
            "more_precip",
            "recent",
            "00006",
            date(2026, 6, 1),
            {"QN_6": [3, 3], "RS": [0.0, 12.4]},
        )
        data = _client(fake, tmp_path).station_data(
            "total_precipitation", date(2026, 6, 1), date(2026, 6, 2)
        )
        np.testing.assert_array_equal(data.values[0], [0.0, 12.4])

    def test_stations_without_data_in_range_are_dropped(self, tmp_path: Path) -> None:
        fake = FakeCDC()
        fake.add("kl", "recent", "00044", date(2026, 1, 1), _tmk([1.0]))
        fake.add(
            "kl",
            "recent",
            "00073",
            date(2026, 1, 1),
            _tmk([-999]),
            positions=[(5.0, 48.0, 13.0)],
        )
        data = _client(fake, tmp_path).station_data(
            "2m_temperature", date(2026, 1, 1), date(2026, 1, 1)
        )
        assert list(data.station_id) == ["00044"]

    def test_duplicate_positions_keep_the_station_with_more_data(
        self, tmp_path: Path
    ) -> None:
        fake = FakeCDC()
        same = [(100.0, 51.0, 12.0)]
        fake.add("kl", "recent", "00001", date(2026, 1, 1), _tmk([1.0, -999]), same)
        fake.add("kl", "recent", "00002", date(2026, 1, 1), _tmk([2.0, 3.0]), same)
        data = _client(fake, tmp_path).station_data(
            "2m_temperature", date(2026, 1, 1), date(2026, 1, 2)
        )
        assert list(data.station_id) == ["00002"]

    def test_corrupt_station_is_skipped(self, tmp_path: Path) -> None:
        fake = FakeCDC()
        fake.add("more_precip", "recent", "05127", date(2026, 6, 1), {"RS": [1.0]})
        fake.add(
            "more_precip",
            "recent",
            "00006",
            date(2026, 6, 1),
            {"RS": [2.0]},
            positions=[(5.0, 48.0, 13.0)],
        )
        client = _client(fake, tmp_path)
        data = client.station_data(
            "total_precipitation", date(2026, 6, 1), date(2026, 6, 1)
        )
        assert list(data.station_id) == ["00006"]
        assert not any("05127" in u for u in fake.archive_requests())

    def test_station_filter_downloads_only_those_archives(self, tmp_path: Path) -> None:
        fake = FakeCDC()
        fake.add("kl", "recent", "00044", date(2026, 1, 1), _tmk([1.0]))
        fake.add(
            "kl",
            "recent",
            "00073",
            date(2026, 1, 1),
            _tmk([2.0]),
            positions=[(5.0, 48.0, 13.0)],
        )
        data = _client(fake, tmp_path).station_data(
            "2m_temperature", date(2026, 1, 1), date(2026, 1, 1), station_ids=["00073"]
        )
        assert list(data.station_id) == ["00073"]
        assert len(fake.archive_requests()) == 1

    def test_unknown_variable_and_reversed_range_raise(self, tmp_path: Path) -> None:
        client = _client(FakeCDC(), tmp_path)
        with pytest.raises(ValueError, match="not available"):
            client.station_data("surface_solar_radiation_downwards", TODAY, TODAY)
        with pytest.raises(ValueError, match="must not be before"):
            client.station_data("2m_temperature", TODAY, TODAY - timedelta(days=1))


def _kl_vapour(
    txk: list[float], tnk: list[float], vpm: list[float]
) -> dict[str, Iterable[float]]:
    """kl product columns with the given TXK, TNK and VPM values."""
    n = len(vpm)
    return {
        "QN_4": [10] * n,
        "TMK": [10.0] * n,
        "TXK": txk,
        "TNK": tnk,
        "UPM": [80.0] * n,
        "VPM": vpm,
    }


#: FAO-56 eq. 11 at 20 °C [kPa].
_E_SAT_20C: float = 2.3383

#: FAO-56 eq. 11 at 0 °C [kPa].
_E_SAT_0C: float = 0.6108


class TestVapourPressureDeficit:
    """The derived product: FAO-56 es from TXK/TNK minus the measured VPM."""

    def test_deficit_from_extremes_and_vapour_pressure(self, tmp_path: Path) -> None:
        fake = FakeCDC()
        fake.add(
            "kl", "recent", "00044", date(2026, 7, 1), _kl_vapour([20.0], [0.0], [10.0])
        )
        data = _client(fake, tmp_path).station_data(
            "vapour_pressure_deficit", date(2026, 7, 1), date(2026, 7, 1)
        )
        # es = mean of e(20 degC) and e(0 degC); VPM 10 hPa = 1 kPa.
        expected = (_E_SAT_20C + _E_SAT_0C) / 2 - 1.0
        assert data.values[0, 0] == pytest.approx(expected, abs=1e-3)

    def test_a_missing_or_implausible_input_makes_the_day_missing(
        self, tmp_path: Path
    ) -> None:
        fake = FakeCDC()
        fake.add(
            "kl",
            "recent",
            "00044",
            date(2026, 7, 1),
            _kl_vapour(
                [20.0, -999, 20.0, 20.0], [0.0, 0.0, 0.0, 0.0], [10.0] * 3 + [99.0]
            ),
        )
        data = _client(fake, tmp_path).station_data(
            "vapour_pressure_deficit", date(2026, 7, 1), date(2026, 7, 4)
        )
        # Day 2: TXK missing; day 4: VPM above its 60 hPa plausible maximum.
        assert np.isfinite(data.values[0, [0, 2]]).all()
        assert np.isnan(data.values[0, [1, 3]]).all()

    def test_implausible_result_is_missing(self, tmp_path: Path) -> None:
        fake = FakeCDC()
        # Plausible inputs, but es - ea is about 11 kPa, above the 10 kPa limit.
        fake.add(
            "kl", "recent", "00044", date(2026, 7, 1), _kl_vapour([50.0], [45.0], [0.0])
        )
        data = _client(fake, tmp_path).station_data(
            "vapour_pressure_deficit", date(2026, 7, 1), date(2026, 7, 1)
        )
        assert data.n_stations == 0

    def test_vapour_pressure_is_read_in_kilopascal(self, tmp_path: Path) -> None:
        fake = FakeCDC()
        fake.add(
            "kl",
            "recent",
            "00044",
            date(2026, 7, 1),
            _kl_vapour([20.0] * 3, [0.0] * 3, [12.5, -999, 70.0]),
        )
        data = _client(fake, tmp_path).station_data(
            "vapour_pressure", date(2026, 7, 1), date(2026, 7, 3)
        )
        # 12.5 hPa = 1.25 kPa; -999 is missing; 70 hPa is above the 60 hPa limit.
        np.testing.assert_array_equal(data.values[0], [1.25, np.nan, np.nan])

    def test_archive_without_vapour_pressure_column_is_skipped(
        self, tmp_path: Path
    ) -> None:
        fake = FakeCDC()
        fake.add("kl", "recent", "00044", date(2026, 7, 1), _tmk([10.0]))
        client = _client(fake, tmp_path)
        day = date(2026, 7, 1)
        assert client.station_data("vapour_pressure_deficit", day, day).n_stations == 0
        assert client.station_data("2m_temperature", day, day).n_stations == 1


class TestBranches:
    """Historical and recent archives."""

    def test_historical_wins_and_recent_fills_the_rest(self, tmp_path: Path) -> None:
        fake = FakeCDC()
        # Historical: Dec 30 - Dec 31 2025; recent: Dec 31 2025 - Jan 2 2026.
        fake.add(
            "kl",
            "historical",
            "00044",
            date(2025, 12, 30),
            _tmk([1.0, 2.0]),
            positions=[(40.0, 52.0, 8.0)],
        )
        fake.add(
            "kl",
            "recent",
            "00044",
            date(2025, 12, 31),
            _tmk([20.0, 3.0, 4.0]),
            positions=[(44.0, 52.9, 8.2)],
        )
        data = _client(fake, tmp_path).station_data(
            "2m_temperature", date(2025, 12, 30), date(2026, 1, 2)
        )
        np.testing.assert_array_equal(data.values[0], [1.0, 2.0, 3.0, 4.0])
        assert data.elevation[0] == 44.0  # position from the (newer) recent archive

    def test_current_year_reads_no_historical_branch(self, tmp_path: Path) -> None:
        fake = FakeCDC()
        fake.add("kl", "recent", "00044", date(2026, 3, 1), _tmk([1.0]))
        _client(fake, tmp_path).station_data(
            "2m_temperature", date(2026, 3, 1), date(2026, 3, 1)
        )
        assert not any("/historical/" in u for u in fake.requests)

    def test_old_years_read_no_recent_branch(self, tmp_path: Path) -> None:
        fake = FakeCDC()
        fake.add("kl", "historical", "00044", date(2010, 1, 1), _tmk([1.0]))
        data = _client(fake, tmp_path).station_data(
            "2m_temperature", date(2010, 1, 1), date(2010, 1, 1)
        )
        assert data.values[0, 0] == 1.0
        assert not any("/recent/" in u for u in fake.requests)

    def test_only_overlapping_historical_archives_are_downloaded(
        self, tmp_path: Path
    ) -> None:
        fake = FakeCDC()
        fake.add("kl", "historical", "00044", date(2010, 1, 1), _tmk([1.0]))
        fake.add(
            "kl",
            "historical",
            "00073",
            date(1990, 1, 1),
            _tmk([1.0]),
            positions=[(5.0, 48.0, 13.0)],
        )
        _client(fake, tmp_path).station_data(
            "2m_temperature", date(2010, 1, 1), date(2010, 1, 1)
        )
        assert [u.rsplit("/", 1)[1] for u in fake.archive_requests()] == [
            "tageswerte_KL_00044_20100101_20100101_hist.zip"
        ]


class TestCache:
    """Archives are cached; recent ones expire, replaced historical ones go."""

    def test_second_read_uses_the_cache(self, tmp_path: Path) -> None:
        fake = FakeCDC()
        fake.add("kl", "recent", "00044", date(2026, 1, 1), _tmk([1.0]))
        client = _client(fake, tmp_path)
        client.station_data("2m_temperature", date(2026, 1, 1), date(2026, 1, 1))
        client.station_data("2m_temperature", date(2026, 1, 1), date(2026, 1, 1))
        assert len(fake.archive_requests()) == 1

    def test_stale_recent_archive_is_downloaded_again(self, tmp_path: Path) -> None:
        fake = FakeCDC()
        name = fake.add("kl", "recent", "00044", date(2026, 1, 1), _tmk([1.0]))
        client = _client(fake, tmp_path, recent_max_age=timedelta(hours=1))
        client.station_data("2m_temperature", date(2026, 1, 1), date(2026, 1, 1))
        old = time.time() - 2 * 3600
        os.utime(tmp_path / "cdc" / "kl" / "recent" / name, (old, old))
        fake.add("kl", "recent", "00044", date(2026, 1, 1), _tmk([5.0]))
        data = client.station_data("2m_temperature", date(2026, 1, 1), date(2026, 1, 1))
        assert len(fake.archive_requests()) == 2
        assert data.values[0, 0] == 5.0

    def test_replaced_historical_archive_is_removed(self, tmp_path: Path) -> None:
        fake = FakeCDC()
        old = fake.add("kl", "historical", "00044", date(2010, 1, 1), _tmk([1.0]))
        client = _client(fake, tmp_path)
        client.station_data("2m_temperature", date(2010, 1, 1), date(2010, 1, 1))
        # The DWD publishes a longer file for the station; the old one disappears.
        fake.files[("kl", "historical")].clear()
        new = fake.add("kl", "historical", "00044", date(2010, 1, 1), _tmk([1.0, 2.0]))
        client.station_data("2m_temperature", date(2010, 1, 1), date(2010, 1, 2))
        cached = {p.name for p in (tmp_path / "cdc" / "kl" / "historical").iterdir()}
        assert cached == {new}
        assert old != new


class TestHTTP:
    """Retries and errors."""

    def test_server_error_is_retried(self, tmp_path: Path) -> None:
        fake = FakeCDC()
        name = fake.add("kl", "recent", "00044", date(2026, 1, 1), _tmk([1.0]))
        url = f"https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/daily/kl/recent/{name}"
        fake.errors[url] = [503, 429]
        data = _client(fake, tmp_path).station_data(
            "2m_temperature", date(2026, 1, 1), date(2026, 1, 1)
        )
        assert data.values[0, 0] == 1.0
        assert fake.requests.count(url) == 3

    def test_client_error_raises(self, tmp_path: Path) -> None:
        fake = FakeCDC()
        name = fake.add("kl", "recent", "00044", date(2026, 1, 1), _tmk([1.0]))
        url = f"https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/daily/kl/recent/{name}"
        fake.errors[url] = [404]
        with pytest.raises(RuntimeError, match="HTTP 404"):
            _client(fake, tmp_path).station_data(
                "2m_temperature", date(2026, 1, 1), date(2026, 1, 1)
            )
        assert not (tmp_path / "cdc" / "kl" / "recent" / name).exists()

    def test_empty_listing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="No station archives"):
            _client(FakeCDC(), tmp_path).station_data(
                "2m_temperature", date(2026, 1, 1), date(2026, 1, 1)
            )
