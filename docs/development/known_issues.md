# Known Issues

> Consolidated list of confirmed bugs, design limitations, and open concerns
> for the `weather` branch (current state: 2026-05-22).
> Items marked **Fixed** have been resolved in the codebase; they are kept
> here for traceability.

---

## Bugs

### ✅ Bug C — Filename collision in `_build_dest_stem()` (fixed — 2026-04-28)

**File:** `packages/weather/datavia/weather/saver_weather.py`

`_build_dest_stem()` now encodes
`{source_name}_{variable}_{YYYYmm_start}_{YYYYmm_end}_{bbox_hash}` for NetCDF
files.  The bbox hash is the first six hex digits of the MD5 of the WKT
bounding-box string, so spatially distinct downloads for the same variable and
period always receive unique filenames.  Parquet (DWD station) files are named
by the minimum year in the datetime column.  See BUG-07 in the done table of
`weather_next_steps.md`.

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

### ✅ Bug 09 — Spatial gap-fill performed once, at write time

**Files:** `datavia/datavia/library/interpolation.py`,
`packages/weather/datavia/weather/zarr_store_manager.py`,
`packages/weather/datavia/weather/saver_weather.py`,
`packages/weather/datavia/weather/getter_weather.py`

The nearest-neighbour nodata fill described in Bug 08 runs exactly once, at
write time, not on every `get_data()` call:

- `ZarrStoreManager.write_dataset()` calls the shared `fill_spatial_gaps()`
  helper on each downloaded slab immediately before `to_zarr(...)`. For
  geographic (`region="auto"`) stores, whose skeleton latitude is descending
  (matching ERA5's native order), the slab is sorted ascending, filled, then
  restored to the store's original order so the region-aligned write still
  lines up.
- `SaverWeather.save()` calls `prepare_netcdf()` on non-Zarr NetCDF sources
  right after copying the file into storage.
- `interpolate_dataset()`/`interpolate_netcdf()` never fill gaps at query
  time; they assume the data they are given has already been gap-filled via
  `prepare_netcdf()` or `ZarrStoreManager.write_dataset()`. A raw `.nc` file
  that has not yet been migrated into a Zarr store returns un-filled `NaN`
  near nodata edges until the next `update_data()` run migrates it.

**Limitation:** gaps between separately-downloaded Zarr cells, and areas
never downloaded at all, remain `NaN` in `get_data()` results.

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

`WeatherPipeline` is documented as requiring:
```python
pipe = WeatherPipeline(config={...})  # 1. construct
pipe()                                 # 2. wire components
pipe.update_data()                     # 3. download
```
In practice, all three public methods (`update_data`, `get_weather_data`,
`get_data`) contain an `if not self.<component>: self()` guard and auto-wire
components on first use.  This means `pipe()` is never strictly required — but
this is not communicated in the public API or docstrings.  The `RuntimeError`
branches that follow each auto-wire guard are unreachable dead code.

**Recommendation:** either remove the explicit `__call__` step from all
documentation and examples (auto-wire is the de facto contract) or remove the
auto-wire guards and make `pipe()` a hard requirement enforced by a clear
error raised before any guard.

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
Available layers include 10-minute temperature, precipitation, radiation, and
sunshine duration (`CDC:OBS_DEU_PT10M_*`).  Note these are *station point
features*, not gridded rasters; for gridded DWD data, HYRAS is preferred.
No downloader implementation exists yet.

---

### ❌ Zarr store as future storage architecture

**Files affected:** `saver_weather.py`, `getter_weather.py`, `datavia/library/formats.py`, DB schema

The current approach saves one NetCDF file per download fragment.  As the data
store grows, `GetterWeather` must open and scan many files per query.  The
recommended long-term replacement is a Zarr directory store per
`(source, variable, year)`.  Each spatial tile fills its chunk files
in-place; writing the same chunk twice is idempotent; `xarray` reads the entire
store as one virtual array regardless of how many incremental downloads were
made.  File count is bounded to one store per `(source, variable, year)`
independent of download history, and filename collisions become impossible by
construction.

**Prerequisite:** Strategy 1 (unique `_build_dest_stem()`) already in place;
Zarr migration can be done as a later, independent step.

---

### ❌ Changelog version not finalised

`docs/changelog.rst` has an `[Unreleased] — 1.0.4` entry covering the full
weather pipeline.  The version number and release date should be set and the
entry promoted to a proper release once this branch is merged.

---

## Code Quality

### ✅ `URLDownloader` chunk size resolved

**File:** `datavia/core/downloader_url.py`

`chunk_size` changed from 4096 to 65536 (64 KB).  The original comment
*"Reduced chunk size for better handling of large files"* was incorrect — smaller
chunks hurt throughput without benefit for remote network downloads.  64 KB aligns
with typical TCP window sizes and reduces Python loop and syscall overhead.

---

### ✅ Progress bar added to `URLDownloader` (fixed)

**File:** `datavia/core/downloader_url.py`

`URLDownloader.download()` now uses `tqdm` for byte-level progress, covering
all pipelines that go through the core URL downloader (elevation, HYRAS, etc.).

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

### ✅ `scripts/` directory role resolved

**Directory:** `scripts/`

`version.sh` and `build_packages.sh` remain in `scripts/` — they are used by CI and must stay.
`test_docs_modern.py` and `test_docs_with_io.py` also stay in `scripts/` because doc-build
tests should not run alongside the main test suite.

The four weather example scripts (`example_weather_query.py`,
`grid_snapshot_germany_2025.py`, `heat_days_germany_2025.py`,
`precipitation_north_south_2025.py`) have been moved to
`docs/user_guide/examples/` and are referenced from `docs/user_guide/examples.rst`
via `literalinclude` directives (Examples 4–7).

---

*For future feature ideas and enhancement requests see
[ideas_for_next_issues.md](../../ideas_for_next_issues.md).*
