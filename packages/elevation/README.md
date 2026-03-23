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
- **Resolution**: 200 m
- **Coverage**: Germany
- **Format**: GeoTIFF (clipped from INSPIRE WCS service)

## Data Licensing & Attribution

The DGM200 is published under the
**Datenlizenz Deutschland – Namensnennung – Version 2.0 (dl-de/by-2-0)**.

Any publication, product, or application that uses data from this pipeline
**must display the following attribution notice** (Quellenvermerk).\
Because datavia downloads, clips, and stores the raster locally, the
"Daten verändert" suffix is required:

> © GeoBasis-DE / [BKG](https://www.bkg.bund.de/) (year of last data access)
> [dl-de/by-2-0](https://www.govdata.de/dl-de/by-2-0) (Daten verändert)

Replace *year of last data access* with the year in which you (or your deployment)
last called `update_data()` or initialised the pipeline.

Full licence text: <https://www.govdata.de/dl-de/by-2-0>

## Dependencies

- `datavia` (core system)
- Standard geospatial dependencies (rasterio, numpy, etc.)