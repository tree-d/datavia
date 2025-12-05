"""
Datavia pipelines package.

Contains individual pipeline modules for each data source:
- elevation.py (renamed from topography)
- soil.py - NEW: Complete soil data integration with SoilGrids API
- weather.py
- radiation.py

Each pipeline is self-contained with downloader, saver, and get_data method.
"""

# Import available pipelines
try:
    from .elevation import ElevationPipeline

    __all__ = ["ElevationPipeline"]
except ImportError:
    __all__ = []

# NEW: Soil pipeline with complete SoilGrids integration
try:
    from .soil import SoilPipeline

    __all__.append("SoilPipeline")
except ImportError:
    pass

# Additional pipelines will be imported as they are implemented
try:
    from .weather import WeatherPipeline

    __all__.append("WeatherPipeline")
except ImportError:
    pass

try:
    from .radiation import RadiationPipeline

    __all__.append("RadiationPipeline")
except ImportError:
    pass
