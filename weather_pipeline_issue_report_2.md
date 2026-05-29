# Weather Pipeline Issue Report — May 2026

Observed while running `weather_exp.py` on 2026-05-29. Two distinct failure modes are reported: one from the local run (all-NaN output), and one from a colleague's run on a different machine (`RuntimeError: No weather files found`).

---

## Bug 3 — ERA5 latitude is descending; `interpolate_na` requires ascending

**Observed by:** bergmi, running `weather_exp.py` on 2026-05-29.

### Error (console output)

```
NetCDF batch interpolation failed: Index 'latitude' must be monotonically increasing
[nan nan nan ... nan nan nan]
```

### What the user sees

The ERA5 download completes (2/2 monthly chunks), the files are copied and registered in the database, and `get_data()` returns silently — but the entire result array is `NaN`. No exception is raised; nothing in the log indicates data loss.

### Root cause

ERA5-Land files downloaded from the CDS API store the latitude dimension in **descending order** (North → South, e.g. 55.1, 55.0, ..., 47.3). This is the native CDS grid orientation.

`interpolate_netcdf()` in `datavia/library/interpolation.py` calls `interpolate_na` on the opened `DataArray` before bilinear interpolation:

```python
# interpolation.py, lines ~364–366
da = da.interpolate_na(dim=x_dim, method="nearest", fill_value="extrapolate")
da = da.interpolate_na(dim=y_dim, method="nearest", fill_value="extrapolate")
```

xarray's `DataArray.interpolate_na()` with `method="nearest"` requires the dimension coordinate to be **monotonically increasing**. ERA5 latitude is monotonically *decreasing*, so the second call raises:

```
ValueError: Index 'latitude' must be monotonically increasing
```

The exception propagates up to `GetterWeather.get_data()` in `getter_weather.py`, where it is caught silently:

```python
# getter_weather.py, lines ~303–304
except Exception as exc:
    logger.warning("NetCDF batch interpolation failed: %s", exc)
```

The `results` array — initialised to `np.full(n_coords, np.nan)` before the try-block — is returned unchanged. The caller receives all-NaN with no indication that anything went wrong beyond a `WARNING`-level log line that is easy to miss.

### Full call chain

```
WeatherPipeline.get_data()
  └─ GetterWeather.get_data()
       ├─ get_weather_paths()          ← returns the June .nc file (correct)
       ├─ interpolate_netcdf(nc_files[0], ...)
       │    └─ xr.open_dataset(nc_path)
       │         da.interpolate_na(dim="latitude", method="nearest", ...)
       │         → ValueError: Index 'latitude' must be monotonically increasing
       └─ except Exception as exc:
            logger.warning(...)        ← exception swallowed
            return np.full(n_coords, np.nan)   ← all-NaN returned silently
```

### Affected code

| File | Location | Issue |
|---|---|---|
| `datavia/library/interpolation.py` | `interpolate_netcdf()`, lines ~364–366 | `interpolate_na` called on a DataArray whose latitude is descending |
| `packages/weather/datavia/weather/getter_weather.py` | `get_data()`, `except Exception as exc` block | Broad `except` silences the `ValueError`; result is silent all-NaN |

### Suggested fix — `interpolation.py`

Sort each spatial dimension to ascending order **before** calling `interpolate_na`. xarray's `sortby()` is order-agnostic (works whether the coord is ascending or descending) and does not affect the interpolation result because `da.interp()` handles both orderings:

```python
# Before the interpolate_na calls, add:
da = da.sortby(x_dim)   # longitude — usually already ascending, but safe to sort
da = da.sortby(y_dim)   # latitude  — ERA5 is descending; this flips it to ascending
da = da.interpolate_na(dim=x_dim, method="nearest", fill_value="extrapolate")
da = da.interpolate_na(dim=y_dim, method="nearest", fill_value="extrapolate")
```

### Suggested fix — `getter_weather.py`

The `except Exception` catch in `get_data()` is too broad. It is designed to allow graceful degradation (fall through to station data), but it silently converts real bugs into all-NaN. At minimum the log level should be `ERROR`, and — because the interpolation failure is not a "partial data" situation but a total failure — the exception should be re-raised after the station path also produces no data:

```python
except Exception as exc:
    logger.error(
        "NetCDF batch interpolation failed for source='%s', variable='%s': %s",
        self.source_name, variable, exc, exc_info=True
    )
    # If no station fallback exists, propagate the failure instead of
    # returning silent NaN.
    if not parquet_files:
        raise
```

### Why it was not caught earlier

The HYRAS source stores latitude in **ascending** order (South → North in the projected ETRS89-LAEA grid). Tests and earlier use cases (8 and 9) used HYRAS or small ERA5 bboxes where the `interpolate_na` call happened to not raise. The descending-latitude issue only surfaces for ERA5.

---

## Bug 4 — `temperature_2m_max` / `temperature_2m_min` are HYRAS/DWD variables; passing them to an ERA5 pipeline silently corrupts the result

**Observed by:** colleague (samufi), running a script with the same config structure on a different machine.

### Error

```
RuntimeError: No weather files found for source='ERA5_land',
variable='2m_temperature', time=[2024-06-15T12:00:00Z, 2024-06-15T12:00:00Z].
Run the pipeline update first.
```

### Variable naming mismatch

`temperature_2m_max` and `temperature_2m_min` are **HYRAS pipeline variable names**, mapped from NetCDF variables `tasmax`/`tasmin` (daily max/min 2-m air temperature). They are defined in:

```python
# source_registry.py
"HYRAS": {
    "nc_variable_map": {
        "tasmax": "temperature_2m_max",   # HYRAS only
        "tasmin": "temperature_2m_min",   # HYRAS only
        ...
    }
}
```

The ERA5_land registry entry has **no entry** for either:

```python
"ERA5_land": {
    "nc_variable_map": {
        "t2m":  "2m_temperature",
        "tp":   "total_precipitation",
        "ssrd": "surface_solar_radiation_downwards",
    }
}
```

The CDS API variable names for temperature extremes are `maximum_2m_air_temperature` / `minimum_2m_air_temperature` — not the pipeline-level aliases.

### Failure path A — CDS API rejects unknown variable names

When `temperature_2m_max` and `temperature_2m_min` are sent verbatim to the CDS API:

```python
# era5_downloader.py
job = client.retrieve(
    "reanalysis-era5-land",
    {"variable": self.variables, ...}   # includes "temperature_2m_max"
)
```

If the CDS API returns an error for unrecognised variable names, `ERA5Downloader.download()` raises `RuntimeError`. This is caught silently in `CompositeWeatherDownloader.download()`:

```python
# composite_downloader.py
except (ImportError, RuntimeError) as exc:
    logger.warning(
        "CompositeWeatherDownloader: grid download skipped (%s). ...", exc
    )
```

With no DWD stations configured, `paths` stays empty → `return "failed"`.

Back in `WeatherPipeline.update_data()`:

```python
if combined_paths == "failed":
    logger.error("download failed for cell %s.", cell)
    all_saved = False
    continue
```

`update_data()` returns `False` but raises **no exception**. The calling script sees nothing wrong. The database is never populated. Subsequent `get_data()` raises `RuntimeError: No weather files found`.

### Failure path B — CDS API accepts the variables but `nc_variable_map` drops them

If the CDS API does accept `temperature_2m_max` (some API versions use different naming conventions), the downloaded file will contain NetCDF variables under internal ECMWF short names (`mx2t`, `mn2t`, etc.). These are **not in ERA5_land's `nc_variable_map`**, so `_resolve_variables()` filters them out:

```python
# saver_weather.py _resolve_variables()
nc_vars = [nc_var_map[v] for v in nc_vars if v in nc_var_map]
# mx2t → not in map → dropped
# mn2t → not in map → dropped
# t2m  → "2m_temperature" ✓
# tp   → "total_precipitation" ✓
```

In this path the DB is populated correctly for `2m_temperature` and `total_precipitation`, but `temperature_2m_max` / `temperature_2m_min` are silently unregistered and can never be retrieved. Asking `get_data()` for `temperature_2m_max` from an ERA5 pipeline will always raise `RuntimeError: No weather files found`, regardless of how many times `update_data()` is run.

### Additionally: `update_data()` failure is invisible to the caller

In both paths above, `update_data()` returns `False` without raising an exception. The caller has no way to detect the failure without inspecting the return value:

```python
result = pipeline.update_data()   # returns False on partial or complete failure
# no exception → caller thinks everything is fine
```

### Affected code

| File | Location | Issue |
|---|---|---|
| `packages/weather/datavia/weather/composite_downloader.py` | `download()`, `except (ImportError, RuntimeError)` | Grid downloader failure is demoted to a warning and swallowed |
| `packages/weather/datavia/weather/pipeline.py` | `update_data()`, `if combined_paths == "failed"` | Returns `False` instead of raising; caller cannot distinguish "nothing to do" from "everything failed" |
| `packages/weather/datavia/weather/source_registry.py` | `ERA5_land` entry | No entry for `temperature_2m_max` / `temperature_2m_min`; no validation prevents users from requesting HYRAS-only variables via an ERA5 pipeline |

### Suggested fixes

**1. Add source-variable validation in `WeatherPipeline.__init__()` / `CompositeWeatherDownloader`**

Cross-reference the requested variables against the source's known variable set (the `nc_variable_map` keys and the conversions dict) and raise immediately if an unknown variable is requested:

```python
# In WeatherPipeline.__init__() or CompositeWeatherDownloader.__init__()
valid_vars = set(SOURCE_REGISTRY.get(source, {}).get("nc_variable_map", {}).values())
# For ERA5: {"2m_temperature", "total_precipitation", "surface_solar_radiation_downwards"}
bad_vars = [v for v in config["variables"] if v not in valid_vars]
if bad_vars:
    raise ValueError(
        f"Variables {bad_vars} are not valid for source '{source}'. "
        f"Valid variables: {sorted(valid_vars)}"
    )
```

**2. Make `update_data()` raise on complete failure**

```python
if not all_saved:
    raise RuntimeError(
        f"WeatherPipeline '{self.name}': one or more cells failed to download. "
        "Check logs for details."
    )
```

---

## Bug 5 — Only `nc_files[0]` is used when multiple NetCDF files cover the query window

### Description

In `GetterWeather.get_data()`, after `get_weather_paths()` returns a list of matching NetCDF files, only the **first** file is passed to `interpolate_netcdf`:

```python
# getter_weather.py
if nc_files:
    ...
    raw_batch = interpolate_netcdf(
        nc_files[0],    # ← only first file; remaining files ignored
        lats, lons, nc_variable, datetime_utc, ...
    )
```

`get_weather_paths()` returns all NetCDF files whose `valid_from`/`valid_until` window overlaps the requested datetime. If a query spans multiple monthly files (e.g. a multi-timestamp request crossing a month boundary), only the first file is queried and the remaining files are silently ignored, producing NaN for all timestamps outside the first file's coverage.

### When this triggers

- Multi-timestamp `get_data()` calls that span more than one monthly chunk (e.g. `datetime_utc=["2024-06-30", "2024-07-01"]`)
- Any scenario where `get_weather_paths()` returns more than one `.nc` file for a single query

### Note on single-timestamp queries

For the specific `weather_exp.py` case (`datetime_utc="2024-06-15T12:00:00Z"`), `get_weather_paths()` returns exactly one file (the June chunk), so this bug does not compound the June 15 failure. It is a latent defect for batched time-series calls.

### Suggested fix

Concatenate all returned NetCDF files along the time dimension before interpolating:

```python
if nc_files:
    import xarray as xr
    ds_list = [xr.open_dataset(p) for p in nc_files]
    combined_nc = xr.concat(ds_list, dim="time")  # or "valid_time"
    # then pass to interpolate_netcdf (or inline the interpolation on combined_nc)
```

A simpler short-term fix: change `interpolate_netcdf` to accept a list of paths, open them all, concatenate, and proceed.

---

## Combined impact of Bugs 3 and 4 on `weather_exp.py`

| User | What happened | Root cause |
|---|---|---|
| bergmi | Download completed (2/2 chunks). `get_data()` returned all-NaN silently. | Bug 3 — ERA5 latitude is descending; `interpolate_na` raised `ValueError` which was swallowed. |
| samufi (friend) | `get_data()` raised `RuntimeError: No weather files found`. | Bug 4 — most likely `temperature_2m_max`/`min` caused a CDS API failure; `update_data()` returned `False` silently and nothing was registered in the DB. Alternatively, the CDS API accepted the variables but the friend's `update_data()` was not called, or a save step failed with NULL timestamps. |

---

## Bug 6 — DWD Parquet filename collision: one use case silently overwrites another's cached data

**Observed by:** bergmi, 2026-05-29. `pixi run UC5` followed by `pixi run UC3`.

### Symptoms

UC3 (*Altitude vs Temperature*) ran successfully the first time. After running UC5 (*Tri-Pipeline Portrait*), UC3 was run again and printed:

```
Weather data already cached. ✓
WARNING: No stations found within 120.0 km of (53.5500, 10.0000) in '...DWD_stations_2024.parquet'
  Hamburg    : no data
  Berlin     : no data
  Cologne    : no data
  ...
  Garmisch-P.: 16.8°C
ERROR: not enough valid data pairs to plot.
```

Only Garmisch-P. returned data (the one city close enough to Munich). All others were NaN.

### Root cause

`_build_dest_stem()` in `saver_weather.py` generates the permanent filename for every DWD Parquet file as:

```python
f"{source_name}_{year}"   # e.g. "DWD_stations_2024"
```

where `year` is derived from the earliest timestamp in the download. Any two DWD downloads whose date range falls in the same calendar year produce **the same filename** — `DWD_stations_2024.parquet` — regardless of which stations or variables they contain.

Both UC3 and UC5 use `source="DWD_stations"` with dates in 2024:

| Use case | Stations | Variables | Dates |
|---|---|---|---|
| UC3 | 8 German cities | `temperature_2m` | 2024-07-17 |
| UC5 | 6 German cities (Munich-centric) | `temperature_2m`, `precipitation`, `relative_humidity_2m`, `wind_speed_10m` | 2024-08-05 to 2024-08-11 |

When UC5 runs, `SaverWeather.save()` copies its Parquet to `DWD_stations_2024.parquet`, overwriting UC3's file. However, the DB cleanup only deletes rows with an **exact `layer_name` match**. UC5 produces multi-variable rows with suffixed names (`DWD_stations_2024_temperature_2m`, `DWD_stations_2024_precipitation`, …), so UC3's old row (`DWD_stations_2024` without suffix) is **never deleted**.

After UC5:
- **File on disk**: UC5's Munich-only, 4-variable, August 2024 data.
- **DB**: UC3's stale row still claims `valid_from=2024-07-17T00:00:00`, pointing at the same overwritten file.

When UC3 runs again, `check_data_exists()` finds the stale DB row (timestamp matches) → "already cached ✓". `get_weather_paths()` returns the path to the file. The file is opened, but it contains only Munich. `interpolate_station_parquet()` finds no stations within 120 km of Hamburg, Berlin, etc. → silent NaN for all cities except Garmisch-P.

### Full sequence

```
pixi run UC3 (first time)
  DWD download: 8 cities, July 17 → DWD_stations_2024.parquet
  DB row: layer_name="DWD_stations_2024", variable="temperature_2m", valid_from=2024-07-17
  UC3 works ✓

pixi run UC5
  CoverageManager detects missing data for Munich/August
  DWD download: Munich only, Aug 5-11 → temp file → shutil.copy2 → DWD_stations_2024.parquet  ← OVERWRITES
  DB: inserts "DWD_stations_2024_temperature_2m" (new row, different layer_name)
  DB: "DWD_stations_2024" (UC3's row) is NOT deleted — layer_name doesn't match

pixi run UC3 (second time)
  check_data_exists("temperature_2m", "2024-07-17") → True  ← finds stale UC3 row
  "Weather data already cached. ✓"  ← skips download
  File is read → contains only Munich
  Hamburg etc. → no stations within 120 km → NaN
  ERROR: not enough valid data pairs
```

### Affected code

| File | Location | Issue |
|---|---|---|
| `packages/weather/datavia/weather/saver_weather.py` | `_build_dest_stem()`, Parquet branch | Year-only stem produces identical filenames for all DWD downloads in the same year |
| `packages/weather/datavia/weather/saver_weather.py` | `_insert_weather_layer()` | `DELETE WHERE layer_name = :layer_name` only removes the exact row; a different `layer_name` from a competing pipeline is never cleaned up |

### Suggested fix

Include the station set and date range in the filename stem so each unique download gets a unique name:

```python
# In _build_dest_stem(), Parquet branch:
df = pd.read_parquet(data_path, columns=["datetime", "station_id"])
year_start = str(df["datetime"].min().year)
year_end   = str(df["datetime"].max().year)
# Hash the sorted station list for a stable 6-char suffix.
import hashlib
station_ids = sorted(df["station_id"].unique().tolist())
station_hash = hashlib.md5(str(station_ids).encode(), usedforsecurity=False).hexdigest()[:6]
return f"{source_name}_{year_start}_{year_end}_{station_hash}"
```

This makes collisions structurally impossible for downloads with different station sets or year spans, at the cost of one extra Parquet column read during save.

---

## Priority

| # | Bug | Severity | Scope |
|---|---|---|---|
| 3 | `interpolate_na` descending latitude → all-NaN | **High** | Every ERA5 `get_data()` call |
| 4 | HYRAS variables in ERA5 pipeline → silent "no files" | **High** | Any config mixing HYRAS/DWD variable names with ERA5 source |
| 5 | Only `nc_files[0]` used for multi-file queries | **Medium** | Multi-timestamp batched queries crossing month boundaries |
| 6 | DWD Parquet filename collision → stale cache returns wrong station set | **High** | Any two DWD pipelines in the same calendar year running in the same workspace |

Bug 3 is the highest priority because it silently returns wrong data (all-NaN) for every `get_data()` call on an ERA5 pipeline, regardless of bounding box or variable choice. Bug 6 is equally dangerous in multi-use-case workspaces because it also silently returns wrong data.

