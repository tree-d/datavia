# Datavia Soil Pipeline

Soil data pipeline for the Datavia geospatial data integration system.

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