"""Datavia soil namespace package.

Contains soil-related pipeline implementations for SoilGrids and HiHydroSoil.
"""

from .composite_downloader import CompositeDownloader
from .hihydrosoil_downloader import HiHydroSoilDownloader
from .pipeline import SoilPipeline
from .soilgrids_downloader import SoilGridsDownloader

__all__ = [
    "CompositeDownloader",
    "HiHydroSoilDownloader",
    "SoilGridsDownloader",
    "SoilPipeline",
]
__path__ = __import__("pkgutil").extend_path(__path__, __name__)
