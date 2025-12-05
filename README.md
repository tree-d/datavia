# Datavia - Geospatial Data Integration System

A modular, pipeline-based system for integrating geospatial data sources in Germany. Datavia provides a unified interface for downloading, storing, and accessing various types of geospatial data including elevation, soil properties, and more.

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Overview

Datavia is designed for researchers who need efficient integration of multiple geospatial data sources. The system uses a modular architecture where **downloaders**, **savers**, and **getters** work together through abstract interfaces to process and provide access to geospatial data.

### Key Features

- 🗺️ **Unified Data Access**: Single interface for multiple geospatial data sources
- 🔄 **Pipeline-Based Architecture**: Modular design for easy extension to new data sources
- 🚀 **Efficient Caching**: Automatic data caching with update management
- 🌍 **Coordinate System Support**: Automatic CRS transformations using PyProj
- 📊 **Spatial Interpolation**: Multiple interpolation methods for data access
- 🐳 **Docker Integration**: Containerized PostGIS database for simplified setup
- 📡 **REST API**: FastAPI-based web interface for data access

### Current Data Sources

- **Elevation Data**: BKG DGM200 (200m resolution German elevation model)
- **Soil Data**: SoilGrids API integration (in development)

The architecture is designed to support expansion to other regions and data types beyond geospatial sources.

## Quick Start

### Installation

#### Option 1: pip (Recommended for most users)

```bash
# Install core system
pip install datavia

# Install with pipeline implementations
pip install datavia[pipelines]
```

#### Option 2: pixi (Simplified dependency management)

```bash
pixi add datavia
```

### Basic Setup

1. **Start the PostGIS database** (required for metadata storage):
   ```bash
   datavia db start
   ```

2. **Configure your first pipeline**:
   ```bash
   datavia config init
   ```

3. **Download elevation data for a region**:
   ```bash
   datavia pipeline run elevation --bbox 50.0 8.0 51.0 9.0
   ```

4. **Access data in your Python code**:
   ```python
   # API examples need to be written based on final implementation
   # See tests/ directory for current working examples
   # TODO: Update after API stabilization
   ```

## Architecture

Datavia follows a **three-component pipeline architecture**:

```
Pipeline = Downloader + Saver + Getter
```

- **Downloader**: Fetches data from external sources (URLs, APIs)
- **Saver**: Stores data locally and metadata in PostGIS database  
- **Getter**: Provides coordinate-based data access with interpolation

### Project Structure

```
datavia/                          # Main package
├── datavia/                      # Core system 
│   ├── core/                     # Pipeline interfaces & implementations
│   ├── library/                  # Shared utilities & database operations
│   │   └── database/             # PostGIS integration
│   ├── cli.py                    # Command-line interface
│   └── config.py                 # Configuration management
│
datavia-pipelines/                # Pipeline implementations
├── pipelines/
│   ├── elevation.py              # German elevation data (BKG DGM200)
│   └── soil.py                   # Soil data (SoilGrids API)
└── tests/                        # Pipeline tests
```

## Usage Examples

### Python API

```python
# Current working example based on tests:
from datavia.core.datavia import Datavia
from datavia_pipelines.pipelines.elevation import ElevationPipeline
import numpy as np

# Initialize pipeline and controller
elevation_pipeline = ElevationPipeline()
dv = Datavia(pipelines=[elevation_pipeline])

# Initialize system
dv()

# Get elevation data for coordinates (lon, lat format)
coordinates = np.array([[10.0, 50.0], [11.0, 51.0]])  # [longitude, latitude]
elevations = dv.elevation.get_data(coords=coordinates, crs_coords="EPSG:4326")

# Note: API is under active development - see tests/ for latest examples
```

### Command Line Interface

```bash
# Database management (implemented)
datavia db start         # Start PostGIS container
datavia db stop          # Stop container
datavia db status        # Check container status

# Other CLI commands are under development
# TODO: Implement pipeline management commands
# TODO: Implement configuration commands
```

### Configuration

Create a `datavia.yml` configuration file:

```yaml
database:
  host: localhost
  port: 5432
  name: datavia
  
pipelines:
  elevation:
    enabled: true
    resolution: 200m
    update_interval: monthly
    
  soil:
    enabled: false
    properties: [ph, organic_carbon, bulk_density]
```

## Installation for Development

For contributors and advanced users:

```bash
# Clone the repository
git clone https://github.com/tree-d/datavia.git
cd datavia

# Install with pixi (recommended for development)
pixi install

# Or install with pip in development mode
pip install -e .[dev]

# Run tests
pytest

# Format code
black .
```

## System Requirements

- **Python**: 3.12+
- **Operating System**: Linux (tested), Windows and macOS support planned
- **Database**: PostgreSQL with PostGIS extension (provided via Docker)
- **Storage**: Variable depending on data sources (elevation ~500MB per state)
- **Memory**: 4GB RAM recommended for processing

## Documentation

- [API Reference](docs/api/)
- [User Guide](docs/user_guide/)
- [Core System Documentation](datavia/README.md)
- [Pipeline Development Guide](datavia-pipelines/README.md)

## Contributing

We welcome contributions! Please see our [Coding Standards](codingStandards.md) for guidelines.

### Development Workflow

1. Fork the repository
2. Create a feature branch
3. Make your changes following our coding standards
4. Add tests for new functionality
5. Submit a pull request

### Adding New Pipelines

The system is designed for easy extension. See the [Pipeline Development Guide](datavia-pipelines/README.md) for creating new data source integrations.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Citation

If you use Datavia in your research, please cite:

```bibtex
@software{datavia2025,
  author = {Berg, Michael},
  title = {Datavia: Pipeline-based Geospatial Data Integration System},
  year = {2025},
  url = {https://github.com/tree-d/datavia},
  version = {1.0.0-dev}
}
```

## Support

- 📋 [Issue Tracker](https://github.com/tree-d/datavia/issues)
- 💬 [Discussions](https://github.com/tree-d/datavia/discussions)
- 📧 Email: michael.berg@ufz.de

## Roadmap

- [ ] Weather data pipeline (DWD integration)
- [ ] Radiation data pipeline  
- [ ] Vector data support (BÜK soil classification)
- [ ] Multi-region support beyond Germany
- [ ] Performance optimization for large-scale processing
- [ ] Web interface for non-technical users
