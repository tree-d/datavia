# Datavia Elevation Pipeline

Elevation data pipeline for the Datavia geospatial data integration system.

## Installation

```bash
# Recommended: Install core + elevation pipeline
pip install datavia[elevation]

# Alternative: Install packages separately (equivalent result)
pip install datavia-elevation  # Automatically installs datavia core as dependency

# Development: Build from source
git clone https://github.com/tree-d/datavia.git
cd datavia
./build_all.sh
pip install dist/datavia-*.whl dist/datavia_elevation-*.whl
```

## Usage

```python
from datavia import Datavia
import numpy as np

try:
    from datavia.elevation import ElevationPipeline
    
    # Initialize elevation pipeline
    elevation = ElevationPipeline()
    
    # Create controller
    dv = Datavia(pipelines=[elevation])
    dv()
    
    # Get elevation data for coordinates (longitude, latitude)
    coords = np.array([[10.0, 50.0], [11.0, 51.0]])
    elevations = elevation.get_data(coords=coords, crs_coords="EPSG:4326")
    print(f"Elevations: {elevations} meters")
    
except ImportError:
    print("Elevation pipeline not installed. Install with: pip install datavia[elevation]")
```

## Data Source

- **Source**: BKG DGM200 (German Federal Agency for Cartography and Geodesy)
- **Resolution**: 200m
- **Coverage**: Germany
- **Format**: GeoTIFF

## Dependencies

- `datavia` (core system)
- Standard geospatial dependencies (rasterio, numpy, etc.)