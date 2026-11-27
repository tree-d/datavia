"""Datavia weather namespace package.

Provides end-to-end weather data integration for Germany using ERA5 gridded
reanalysis (NetCDF), HYRAS daily grids (NetCDF), and daily DWD station
observations from the Climate Data Center, either kriged onto the HYRAS 1 km
grid (source ``"DWD_stations"``) or as a station Parquet supplement that is
blended into another source at query time.
"""

import pkgutil

from .composite_downloader import CompositeWeatherDownloader
from .coverage_manager import CoverageCell, CoverageManager
from .dwd_cdc import CDCClient, StationData
from .dwd_downloader import DWDStationDownloader
from .dwd_kriging_downloader import DWDKrigingDownloader
from .era5_downloader import ERA5Downloader
from .getter_weather import GetterWeather
from .hyras_downloader import HYRASDownloader
from .pipeline import WeatherPipeline
from .saver_weather import SaverWeather
from .variogram_cache import VariogramCache
from .zarr_store_manager import ZarrStoreManager

__all__ = [
    "CDCClient",
    "CompositeWeatherDownloader",
    "CoverageCell",
    "CoverageManager",
    "DWDKrigingDownloader",
    "DWDStationDownloader",
    "ERA5Downloader",
    "GetterWeather",
    "HYRASDownloader",
    "SaverWeather",
    "StationData",
    "VariogramCache",
    "WeatherPipeline",
    "ZarrStoreManager",
]
__path__ = pkgutil.extend_path(__path__, __name__)
