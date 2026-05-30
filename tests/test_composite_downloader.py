"""Unit tests for
:class:`~datavia.weather.composite_downloader.CompositeWeatherDownloader`.

Covers downloaders property, download() delegation, and chunk_by forwarding.
"""

from typing import ClassVar
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------


class TestCompositeWeatherDownloader:
    """Tests for the composite downloader structure and delegation."""

    _BASE_CFG: ClassVar[dict] = {
        "variables": ["2m_temperature"],
        "date_start": "2024-01-01",
        "date_end": "2024-01-31",
    }

    def test_era5_only_mode(self) -> None:
        """ERA5_land source without dwd_stations yields one ERA5 downloader."""
        from datavia.weather.composite_downloader import CompositeWeatherDownloader
        from datavia.weather.era5_downloader import ERA5Downloader

        composite = CompositeWeatherDownloader(
            config={"source": "ERA5_land", **self._BASE_CFG}
        )
        assert len(composite.downloaders) == 1
        assert isinstance(composite.downloaders[0], ERA5Downloader)
        assert composite._dwd is None

    def test_era5_with_dwd_mode(self) -> None:
        """ERA5_land source with dwd_stations yields ERA5 + DWD downloaders."""
        from datavia.weather.composite_downloader import CompositeWeatherDownloader
        from datavia.weather.dwd_downloader import DWDStationDownloader
        from datavia.weather.era5_downloader import ERA5Downloader

        composite = CompositeWeatherDownloader(
            config={"source": "ERA5_land", "dwd_stations": [], **self._BASE_CFG}
        )
        assert len(composite.downloaders) == 2
        assert isinstance(composite.downloaders[0], ERA5Downloader)
        assert isinstance(composite.downloaders[1], DWDStationDownloader)

    def test_hyras_only_mode(self) -> None:
        """HYRAS source without dwd_stations yields one HYRAS downloader."""
        from datavia.weather.composite_downloader import CompositeWeatherDownloader
        from datavia.weather.hyras_downloader import HYRASDownloader

        composite = CompositeWeatherDownloader(
            config={"source": "HYRAS", **self._BASE_CFG}
        )
        assert len(composite.downloaders) == 1
        assert isinstance(composite.downloaders[0], HYRASDownloader)
        assert composite._dwd is None

    def test_hyras_with_dwd_mode(self) -> None:
        """HYRAS source with dwd_stations yields HYRAS + DWD downloaders."""
        from datavia.weather.composite_downloader import CompositeWeatherDownloader
        from datavia.weather.dwd_downloader import DWDStationDownloader
        from datavia.weather.hyras_downloader import HYRASDownloader

        composite = CompositeWeatherDownloader(
            config={"source": "HYRAS", "dwd_stations": [], **self._BASE_CFG}
        )
        assert len(composite.downloaders) == 2
        assert isinstance(composite.downloaders[0], HYRASDownloader)
        assert isinstance(composite.downloaders[1], DWDStationDownloader)

    def test_dwd_only_mode(self) -> None:
        """DWD_stations source yields one DWD downloader and no grid downloader."""
        from datavia.weather.composite_downloader import CompositeWeatherDownloader
        from datavia.weather.dwd_downloader import DWDStationDownloader

        composite = CompositeWeatherDownloader(
            config={"source": "DWD_stations", **self._BASE_CFG}
        )
        assert len(composite.downloaders) == 1
        assert isinstance(composite.downloaders[0], DWDStationDownloader)
        assert composite._grid is None

    def test_download_combines_paths(self) -> None:
        """download() joins grid and DWD paths with a newline."""
        from datavia.weather.composite_downloader import CompositeWeatherDownloader

        composite = CompositeWeatherDownloader(
            config={"source": "ERA5_land", "dwd_stations": [], **self._BASE_CFG}
        )
        composite._grid.download = MagicMock(return_value="/tmp/era5.nc")
        composite._dwd.download = MagicMock(return_value="/tmp/dwd.parquet")

        result = composite.download()

        assert result == "/tmp/era5.nc\n/tmp/dwd.parquet"

    def test_download_grid_error_falls_back_to_dwd(self) -> None:
        """RuntimeError from grid download is caught; DWD path is still returned."""
        from datavia.weather.composite_downloader import CompositeWeatherDownloader

        composite = CompositeWeatherDownloader(
            config={"source": "ERA5_land", "dwd_stations": [], **self._BASE_CFG}
        )
        composite._grid.download = MagicMock(
            side_effect=RuntimeError("CDS unavailable")
        )
        composite._dwd.download = MagicMock(return_value="/tmp/dwd.parquet")

        result = composite.download()

        assert result == "/tmp/dwd.parquet"
        composite._dwd.download.assert_called_once()

    def test_download_grid_import_error_falls_back_to_dwd(self) -> None:
        """ImportError (missing cdsapi) from grid is caught; DWD path returned."""
        from datavia.weather.composite_downloader import CompositeWeatherDownloader

        composite = CompositeWeatherDownloader(
            config={"source": "ERA5_land", "dwd_stations": [], **self._BASE_CFG}
        )
        composite._grid.download = MagicMock(
            side_effect=ImportError("No module named 'cdsapi'")
        )
        composite._dwd.download = MagicMock(return_value="/tmp/dwd.parquet")

        result = composite.download()

        assert result == "/tmp/dwd.parquet"
        composite._dwd.download.assert_called_once()


# ---------------------------------------------------------------------------
# WeatherPipeline

# ---------------------------------------------------------------------------


class TestCompositeDownloaderChunkByForwarding:
    """Tests that ``chunk_by`` is forwarded from config to the grid downloader."""

    def test_chunk_by_forwarded_to_grid_downloader(self) -> None:
        """CompositeWeatherDownloader forwards chunk_by to the ERA5 grid downloader."""
        from datavia.weather.composite_downloader import CompositeWeatherDownloader

        config = {
            "source": "ERA5_land",
            "variables": ["2m_temperature"],
            "date_start": "2024-01-01",
            "date_end": "2024-01-31",
            "chunk_by": "quarterly",
        }

        with patch(
            "datavia.weather.composite_downloader.get_grid_downloader_class"
        ) as mock_registry:
            mock_grid_class = MagicMock()
            mock_grid_class.return_value = MagicMock()
            mock_registry.return_value = mock_grid_class

            CompositeWeatherDownloader(config=config)

        call_kwargs = mock_grid_class.call_args[1]
        assert call_kwargs.get("chunk_by") == "quarterly", (
            f"Expected chunk_by='quarterly' forwarded to grid downloader, "
            f"got {call_kwargs}"
        )

    def test_chunk_by_absent_not_forwarded(self) -> None:
        """When chunk_by is not in config, it is not passed to the grid downloader."""
        from datavia.weather.composite_downloader import CompositeWeatherDownloader

        config = {
            "source": "ERA5_land",
            "variables": ["2m_temperature"],
            "date_start": "2024-01-01",
            "date_end": "2024-01-31",
        }

        with patch(
            "datavia.weather.composite_downloader.get_grid_downloader_class"
        ) as mock_registry:
            mock_grid_class = MagicMock()
            mock_grid_class.return_value = MagicMock()
            mock_registry.return_value = mock_grid_class

            CompositeWeatherDownloader(config=config)

        call_kwargs = mock_grid_class.call_args[1]
        assert "chunk_by" not in call_kwargs, (
            f"chunk_by must not be forwarded when absent from config, got {call_kwargs}"
        )


# ---------------------------------------------------------------------------
# WeatherPipeline — chunk_by config key acceptance
