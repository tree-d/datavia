# Datavia Weather Pipeline

Multi-source weather data pipeline for the Datavia geospatial data integration
system.  Three sources are supported through a single `WeatherPipeline`
interface backed by a `CompositeWeatherDownloader`:

| Source | Credentials |
|---|---|
| **HYRAS** — DWD daily gridded observations (ETRS89-LAEA, ~1 km) | none |
| **ERA5-Land** — Copernicus hourly reanalysis (WGS84, 0.1°) | `~/.cdsapirc` |
| **DWD stations** — Open-Meteo point observations (Parquet) | none |

All downloads are registered in the shared SQLite `weather_layers` table with
`valid_from`/`valid_until` temporal bounds.  At query time `GetterWeather`
interpolates the gridded source (HYRAS or ERA5-Land) at the requested
coordinates, optionally blending with inverse-distance-weighted DWD station
observations.

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

### HYRAS (no credentials required)

HYRAS is the recommended starting point — daily gridded observations for
Germany, freely available from DWD OpenData under CC BY 4.0.

```python
from datavia.weather import WeatherPipeline
import numpy as np

# Build a HYRAS pipeline for a one-week summer window.
pipeline = WeatherPipeline(config={
    "source":     "HYRAS",
    "variables":  ["2m_temperature", "total_precipitation"],
    "date_start": "2024-06-01",
    "date_end":   "2024-06-07",
})

# Wire up all components (downloader, saver, getter).
pipeline()

# Download annual HYRAS NetCDF files from DWD OpenData.
# Incremental — re-running is safe; files already on disk are skipped.
pipeline.update_data()

# Query interpolated values at [lon, lat] coordinates in EPSG:4326.
# HYRAS is natively in ETRS89-LAEA (EPSG:3035); the getter reprojects
# input coordinates automatically.
coords = np.array([[13.4, 52.5], [10.0, 50.0]])  # Berlin, Kassel
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

### ERA5-Land (requires Copernicus credentials)

```python
pipeline = WeatherPipeline(config={
    "source":     "ERA5_land",
    "variables":  ["2m_temperature", "total_precipitation"],
    "date_start": "2024-06-01",
    "date_end":   "2024-06-30",
})
pipeline()
pipeline.update_data()
```

### Using the Datavia orchestrator with multiple pipelines

```python
from datavia import Datavia

hyras   = WeatherPipeline(config={"source": "HYRAS",    "variables": ["2m_temperature"], "date_start": "2024-06-01", "date_end": "2024-06-30"})
era5    = WeatherPipeline(config={"source": "ERA5_land", "variables": ["2m_temperature"], "date_start": "2024-06-01", "date_end": "2024-06-30"})

dv = Datavia(pipelines=[hyras, era5])
dv()            # initialises all pipeline components
dv.update_all() # downloads all sources

---

## Configuration options

### HYRAS

```python
WeatherPipeline(config={
    "source":     "HYRAS",

    # Any combination of the six supported HYRAS variables:
    "variables":  ["2m_temperature", "total_precipitation",
                   "temperature_2m_max", "temperature_2m_min",
                   "surface_solar_radiation_downwards", "relative_humidity_2m"],

    # Date range (ISO-8601 strings).  The downloader expands to whole calendar
    # years because one HYRAS file covers an entire year per variable.
    "date_start": "2024-01-01",
    "date_end":   "2024-12-31",

    # Optional: blend in DWD point-station observations.
    "dwd_stations": [
        {"id": "Berlin",  "latitude": 52.52, "longitude": 13.41},
        {"id": "Munich",  "latitude": 48.14, "longitude": 11.58},
    ],
})
```

### ERA5-Land

```python
WeatherPipeline(config={
    "source":     "ERA5_land",

    # CDS API variable names for reanalysis-era5-land:
    "variables":  ["2m_temperature", "total_precipitation",
                   "surface_solar_radiation_downwards"],

    # Date range for the download (ISO-8601 strings).
    "date_start": "2024-01-01",
    "date_end":   "2024-12-31",

    # ERA5 bounding box [north, west, south, east] in degrees.
    # Edges are automatically snapped outward to the 0.1° ERA5-Land grid.
    "era5_bbox": [55.1, 5.9, 47.3, 15.0],  # Germany default

    # Optional: blend in DWD point-station observations.
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

| Source | Format | Grid / resolution | Auth required |
|---|---|---|---|
| **HYRAS** (DWD OpenData) | NetCDF (`.nc`) | ETRS89-LAEA (EPSG:3035), ~1 km | none |
| **ERA5-Land** (Copernicus CDS) | NetCDF (`.nc`) | WGS84, 0.1° (~9 km) | `~/.cdsapirc` |
| **DWD stations** (Open-Meteo archive API) | Parquet (`.parquet`) | point observations | none |

### HYRAS variables

HYRAS files are already in the final target units — no conversion is applied.
The pipeline internally translates between the short CF names stored in the
NetCDF files (e.g. `tas`) and the descriptive pipeline API names (e.g.
`2m_temperature`) via `nc_variable_map` in `source_registry.py`.

| Pipeline variable name | NetCDF CF name | Unit | Description |
|---|---|---|---|
| `2m_temperature` | `tas` | °C | Daily mean 2-m air temperature |
| `temperature_2m_max` | `tasmax` | °C | Daily maximum 2-m air temperature |
| `temperature_2m_min` | `tasmin` | °C | Daily minimum 2-m air temperature |
| `total_precipitation` | `pr` | mm/day | Daily precipitation sum |
| `surface_solar_radiation_downwards` | `rsds` | W/m² | Daily mean global solar radiation |
| `relative_humidity_2m` | `hurs` | % | Daily mean 2-m relative humidity |

### ERA5-Land variables (commonly used)

Unit conversions (K→°C, m→mm, SSRD→PAR) are applied automatically in
`GetterWeather.get_data()` via `datavia.library.unit_conversions`.

| Pipeline variable name | Raw ERA5 unit | Returned unit |
|---|---|---|
| `2m_temperature` | Kelvin | °C |
| `total_precipitation` | m water equiv. | mm |
| `surface_solar_radiation_downwards` | J m⁻² | µmol(photons) m⁻² s⁻¹ PAR |

### DWD / Open-Meteo variables (commonly used)

`temperature_2m`, `precipitation`, `wind_speed_10m`, `relative_humidity_2m`

---

## Architecture

```
WeatherPipeline
├── CompositeWeatherDownloader
│   ├── HYRASDownloader         → annual NetCDF from DWD OpenData (ETRS89-LAEA)
│   ├── ERA5Downloader          → ERA5-Land NetCDF via cdsapi (CDS API v2, WGS84)
│   └── DWDStationDownloader    → Open-Meteo archive API → Parquet
├── SaverWeather                → copy files + insert weather_layers DB rows
└── GetterWeather               → interpolate + unit conversion + optional IDW blend
```

Key design decisions:

- **Source registry** — `source_registry.py` maps each source name
  (`"HYRAS"`, `"ERA5_land"`, `"DWD_stations"`) to its downloader class,
  per-variable unit conversions, and (for HYRAS) an `nc_variable_map` that
  translates between the short CF names inside the NetCDF files (e.g. `tas`)
  and the descriptive pipeline API names (e.g. `2m_temperature`).
- **HYRAS coordinate system** — HYRAS files use ETRS89-LAEA (EPSG:3035), a
  metric projected CRS.  `GetterWeather` calls `get_nc_variable_name()` to
  resolve the correct in-file variable name, and `interpolate_netcdf`
  auto-detects the file CRS from the CF `grid_mapping` attribute and uses
  `pyproj` to reproject WGS84 query coordinates before interpolating.
- **HYRAS annual files** — one NetCDF file covers a full calendar year per
  variable.  The downloader expands any requested date range to whole
  calendar years and auto-discovers the current DWD version string (e.g.
  `v6-1`) from the HTML directory listing so version bumps need no code change.
- **CDS API v2** — ERA5 requests use `data_format`, `download_format`, and
  `grid` keys; the deprecated `format` / `date` keys are not sent.
- **ERA5-Land dataset** — `reanalysis-era5-land` (0.1°) is used instead of
  the coarser `reanalysis-era5-single-levels` (0.25°).
- **Bbox grid-snapping** — ERA5 bounding box edges are rounded outward to the
  0.1° ERA5-Land grid so every requested grid cell is fully included.
- **buffer_days** — ERA5 requests include 1 extra day before `date_start` so
  accumulative variables (precipitation, SSRD) that reset at UTC midnight have
  sufficient context to reconstruct the first local-day total.
- **Multi-variable NetCDF** — `SaverWeather.save()` inserts one
  `weather_layers` row per variable when a single NC file contains multiple
  variables, so per-variable path queries in `GetterWeather` work correctly.
- **Unit conversions** — ERA5 raw values (K, m, J m⁻²) are converted to
  human-readable units (°C, mm, µmol m⁻² s⁻¹) by
  `datavia.library.unit_conversions`.  HYRAS values are already in target
  units and no conversion is applied.
- **Disk–DB reconciliation** — :meth:`Pipeline.sync_files_and_database` (Pipeline
  base class) is called at the start of `update_data()` to detect orphan DB
  rows (file deleted) and orphan disk files (DB reset), ensuring the DB always
  reflects what is actually on disk before the download delta is computed.
  `SaverWeather` supplies the disk-side primitive (`list_managed_files`) and
  `GetterWeather` supplies the DB-side primitive (`get_registered_uris`);
  reconciliation is coordinated by the Pipeline, not the Saver.
- **Optional ERA5 dependency** — `cdsapi` is only required for ERA5 downloads
  (`datavia-weather[era5]`); HYRAS and DWD-only usage works without
  Copernicus credentials.

---

## Running tests

```bash
# Unit tests — no network required (~4 s)
pytest tests/test_weather_pipeline_unit.py -v

# End-to-end tests — HYRAS (no credentials needed; downloads ~60 MB per variable/year)
DATAVIA_E2E=1 pytest tests/test_weather_e2e.py::TestHYRASE2E -v

# End-to-end tests — DWD stations only (no credentials needed)
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


### Configurable spatial and temporal extent

`update_data()` currently downloads the full bounding box and date range
declared in the pipeline config without inspecting what is already on disk.
A planned incremental download manager would:

- **Sub-region bounding box** — callers should be able to specify a custom
  sub-region bounding box to download a smaller geographic area instead of the
  full default extent (e.g. just Berlin rather than all of Germany).
- **Respect already-downloaded extents** — before issuing a CDS or Open-Meteo
  request, query the `weather_layers` table for overlapping spatial bounding
  boxes and temporal ranges and request only the missing portion.  This makes
  repeated or widened requests safe and cheap.  The mechanism that computes the
  missing portion is the auto-merge performed in `WeatherPipeline.__init__`;
  see **Reconfigure the Pipeline** below.
- **Handle superseding regions correctly** — if Leipzig and Berlin have been
  downloaded separately and Sachsen (a larger region that contains both) is
  requested next, only the geographic extension that is not yet covered should
  be fetched rather than re-downloading the already-available tiles.
- **Prioritise small or urgent requests** — a download queue with configurable
  priority levels would let time-critical short requests complete before large
  background bulk downloads. _not urgent feature_
- **Evaluate compressed bulk downloads** — for large spatial or temporal
  extents a single zip archive may be faster to transfer than many individual
  files; the implementation should benchmark both options and choose
  accordingly.

### Reconfigure the Pipeline

Pipeline instances are currently identified only by the name supplied by the
caller.  If the same name is reused with a changed config, the DB record is
overwritten while existing files on disk are untouched — the getter then
silently serves data that belongs to the old config (different region, date
range, or variables) with no warning to the caller.  The following enhancements
address this and related lifecycle issues:

- **Auto-merge of existing coverage on init** — on `WeatherPipeline.__init__`,
  the pipeline should query `weather_layers` for all coverage that matches the
  same source and variables, merge it with the declared config, and expose the
  union as the **effective config**.  Only the portion declared in the current
  config but not yet on disk is downloaded.  A `get_config()` method returns
  the full effective config so callers can always inspect the real state:

  ```python
  pipeline = WeatherPipeline(config={
      "source": "ERA5_land",
      "variables": ["2m_temperature"],
      "date_start": "2024-07-01",   # Jul–Dec not yet downloaded
      "date_end":   "2024-12-31",
  })
  # Jan–Jun already on disk → effective config spans Jan–Dec
  print(pipeline.get_config()["date_start"])  # "2024-01-01"
  ```

  Pass `replace=True` to discard all previous coverage and start clean — useful
  when old data was a mistake and should no longer be served:

  ```python
  pipeline = WeatherPipeline(config={...}, replace=True)
  ```

- **Live reconfiguration via `reconfigure()`** — after construction, callers
  can mutate the pipeline config without restarting.  The method diffs the new
  config against the effective config, downloads only the added extent, and
  updates the DB.  `replace=True` drops the existing coverage first.

  Renaming the pipeline is also supported: `SaverWeather` will rename all
  managed files on disk and update every matching row in `weather_layers`
  atomically, so no data is orphaned and no path references break:

  ```python
  pipeline.reconfigure(
      {"name": "era5_germany_2024", "date_end": "2025-03-31"},
  )

  # Discard old coverage and start fresh under a new name
  pipeline.reconfigure({"name": "era5_berlin_only", ...}, replace=True)
  ```

- **Orchestrator-level consistency check** — the `Datavia` orchestrator holds
  references to all registered pipelines in memory and is therefore the right
  place to run a cross-pipeline audit.  A planned `dv.check_pipelines()` method
  would compare each pipeline's declared config against the `weather_layers` DB
  records and report any inconsistencies (name collisions, orphaned files,
  coverage gaps) without modifying anything:

  ```python
  dv = Datavia(pipelines=[pipeline_a, pipeline_b])
  dv.check_pipelines()  # raises or warns on any inconsistency
  ```

  One known limitation: if the caller accidentally constructs two `Datavia`
  instances in the same process (or across processes sharing the same DB), each
  orchestrator has an incomplete in-memory view of the registered pipelines.
  A lightweight DB-level lock or a singleton guard on `Datavia` should be
  evaluated to prevent conflicting audits or concurrent writes from corrupting
  the `weather_layers` table.

- **CLI support** — the `datavia` command-line tool should expose a
  `reconfigure` sub-command so operators can adjust pipeline settings without
  writing Python code:

  ```bash
  datavia pipeline reconfigure --name my_pipeline \
      --date-end 2025-03-31

  # Rename and extend in one step
  datavia pipeline reconfigure --name my_pipeline \
      --new-name era5_germany_2024 --date-end 2025-03-31

  # Audit all registered pipelines for inconsistencies
  datavia pipeline check
  ```




---

## Data licensing

- **ERA5-Land** — Copernicus Climate Change Service (C3S) / ECMWF.
  Licence: https://cds.climate.copernicus.eu/api/v2/terms/static/licence-to-use-copernicus-products.pdf
- **HYRAS** — Deutscher Wetterdienst (DWD), gridded observation dataset for Germany.
  Licence: Creative Commons Attribution 4.0 International (CC BY 4.0).
  Attribution: Deutscher Wetterdienst (DWD), https://opendata.dwd.de/climate_environment/CDC/grids_germany/daily/hyras_de/
- **DWD / Open-Meteo** — DWD Open Data, CC-BY 4.0.
  Attribution: https://open-meteo.com/en/docs/dwd-api
