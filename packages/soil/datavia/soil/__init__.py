"""Datavia soil namespace package.

Contains soil-related pipeline implementations.
"""

from .multiband_getter import MultibandGetter
from .multiband_saver import MultibandSaver
from .pipeline import SoilPipeline
from .soilgrids_downloader import SoilGridsDownloader

__all__ = ["SoilPipeline", "SoilGridsDownloader", "MultibandSaver", "MultibandGetter"]
__path__ = __import__("pkgutil").extend_path(__path__, __name__)
