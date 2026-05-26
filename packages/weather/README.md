# Datavia Weather Pipeline

ERA5-Land gridded reanalysis and DWD station observation pipeline for the
Datavia geospatial data integration system.  The two sources are unified
through a single `WeatherPipeline` interface backed by a
`CompositeWeatherDownloader` that delegates to `ERA5Downloader` and
`DWDStationDownloader`.  All downloads are registered in the shared SQLite
`weather_layers` table with `valid_from`/`valid_until` temporal bounds.
At query time `GetterWeather` bilinearly interpolates ERA5 data, applies
inverse-distance weighting over nearby DWD stations, and blends the two
estimates.

---

## Installation

```bash
# Core + ERA5 support (requires ~/.cdsapirc Copernicus credentials)
pip install datavia[weather,era5]

# Core + DWD station support only (no credentials required)
pip install datavia[weather]

# Development: install the package directly from the git workspace
pip install -e packages/weather
```

### Copernicus CDS credentials (ERA5 only)

ERA5 downloads require a free Copernicus account and a `~/.cdsapirc` file.
See https://cds.climate.copernicus.eu/how-to-api for setup instructions.

---

## Quick start

```python
from datavia import Datavia
from datavia.weather import WeatherPipeline
import numpy as np

# Initialise the pipeline
pipeline = WeatherPipeline(config={
    "variables": ["2m_temperature", "total_precipitation"],
    "source": "HYRAS",
    "date_start": "2024-06-01",
    "date_end": "2024-06-30",
})

pipeline2 = WeatherPipeline(config={
    "variables": ["2m_temperature", "total_precipitation"],
    "source": "ERA5-land",
    "date_start": "2024-06-01",
    "date_end": "2024-06-30",
})

# Wire up all components (downloader, saver, getter)
dv = Datavia(pipelines=[pipeline, pipeline2])
dv()

# Download ERA5-Land NetCDF and DWD station Parquet files
# (incremental — safe to call repeatedly; files already on disk are skipped)
pipeline.update_data()

# Query interpolated values at [lon, lat] coordinates in EPSG:4326
coords = np.array([[13.4, 52.5], [10.0, 50.0]])
values = pipeline.get_data(
    coords=coords,
    crs_coords="EPSG:4326",
    variable="2m_temperature",
    datetime_utc="2024-06-15T12:00:00",
)
# values.shape == (2,) — one value per coordinate in °C

# Single-point convenience method
temperature = pipeline.get_weather_data(
    lat=52.5,
    lon=13.4,
    variable="2m_temperature",
    datetime_utc="2024-06-15T12:00:00",
)
```

---

## Configuration options

```python
WeatherPipeline(config={
    # ERA5 variable names (CDS API names for reanalysis-era5-land)
    "variables": ["2m_temperature", "total_precipitation",
                  "surface_solar_radiation_downwards"],

    # Date range for the download (ISO-8601 strings)
    "date_start": "2024-01-01",
    "date_end": "2024-12-31",

    # ERA5 bounding box [north, west, south, east] in degrees.
    # Edges are automatically snapped outward to the 0.1° ERA5-Land grid.
    "era5_bbox": [55.1, 5.9, 47.3, 15.0],  # Germany default

    # DWD station list — each dict must have id, latitude, longitude keys
    "dwd_stations": [
        {"id": "Berlin",    "latitude": 52.52, "longitude": 13.41},
        {"id": "Munich",    "latitude": 48.14, "longitude": 11.58},
        {"id": "Hamburg",   "latitude": 53.55, "longitude": 10.0},
        {"id": "Frankfurt", "latitude": 50.11, "longitude": 8.68},
    ],
})
```

---

## Data sources and variables

| Source | Format | Resolution | Auth required |
|---|---|---|---|
| ERA5-Land (Copernicus CDS) | NetCDF (`.nc`) | 0.1° (~9 km) | `~/.cdsapirc` |
| DWD stations (Open-Meteo archive API) | Parquet (`.parquet`) | point observations | none |

### ERA5-Land variables (commonly used)

| CDS variable name | Raw ERA5 unit | Returned unit |
|---|---|---|
| `2m_temperature` | Kelvin | °C |
| `total_precipitation` | m water equiv. | mm |
| `surface_solar_radiation_downwards` | J m⁻² | µmol(photons) m⁻² s⁻¹ PAR |

Unit conversions (K→°C, m→mm, SSRD→PAR) are applied automatically in
`GetterWeather.get_data()` via `datavia.library.unit_conversions`.

### DWD / Open-Meteo variables (commonly used)

`temperature_2m`, `precipitation`, `wind_speed_10m`, `relative_humidity_2m`

---

## Architecture

```
WeatherPipeline
├── CompositeWeatherDownloader
│   ├── ERA5Downloader          → ERA5-Land NetCDF via cdsapi (CDS API v2)
│   └── DWDStationDownloader    → Open-Meteo archive API → Parquet
├── SaverWeather                → copy files + insert weather_layers DB rows
└── GetterWeather               → interpolate + ERA5 unit conversion + IDW blend
```

Key design decisions:

- **CDS API v2** — ERA5 requests use `data_format`, `download_format`, and
  `grid` keys; the deprecated `format` / `date` keys are not sent.
- **ERA5-Land dataset** — `reanalysis-era5-land` (0.1°) is used instead of
  the coarser `reanalysis-era5-single-levels` (0.25°).
- **Bbox grid-snapping** — bounding box edges are rounded outward to the
  0.1° ERA5-Land grid so every requested grid cell is fully included.
- **buffer_days** — ERA5 requests include 1 extra day before `date_start` so
  accumulative variables (precipitation, SSRD) that reset at UTC midnight have
  sufficient context to reconstruct the first local-day total.
- **Multi-variable NetCDF** — `SaverWeather.save()` inserts one
  `weather_layers` row per variable when a single NC file contains multiple
  variables, so per-variable path queries in `GetterWeather` work correctly.
- **Unit conversions** — ERA5 raw values (K, m, J m⁻²) are converted to
  human-readable units (°C, mm, µmol m⁻² s⁻¹) by
  `datavia.library.unit_conversions.convert_era5_variable`, keeping
  `interpolation.py` format-agnostic.
- **Optional ERA5 dependency** — `cdsapi` is only required for ERA5 downloads
  (`datavia-weather[era5]`); DWD-only usage works without Copernicus credentials.

---

## Running tests

```bash
# Unit tests — no network required (~4 s)
pytest tests/test_weather_pipeline_unit.py -v

# End-to-end tests — DWD only (no credentials needed)
DATAVIA_E2E=1 pytest tests/test_weather_e2e.py::TestDWDStationE2E -v

# End-to-end tests — ERA5 (requires ~/.cdsapirc)
DATAVIA_E2E=1 pytest tests/test_weather_e2e.py::TestERA5E2E -v
```

---

## Dependencies

| Package | Purpose | Install extra |
|---|---|---|
| `xarray >= 2024.0` | NetCDF read/slice along time + space | `weather` |
| `netCDF4 >= 1.7` | xarray NetCDF backend | `weather` |
| `pyarrow >= 14.0` | Parquet read/write | `weather` |
| `pandas >= 2.2` | Station data manipulation | `weather` |
| `requests` | Open-Meteo HTTP calls | `weather` |
| `cdsapi >= 0.7` | ERA5 downloads from Copernicus CDS | `weather[era5]` (optional) |

---

## Planned enhancements

### Flexible output CRS in `get_data`

`GetterWeather.get_data()` currently accepts `crs_coords="EPSG:4326"` for the
*input* coordinates only, and always returns scalar values (no spatial
reprojection of the output).  A future extension will allow callers to request
output data in an arbitrary CRS — for example to receive values on the native
HYRAS ETRS89-LAEA grid (EPSG:3035) or any other projection without having to
reproject manually afterwards:

```python
# Proposed future API — not yet implemented
values = pipeline.get_data(
    coords=np.array([[4_438_345, 2_781_634]]),
    crs_coords="EPSG:3035",          # input coordinates already in LAEA metres
    variable="relative_humidity_2m",
    datetime_utc="2024-06-15T12:00:00",
)
```

This would let users work entirely in their preferred projection and avoid
round-trip WGS84 conversions when the source data is natively in EPSG:3035.
Tracked as a follow-up to Phase A3 (interpolation CRS auto-detection).

### Configurable temporal resolution

All sources currently return daily values (HYRAS is daily by definition; ERA5
is downloaded at hourly intervals but queries return the nearest single time
step).  A planned `"temporal_resolution"` config key would let users choose
explicitly:

```python
# Proposed future API — not yet implemented
WeatherPipeline(config={
    "source":               "ERA5_land",
    "variables":            ["2m_temperature"],
    "date_start":           "2024-01-01",
    "date_end":             "2024-12-31",
    "temporal_resolution":  "hourly",   # or "daily" (default)
})
```

- `"daily"` — one value per calendar day (noon or daily mean depending on the
  variable); compatible with HYRAS, ERA5, and DWD station data.
- `"hourly"` — one value per hour; ERA5 and DWD stations support this natively;
  HYRAS is daily-only and would raise a `ValueError` if hourly is requested.

---

## Data licensing

- **ERA5-Land** — Copernicus Climate Change Service (C3S) / ECMWF.
  Licence: https://cds.climate.copernicus.eu/api/v2/terms/static/licence-to-use-copernicus-products.pdf
- **HYRAS** — Deutscher Wetterdienst (DWD), gridded observation dataset for Germany.
  Licence: Creative Commons Attribution 4.0 International (CC BY 4.0).
  Attribution: Deutscher Wetterdienst (DWD), https://opendata.dwd.de/climate_environment/CDC/grids_germany/daily/hyras_de/
- **DWD / Open-Meteo** — DWD Open Data, CC-BY 4.0.
  Attribution: https://open-meteo.com/en/docs/dwd-api
