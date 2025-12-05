# Datavia Core System

> ⚠️ **Development Status**: Core APIs are stabilizing. Some examples show planned interfaces - see `../tests/` for current working code.

The core system provides the foundational infrastructure for the Datavia geospatial data integration platform. This package contains abstract interfaces, implementation classes, shared utilities, and database operations that power the pipeline-based architecture.

## Architecture Overview

The core system is organized into three main components:

### Core Module (`datavia/core/`)
Contains the fundamental pipeline interfaces and base implementations:

- **`interfaces.py`**: Abstract base classes defining the pipeline contract
- **`datavia.py`**: Main controller class for managing pipeline instances
- **`downloader_url.py`**: Robust URL-based downloader with retry logic
- **`saver_tiff.py`**: TIFF file storage with PostGIS metadata management
- **`getter_tiff.py`**: Coordinate-based data retrieval with interpolation

### Library Module (`datavia/library/`)
Shared utilities and operations used across pipelines:

- **Spatial Operations** (`spatial_ops.py`): Value extraction, multi-band processing
- **Coordinate Transforms** (`coordinate_transforms.py`): CRS transformations using PyProj
- **Interpolation** (`interpolation.py`): Spatial interpolation methods
- **Quality Control** (`quality_control.py`): Data validation utilities
- **File Formats** (`formats.py`): Format detection and handling

### Database Module (`datavia/library/database/`)
PostgreSQL/PostGIS integration for metadata management:

- **`connection.py`**: Thread-safe database connection management
- **`query.py`**: Database query functions and spatial operations
- **`enhanced_schema.sql`**: Database schema supporting temporal data
- **`start.py`**: Database initialization and container management

## Core Interfaces

All pipelines implement three core interfaces:

### Downloader Interface
```python
from datavia.core.interfaces import Downloader

class MyDownloader(Downloader):
    def download(self, source_info: dict) -> Path:
        """Download data from source and return local file path."""
        # Implementation here
        pass
        
    def validate(self, file_path: Path) -> bool:
        """Validate downloaded data integrity."""
        # Implementation here
        pass
```

### Saver Interface
```python
from datavia.core.interfaces import Saver

class MySaver(Saver):
    def save(self, data_path: Path, metadata: dict) -> bool:
        """Save data locally and metadata to database."""
        # Implementation here
        pass
        
    def get_metadata(self, identifier: str) -> dict:
        """Retrieve metadata for saved data."""
        # Implementation here
        pass
```

### Getter Interface
```python
from datavia.core.interfaces import Getter

class MyGetter(Getter):
    def get_value(self, latitude: float, longitude: float, **kwargs) -> float:
        """Get interpolated value at coordinates."""
        # Implementation here
        pass
        
    def get_values_batch(self, coordinates: List[Tuple[float, float]], **kwargs) -> List[float]:
        """Get values for multiple coordinates efficiently."""
        # Implementation here
        pass
```

## Configuration System

The configuration system (`config.py`) provides automatic project structure detection and centralized settings management:

```python
# Configuration system implementation in progress
# Current configuration is handled via YAML files
# TODO: Implement DataviaConfig class
# See cli.py load_config_if_exists() for current approach
```

### Configuration File Structure
```yaml
# datavia.yml
database:
  host: localhost
  port: 5432
  name: datavia
  user: datavia
  
storage:
  data_dir: ./data
  cache_size_mb: 1000
  
api:
  timeout_seconds: 30
  retry_attempts: 3
  
pipelines:
  elevation:
    enabled: true
    resolution: 200m
    update_interval: monthly
```

## Database Operations

### Connection Management
```python
from datavia.library.database.connection import get_connection

# Thread-safe connection
with get_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM spatial_metadata")
    results = cursor.fetchall()
```

### Spatial Queries
```python
from datavia.library.database.query import (
    find_tiles_for_point,
    get_data_coverage,
    update_tile_metadata
)

# Find data tiles containing a point
tiles = find_tiles_for_point(latitude=50.7753, longitude=6.0839)

# Check data coverage for a bounding box
coverage = get_data_coverage(
    bbox=(50.0, 6.0, 51.0, 7.0),
    data_source='elevation'
)
```

## Spatial Operations

### Coordinate Transformations
```python
from datavia.library.coordinate_transforms import (
    transform_coordinates,
    get_utm_zone,
    validate_coordinates
)

# Transform between coordinate systems
utm_x, utm_y = transform_coordinates(
    latitude=50.7753, 
    longitude=6.0839,
    source_crs='EPSG:4326',
    target_crs='EPSG:25832'
)

# Automatic UTM zone detection
utm_crs = get_utm_zone(latitude=50.7753, longitude=6.0839)

# Coordinate validation
is_valid = validate_coordinates(latitude=50.7753, longitude=6.0839)
```

### Data Extraction and Interpolation
```python
from datavia.library.spatial_ops import (
    extract_value_at_point,
    extract_values_for_polygon,
    interpolate_missing_values
)

from datavia.library.interpolation import (
    bilinear_interpolation,
    nearest_neighbor,
    cubic_interpolation
)

# Extract value from raster at point
value = extract_value_at_point(
    raster_path='elevation.tif',
    latitude=50.7753,
    longitude=6.0839,
    interpolation_method='bilinear'
)

# Multi-band extraction
values = extract_values_for_polygon(
    raster_path='soil_properties.tif',
    polygon_coords=[(50.0, 6.0), (51.0, 6.0), (51.0, 7.0), (50.0, 7.0)],
    bands=[1, 2, 3]  # pH, organic carbon, bulk density
)
```

## Pipeline Controller

The main controller manages pipeline instances and orchestrates data operations:

```python
from datavia.core.datavia import Datavia
from datavia_pipelines.pipelines.elevation import ElevationPipeline
import numpy as np

# Initialize with pipeline list
elevation_pipeline = ElevationPipeline()
dv = Datavia(pipelines=[elevation_pipeline])

# Initialize system (calls initialize_database)
dv()

# Access pipeline by name
coordinates = np.array([[10.0, 50.0]])  # [longitude, latitude]
elevation = dv.elevation.get_data(coords=coordinates, crs_coords="EPSG:4326")

# Note: API design is evolving - see tests/ for current examples
```

## CLI Integration

The core system provides CLI commands through `cli.py`:

```bash
# Currently implemented CLI commands:
datavia db start          # Start PostGIS container
datavia db stop           # Stop container  
datavia db status         # Check container status

# Additional CLI commands under development:
# TODO: Configuration management
# TODO: Pipeline management
# TODO: Cache management
```

## Error Handling and Logging

The core system includes comprehensive error handling:

```python
from datavia.core.interfaces import DataviaError
from datavia.library.quality_control import validate_data

try:
    # Pipeline operations
    result = dv.get_data('elevation', lat=50.5, lon=6.5)
except DataviaError as e:
    # Handle datavia-specific errors
    logger.error(f"Data access failed: {e}")
except Exception as e:
    # Handle unexpected errors
    logger.exception(f"Unexpected error: {e}")

# Data validation
is_valid, issues = validate_data(
    data_path='downloaded_file.tif',
    expected_crs='EPSG:25832',
    expected_bounds=(50.0, 6.0, 51.0, 7.0)
)
```

## Testing

The core system includes utilities for testing pipeline implementations:

```python
from datavia.testing import PipelineTestCase

class TestMyPipeline(PipelineTestCase):
    def setUp(self):
        self.pipeline = MyPipeline()
        
    def test_download(self):
        result = self.pipeline.downloader.download(test_source)
        self.assertTrue(result.exists())
        
    def test_save_and_retrieve(self):
        self.pipeline.saver.save(test_data, test_metadata)
        value = self.pipeline.getter.get_value(50.5, 6.5)
        self.assertIsNotNone(value)
```

## Performance Considerations

### Caching Strategy
- **Tile-based caching**: Data stored in geographic tiles for efficient access
- **Metadata caching**: Database queries cached for repeated access patterns
- **Memory management**: Configurable cache sizes and cleanup policies

### Parallel Processing
```python
from concurrent.futures import ThreadPoolExecutor
from datavia.library.spatial_ops import extract_value_at_point

def process_coordinates_parallel(coordinates, raster_path):
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(extract_value_at_point, raster_path, lat, lon)
            for lat, lon in coordinates
        ]
        return [future.result() for future in futures]
```

### Database Optimization
- **Spatial indexing**: PostGIS spatial indexes for fast geographic queries
- **Connection pooling**: Efficient database connection management
- **Bulk operations**: Batch inserts and updates for better performance

## Extension Points

### Custom Downloaders
```python
from datavia.core.interfaces import Downloader

class APIDownloader(Downloader):
    """Download data from REST APIs."""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        
    def download(self, source_info: dict) -> Path:
        # Custom API download logic
        pass
```

### Custom Data Formats
```python
from datavia.library.formats import register_format_handler

@register_format_handler('.nc')
def handle_netcdf(file_path: Path) -> dict:
    """Handle NetCDF files."""
    # Custom format handling
    pass
```

## Development Guidelines

When extending the core system:

1. **Follow the interface contracts** defined in `interfaces.py`
2. **Use the configuration system** for all settings
3. **Implement proper error handling** with descriptive messages
4. **Add comprehensive tests** for new functionality
5. **Document public APIs** with docstrings
6. **Consider thread safety** for shared operations

See [Coding Standards](../codingStandards.md) for detailed guidelines.

## Dependencies

Core system dependencies:
- `rasterio>=1.4.3`: Geospatial raster I/O
- `pyproj>=3.7.1`: Coordinate system transformations
- `psycopg2-binary>=2.9.9`: PostgreSQL database connectivity
- `sqlalchemy>=2.0.41`: Database ORM and connection management
- `numpy>=2.2.0`: Numerical operations
- `pydantic>=2.11.4`: Data validation and settings management
- `click>=8.1.8`: CLI framework
- `rich>=14.0.0`: Enhanced CLI output

See [pyproject.toml](pyproject.toml) for the complete dependency list.