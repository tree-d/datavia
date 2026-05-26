# Weather Source Architecture — Implementation Plan

> **Purpose:** Plan for introducing named, configurable weather sources,
> fixing the `source_name` collision bug, adding `HYRASDownloader`, and
> unifying the `config={...}` interface across all three pipeline packages.
>
> **Status:** Design approved — not yet implemented.
> **Branch:** `weather`

---

## Table of Contents

1. [Motivation](#1-motivation)
2. [Decisions made](#2-decisions-made)
3. [Five source modes](#3-five-source-modes)
4. [Phase A — Foundation](#4-phase-a--foundation-independent)
5. [Phase B — WeatherPipeline source wiring](#5-phase-b--weatherpipeline-source-wiring)
6. [Phase C — GetterWeather unit conversion fix](#6-phase-c--getterweather-unit-conversion-fix)
7. [Phase D — HYRASDownloader](#7-phase-d--hyrasdownloader)
8. [Phase E — CompositeWeatherDownloader refactor](#8-phase-e--compositeweatherdownloader-refactor)
9. [Phase F — Elevation and Soil config unification](#9-phase-f--elevation-and-soil-config-unification)
10. [Phase G — Tests](#10-phase-g--tests)
11. [Affected files](#11-affected-files)
12. [Verification checklist](#12-verification-checklist)
13. [Known risks and open questions](#13-known-risks-and-open-questions)

---

## 1. Motivation

### Bug: `source_name` collision in the database

All `WeatherPipeline` instances today use the module-level constant
`_PIPELINE_NAME = "weather"` as their `source_name`.  Creating two pipelines
(e.g. one for ERA5-Land and one for HYRAS) causes both to write rows under
`source_name = "weather"`, so one pipeline's data shadows the other's.

The same is true for `ElevationPipeline` (`"elevation"`) and `SoilPipeline`
(`"soil"`) whenever a project needs more than one variant.

### Goal: user-chosen, collision-free source names

`config["source"]` becomes the pipeline's `name` and flows into the DB as
`source_name`.  The user is responsible for choosing safe Python identifiers
(no hyphens — see decision below).

### Goal: five pluggable source modes

See [Section 3](#3-five-source-modes) for the full matrix.

### Goal: source-aware unit conversions

`GetterWeather` currently calls `convert_era5_variable()` unconditionally on
every `.nc` file.  HYRAS data is already in target units (°C, mm/day, W/m²),
so this produces wrong values.  A source registry maps each source to its
own conversion rules, with optional user override.

### Goal: uniform `config={...}` interface

`WeatherPipeline` already accepts a `config` dict.  `ElevationPipeline` and
`SoilPipeline` use positional/keyword constructor args.  All three should use
the same `config={...}` pattern so the interfaces are consistent for users and
for the `Datavia` controller.

---

## 2. Decisions made

| Question | Decision |
|---|---|
| `config["source"]` naming constraints | **User must provide a safe Python identifier** (underscores allowed, hyphens forbidden). `pipeline.name` is used as a Python attribute by `Datavia.add_pipeline()`. |
| DWD-stations-only mode trigger | **Explicit:** `config["source"] = "DWD_stations"`. |
| `HYRASDownloader` in scope? | **Yes — fully implemented** in this plan. |
| Soil and Elevation unification depth | **Full:** same `source → source_name → DB` flow; fixes `raster_layers` naming too. |
| Unit conversion framework | **Internal lookup table only** — no `pint` dependency. `{"from": "K", "to": "degC"}` style overrides in config. |
| Multi-grid compare (ERA5 + HYRAS) | **User responsibility** — two separate pipelines, no composite getter. |

---

## 3. Five source modes

| `config["source"]` | `dwd_stations` in config? | Active downloaders | Grid interpolation | Station IDW |
|---|---|---|---|---|
| `"ERA5_land"` | no | `ERA5Downloader` | ✓ ERA5 | — |
| `"ERA5_land"` | yes | `ERA5Downloader` + `DWDStationDownloader` | ✓ ERA5 | ✓ blend |
| `"HYRAS"` | no | `HYRASDownloader` | ✓ HYRAS | — |
| `"HYRAS"` | yes | `HYRASDownloader` + `DWDStationDownloader` | ✓ HYRAS | ✓ blend |
| `"DWD_stations"` | (implied) | `DWDStationDownloader` | — | ✓ IDW only |

ERA5 graceful failure (missing `cdsapi` / missing `~/.cdsapirc`) is preserved:
the downloader logs a warning and returns only station data if available.

---

## 4. Phase A — Foundation *(independent)*

### A1 — Config validation utility

**File:** `datavia/core/interfaces.py` (add near base `Pipeline` class)

Add `validate_pipeline_config(config, required_keys, known_keys)`:

- Raises `ValueError` listing **all** missing required keys in one message.
- Raises `ValueError` listing **all** unrecognised keys in one message (typo guard).
- Called at `__init__` time in each pipeline subclass before any other logic.

```python
# Example error messages
ValueError: WeatherPipeline config missing required key(s): {'source', 'date_start'}
ValueError: WeatherPipeline config contains unknown key(s): {'soruce', 'era_bbox'}
```

### A2 — Source registry

**New file:** `packages/weather/datavia/weather/source_registry.py`

Contents:

```python
SOURCE_REGISTRY = {
    "ERA5_land": {
        "grid_downloader": ERA5Downloader,
        "conversions": {
            "2m_temperature":                    {"from": "K",    "to": "degC"},
            "total_precipitation":               {"from": "m",    "to": "mm"},
            "surface_solar_radiation_downwards": {"from": "J_m2", "to": "PAR"},
        },
    },
    "HYRAS": {
        "grid_downloader": HYRASDownloader,
        "conversions": {},          # already in target units
    },
    "DWD_stations": {
        "grid_downloader": None,
        "conversions": {},
    },
}

FROM_TO_CONVERSION_MAP = {
    ("K",    "degC"): kelvin_to_celsius,
    ("m",    "mm"):   precipitation_m_to_mm,
    ("J_m2", "PAR"): ssrd_to_par,
    # extend here for new sources
}
```

Public helpers:

- `get_grid_downloader_class(source_name) -> type[Downloader] | None`
- `apply_conversion(source_name, variable, value, user_overrides=None) -> float`
  — merges user-provided `config["unit_conversions"]` over the registry defaults,
  then applies the resolved conversion function (or returns `value` unchanged).

### A3 — Fix `interpolate_netcdf` coordinate dimensions

**File:** `datavia/library/interpolation.py`

> **Investigated 2026-04-20** — see [`docs/development/hyras_era5_spike_findings.md`](hyras_era5_spike_findings.md)
> for full details and verified code snippets.

Two bugs were confirmed with real files:

#### Sub-fix a) HYRAS — ETRS89 LAEA (EPSG:3035) projection

ERA5 NetCDF uses `latitude` / `longitude` 1-D dimension names (WGS84 degrees).
HYRAS NetCDF uses `x` / `y` dimensions in **metres** under the
**ETRS89 LAEA Europe (EPSG:3035)** projection — *not* rotated-pole as originally
assumed.  Calling `interp(lat=..., lon=...)` raises
`ValueError: Dimensions {'lat','lon'} do not exist`.

Fix: detect `x`/`y` dims and a `grid_mapping` attribute → reproject the
input WGS84 point to the file CRS via `pyproj` → call `interp(y=..., x=...)`.

```python
import pyproj

crs_file = pyproj.CRS.from_cf(ds[grid_mapping_name].attrs)
transformer = pyproj.Transformer.from_crs("EPSG:4326", crs_file, always_xy=True)
x_proj, y_proj = transformer.transform(lon_wgs84, lat_wgs84)
value = ds[variable].interp(y=y_proj, x=x_proj)
```

`pyproj 3.7.1` is already in the pixi environment — no new dependency needed.

#### Sub-fix b) ERA5 new CDS API — `valid_time` coordinate

The new CDS API (cdsapi ≥ 0.7) returns files whose time dimension is named
`valid_time`, not `time`.  `interpolate_netcdf` only checks for `"time"` in
`ds.coords`, so time selection is silently skipped and all time steps are
returned as an array instead of a scalar.

Fix: fall back to `valid_time` when `time` is absent:

```python
time_dim = "time" if "time" in ds.coords else "valid_time"
```

#### Filename pattern (for HYRASDownloader, Phase D)

All HYRAS files carry a `_de` suffix before `.nc`:
`{prefix}_{year}_v{X}-{Y}_de.nc`.
The HTML-scrape regex in `HYRASDownloader` must include `_de`.

---

## 5. Phase B — WeatherPipeline source wiring *(depends on A1, A2)*

**File:** `packages/weather/datavia/weather/pipeline.py`

Changes:

- Remove `_PIPELINE_NAME` module constant.
- Add `validate_pipeline_config()` call at `__init__`.
- Required keys: `{"source", "variables", "date_start", "date_end"}`.
- Known keys: required set + `{"era5_bbox", "dwd_stations", "unit_conversions", "buffer_days"}`.
- Extract `name = config["source"]`; pass to `super().__init__(name=name, ...)`.
- `__call__` passes `self.name` (not a constant) to `SaverWeather(self.name)` and
  `GetterWeather(self.name, ...)`.

```python
# Before
WeatherPipeline()   # source_name always "weather"

# After
WeatherPipeline(config={
    "source":     "ERA5_land",
    "variables":  ["2m_temperature", "total_precipitation"],
    "date_start": "2024-01-01",
    "date_end":   "2024-12-31",
})
# source_name in DB → "ERA5_land"
```

---

## 6. Phase C — GetterWeather unit conversion fix *(depends on A2, B)*

**File:** `packages/weather/datavia/weather/getter_weather.py`

- `GetterWeather.__init__` gains a `unit_overrides: dict | None = None` argument.
- Replace `convert_era5_variable(raw_gridded, variable)` with
  `apply_conversion(self.source_name, variable, raw_gridded, self._unit_overrides)`.
- `WeatherPipeline.__call__` passes `config.get("unit_conversions")` when
  constructing `GetterWeather`.

This means HYRAS data (already in °C / mm / W/m²) passes through unchanged, while
ERA5 data still gets converted from raw units.

---

## 7. Phase D — HYRASDownloader *(depends on A2, A3)*

**New file:** `packages/weather/datavia/weather/hyras_downloader.py`

### Variable mapping

| `config["variables"]` name | HYRAS subdir | Filename prefix | NC variable | Unit |
|---|---|---|---|---|
| `2m_temperature` | `air_temperature_mean` | `tas_hyras_1` | `tas` | °C |
| `temperature_2m_max` | `air_temperature_max` | `tasmax_hyras_1` | `tasmax` | °C |
| `temperature_2m_min` | `air_temperature_min` | `tasmin_hyras_1` | `tasmin` | °C |
| `total_precipitation` | `precipitation` | `pr_hyras_1` | `pr` | mm/day |
| `surface_solar_radiation_downwards` | `radiation_global` | `rsds_hyras_5` | `rsds` | W/m² |
| `relative_humidity_2m` | `humidity` | `hurs_hyras_1` | `hurs` | % |

> **ET0 note:** `et0_fao_evapotranspiration` is **not available from HYRAS**.
> It is available via DWD/Open-Meteo stations — if a user adds `dwd_stations`
> to a HYRAS pipeline and requests this variable, `DWDStationDownloader` will
> fetch it via the Open-Meteo variable name `et0_fao_evapotranspiration`.

### Base URL

```
https://opendata.dwd.de/climate_environment/CDC/grids_germany/daily/hyras_de/
```

### Version auto-discovery

DWD releases version updates (e.g. `v6-1` → `v6-2`) without notice.
`HYRASDownloader._discover_latest_filename(subdir, year)` fetches an HTML
directory listing and extracts the latest matching filename via regex.

### `download()` logic

For each `(year, variable)` pair in the date range:

1. Build the subdir URL from the variable mapping.
2. Call `_discover_latest_filename(subdir, year)` to get the exact filename.
3. Download the file via `URLDownloader` (inherited retry logic applies).
4. Return newline-joined paths of all downloaded files.

One `.nc` file covers an entire calendar year.  The date range is expanded
to whole years — e.g. `2024-06-01` to `2025-02-28` downloads `2024` and
`2025` files for each requested variable.

---

## 8. Phase E — CompositeWeatherDownloader refactor *(depends on A2, D)*

**File:** `packages/weather/datavia/weather/composite_downloader.py`

- `__init__` reads `config["source"]` and calls
  `get_grid_downloader_class(source)` from the registry.
- Grid downloader is instantiated only when the registry returns a class
  (i.e. source is not `"DWD_stations"`).
- `DWDStationDownloader` is instantiated only when `"dwd_stations"` is present
  in config (or source is `"DWD_stations"`).
- ERA5 graceful failure (ImportError / RuntimeError) is unchanged.

---

## 9. Phase F — Elevation and Soil config unification *(depends on A1)*

### F1 — ElevationPipeline

**File:** `packages/elevation/datavia/elevation/pipeline.py`

```python
# Before
ElevationPipeline(name="elevation", url="https://...")

# After
ElevationPipeline(config={
    "source": "elevation",          # required
    "url":    "https://...",        # optional — default URL used if omitted
})
```

Required keys: `{"source"}` | Known keys: + `{"url"}`.
The existing default URL constant is kept in the module as the fallback.

### F2 — SoilPipeline

**File:** `packages/soil/datavia/soil/pipeline.py`

```python
# Before
SoilPipeline(name="soil", properties=["clay", "sand"], depths=["0-5cm"])

# After
SoilPipeline(config={
    "source":     "soil",           # required
    "properties": ["clay", "sand"], # optional — defaults unchanged
    "depths":     ["0-5cm"],        # optional
    "statistic":  "mean",           # optional
})
```

Required keys: `{"source"}` | Known keys: + `{"properties", "depths", "statistic"}`.

---

## 10. Phase G — Tests *(depends on all above)*

### G1 — Updates to `tests/test_weather_pipeline_unit.py`

- **Config validation:** missing required key raises `ValueError`; unknown key
  raises `ValueError`; valid config passes without error.
- **Source registry:** `apply_conversion` returns correct value for `"ERA5_land"`;
  returns input unchanged for `"HYRAS"`; user override is applied correctly.
- **`CompositeWeatherDownloader`:** mock-based test for each of the five source
  modes — verify exactly the expected sub-downloader(s) are created.
- **`GetterWeather`:** verify that `apply_conversion` is called with the correct
  `source_name`, not the hardcoded ERA5 path.

### G2 — New `tests/test_hyras_downloader.py`

- URL construction per variable and year.
- Version auto-discovery (mocked HTML directory listing response).
- Single-variable / single-year download (mocked `requests.get`).
- Multi-year date range expansion (unit test, no network).

### G3 — Elevation and Soil test updates

All existing constructor call sites updated from keyword args to
`config={...}` style.

---

## 11. Affected files

| File | Change type |
|---|---|
| `datavia/core/interfaces.py` | Add `validate_pipeline_config()` |
| `datavia/library/interpolation.py` | Fix coordinate dimension auto-detection (A3) |
| `datavia/library/unit_conversions.py` | Keep existing functions; `convert_era5_variable` deprecated (still callable) |
| `packages/weather/datavia/weather/source_registry.py` | **New file** |
| `packages/weather/datavia/weather/hyras_downloader.py` | **New file** |
| `packages/weather/datavia/weather/pipeline.py` | Source wiring, config validation |
| `packages/weather/datavia/weather/composite_downloader.py` | Source-driven downloader selection |
| `packages/weather/datavia/weather/getter_weather.py` | Source-aware unit conversion |
| `packages/elevation/datavia/elevation/pipeline.py` | `config={...}` interface |
| `packages/soil/datavia/soil/pipeline.py` | `config={...}` interface |
| `tests/test_weather_pipeline_unit.py` | New test cases (G1) |
| `tests/test_hyras_downloader.py` | **New file** (G2) |

---

## 12. Verification checklist

```bash
# Unit tests — no network required
pytest tests/test_weather_pipeline_unit.py -v
pytest tests/test_hyras_downloader.py -v

# Config validation smoke test (in REPL)
from datavia.weather import WeatherPipeline
WeatherPipeline(config={"source": "ERA5_land"})
# → ValueError: missing required key(s): {'variables', 'date_start', 'date_end'}

WeatherPipeline(config={"source": "ERA5_land", "variables": ["2m_temperature"],
                         "date_start": "2024-01-01", "date_end": "2024-12-31",
                         "typo_key": True})
# → ValueError: unknown key(s): {'typo_key'}

# DB collision fix (two pipelines, distinct source_names in DB)
p1 = WeatherPipeline(config={"source": "ERA5_land", "variables": ["2m_temperature"],
                               "date_start": "2024-01-01", "date_end": "2024-01-31"})
p2 = WeatherPipeline(config={"source": "HYRAS", "variables": ["2m_temperature"],
                               "date_start": "2024-01-01", "date_end": "2024-01-31"})
dv = Datavia(pipelines=[p1, p2])
dv()
# dv.ERA5_land and dv.HYRAS are separate attributes — no collision

# E2E — DWD only, no credentials needed
DATAVIA_E2E=1 pytest tests/test_weather_e2e.py::TestDWDStationE2E -v
```

---

## 13. Known risks and open questions

### ✅ A3 is a confirmed blocker for D — now fully characterised

`interpolate_netcdf` must handle HYRAS `x`/`y` dimensions before
`HYRASDownloader` is useful.  Both bugs have been confirmed with real files
and the fixes are specified in the A3 section above.
See also [`docs/development/hyras_era5_spike_findings.md`](hyras_era5_spike_findings.md).

### ⚠️ HYRAS file sizes

Each HYRAS `.nc` file is 20–110 MB per variable per year (see the source
comparison doc).  For multi-year, multi-variable requests the total download
can be several GB.  The downloader should log file sizes and warn if the
total exceeds a reasonable threshold.

### ⚠️ HYRAS version numbers change without notice

The auto-discovery HTML scrape must be robust to DWD changing directory
structure.  Log the discovered filename clearly so users can diagnose
failures.

### ℹ️ ET0 only available via stations

`et0_fao_evapotranspiration` is not in HYRAS or ERA5-land.  It is available
via Open-Meteo stations.  Users who need it must include `"dwd_stations"` in
their HYRAS or ERA5_land pipeline config.

### ℹ️ `convert_era5_variable` deprecation

The existing function is kept for backward compatibility (tests, external
callers) but is no longer called from `GetterWeather`.  Add a deprecation
warning to the docstring.
