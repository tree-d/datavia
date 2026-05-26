# Known Issues

> Consolidated list of confirmed bugs, design limitations, and open concerns
> for the `weather` branch (current state: 2026-05-22).
> Items marked **Fixed** have been resolved in the codebase; they are kept
> here for traceability.

---

## Bugs

### ⚠️ Bug C — Filename collision in `_build_dest_stem()` (open)

**File:** `packages/weather/datavia/weather/saver_weather.py`

`_build_dest_stem()` encodes only `(source_name, variable, year)` in the
destination filename.  Two downloads for the same variable and year with
different bounding boxes or different monthly chunks can produce the same
stem, causing the second `shutil.copy2()` call to silently overwrite the
first file on disk and the second DB insert to register a duplicate row.

**Impact:** incremental spatial coverage breaks; re-running `update_data()`
with a shifted `era5_bbox` does not extend coverage — it overwrites existing
data.

**Planned fix (Strategy 1):** extend the stem with a `YYYYMM` range suffix and
a short hex hash of the bounding box.  See
[weather_file_management_strategies.md](weather_file_management_strategies.md)
for a full comparison of six approaches.

---

### ✅ Bug B — `valid_time` coordinate in ERA5 NetCDF files (fixed)

Newer `cdsapi >= 0.7` releases name the time dimension `valid_time` instead of
`time`.  `interpolate_netcdf()` now checks for both names transparently.

---

### ✅ Bug 01 — Stale DB rows blocked incremental downloads (fixed)

`update_data()` now calls `sync_files_and_database()` before computing the
coverage gap, so stale rows for deleted files no longer cause the pipeline to
skip downloads that are actually needed.

---

### ✅ Bug 06 — Year-end `valid_until` too early for HYRAS precipitation (fixed)

HYRAS precipitation stores the last daily value at 06:00 UTC on 31 December.
`valid_until` is now rounded up to `23:59:59` on the last day so that
full-year queries succeed.

---

### ✅ Bug 07 — Temp-file stem leaked into permanent filenames (fixed)

Destination filenames are now derived from file **content** (source, variable,
year) rather than from the random `tmp*` stem produced by `tempfile.mkstemp`.

---

### ✅ Bug 05 — Per-point file-open overhead in `interpolate_netcdf` (fixed)

`interpolate_netcdf()` now opens each NetCDF file exactly once and interpolates
all `N` coordinate points in a single vectorised `xarray.DataArray.interp()`
call.

---

### ✅ Bug 08 — NaN propagation at domain edges during interpolation (fixed)

Nodata cells are replaced with the nearest valid neighbour (cardinal-direction
propagation) before bilinear interpolation so that stencils touching the domain
boundary always receive a finite value.

---

## Design Limitations

### ⚠️ Newline-joined path string between composite downloader and pipeline

**File:** `packages/weather/datavia/weather/composite_downloader.py` →
`packages/weather/datavia/weather/pipeline.py`

`CompositeWeatherDownloader.download()` returns multiple file paths as a single
newline-joined `str`; `WeatherPipeline.update_data()` splits it with
`.splitlines()`.  This is an informal internal protocol not enforced by the
`Downloader` ABC (which only specifies a `str` return).  Any future downloader
that incidentally returns a multi-line string for a different reason would be
misinterpreted.

**Preferred future direction:** introduce a `MultiPathDownloader` protocol or
change the return type to `list[str]` and update the ABC.

---

### ⚠️ `CoverageManager` is reconstructed on every `update_data()` call

**File:** `packages/weather/datavia/weather/pipeline.py`

A new `CoverageManager` instance (which queries `weather_layers`) is created
inside `update_data()` on every call.  For typical usage this is acceptable,
but a scheduler that calls `update_data()` frequently would issue O(n) redundant
DB queries.

**Mitigation:** cache the manager as a pipeline attribute and invalidate it
after a successful save, or push the coverage check into a lazy property.

---

### ⚠️ ERA5 + DWD station blending is scientifically questionable

**File:** `packages/weather/datavia/weather/getter_weather.py`

ERA5-Land is a data-assimilation product that already incorporates DWD station
observations.  Blending its output with raw station data via
`blend_gridded_and_station()` effectively double-counts the same measurements.
The hardcoded `station_weight=0.6` has no literature citation.

**Recommendation:** restrict station blending to HYRAS sources (pure
observational gridding, no reanalysis background), or replace the fixed weight
with a distance- or uncertainty-based scheme.  Track this as a scientific
concern in the project backlog.

---

### ⚠️ Open-Meteo licensing for `DWDStationDownloader`

**File:** `packages/weather/datavia/weather/dwd_downloader.py`

The Open-Meteo free-tier API used by `DWDStationDownloader` restricts use to
**non-commercial** purposes.  UFZ / Helmholtz grant-funded research is a legal
grey area.  Options:

1. Subscribe to the Open-Meteo commercial tier.
2. Replace with [`wetterdienst`](https://github.com/earthobservations/wetterdienst)
   (pulls from DWD CDC directly; CC BY 4.0).
3. Drop station blending for ERA5 sources (see scientific concern above) and
   limit `DWDStationDownloader` to HYRAS-only workflows.

---

### ⚠️ Silent `None` fallback for HYRAS downloader in `source_registry.py`

**File:** `packages/weather/datavia/weather/source_registry.py`

`HYRASDownloader` is imported inside a `try/except ImportError` block at module
load time.  If the import fails for any reason other than the module not
existing (e.g. a syntax error or a transitive import error), the registry
silently sets `"grid_downloader": None` for `"HYRAS"` and the user sees a
confusing "HYRAS downloader unavailable" message at runtime with no indication
of the root cause.

**Preferred fix:** move to a lazy import inside `get_grid_downloader_class()`
so import errors surface with a full traceback.

---

### ⚠️ `temporal_resolution` accepted by `HYRASDownloader` but only `"daily"` works

**File:** `packages/weather/datavia/weather/hyras_downloader.py`

The `temporal_resolution` parameter is validated (`"hourly"` raises immediately)
and stored, but no aggregation or sub-daily mode is implemented.  The parameter
exists for forward compatibility.  A user passing `"daily"` explicitly receives
no different behaviour from the default, which is fine — but passing any other
string value silently succeeds and is ignored.

---

### ⚠️ Three-step initialisation pattern can be confusing

**File:** `packages/weather/datavia/weather/pipeline.py`

`WeatherPipeline` requires:
```python
pipe = WeatherPipeline(config={...})  # 1. construct
pipe()                                 # 2. wire components
pipe.update_data()                     # 3. download
```
`get_weather_data()` and `get_data()` auto-wire components if step 2 is skipped,
but `update_data()` only auto-wires and then immediately raises `RuntimeError`
if the downloader is still `None`.  The two paths are inconsistent; the
`RuntimeError` branches after the auto-init guard are currently unreachable.

**Recommendation:** either always auto-wire on first use (remove the explicit
`__call__` requirement from the public API) or remove the auto-wire guards and
document clearly that `pipe()` is mandatory.

---

## Deferred Features

### ❌ `SaverWeather.rename_all(old_source_name, new_source_name)`

Atomically rename all managed files and update every matching `weather_layers`
row in a single transaction.  Needed when a pipeline is renamed after data is
already on disk.

---

### ❌ `Datavia.check_pipelines()`

Read-only orchestrator audit — raises `DataviaConsistencyError` on name
collisions, missing files, or declared coverage with no DB rows.

---

### ❌ Potential Evapotranspiration (PET) not implemented

Neither Penman-Monteith over forest nor FAO-56 over reference grass is
available.  HYRAS and ERA5 both provide the necessary inputs (temperature,
radiation, humidity).

---

### ❌ Automatic deduplication of data files

`sync_files_and_database()` handles orphaned DB rows and orphaned on-disk files
but does **not** detect or remove duplicate files (same content, different
name).  This affects all pipeline types (TIFF, NetCDF, Parquet).

**Open question:** which file is "canonical" when two are byte-identical?  A
content hash (e.g. MD5 of the first 64 KB) stored alongside the DB row would
make deduplication deterministic.  Testing strategy is also open — the fixture
would need to manufacture identical files.

---

### ❌ `cdc.dwd.de/geoserver/` as additional DWD data source

The WFS 2.0 endpoint `https://cdc.dwd.de/geoserver/ows` exposes high-resolution
observational grids and point data under CC BY 4.0 (no licensing concerns).
Research and example queries are in
[weather_sources_for_tree_modelling.md](weather_sources_for_tree_modelling.md).
No downloader implementation exists yet.

---

### ❌ Changelog not updated for current branch

`docs/changelog.rst` has an `[Unreleased]` entry for the SQLite migration.
The last stable release is `1.0.0`.  Update once this branch is merged.

---

## Code Quality

### ⚠️ `URLDownloader` chunk size undocumented

**File:** `datavia/core/downloader_url.py`

`URLDownloader.__init__()` sets `self.chunk_size = 4096` with the comment
*"Reduced chunk size for better handling of large files"*.  The reason for the
reduction and its effect on performance have not been investigated or documented.
A benchmark against the default `requests` stream chunk size (8192 B) is needed.

---

### ⚠️ Progress bar missing from `URLDownloader` and TIFF-level downloaders

**File:** `datavia/core/downloader_url.py`

`tqdm` is used in `era5_downloader.py` for chunk-level progress.  The core
`URLDownloader` (used by elevation and HYRAS) has no progress bar, so large
TIFF or annual NetCDF downloads give no visual feedback.  Adding `tqdm` to
`URLDownloader.download()` would benefit all pipelines uniformly.

---

### ⚠️ `get_remote_available_properties` not part of the core interface

**Files:** `datavia/core/interfaces.py`,
`packages/soil/datavia/soil/downloader.py`

The method exists on `SoilGridsDownloader`, `HiHydroSoilDownloader`, and the
composite soil downloader, but is not declared in the `Downloader` ABC.  Code
that needs to call it must either perform an `isinstance` check or accept a
duck-typed duck call, which is fragile.

**Decision needed:** promote to the ABC with a default `raise
NotImplementedError` body, or leave as a soil-specific capability.

---

### ⚠️ `datetime` absent from the core interface contract

**File:** `datavia/core/interfaces.py`

`Downloader`, `Saver`, and `Getter` have no temporal field.  Temporal context
is carried implicitly via `valid_from`/`valid_until` in the `weather_layers`
DB schema and via `datetime_utc` keyword arguments to `get_data()`.  This
makes temporal pipelines (weather) feel bolted on compared to spatial pipelines
(elevation, soil).

**Decision needed:** add an optional `datetime` concept to the interface
contract, or document explicitly that temporal context is a weather-pipeline
extension and will not be generalised.

---

### ⚠️ Ruff ignore rules in `pyproject.toml` not fully reviewed

**File:** `pyproject.toml`

Current blanket ignores: `E501`, `B008`, `N999`.  Per-file ignores for tests:
`D102`, `D103`, `PLC0415`, `I001`.  Per-file ignore for packages: `PLC0415`.
Some of these were added to silence noise during rapid development.  A
systematic pass should verify each ignore is still justified and remove those
that are not.

---

### ⚠️ `scripts/` directory role unclear

**Directory:** `scripts/`

`version.sh` and `build_packages.sh` are used in CI and must stay.
`benchmark_era5_chunking.py`, `example_weather_query.py`, and the annual
analysis scripts (`grid_snapshot_germany_2025.py`, `heat_days_germany_2025.py`,
`precipitation_north_south_2025.py`) are usage examples or one-off analyses.
Decide whether to move example scripts to `docs/user_guide/` as executable
notebooks or keep them in `scripts/` with a clear README.

---

*For future feature ideas and enhancement requests see
[ideas_for_next_issues.md](../../ideas_for_next_issues.md).*
