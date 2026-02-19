# Datavia Soil Pipeline

Soil data pipeline for the Datavia geospatial data integration system.

## ⚠️ Development Status Warning

**This package is currently under active development and has known structural issues:**

- **Interface Incompatibility**: `SoilPipeline.__call__` passes `url=None`, but the core `Pipeline.__call__` requires a valid URL parameter. Calling `SoilPipeline()` followed by `pipeline()` will raise a `ValueError`.

- **Downloader Configuration Mismatch**: `SoilGridsDownloader` expects a dictionary config, but the Pipeline base class passes a string URL. The constructor signatures are incompatible.

- **Missing Methods**: Uses `self.saver.get_my_raster_layers`, `self.saver.save_tiff_metadata`, and `super().get_data_info()` which are not defined in the core base classes.

- **Import Path Issues**: Imports `from ..config import get_config` but config is in the core datavia package. Should be `from datavia.config import get_config`.

**Recommendation**: This package is not production-ready. Use with caution and expect API changes. Consider using the elevation pipeline for stable functionality until soil pipeline development is complete.

## Installation

```bash
# Recommended: Install core + soil pipeline
pip install datavia[soil]

# Alternative: Install packages separately (equivalent result)
pip install datavia-soil  # Automatically installs datavia core + soilgrids as dependencies

# Development: Build from source
git clone https://github.com/tree-d/datavia.git
cd datavia
./build_all.sh
pip install dist/datavia-*.whl dist/datavia_soil-*.whl
```

## Usage

```python
from datavia import Datavia
import numpy as np

try:
    from datavia.soil import SoilPipeline
    
    # Initialize soil pipeline
    soil = SoilPipeline()
    
    # Create controller
    dv = Datavia(pipelines=[soil])
    dv()
    
    # Get soil data for coordinates (longitude, latitude)
    coords = np.array([[10.0, 50.0], [11.0, 51.0]])
    soil_data = soil.get_data(coords=coords, crs_coords="EPSG:4326")
    
    # Access soil properties
    print(f"Clay content: {soil_data['clay']} %")
    print(f"Sand content: {soil_data['sand']} %") 
    print(f"Silt content: {soil_data['silt']} %")
    print(f"pH values: {soil_data['ph']}")
    print(f"Organic carbon: {soil_data['carbon']} ‰")
    
except ImportError:
    print("Soil pipeline not installed. Install with: pip install datavia[soil]")
```

## Data Source

- **Source**: SoilGrids API (ISRIC - World Soil Information)
- **Resolution**: 250m
- **Coverage**: Germany (selective download strategy)
- **Format**: Multi-band GeoTIFF
- **Properties**: Clay, sand, silt, pH, organic carbon content

## Dependencies

- `datavia` (core system)
- `soilgrids` (SoilGrids API client)
- Standard geospatial dependencies (rasterio, numpy, etc.)