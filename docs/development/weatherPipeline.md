# Plan B: Weather Pipeline
TL;DR
The weather pipeline follows the same three-part pattern as elevation and soil (Downloader → Saver → Getter) but uses NetCDF for gridded ERA5/ICON data and Parquet for DWD station time series, since neither is a static spatial raster. A new weather_layers table in the shared SQLite DB tracks every downloaded file with temporal valid_from/valid_until columns. At query time, GetterWeather blends station and gridded data at the requested coordinates.

## Steps

### Database layer

Extend init.sql with a new weather_layers table:
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
layer_name TEXT NOT NULL — e.g. era5_temperature_2m
source_name TEXT — era5, dwd_stations, dwd_icon, etc.
variable TEXT — e.g. temperature_2m, precipitation, wind_speed_10m
file_format TEXT — netcdf or parquet
valid_from TEXT (ISO-8601 datetime)
valid_until TEXT (ISO-8601 datetime)
uri TEXT — absolute path to the .nc or .parquet file
acquisition_time TEXT
bbox TEXT — WKT footprint (Germany bounding box for gridded; NULL for station files)
crs TEXT
metadata TEXT — JSON blob for extra fields (grid resolution, station count, etc.)
Index on (source_name, variable, valid_from, valid_until)
```

### Add weather query functions to query.py:

get_weather_paths(source_name, variable, from_dt, to_dt) → list of uri where valid_from ≤ to_dt AND valid_until ≥ from_dt
get_weather_metadata(source_name, variable) → list of metadata dicts
check_weather_source_exists(source_name, variable) → bool

### Core library additions

Create datavia/library/weather/ directory with:
__init__.py
interpolation.py:
interpolate_netcdf(nc_path, lat, lon, variable, datetime_utc) → scalar or array — opens with xarray, selects nearest time step, bilinear-interpolates to the coordinate
interpolate_station_parquet(parquet_path, lat, lon, variable, datetime_utc, radius_km) → scalar — loads station records within a radius, inverse-distance-weighted average
blend_gridded_and_station(gridded_value, station_value, station_weight) → scalar — simple weighted average (station data is typically higher quality near coords where it exists)
formats.py:
read_netcdf_metadata(nc_path) → dict of {valid_from, valid_until, variables, crs, bbox, resolution_x, resolution_y} — reads CF conventions metadata from the NetCDF file without loading the full array
write_parquet(records, path) — writes a list of station observation dicts to a Parquet file via pandas.DataFrame.to_parquet()
read_parquet_time_range(path, variable, from_dt, to_dt) → DataFrame

### Core interface implementations

#### Create datavia/core/saver_weather.py implementing Saver:

SaverWeather(source_name, data_dir)
save(raw_path, file_format) — copies file to data/{source_name}_{stem}.{ext}, reads temporal metadata via formats.read_netcdf_metadata() or infers it from Parquet contents, inserts a row into weather_layers
check_data_exists(variable, from_dt, to_dt) → bool — calls check_weather_source_exists() narrowed by time range
sync_files_and_database() — scans data/{source_name}_*.nc and data/{source_name}_*.parquet, reconciles against DB rows (same orphan-removal pattern as TiffSaver.sync_files_and_database())

#### Create datavia/core/getter_weather.py implementing Getter:

GetterWeather(source_name)
get_existing_layers() → calls get_weather_metadata(source_name)
get_data(lat, lon, variable, datetime_utc) → float | np.ndarray:
Queries get_weather_paths(source_name, variable, ...) to find relevant files
If NetCDF files found: calls interpolate_netcdf()
If Parquet files found: calls interpolate_station_parquet()
If both: blends with blend_gridded_and_station()
Returns a float for a single datetime or np.ndarray for a list

### Package scaffold

**Create packages/weather/ following the same namespace-package pattern as elevation and soil:**
packages/weather/pyproject.toml — declares datavia-weather, deps: datavia>=1.0.0.dev0, xarray>=2025.0, netCDF4>=1.7, pyarrow>=19.0, cdsapi>=0.7 (ERA5), pandas>=2.2
packages/weather/datavia/weather/__init__.py
packages/weather/datavia/weather/pipeline.py — WeatherPipeline(Pipeline) composing downloader + saver + getter, with a name = "weather" attribute
packages/weather/datavia/weather/era5_downloader.py — ERA5Downloader(Downloader): uses cdsapi.Client to request hourly ERA5 data for Germany bounding box for a date range; saves as .nc; requires ~/.cdsapirc credentials (document this)
packages/weather/datavia/weather/dwd_downloader.py — DWDStationDownloader(Downloader): fetches DWD station observations (temperature, precipitation etc.) using the open-meteo or wetterdienst Python library; saves as .parquet
packages/weather/datavia/weather/composite_downloader.py — CompositeWeatherDownloader: orchestrates ERA5 + DWD downloads in sequence, same pattern as soil's CompositeDownloader

### Main package wiring

Add xarray, netCDF4, pyarrow to pyproject.toml under [project.optional-dependencies] key weather, alongside datavia-weather
Wire up the weather CLI path in cli.py: the existing --weather update command already calls update_pipeline("weather", ...) — it will work once datavia-weather is installed
Update pixi.toml to add xarray, netCDF4, pyarrow to the dev environment

### Tests

Create tests/test_weather_pipeline_unit.py (mirroring test_soil_pipeline_unit.py):

Test SaverWeather.save() with a mock NetCDF file and verify weather_layers DB insert (using sqlite:///:memory: fixture from conftest.py)
Test GetterWeather.get_data() with a pre-seeded DB row and a mock interpolate_netcdf() call
Test blend_gridded_and_station() directly with known values
Create tests/test_weather_e2e.py (gated by DATAVIA_E2E=1, mirroring test_soil_e2e.py):

Live ERA5 download for a small bounding box and short date range
Live DWD station download for Germany
Point query for a known coordinate + datetime, verify result is a finite float in a realistic range

### New dependencies introduced

Package	Purpose	Where
xarray	Read/slice NetCDF along time + space dimensions	datavia-weather + dev env
netCDF4	xarray NetCDF backend	datavia-weather + dev env
pyarrow	Parquet read/write backend for pandas	datavia-weather + dev env
cdsapi	ERA5 downloads from Copernicus CDS	datavia-weather
wetterdienst or Open-Meteo HTTP	DWD station data	datavia-weather

### Verification

pytest tests/test_weather_pipeline_unit.py -v — passes without internet
DATAVIA_E2E=1 pytest tests/test_weather_e2e.py -v — passes with valid ~/.cdsapirc
datavia update weather produces data/era5_temperature_2m.nc, data/dwd_stations.parquet, and rows in data/datavia.db under weather_layers
GetterWeather.get_data(lat=52.5, lon=13.4, variable="temperature_2m", datetime_utc=...) returns a float (Berlin)