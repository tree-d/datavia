"""Datavia soil namespace package.

Contains soil-related pipeline implementations.
"""

from .pipeline import SoilPipeline
from .soilgrids_downloader import SoilGridsDownloader

__all__ = ["SoilPipeline", "SoilGridsDownloader"]
__path__ = __import__("pkgutil").extend_path(__path__, __name__)
