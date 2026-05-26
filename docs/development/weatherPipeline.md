# Weather Pipeline

The weather pipeline follows the same three-part pattern as elevation and soil
(Downloader → Saver → Getter) but uses NetCDF for gridded ERA5 data and Parquet
for DWD station time series, since neither is a static spatial raster. A new
`weather_layers` table in the shared SQLite DB tracks every downloaded file with
temporal `valid_from`/`valid_until` columns. At query time `GetterWeather` blends
station and gridded data at the requested coordinates.

**Status: core implementation complete — E2E tests and CLI smoke-test open.**

---

## Implementation notes

### Ideas for further steps

- Look for some experience in copernicus project from BioDT
- Create some Option to only download for single day/month/year
- Create some self containing management for deletion and sorting of old data


### Design decisions made during implementation

- `SaverWeather` and `GetterWeather` live in `packages/weather/` (not `datavia/core/`),
  because no other package needs them. Same reasoning as `TiffSaver`/`GetterTiff`
  being in `datavia/core/` only because two packages share them.
- `cdsapi` is an **optional** dependency (`datavia-weather[era5]`) because DWD-only
  usage does not need Copernicus credentials.
- DWD station data is fetched via the **Open-Meteo HTTP API** (no auth required,
  no `wetterdienst` dependency needed).
- `formats.py` exposes `extract_netcdf_layer_metadata()` (not `read_netcdf_metadata`
  as originally planned) — it reads metadata from an already-downloaded file and
  returns `{valid_from, valid_until, variables, crs, bbox, resolution_x, resolution_y}`.
- A `CompositeDownloader(Downloader, ABC)` abstract base class was added to
  `datavia/core/interfaces.py`. Both `CompositeWeatherDownloader` and
  `packages/soil/datavia/soil/composite_downloader.py` now extend it and implement
  the required `downloaders` property.

---

## Steps

### ✅ Database layer

`datavia/library/database/init.sql` — `weather_layers` table and index added:

`datavia/library/database/start.py` — removed the `has_table("raster_layers")`
guard that prevented `weather_layers` from being created on existing databases.
All `CREATE TABLE/INDEX IF NOT EXISTS` statements now always run (idempotent).

### ✅ Weather query functions — `datavia/library/database/query.py`

- `get_weather_paths(source_name, variable, from_dt, to_dt) → list[str]`
- `get_weather_metadata(source_name, variable=None) → list[dict]`
- `check_weather_source_exists(source_name, variable=None, from_dt=None, to_dt=None) → bool`

### ✅ Core library additions

**`datavia/library/interpolation.py`**

- `interpolate_netcdf(nc_path, lat, lon, variable, datetime_utc)`
- `blend_gridded_and_station(gridded_value, station_value, station_weight=0.6)`

**`datavia/library/formats.py`**

- `extract_netcdf_layer_metadata(filepath) → dict` — returns
  `{valid_from, valid_until, variables, crs, bbox, resolution_x, resolution_y}`.
- `write_parquet(records, path)` — writes a list of dicts to Parquet via pandas.
- `read_parquet_time_range(path, variable, from_dt, to_dt) → DataFrame`

### ✅ `CompositeDownloader` ABC — `datavia/core/interfaces.py`

New abstract base class `CompositeDownloader(Downloader, ABC)`:
- Sets `url=""` (no single canonical endpoint).
- Requires subclasses to implement `@property downloaders() → list[Downloader]`.
- Used by both weather and soil composite downloaders.

### ✅ Interface implementations — `packages/weather/datavia/weather/`

**`saver_weather.py`** — `SaverWeather(Saver)`:
- `save(data_path, …) → bool` — copies file to `data/{source_name}_{stem}.{ext}`,
  infers format from extension, extracts temporal metadata, inserts into `weather_layers`.
- `check_data_exists(variable, from_dt, to_dt) → bool`
- `sync_files_and_database() → bool` — reconciles files on disk with DB rows
  (same orphan-removal pattern as `TiffSaver`).

**`getter_weather.py`** — `GetterWeather(Getter)`:
- `get_existing_layers() → set[str]`
- `get_data(coords, …, **kwargs) → np.ndarray` — pipeline interface.
- `get_weather_data(lat, lon, variable, datetime_utc, …) → float` — dispatches to
  `interpolate_netcdf`, `interpolate_station_parquet`, or blends both.

### ✅ Package scaffold — `packages/weather/`

Follows the same namespace-package pattern (PEP 420) as elevation and soil.

- `pyproject.toml` — `datavia-weather`; core deps: `xarray`, `netCDF4`, `pyarrow`,
  `pandas`; optional `[era5]` extra: `cdsapi>=0.7`.
- `datavia/weather/__init__.py` — exports `WeatherPipeline`, `ERA5Downloader`,
  `DWDStationDownloader`, `CompositeWeatherDownloader`, `SaverWeather`, `GetterWeather`.
- `pipeline.py` — `WeatherPipeline(Pipeline)`, `name = "weather"`.
- `era5_downloader.py` — `ERA5Downloader(APIDownloader)`: calls
  `cdsapi.Client().retrieve(…)`, writes `.nc` to a temp file. Requires
  `~/.cdsapirc`. Note: date range is currently simplified — see open items.
- `dwd_downloader.py` — `DWDStationDownloader(APIDownloader)`: fetches from
  Open-Meteo (`https://api.open-meteo.com`), combines station records, writes
  `.parquet`.
- `composite_downloader.py` — `CompositeWeatherDownloader(CompositeDownloader)`:
  runs ERA5 then DWD, returns newline-joined file paths.

### ✅ Main package wiring

- `pyproject.toml` (root) — `weather` optional-dep group includes `xarray`,
  `netCDF4`, `pyarrow`, `datavia-weather`.
- `packages/weather/pyproject.toml` — `cdsapi` moved to `[era5]` optional extra.
- `pixi.toml` — `xarray`, `netcdf4`, `pyarrow` added to conda deps; `datavia-weather`
  added as editable PyPI dep. `pixi install` completed successfully.

### ✅ Unit tests — `tests/test_weather_pipeline_unit.py`

26 tests across 6 classes, all passing without network access:

| Class | Tests |
|---|---|
| `TestBlendGriddedAndStation` | 5 — blending math and boundary conditions |
| `TestWeatherQueryHelpers` | 4 — DB round-trip with `sqlite_db` in-memory fixture |
| `TestSaverWeather` | 3 — save / idempotency / `check_data_exists` |
| `TestGetterWeather` | 6 — layers, error cases, NetCDF dispatch, scalar result |
| `TestCompositeWeatherDownloader` | 3 — `downloaders` property, path joining, error |
| `TestWeatherPipeline` | 4 — init, path splitting, failure modes |

Run with: `pytest tests/test_weather_pipeline_unit.py -v`

### ✅ Soil `CompositeDownloader` migration

`packages/soil/datavia/soil/composite_downloader.py` updated to extend
`CompositeDownloaderABC` (imported alias for `datavia.core.interfaces.CompositeDownloader`).
`super().__init__()` and the `downloaders` property implemented — now consistent with
the weather composite.

---

## Open items

### ⬜ E2E tests — `tests/test_weather_e2e.py`

Gated by `DATAVIA_E2E=1`, mirroring `test_soil_e2e.py`:

- Live ERA5 download for a small bounding box and short date range (requires
  valid `~/.cdsapirc`).
- Live DWD station download for Germany via Open-Meteo (no auth needed).
- Point query for a known coordinate + datetime:
  `GetterWeather.get_data(lat=52.5, lon=13.4, variable="temperature_2m", datetime_utc=…)`
  should return a finite float in a realistic range.

### ⬜ ERA5 date-range generation

`ERA5Downloader.download()` currently passes simplified year/month/day values to
`cdsapi`. For production use, generate proper lists from `date_start` to `date_end`
using `datetime.date` ranges, e.g.:

```python
days = [
    d.strftime("%Y-%m-%d")
    for d in (date_start + timedelta(n) for n in range((date_end - date_start).days + 1))
]
```

### ⬜ CLI smoke-test

The existing `datavia update weather` route calls `update_pipeline("weather", …)`
which should work once `datavia-weather` is installed. Verify end-to-end:

```bash
datavia update weather
# expect: data/era5_*.nc, data/dwd_stations*.parquet, rows in data/datavia.db
```

---

## Dependencies introduced

| Package | Purpose | Where |
|---|---|---|
| `xarray>=2024.0` | Read/slice NetCDF along time + space | `datavia-weather` + dev env |
| `netCDF4>=1.7` | xarray NetCDF backend | `datavia-weather` + dev env |
| `pyarrow>=14.0` | Parquet read/write for pandas | `datavia-weather` + dev env |
| `pandas>=2.2` | Station data manipulation | `datavia-weather` |
| `cdsapi>=0.7` | ERA5 downloads from Copernicus CDS | `datavia-weather[era5]` (optional) |
| Open-Meteo HTTP | DWD station observations | `datavia-weather` (stdlib `urllib`) |