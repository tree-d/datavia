"""Unit tests for :class:`~datavia.weather.dwd_downloader.DWDStationDownloader`.

All network access is mocked.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests

_STATIONS = [{"id": "S1", "latitude": 52.5, "longitude": 13.4}]
_PAYLOAD = {
    "hourly": {
        "time": ["2024-01-01T00:00"],
        "temperature_2m": [1.5],
        "precipitation": [0.2],
    }
}


def _response(status: int = 200, payload=None, headers=None) -> MagicMock:
    resp = MagicMock(status_code=status, text="error body", headers=headers or {})
    resp.json.return_value = payload if payload is not None else _PAYLOAD
    return resp


@pytest.fixture
def tmp_tempdir(tmp_path, monkeypatch):
    """Write the downloader's temporary parquet into *tmp_path*."""
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path


def _download(responses, **kwargs):
    """Run ``download()`` with ``requests.get`` returning *responses* in turn."""
    from datavia.weather.dwd_downloader import DWDStationDownloader

    kwargs.setdefault("variables", ["2m_temperature", "total_precipitation"])
    kwargs.setdefault("date_start", "2024-01-01")
    kwargs.setdefault("date_end", "2024-01-01")
    kwargs.setdefault("stations", _STATIONS)
    dl = DWDStationDownloader(**kwargs)
    with (
        patch(
            "datavia.weather.dwd_downloader.requests.get", side_effect=responses
        ) as mock_get,
        patch("datavia.weather.dwd_downloader.time.sleep") as mock_sleep,
    ):
        path = dl.download()
    return path, mock_get, mock_sleep


class TestDWDStationDownloaderVariables:
    """Variable validation for the hourly-only Open-Meteo request."""

    @pytest.mark.parametrize("var", ["temperature_2m_max", "temperature_2m_min"])
    def test_daily_only_variables_rejected(self, var: str) -> None:
        from datavia.weather.dwd_downloader import DWDStationDownloader

        with pytest.raises(ValueError, match="hourly-only"):
            DWDStationDownloader(variables=["2m_temperature", var])

    def test_unknown_variable_rejected(self) -> None:
        from datavia.weather.dwd_downloader import DWDStationDownloader

        with pytest.raises(ValueError, match="Unsupported"):
            DWDStationDownloader(variables=["not_a_variable"])

    def test_split_supported_keeps_order(self) -> None:
        from datavia.weather.dwd_downloader import DWDStationDownloader

        supported, unsupported = DWDStationDownloader.split_supported(
            ["temperature_2m_max", "total_precipitation", "10m_wind", "2m_temperature"]
        )
        assert supported == ["total_precipitation", "2m_temperature"]
        assert unsupported == ["temperature_2m_max", "10m_wind"]

    def test_hourly_request_uses_mapped_names(self, tmp_tempdir) -> None:
        path, mock_get, _ = _download([_response()])

        params = mock_get.call_args.kwargs["params"]
        assert params["hourly"] == "temperature_2m,precipitation"
        assert "daily" not in params
        assert path.startswith(str(tmp_tempdir))
        df = pd.read_parquet(path)
        assert list(df["2m_temperature"]) == [1.5]


class TestDWDStationDownloaderDates:
    """Request date range: padded end, clamped to today in UTC."""

    def test_end_date_padded_by_one_day(self, tmp_tempdir) -> None:
        """The day after date_end closes the last interval-end day."""
        _, mock_get, _ = _download([_response()], date_end="2024-01-31")
        params = mock_get.call_args.kwargs["params"]
        assert params["start_date"] == "2024-01-01"
        assert params["end_date"] == "2024-02-01"

    def test_end_date_clamped_to_utc_today(self, tmp_tempdir) -> None:
        """Open-Meteo rejects end dates after the current UTC date."""
        with patch(
            "datavia.weather.dwd_downloader._utc_today", return_value=date(2024, 1, 10)
        ):
            _, mock_get, _ = _download(
                [_response()], date_start="2024-01-05", date_end="2024-01-10"
            )
        assert mock_get.call_args.kwargs["params"]["end_date"] == "2024-01-10"

    def test_default_dates_use_utc_today(self) -> None:
        from datavia.weather.dwd_downloader import DWDStationDownloader

        with patch(
            "datavia.weather.dwd_downloader._utc_today", return_value=date(2024, 3, 3)
        ):
            dl = DWDStationDownloader()
        assert (dl.date_start, dl.date_end) == ("2024-03-03", "2024-03-03")


class TestDWDStationDownloaderErrors:
    """HTTP failures, retries and empty payloads."""

    def test_rate_limit_is_retried(self, tmp_tempdir) -> None:
        _, mock_get, mock_sleep = _download(
            [_response(429, headers={"Retry-After": "2"}), _response()]
        )
        assert mock_get.call_count == 2
        mock_sleep.assert_called_once_with(2.0)

    def test_persistent_server_error_raises_after_retries(self, tmp_tempdir) -> None:
        with pytest.raises(RuntimeError, match="returned 503"):
            _download([_response(503)] * 3)

    def test_client_error_is_not_retried(self, tmp_tempdir) -> None:
        # A retry would succeed on the second response, so raising proves none.
        with pytest.raises(RuntimeError, match="returned 400"):
            _download([_response(400), _response()])

    def test_network_error_becomes_runtime_error(self, tmp_tempdir) -> None:
        with pytest.raises(RuntimeError, match="S1"):
            _download(requests.ConnectionError("unreachable"))

    def test_empty_hourly_payload_raises(self, tmp_tempdir) -> None:
        with pytest.raises(RuntimeError, match="no records"):
            _download([_response(payload={"hourly": {}})])
