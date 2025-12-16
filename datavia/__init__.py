"""Datavia - Pipeline based Geospatial Data Integration System.

A modular, pipeline-based system for integrating geospatial data sources.

Core components (always available):
- datavia.core: Core system interfaces and implementations

Optional pipeline components (install separately):
- datavia.elevation: Elevation data pipeline (install: pip install datavia[elevation])  
- datavia.soil: Soil data pipeline (install: pip install datavia[soil])
- datavia.weather: Weather data pipeline (future, install: pip install datavia[weather])

Example usage:
    # Core is always available
    from datavia import Datavia
    
    # Pipelines require separate installation
    try:
        from datavia.elevation import ElevationPipeline
        elevation = ElevationPipeline()
    except ImportError:
        print("Elevation pipeline not installed. Install with: pip install datavia[elevation]")
        elevation = None
    
    try:
        from datavia.soil import SoilPipeline  
        soil = SoilPipeline()
    except ImportError:
        print("Soil pipeline not installed. Install with: pip install datavia[soil]")
        soil = None
    
    # Create controller with available pipelines
    available_pipelines = [p for p in [elevation, soil] if p is not None]
    dv = Datavia(pipelines=available_pipelines)
    dv()
"""

from .core.datavia import Datavia

# Make commonly used classes available at top level
__all__ = ["Datavia"]

__version__ = "1.0.0-dev"
