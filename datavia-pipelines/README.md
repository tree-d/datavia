# Datavia Pipelines

> ✅ **Working Examples**: For current working code, see `tests/test_elevation_pipeline.py` and `tests/test_datavia_pip_ele.py`

Pre-built pipeline implementations for common geospatial data sources in Germany. This package provides ready-to-use pipelines for elevation, soil, and other environmental data sources that implement the Datavia core interfaces.

## Available Pipelines

### Elevation Pipeline (`elevation.py`)
**Status**: ✅ Production Ready  
**Data Source**: BKG DGM200 (German Federal Agency for Cartography and Geodesy)  
**Coverage**: Germany  
**Resolution**: 200m  

Provides access to German elevation data from the official DGM200 dataset.

#### Features
- **High Accuracy**: Official government elevation model
- **Complete Coverage**: All of Germany at 200m resolution
- **Automatic Tiling**: Downloads data in manageable tiles
- **CRS Support**: Native EPSG:25832 (UTM Zone 32N) with automatic transformation

#### Usage
```python
from pipelines.elevation import ElevationPipeline
from datavia.core.datavia import Datavia
from datavia.datavia.runner import start_container, get_container_status
from datavia.library.database.start import initialize_database
import numpy as np

# Initialize pipeline with URL
elevation = ElevationPipeline(
    url="https://sgx.geodatenzentrum.de/wcs_dgm200_inspire?VERSION=2.0.1&SERVICE=WCS&REQUEST=GetCoverage&COVERAGEID=dgm200_inspire__EL.GridCoverage&format=image/tiff&crs=EPSG:25832&bbox=280000,5235000,921000,6101000"
)

# Setup system
if not get_container_status():
    start_container()
initialize_database()

# Update data if needed
elevation.sync_files_and_database()
if not elevation.find_files():
    elevation.update_data()

# Get elevation data (coordinates in [longitude, latitude] format)
coordinates = np.array([[10.0, 50.0], [11.0, 51.0]])
elevations = elevation.get_data(coords=coordinates, crs_coords="EPSG:4326")
print(f"Elevations: {elevations}")
```

#### CLI Usage
```bash
# Currently available:
datavia db start     # Start PostGIS container
datavia db stop      # Stop container
datavia db status    # Check status

# Pipeline CLI commands under development:
# TODO: datavia pipeline run elevation --bbox
# TODO: datavia pipeline status elevation  
# TODO: datavia pipeline update elevation
```

#### Data Information
- **Source**: [BKG GeoBasis-DE](https://www.bkg.bund.de/)
- **License**: Open data (GeoNutzV)
- **Format**: GeoTIFF with spatial reference
- **Update Frequency**: Irregular (government updates)
- **Typical Tile Size**: ~10-50MB per tile
- **Total Data Size**: ~2-5GB for complete Germany

---

### Soil Pipeline (`soil.py`)
**Status**: 🔧 In Development (Phase 1.3)  
**Data Sources**: SoilGrids API + BÜK (German Soil Survey)  
**Coverage**: Germany  
**Resolution**: 250m (SoilGrids), Vector (BÜK)  

Integrates multiple soil data sources for comprehensive soil property access.

#### Planned Features
- **SoilGrids Integration**: Global soil properties via ISRIC API
- **BÜK Integration**: German soil classification from BGR
- **Multi-Property Support**: pH, organic carbon, bulk density, texture
- **Selective Downloads**: Configure which properties to include
- **Unified API**: Single interface for both raster and vector soil data

#### Development Status
```python
# Soil pipeline implementation in progress
# Current focus: SoilGrids API integration with selective downloads

# Planned interface (not yet implemented):
# from pipelines.soil import SoilPipeline
# soil = SoilPipeline(properties=['ph', 'organic_carbon'])
# soil.get_data(coords=coordinates, property='ph')

# TODO: Complete SoilGrids integration
# TODO: Add BÜK shapefile support
# TODO: Implement multi-property access
```

#### Data Sources
1. **SoilGrids (ISRIC)**
   - Global 250m resolution
   - Properties: pH, SOC, bulk density, texture, etc.
   - Multiple depth layers (0-5, 5-15, 15-30, 30-60, 60-100, 100-200cm)
   - API access with TIFF downloads

2. **BÜK (German Soil Survey)**
   - Vector-based soil classification
   - German-specific soil types and properties
   - High accuracy for regulatory applications
   - Shapefile format with PostGIS integration

---

## Pipeline Architecture

All pipelines follow the same three-component architecture:

```
Pipeline = Downloader + Saver + Getter
```

### Implementation Pattern
```python
from datavia.core.interfaces import Pipeline, Downloader, Saver, Getter

class MyPipeline(Pipeline):
    def __init__(self):
        self.downloader = MyDownloader()
        self.saver = MySaver()
        self.getter = MyGetter()
        
    def download_region(self, bbox, **kwargs):
        # Download data for bounding box
        pass
        
    def get_value(self, latitude, longitude, **kwargs):
        # Get interpolated value at point
        return self.getter.get_value(latitude, longitude, **kwargs)
```

### Interface Contracts

#### Downloader
- Downloads raw data from external sources
- Handles authentication, retry logic, and validation
- Returns local file paths for downloaded data

#### Saver  
- Stores data locally in organized structure
- Saves metadata to PostGIS database
- Manages spatial indexing and tiling

#### Getter
- Provides coordinate-based data access
- Implements spatial interpolation
- Supports batch operations for efficiency

## Configuration

Configure pipelines through YAML files:

```yaml
# datavia-pipelines.yml
elevation:
  enabled: true
  data_dir: ./data/elevation
  tile_size: 1000  # meters
  interpolation: bilinear
  cache_size_mb: 500
  
soil:
  enabled: true
  data_dir: ./data/soil
  properties:
    - ph
    - organic_carbon
    - bulk_density
  depth_ranges:
    - [0, 30]    # 0-30cm
    - [30, 100]  # 30-100cm
  sources:
    soilgrids:
      enabled: true
      api_key: null  # No API key required
    buek:
      enabled: true
      shapefile_path: ./data/buek/buek1000.shp
```

## Installation

### As Part of Datavia
```bash
pip install datavia[pipelines]
```

### Standalone Installation
```bash
pip install datavia-pipelines
```

### Development Installation
```bash
git clone https://github.com/tree-d/datavia.git
cd datavia/datavia-pipelines
pip install -e .
```

## Creating Custom Pipelines

### 1. Implement Core Interfaces
```python
from datavia.core.interfaces import Downloader, Saver, Getter, Pipeline
from pathlib import Path

class WeatherDownloader(Downloader):
    def download(self, source_info: dict) -> Path:
        # Download weather data from DWD
        pass
        
    def validate(self, file_path: Path) -> bool:
        # Validate downloaded data
        pass

class WeatherSaver(Saver):
    def save(self, data_path: Path, metadata: dict) -> bool:
        # Save weather data and metadata
        pass
        
class WeatherGetter(Getter):
    def get_value(self, latitude: float, longitude: float, **kwargs) -> float:
        # Get interpolated weather value
        pass

class WeatherPipeline(Pipeline):
    def __init__(self):
        self.downloader = WeatherDownloader()
        self.saver = WeatherSaver()  
        self.getter = WeatherGetter()
```

### 2. Add Configuration Support
```python
from datavia.config import DataviaConfig

class WeatherPipeline(Pipeline):
    def __init__(self, config: DataviaConfig = None):
        self.config = config or DataviaConfig()
        weather_config = self.config.pipelines.get('weather', {})
        
        self.api_key = weather_config.get('api_key')
        self.stations = weather_config.get('stations', 'all')
```

### 3. Implement CLI Integration
```python
# In cli.py or as a plugin
import click
from datavia.cli import cli

@cli.group()
def weather():
    """Weather data pipeline commands."""
    pass

@weather.command()
@click.option('--bbox', nargs=4, type=float, help='Bounding box')
def download(bbox):
    """Download weather data."""
    pipeline = WeatherPipeline()
    pipeline.download_region(bbox)
```

### 4. Add Tests
```python
import pytest
from datavia_pipelines.weather import WeatherPipeline

class TestWeatherPipeline:
    def test_download(self):
        pipeline = WeatherPipeline()
        result = pipeline.download_region(bbox=(50.0, 6.0, 51.0, 7.0))
        assert result is True
        
    def test_get_value(self):
        pipeline = WeatherPipeline()
        temp = pipeline.get_value(50.7, 6.8, parameter='temperature')
        assert isinstance(temp, float)
```

## Testing

### Run All Pipeline Tests
```bash
cd datavia-pipelines/
pytest tests/
```

### Test Specific Pipeline
```bash
pytest tests/test_elevation_pipeline.py -v
```

### Integration Tests
```bash
# Test with real data (requires network access)
pytest tests/test_datavia_pip_ele.py --integration

# Test elevation pipeline end-to-end
pytest tests/test_elevation_pipeline.py --e2e
```

### Test Configuration
```python
# tests/conftest.py
import pytest
from datavia.config import DataviaConfig

@pytest.fixture
def test_config():
    return DataviaConfig({
        'database': {
            'host': 'localhost',
            'name': 'test_datavia'
        },
        'storage': {
            'data_dir': './test_data'
        }
    })
```

## Performance Optimization

### Caching Strategies
```python
# Configure tile-based caching
elevation = ElevationPipeline(config={
    'cache': {
        'tile_size': 1000,      # 1km tiles
        'max_cache_size_mb': 1000,
        'cleanup_interval': 3600  # 1 hour
    }
})

# Preload data for better performance
elevation.preload_region(bbox=(50.0, 6.0, 52.0, 8.0))
```

### Batch Processing
```python
# Process multiple coordinates efficiently
coordinates = [(lat, lon) for lat in range(50, 52) for lon in range(6, 8)]

# Batch processing (recommended)
elevations = elevation.get_values_batch(coordinates)

# Parallel processing for large datasets
from concurrent.futures import ThreadPoolExecutor

def process_chunk(coord_chunk):
    return elevation.get_values_batch(coord_chunk)

chunk_size = 1000
chunks = [coordinates[i:i+chunk_size] for i in range(0, len(coordinates), chunk_size)]

with ThreadPoolExecutor(max_workers=4) as executor:
    results = list(executor.map(process_chunk, chunks))
```

## Data Management

### Storage Organization
```
data/
├── elevation/
│   ├── tiles/
│   │   ├── tile_32_5665000_5700000.tif
│   │   └── tile_32_5700000_5735000.tif
│   └── metadata/
│       └── elevation_metadata.json
└── soil/
    ├── soilgrids/
    │   ├── ph_0-30cm.tif
    │   └── organic_carbon_0-30cm.tif
    └── buek/
        └── buek1000.shp
```

### Database Schema
```sql
-- Spatial metadata table
CREATE TABLE spatial_data_tiles (
    id SERIAL PRIMARY KEY,
    data_source VARCHAR(50) NOT NULL,
    tile_id VARCHAR(100) NOT NULL,
    geometry GEOMETRY(POLYGON, 25832) NOT NULL,
    file_path TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Spatial index for efficient queries
CREATE INDEX idx_spatial_tiles_geom ON spatial_data_tiles USING GIST (geometry);
```

### Data Updates
```python
# Check for updates
pipeline.check_for_updates()

# Force update specific region
pipeline.update_region(bbox=(50.0, 6.0, 51.0, 7.0), force=True)

# Clean up old data
pipeline.cleanup_old_data(days=30)
```

## Roadmap

### Immediate (Phase 1.3 - Current)
- [ ] Complete soil pipeline implementation
- [ ] SoilGrids API integration with selective downloads
- [ ] BÜK shapefile integration
- [ ] Multi-property soil data access

### Short Term (Phase 2)
- [ ] Weather pipeline (DWD integration)
- [ ] Radiation pipeline
- [ ] Vector data support improvements
- [ ] Performance optimization

### Medium Term (Phase 3)
- [ ] Real-time data sources
- [ ] Temporal data support
- [ ] Multi-region expansion beyond Germany
- [ ] Machine learning interpolation methods

### Long Term (Phase 4)
- [ ] Satellite data integration
- [ ] Climate projection data
- [ ] Custom interpolation algorithms
- [ ] Distributed processing support

## Contributing

### Pipeline Development Guidelines
1. **Follow the interface contracts** from datavia-core
2. **Include comprehensive tests** with real data validation
3. **Document data sources** with licensing and usage information  
4. **Implement proper error handling** for network and data issues
5. **Consider performance** for large-scale data access

### Contribution Process
1. Fork the repository
2. Create a feature branch for your pipeline
3. Implement using the pipeline template
4. Add tests and documentation
5. Submit a pull request

See [Coding Standards](../codingStandards.md) for detailed guidelines.

## Dependencies

Pipeline-specific dependencies:
- `requests>=2.32.3`: HTTP client for API access
- `soilgrids>=0.1.0`: SoilGrids API client
- `tqdm>=4.67.1`: Progress bars for downloads
- Plus all datavia-core dependencies

See [pyproject.toml](../datavia/pyproject.toml) for the complete list.

## Support

- 📋 [Issue Tracker](https://github.com/tree-d/datavia/issues)
- 💬 [Pipeline Discussions](https://github.com/tree-d/datavia/discussions/categories/pipelines)
- 📧 Email: michael.berg@ufz.de