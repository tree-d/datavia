# Weather Pipeline

The weather pipeline follows the same three-part pattern as elevation and soil
(Downloader → Saver → Getter) but uses NetCDF for gridded ERA5 data and Parquet
for DWD station time series. A `weather_layers` table in the shared SQLite DB
tracks every downloaded file with temporal `valid_from`/`valid_until` columns.
At query time `GetterWeather` blends station and gridded data at the requested
coordinates.

**Status: unit tests passing — e2e tests failing due to known bugs listed below.**

---

## Design decisions

- `SaverWeather` and `GetterWeather` live in `packages/weather/` (not `datavia/core/`),
  because no other package needs them.
- `cdsapi` is an **optional** dependency (`datavia-weather[era5]`) — DWD-only usage
  does not need Copernicus credentials.
- DWD station data is fetched via the **Open-Meteo archive API** (no auth required).
- A `CompositeDownloader(Downloader, ABC)` abstract base class was added to
  `datavia/core/interfaces.py`, shared by weather and soil composite downloaders.

---

## What is implemented

| Component | Location | Notes |
|---|---|---|
| `weather_layers` DB table + index | `datavia/library/database/init.sql` | idempotent `CREATE … IF NOT EXISTS` |
| `get_weather_paths`, `get_weather_metadata`, `check_weather_source_exists` | `datavia/library/database/query.py` | |
| `interpolate_netcdf`, `interpolate_station_parquet`, `blend_gridded_and_station` | `datavia/library/interpolation.py` | |
| `extract_netcdf_layer_metadata`, `write_parquet`, `read_parquet_time_range` | `datavia/library/formats.py` | |
| `CompositeDownloader` ABC | `datavia/core/interfaces.py` | |
| `ERA5Downloader` | `packages/weather/datavia/weather/era5_downloader.py` | CDS API, writes `.nc`; `_build_request_date_fields()` builds year/month/day lists |
| `DWDStationDownloader` | `packages/weather/datavia/weather/dwd_downloader.py` | Open-Meteo, writes `.parquet` |
| `CompositeWeatherDownloader` | `packages/weather/datavia/weather/composite_downloader.py` | runs ERA5 then DWD, returns newline-joined paths |
| `SaverWeather` | `packages/weather/datavia/weather/saver_weather.py` | copy → metadata → DB insert; `sync_files_and_database()` |
| `GetterWeather` | `packages/weather/datavia/weather/getter_weather.py` | dispatches to NetCDF, Parquet, or blend |
| `WeatherPipeline` | `packages/weather/datavia/weather/pipeline.py` | `name = "weather"` |
| `cli_utils.update_pipeline()` return value | `datavia/cli_utils.py` | was always `True`; now forwards the actual result |
| Unit tests | `tests/test_weather_pipeline_unit.py` | 32 tests, 7 classes, no network required |
| E2E tests | `tests/test_weather_e2e.py` | gated by `DATAVIA_E2E=1`; ERA5 also requires `~/.cdsapirc` |

**Test counts:** 295 unit tests pass, 14 skip (< 4 s, no network).
All 6 e2e tests fail with `DATAVIA_E2E=1` due to the bugs below.

To run e2e tests:
```bash
DATAVIA_E2E=1 pytest tests/test_weather_e2e.py::TestDWDStationE2E -v   # no credentials needed
DATAVIA_E2E=1 pytest tests/test_weather_e2e.py::TestERA5E2E -v         # requires ~/.cdsapirc
```

---

## Open bugs and planned work

Discovered by comparing the implementation against `weather_pipeline_report.md`
(ERA5-Land reference from the BioDT/Grasslands project). Listed by severity.

### Group 1 — Critical bugs (causing all e2e test failures)

#### 🐛 1. Wrong DWD API hostname

**File:** `packages/weather/datavia/weather/dwd_downloader.py`

`_OPEN_METEO_URL` is `"https://historical-api.open-meteo.com/v1/archive"` — this
hostname does not exist. The correct endpoint is:
`https://archive-api.open-meteo.com/v1/archive`

#### 🐛 2. Wrong ERA5 dataset name

**File:** `packages/weather/datavia/weather/era5_downloader.py`

`client.retrieve()` requests `"reanalysis-era5-single-levels"` — a coarser global
product (0.25°). Change to `"reanalysis-era5-land"` (0.1° land surface reanalysis).

#### 🐛 3. CDS API v2 request parameter keys

**File:** `packages/weather/datavia/weather/era5_downloader.py`

The CDS migrated to API v2 in late 2024. The old `"format": "netcdf"` key is
rejected. Replace with:

```python
"data_format": "netcdf",
"download_format": "unarchived",
"grid": "0.1/0.1",
```

Also remove the redundant `"date"` field — the year/month/day arrays already
encode the full range.

#### 🐛 4. Index alignment bug in `interpolate_station_parquet`

**File:** `datavia/library/interpolation.py`

After the radius filter `df = df[dist_km <= radius_km].copy()`, the DataFrame
retains original row indices while `dist_km` becomes a new 0-based numpy array.
The subsequent `dist_km[df.index]` then uses the old pandas row numbers, causing
wrong values or an `IndexError`. Fix: reset `df.index` and rebuild `dist_km` in
the same step.

---

### Group 2 — Correctness issues

#### ⚠ 5. No unit conversion applied to ERA5 raw values

ERA5 returns temperature in **Kelvin**, precipitation in **metres**, SSRD in
**J/m²**. The e2e range check (`-25 … 50 °C`) will always fail without conversion.

Add `datavia/library/unit_conversions.py`:

```python
kelvin_to_celsius(values)
precipitation_m_to_mm(values)
ssrd_to_par(ssrd_daily_j_m2)            # × 4.57 × 0.5 / 86400
convert_era5_variable(values, variable)  # dispatches by variable name
```

Call from `GetterWeather.get_weather_data()` so `interpolation.py` stays
format-agnostic.

#### ⚠ 6. Variable name inferred from temp-file stem (unreliable)

**File:** `packages/weather/datavia/weather/saver_weather.py`

`_extract_variable_from_stem()` strips the source prefix from a stem like
`era5_tmpXYZabc` (from `tempfile.mkstemp(prefix="era5_")`), yielding a random
string. Fix: add an explicit `variable: str` parameter to `SaverWeather.save()`
and propagate it from `WeatherPipeline.update_data()`.

#### ⚠ 7. CRS extraction returns CF convention string, not EPSG code

**File:** `datavia/library/formats.py`

`ds.attrs.get("grid_mapping_name", "EPSG:4326")` returns e.g.
`"latitude_longitude"` when the attribute is present. Default to `"EPSG:4326"`
unconditionally for geographic-coordinate ERA5 files.

---

### Group 3 — Robustness improvements

#### 8. ERA5 bbox grid-snapping

Add `ERA5Downloader._snap_bbox(bbox)` — rounds each edge to the 0.1° grid
(`ceil` north/east, `floor` south/west) before the CDS request.

#### 9. Deduplicate `get_weather_paths` call in `GetterWeather`

**File:** `packages/weather/datavia/weather/getter_weather.py`

`nc_paths` and `parquet_paths` are assigned from two identical
`get_weather_paths()` calls. Make one call and split by extension.

#### 10. Buffer-day download for accumulative ERA5 variables

Precipitation and SSRD reset at UTC midnight, not local midnight. Add
`buffer_days: int = 1` to `ERA5Downloader` that widens `date_start` before the
CDS request so the first local-day total can be reconstructed.

---

### Group 4 — Code quality

#### 11. Replace f-string logger calls in `formats.py`

`process_temporal_netcdf` uses `logger.error(f"...")` — use `%s`-style
lazy formatting throughout, consistent with the rest of the codebase.

#### 12. Extend unit tests

Add tests to `tests/test_weather_pipeline_unit.py` for:
- `unit_conversions.py` — K→°C, m→mm, PAR, variable-name dispatch
- Fixed index-alignment in `interpolate_station_parquet`
- `ERA5Downloader._snap_bbox()`
- Explicit `variable` parameter in `SaverWeather.save()`

---

### Deferred

- Daily aggregation (mean/min/max, PAR, PET) — address after hourly pipeline is stable.
- Self-contained management for deletion/archiving of old data files.
- Option to download only for a single day/month/year.

---

## Dependencies

| Package | Purpose | Where |
|---|---|---|
| `xarray>=2024.0` | Read/slice NetCDF along time + space | `datavia-weather` + dev env |
| `netCDF4>=1.7` | xarray NetCDF backend | `datavia-weather` + dev env |
| `pyarrow>=14.0` | Parquet read/write for pandas | `datavia-weather` + dev env |
| `pandas>=2.2` | Station data manipulation | `datavia-weather` |
| `cdsapi>=0.7` | ERA5 downloads from Copernicus CDS | `datavia-weather[era5]` (optional) |
| Open-Meteo HTTP | DWD station observations | `datavia-weather` (stdlib `urllib`) |
