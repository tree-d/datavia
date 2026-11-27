# Datavia - Geospatial Data Integration System

A modular, pipeline-based system for integrating geospatial data sources in Germany. Datavia gives one interface for downloading, storing, and querying elevation, soil, and weather data at arbitrary coordinates.

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

## Overview

Each data source is a **pipeline** built from three components:

```
Pipeline = Downloader + Saver + Getter
```

- **Downloader**: fetches data from the external source (URL, WCS, API)
- **Saver**: stores it locally and registers it in the metadata database
- **Getter**: returns values at coordinates, with CRS transformation and interpolation

The core package (`datavia`) contains no pipeline code. Each pipeline is a separate, optional package:

| Pipeline | Install | Data |
|---|---|---|
| `ElevationPipeline` | `datavia[elevation]` | BKG DGM200 elevation model, 200 m |
| `SoilPipeline` | `datavia[soil]` | SoilGrids 2.0 + HiHydroSoil v2.0 (texture, pH, carbon, hydraulic properties, …) |
| `WeatherPipeline` | `datavia[weather]` | ERA5-Land (hourly), HYRAS (daily, 1 km), DWD station observations kriged onto the HYRAS grid. Temperature, precipitation, solar radiation (as PAR), relative humidity |

For every variable, unit, and request parameter, see [Pipelines and Variables](docs/user_guide/variables.rst).

## Installation

```bash
pip install datavia[elevation]        # core + one pipeline
pip install datavia[elevation,soil]   # several pipelines
pip install datavia[pipelines]        # all pipelines
pip install datavia[all]              # all pipelines + vector support

# Kriged DWD station data (source "DWD_stations") also needs Mojo/MAX,
# see packages/kriging/README.md
pip install datavia-weather[kriging]
```

From GitHub with pixi:

```bash
pixi add --git https://github.com/tree-d/datavia --branch main --pypi datavia
pixi add --git https://github.com/tree-d/datavia --branch main --subdirectory packages/elevation --pypi datavia-elevation
pixi add --git https://github.com/tree-d/datavia --branch main --subdirectory packages/soil --pypi datavia-soil
pixi add --git https://github.com/tree-d/datavia --branch main --subdirectory packages/weather --pypi datavia-weather
```

**Vector data (shapefiles):** the `vector` extra uses Fiona, which needs GDAL. Install GDAL first (`conda install -c conda-forge gdal` or `pixi add gdal`), then `pip install datavia[vector]`. Without GDAL, Fiona fails to build with errors about `gdal-config`.

**ERA5-Land** downloads need a Copernicus CDS API key in `~/.cdsapirc` ([how to](https://cds.climate.copernicus.eu/how-to-api)). The other sources need no credentials.

## Quick Start

```python
import numpy as np
from datavia import Datavia
from datavia.elevation import ElevationPipeline
from datavia.weather import WeatherPipeline

hyras = WeatherPipeline(config={
    "source": "HYRAS",
    "variables": ["2m_temperature"],
    "date_start": "2024-06-01",
    "date_end": "2024-06-30",
})

# dv() sets up the metadata database; it downloads nothing.
# Each pipeline is then available under its source name.
dv = Datavia(pipelines=[ElevationPipeline(), hyras])
dv()

# Download whatever is not stored yet (safe to call repeatedly).
dv.elevation.update_data()
dv.HYRAS.update_data()

# Query at [longitude, latitude] pairs; other CRSs via crs_coords.
coords = np.array([[13.405, 52.520], [11.582, 48.135]])  # Berlin, Munich
heights = dv.elevation.get_data(coords, crs_coords="EPSG:4326")      # shape (N,)
temps = dv.HYRAS.get_data(coords, variable="2m_temperature",
                          datetime_utc="2024-06-15")                 # shape (N,)
```

More in the [user guide](docs/user_guide/): [quick start](docs/user_guide/quick_start.rst), [basic usage](docs/user_guide/basic_usage.rst), [examples](docs/user_guide/examples.rst), [kriging](docs/user_guide/kriging.rst).

## Configuration

No setup is required. A SQLite metadata database is created automatically on first use, and data and logs go to `.datavia/` in your project directory. A `.gitignore` is written there so downloaded rasters are never committed.

Optional settings go in `~/.datavia/datavia.conf`, or in `datavia.conf` in the current directory. The annotated template [datavia.conf](datavia.conf) lists them all. For example:

```ini
# Share one ~/.datavia/ across all projects instead of one per project
[paths]
storage = global

# Use PostgreSQL instead of SQLite
[database]
url = postgresql://user:password@localhost:5432/gis
```

## Command Line

```bash
datavia config init --elevation --soil --weather   # write datavia_config.py
datavia config validate                            # check it
datavia config status                              # show installed pipelines

datavia update elevation
datavia update soil
datavia update weather                    # all configured weather sources
datavia update weather --source HYRAS     # one source
datavia update all                        # every configured pipeline
```

## Project Structure

```
datavia/
├── datavia/            # core package: interfaces, controller, CLI, config, shared library
├── packages/
│   ├── elevation/      # datavia-elevation
│   ├── soil/           # datavia-soil
│   ├── weather/        # datavia-weather
│   └── kriging/        # datavia-kriging (Mojo), used by the DWD_stations weather source
├── tests/
├── scripts/            # package build and docs test scripts
└── docs/               # Sphinx documentation
```

## Development

```bash
git clone https://github.com/tree-d/datavia.git
cd datavia
pixi install

pixi run test          # test suite
pixi run lint          # ruff check
pixi run format        # ruff format
pixi run type-check    # mypy
pixi run docs          # build the Sphinx docs
pixi run test-docs     # run the doctests in the docs
```

See [Local Development Setup](docs/development/local-setup.md) for editable installs of the pipeline packages. Follow the [Coding Standards](docs/development/coding-standards.md) for contributions.

### Adding New Pipelines

Each pipeline is a separate package under `packages/<name>/` that adds `datavia.<name>` to the shared namespace. A single GeoTIFF at a URL may not need a new package at all: `ElevationPipeline({"source": ..., "url": ...})` handles it.

See **[Adding a New Pipeline](docs/development/adding-a-pipeline.md)** for the component contract, worked examples, packaging, CLI wiring, tests, and a checklist of every file to touch. The existing pipelines ([elevation](packages/elevation/), [soil](packages/soil/), [weather](packages/weather/)) are the reference implementations.

## Requirements

- Python 3.12+
- Linux (tested); Windows and macOS are not yet supported
- Network access for downloads

## License

The code is licensed under the terms in [LICENSE](LICENSE). Downloaded data is subject to each provider's own licence, and applications and publications using it must give the required attribution. *Data Licensing & Attribution* in [docs/index.rst](docs/index.rst) has the notices for the elevation and soil sources.

## Support

- 📋 [Issue Tracker](https://github.com/tree-d/datavia/issues)
- 💬 [Discussions](https://github.com/tree-d/datavia/discussions)
- 📧 Email: peter.mace@ufz.de

## Roadmap

- [x] Soil data pipeline (SoilGrids + HiHydroSoil)
- [x] Weather data pipeline (ERA5-Land, HYRAS, kriged DWD stations)
- [ ] Vector data support (BÜK soil classification)
- [ ] Multi-region support beyond Germany
- [ ] Performance optimization for large-scale processing
- [ ] Web interface for non-technical users
