# Datavia weather Pipeline

weather data pipeline for the Datavia geospatial data integration system.
Integrates two remote sources — weather stations and era5 data — through a single `weatherPipeline` interface backed by
a `CompositeDownloader` that routes each coverage to the correct backend.
All data is registered as individual single-band GeoTIFF layers in SQLite.
Downloads are incremental: only missing coverages are fetched on each call.

## Installation

```bash
# Recommended: Install core + weather pipeline
pip install datavia[weather]

# Alternative: Install packages separately (equivalent result)
pip install datavia-weather  # Automatically installs datavia core dependency

# Development: Build from source
git clone https://github.com/tree-d/datavia.git
cd datavia
./build_all.sh
pip install dist/datavia-*.whl dist/datavia_weather-*.whl
```

## Usage

```python
from datavia import Datavia
from datavia.weather import weatherPipeline
import numpy as np

# Initialize pipeline (defaults: clay, sand, silt, ph, carbon +
# field_capacity, wilting_point, porosity, hydraulic_conductivity)
weather = weatherPipeline()

# Create controller and initialise all components
dv = Datavia(pipelines=[weather])
dv()

# Download missing coverages (incremental — safe to call repeatedly)
weather.update_data()

# Get weather data for coordinates (longitude, latitude in EPSG:4326)
coords = np.array([[10.0, 50.0], [11.0, 51.0]])

# get_data() returns dict[str, np.ndarray] keyed by coverage ID
# (have to overthink this)
# e.g. {need to fill this}
weather_data = weather.get_data(coords=coords, crs_coords="EPSG:4326")

    # some gap filling comment
    # this needs to be updated

```

### Requesting specific properties and depths

```python
# Single property, single depth → returns np.ndarray directly (not a dict)
temperature_values = weather.get_data(

    # some gap filling comment
    # this needs to be updated
)

# Reconfigure the pipeline for a different property set
weather.configure(
    # some gap filling comment
    # this needs to be updated
)
weather.update_data()  # download any newly requested coverages
```

### Checking available data

```python
# Properties currently stored in the local database
print(weather.get_available_properties())
```


## Data Sources

- some gap filling comment
- this needs to be updated

## Data Licensing & Attribution

- some gap filling comment
- this needs to be updated

## Dependencies

- some gap filling comment
- this needs to be updated