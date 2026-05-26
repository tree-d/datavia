"""Datavia weather namespace package.

Provides end-to-end weather data integration for Germany using ERA5 gridded
reanalysis (NetCDF) and DWD station observations (Parquet).
"""

from .composite_downloader import CompositeWeatherDownloader
from .dwd_downloader import DWDStationDownloader
from .era5_downloader import ERA5Downloader
from .getter_weather import GetterWeather
from .pipeline import WeatherPipeline
from .saver_weather import SaverWeather

__all__ = [
    "CompositeWeatherDownloader",
    "DWDStationDownloader",
    "ERA5Downloader",
    "GetterWeather",
    "SaverWeather",
    "WeatherPipeline",
]
__path__ = __import__("pkgutil").extend_path(__path__, __name__)
