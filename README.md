# Datavia - Geospatial Data Integration System

A modular, pipeline-based system for integrating geospatial data sources in Germany. Datavia provides a unified interface for downloading, storing, and accessing various types of geospatial data including elevation, soil properties, and more.

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

## Overview

Datavia is designed for researchers who need efficient integration of multiple geospatial data sources. The system uses a modular architecture where **downloaders**, **savers**, and **getters** work together through abstract interfaces to process and provide access to geospatial data.

### Key Features

- 🗺️ **Unified Data Access**: Single interface for multiple geospatial data sources
- 🔄 **Pipeline-Based Architecture**: Modular design for easy extension to new data sources
- 🌍 **Coordinate System Support**: Automatic CRS transformations using PyProj
- 📊 **Spatial Interpolation**: Multiple interpolation methods for data access
- 🗄️ **Zero-setup Database**: SQLite metadata database created automatically on first use

### Available Pipelines (Modular Installation)

- **📈 Elevation Pipeline** (`datavia[elevation]`): BKG DGM200 (200m resolution German elevation model)
- **🌱 Soil Pipeline** (`datavia[soil]`): SoilGrids + HiHydroSoil integration with selective download strategy
- **🌤️ Weather Pipeline** (`datavia[weather]`): Available — HYRAS daily gridded data (precipitation, temperature) + DWD station data; ERA5 in development
- **☀️ Radiation Pipeline**: Planned - CAMS radiation data

Each pipeline is a separate, optional package that extends the core system with specific data source capabilities.

## Quick Start

### Installation

- **Note: this is not fully developed yet. Developers should have a look at [Local Development Setup](docs/development/local-setup.md)**

#### Option 1: pip (Recommended for most users)

```bash
# Install CORE SYSTEM ONLY (no pipeline code included)
pip install datavia

# Install with specific pipelines (downloads additional packages)
pip install datavia[elevation]     # Core + elevation pipeline code
pip install datavia[soil]          # Core + soil pipeline code  
pip install datavia[elevation,soil] # Core + multiple pipelines

# or use this (as datavia is dependency of the pipeline packages)
pip install datavia-elevation
pip install datavia-soil

# Install everything
pip install datavia[all]
```

**Vector data (shapefiles)**: To enable Fiona-based shapefile support, install GDAL first, then install the `vector` extra:

```bash
# Linux (conda-forge)
conda install -c conda-forge gdal

# pixi
pixi add gdal

# Then install datavia with vector support
pip install datavia[vector]
```

If GDAL is missing, Fiona will fail to build with errors about `gdal-config`.

**Important**: The core `datavia` package contains NO pipeline implementations by design. Pipelines are separate packages (`datavia-elevation`, `datavia-soil`) that are installed only when explicitly requested.

#### Option 2: pixi (Development environment)

```bash
pixi add --pypi datavia
# or with elevationpipeline
pixi add --pypi datavia[soil]

# Vector support (requires GDAL from conda-forge in your pixi.toml)
pixi add gdal -c conda-forge
pixi add --pypi datavia[vector]
```

### Basic Setup

1. **No database setup required** — a SQLite database is created automatically in your
   data directory on first use.  No Docker, no credentials, no configuration needed.

2. **Advanced configuration** (optional — most users can skip this):

   Datavia stores data and logs in `.datavia/` inside your project directory by default.
   A `.gitignore` is written there automatically so large GeoTIFF files are never
   accidentally committed.

   To switch to **global storage** (one shared `~/.datavia/` for all projects — avoids
   re-downloading the same data in multiple projects):
   ```bash
   mkdir -p ~/.datavia
   cat > ~/.datavia/datavia.conf << 'EOF'
   [paths]
   storage = global
   EOF
   ```
   An annotated template with all available settings is in `datavia.conf` at the
   repository root.

   To use **PostgreSQL instead of SQLite**, add a `[database]` section to your
   `datavia.conf`:
   ```ini
   [database]
   url = postgresql://user:password@localhost:5432/gis
   ```

3. **Use in Python code**:
   ```python
   from datavia import Datavia
   from datavia.elevation import ElevationPipeline
   import numpy as np

   # 1. Build the controller and register pipelines
   dv = Datavia(pipelines=[ElevationPipeline()])

   # 2. dv() initialises the SQLite metadata database and each pipeline's
   #    Downloader / Saver / Getter components.  No data is downloaded.
   dv()

   # 3. Download data from the external source (BKG for elevation).
   #    Only needed once; data is cached locally after the first run.
   #    Returns True on success, False if the download failed.
   #    First download: ~500 MB, may take several minutes.
   success = dv.elevation.update_data()

   # 4. Query values at coordinates [longitude, latitude] in WGS84.
   #    Returns a numpy array of shape (N,) — one value per coordinate.
   #    Other CRS are supported via crs_coords, e.g. "EPSG:25832".
   coords = np.array([[10.0, 50.0]])          # [lon, lat]
   elevation_data = dv.elevation.get_data(coords, crs_coords="EPSG:4326")
   print(elevation_data)                      # e.g. [471.3]
   ```


## Architecture

Datavia follows a **three-component pipeline architecture**:

```
Pipeline = Downloader + Saver + Getter
```

- **Downloader**: Fetches data from external sources (URLs, APIs)
- **Saver**: Stores data locally and metadata in the SQLite database
- **Getter**: Provides coordinate-based data access with interpolation

### Project Structure

```
datavia/                          # Repository root
├── pyproject.toml               # Main core package configuration
├── datavia/                     # Core Python package
│   ├── __init__.py              # Main namespace package
│   ├── core/                    # Pipeline interfaces & implementations
│   ├── library/                 # Shared utilities & database operations
│   │   └── database/            # SQLite metadata database
│   ├── cli.py                   # Command-line interface
│   └── config.py                # Configuration management
├── packages/                    # Separate pipeline packages
│   ├── elevation/               # datavia-elevation package
│   │   ├── pyproject.toml
│   │   └── datavia/elevation/   # Elevation pipeline code
│   ├── soil/                    # datavia-soil package
│   │   ├── pyproject.toml
│   │   └── datavia/soil/        # Soil pipeline code
│   └── weather/                 # datavia-weather package
│       ├── pyproject.toml
│       └── datavia/weather/     # Weather pipeline code
├── tests/                       # Test suite
├── scripts/                     # Build and utility scripts
└── docs/                        # Documentation
```

## Usage Examples

### Python API

```python
# Core is always available
from datavia import Datavia
import numpy as np

# Pipelines are optional - handle missing installations gracefully
pipelines = []

try:
    from datavia.elevation import ElevationPipeline
    elevation = ElevationPipeline()
    pipelines.append(elevation)
    print("✅ Elevation pipeline loaded")
except ImportError:
    print("❌ Elevation pipeline not installed. Install: pip install datavia[elevation]")
    elevation = None

try:
    from datavia.soil import SoilPipeline
    soil = SoilPipeline() 
    pipelines.append(soil)
    print("✅ Soil pipeline loaded")
except ImportError:
    print("❌ Soil pipeline not installed. Install: pip install datavia[soil]")
    soil = None

# Create controller with available pipelines
dv = Datavia(pipelines=pipelines)
dv()

#update data
dv.elevation.update_data()

# Use available pipelines
coordinates = np.array([[10.0, 50.0], [11.0, 51.0]])  # [longitude, latitude]
elevations = dv.elevation.get_data(coords=coordinates, crs_coords="EPSG:4326")

```

### Command Line Interface

```bash
# Configuration management
datavia config init --elevation --soil  # Initialize config with selected pipelines
datavia config validate                  # Validate configuration file
datavia config status                    # Show installation status

# Data updates
datavia update elevation                 # Download / refresh elevation data
datavia update soil                      # Download / refresh soil data
datavia update weather                   # Update all configured weather pipelines
datavia update weather --source HYRAS   # Update only the HYRAS source
datavia update weather --source ERA5_land      # Update only ERA5
datavia update weather --source DWD_stations   # Update only DWD stations

# Development/Testing
python -m datavia.cli update elevation   # Alternative CLI access
```

### Configuration

Datavia creates configuration via CLI, but you can customize it.

Just use this cmd in the terminal:

```bash
datavia config init --elevation #this will add datavia_config.py with elevation pipeline to your current dir
```

## Installation for Development

For contributors and advanced users:

```bash
# Clone the repository
git clone https://github.com/tree-d/datavia.git
cd datavia

# Install development environment with pixi (recommended)
cd datavia/  # Enter package directory
pixi install
pixi shell

# Or install with pip in development mode
pip install -e .[dev]

# Build all packages
./build_all.sh

# Run tests
pytest

# Format code
black datavia/
```

## System Requirements

- **Python**: 3.12+
- **Operating System**: Linux (tested), Windows and macOS support planned
- **Database**: SQLite (built into Python — no installation required).  PostgreSQL is supported as an opt-in via `datavia.conf`.
- **Storage**: 
  - Core system: <50MB
  - Elevation pipeline: ~500MB per German state
  - Soil pipeline: ~280MB (selective download strategy)
- **Memory**: 4GB RAM recommended for data processing
- **Network**: Required for initial data downloads

## Documentation

- [API Reference](docs/api/)
- [User Guide](docs/user_guide/)
- [Development Guide](docs/development/)
- [Coding Standards](docs/development/coding-standards.md)

## Contributing

We welcome contributions! Please see our [Coding Standards](docs/development/coding-standards.md) for guidelines.

### Development Workflow

1. Fork the repository
2. Read through documentation in docs/development/
3. Create a feature branch
4. Make your changes following our coding standards
5. Add tests for new functionality
6. Submit a pull request

### Adding New Pipelines

The system uses namespace packages for easy extension:

1. **Create new package**: Add directory under `packages/your_pipeline/`
2. **Add pyproject.toml**: Configure package metadata and dependencies
3. **Implement pipeline class**: Follow `packages/elevation/datavia/elevation/pipeline.py` as template
4. **Add documentation**: Create README.md in package directory
5. **Add tests**: Create test files in `tests/`

See existing pipelines ([elevation](packages/elevation/), [soil](packages/soil/)) as examples.

## License

See the [LICENSE](LICENSE) file for details.

## Support

- 📋 [Issue Tracker](https://github.com/tree-d/datavia/issues)
- 💬 [Discussions](https://github.com/tree-d/datavia/discussions)
- 📧 Email: michael.berg@ufz.de

## Roadmap

- [x] Soil data pipeline (SoilGrids + HiHydroSoil integration)
- [x] Weather data pipeline (HYRAS + DWD station integration)
- [ ] Radiation data pipeline  
- [ ] Vector data support (BÜK soil classification)
- [ ] Multi-region support beyond Germany
- [ ] Performance optimization for large-scale processing
- [ ] Web interface for non-technical users
