"""Datavia weather namespace package.

Provides end-to-end weather data integration for Germany using ERA5 gridded
reanalysis (NetCDF), HYRAS daily grids (NetCDF), and DWD station observations
(Parquet).
"""

import pkgutil

from .composite_downloader import CompositeWeatherDownloader
from .coverage_manager import CoverageCell, CoverageManager
from .dwd_downloader import DWDStationDownloader
from .era5_downloader import ERA5Downloader
from .getter_weather import GetterWeather
from .pipeline import WeatherPipeline
from .saver_weather import SaverWeather
from .zarr_store_manager import ZarrStoreManager

try:
    from .hyras_downloader import HYRASDownloader
except ImportError:
    HYRASDownloader = None  # type: ignore[assignment,misc]

__all__ = [
    "CompositeWeatherDownloader",
    "CoverageCell",
    "CoverageManager",
    "DWDStationDownloader",
    "ERA5Downloader",
    "GetterWeather",
    "HYRASDownloader",
    "SaverWeather",
    "WeatherPipeline",
    "ZarrStoreManager",
]
__path__ = pkgutil.extend_path(__path__, __name__)
