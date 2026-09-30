"""Unit tests for :class:`~datavia.weather.dwd_downloader.DWDStationDownloader`.

All network access is mocked.
"""

from unittest.mock import MagicMock, patch

import pytest

_STATIONS = [{"id": "S1", "latitude": 52.5, "longitude": 13.4}]


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

    def test_hourly_request_uses_mapped_names(self, tmp_path) -> None:
        from datavia.weather.dwd_downloader import DWDStationDownloader

        response = MagicMock(status_code=200)
        response.json.return_value = {
            "hourly": {
                "time": ["2024-01-01T00:00"],
                "temperature_2m": [1.5],
                "precipitation": [0.2],
            }
        }
        dl = DWDStationDownloader(
            variables=["2m_temperature", "total_precipitation"],
            date_start="2024-01-01",
            date_end="2024-01-01",
            stations=_STATIONS,
        )
        with (
            patch(
                "datavia.weather.dwd_downloader.requests.get", return_value=response
            ) as mock_get,
            patch(
                "datavia.weather.dwd_downloader.tempfile.mkstemp",
                return_value=(0, str(tmp_path / "out.parquet")),
            ),
        ):
            dl.download()

        params = mock_get.call_args.kwargs["params"]
        assert params["hourly"] == "temperature_2m,precipitation"
        assert "daily" not in params
