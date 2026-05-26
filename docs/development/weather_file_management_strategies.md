# Weather File Management — Strategy Comparison

> **Context (2026-05-08):** Bug 3 from [era5_pipeline_issue_report.md](era5_pipeline_issue_report.md)
> identified that `_build_dest_stem()` encodes only `(source, variable, year)` in the
> filename.  Two ERA5 downloads for the same variable and year but different bounding
> boxes or months produce the same filename and the second silently overwrites the
> first.  This document compares all strategies considered for solving the broader
> "many fragment files" problem, from minimal fixes to full architecture replacements.

---

## Current state (baseline)

```
Download 1 → ERA5_land_t2m_2024.nc   (Germany, July)
Download 2 → ERA5_land_t2m_2024.nc   (Thuringia, June)  ← overwrites Download 1
```

`_build_dest_stem()` in `saver_weather.py`:

```python
return f"{source_name}_{nc_vars[0]}_{year}"
```

`CoverageManager`'s 4-strip spatial subtraction logic is correct but relies on each
downloaded tile receiving a unique filename.  As long as filenames collide, incremental
spatial coverage is broken.

---

## Strategy 1 — Unique filename with month range + bbox hash

**Category:** minimal fix / patch  
**Files affected:** `saver_weather._build_dest_stem()` only

### How it works

Extend the stem to include a `YYYYMM`-range (from `valid_from` / `valid_until`, both
already available inside `_build_dest_stem()` via `extract_netcdf_layer_metadata()`)
and a short hash of the bbox WKT (also returned by `extract_netcdf_layer_metadata()`):

```python
import hashlib, json

month_start = valid_from[5:7] if valid_from else "XX"
month_end   = valid_until[5:7] if valid_until else "XX"
year_start  = valid_from[:4]   if valid_from else "unknown"
year_end    = valid_until[:4]  if valid_until else "unknown"
bbox_tag    = hashlib.md5(json.dumps(sorted(bbox)).encode()).hexdigest()[:6]

return f"{source_name}_{nc_vars[0]}_{year_start}{month_start}_{year_end}{month_end}_{bbox_tag}"
# e.g.  ERA5_land_t2m_202406_202407_a3f91c.nc
```

### Pros
- One-function change, zero new dependencies, zero schema change.
- `CoverageManager` continues to work unchanged.
- Human-readable time range in the filename aids manual inspection.

### Cons
- File count still grows with every incremental download — no consolidation.
- `GetterWeather` must open potentially many files per query (already the case today).
- Bbox hash is opaque; not immediately obvious what spatial area a file covers.

### When to prefer
Immediate, low-risk fix that unblocks the existing pipeline.  Suitable as a
first step before any of the more architectural approaches below.

---

## Strategy 2 — Fixed spatial tile grid (snap-to-grid)

**Category:** CoverageManager refactor  
**Files affected:** `coverage_manager.py`, `saver_weather._build_dest_stem()`

### How it works

Before issuing any download, snap the requested bbox to a fixed tile grid (e.g. 2°×2°
in EPSG:4326).  Every download for tile `[lon 10–12, lat 50–52]` always produces
`ERA5_land_t2m_2024_10-12_50-52.nc` — independent of what was requested.

```
Request: lon 10.4–11.1, lat 51.1–55.5
Snapped tiles: [10-12, 50-52], [10-12, 52-54], [10-12, 54-56]
```

`CoverageManager` operates at tile granularity rather than arbitrary bboxes.  Each
tile is either fully covered or fully missing — no 4-strip partial remainder logic needed.

### Pros
- Collision is impossible by construction.
- File names are stable and human-readable.
- Simplifies `CoverageManager` significantly (full tile = covered, not tile = missing).
- Tile cache is reusable across all pipelines and projects sharing the same data directory.

### Cons
- May download a slightly larger area than requested (bbox rounded up to tile boundaries).
- Tile size is a design choice with trade-offs: small tiles → more files and more CDS
  requests; large tiles → more wasted bandwidth on edge cases.
- Requires a breaking change to `CoverageManager` and `_build_dest_stem()`.

### When to prefer
When the use case is repeated queries over a stable geographic region (e.g. all of
Germany) and minimising CDS API calls matters more than download precision.

---

## Strategy 3 — WeatherFilesMaintainer (post-download consolidation)

**Category:** new maintenance class, separate from the download pipeline  
**Files affected:** new `maintainer_weather.py`; `saver_weather.py` (atomic write helper)

### How it works

A new `WeatherFilesMaintainer` class runs independently of `update_data()` — it is
called manually (or optionally at pipeline end) and operates only on already-saved
files.  It provides the following operations:

| Method | What it does |
|---|---|
| `merge_fragments(source, variable, dry_run)` | Merge spatially/temporally adjacent files into one using `xr.combine_by_coords()`.  Atomic write (temp → `os.replace`).  Updates DB rows in a single transaction. |
| `coarsen_old_data(source, variable, older_than_days, target_freq, dry_run)` | Resample time axis of old files (e.g. hourly → daily mean) to shrink cold storage. |
| `audit_consistency(source, variable)` | Re-read each file's actual `valid_from`, `valid_until`, bbox and compare to DB row.  Flag divergences without modifying anything. |
| `check_integrity(source, variable)` | Open each file with `xr.open_dataset()` and verify expected variables and coordinate axes are non-empty.  Quarantine broken files. |
| `detect_gaps(source, variable, bbox, date_start, date_end)` | Return `(date_start, date_end)` ranges with no registered coverage.  Read-only. |
| `rechunk(source, variable)` | Rewrite files with time-first chunk layout for faster point-query access by `GetterWeather`. |
| `deduplicate(source, variable)` | Identify DB rows with overlapping `(bbox, time_range)` and remove the less complete duplicate. |

All mutating operations support `dry_run=True` for safe previewing.

### Pros
- Zero impact on the existing download/save/query flow.
- Each method is independently useful and testable.
- `coarsen_old_data` and `rechunk` have no equivalent in any other strategy.
- Can be called from a CLI command (`datavia maintain --source ERA5_land`).

### Cons
- Does not prevent new collisions — Strategy 1 or 2 must also be applied first.
- Merge step has a coordinate alignment risk: if CDS delivers tiles on slightly
  different grids, `xr.combine_by_coords()` raises or produces NaN fill.
- Read-modify-write is non-atomic at the filesystem level; crash mid-write loses data
  unless `os.replace()` is used consistently.
- Concurrent workers (benchmark scripts) could race on the same file.

### When to prefer
As a long-running data store ages: after Strategy 1 or 2 is in place, a maintainer
provides consolidation, cold-storage compression, and diagnostics that no other
strategy offers.

---

## Strategy 4 — Zarr store (append-in-place)

**Category:** storage format replacement  
**Files affected:** `saver_weather.py`, `getter_weather.py`, `formats.py`, DB schema

### How it works

Replace per-download `.nc` files with a Zarr directory store per
`(source, variable, year)`.  Zarr partitions data into small chunk files identified
by array position:

```
data/ERA5_land/2m_temperature/2024/
    .zarray        ← shape, chunks, dtype, fill_value
    .zattrs        ← CF metadata
    0.0.0          ← chunk [lat 0:10, lon 0:10, time 0:8760]
    1.0.0          ← chunk [lat 10:20, lon 0:10, time 0:8760]
    ...
```

Writing a new spatial tile fills in the chunk files for that region.  Writing the
same chunk twice is idempotent (updates the existing chunk file).  `xarray` reads the
entire store as one virtual array regardless of how many chunks are populated — missing
chunks appear as fill values.

### Pros
- Zero filename collision possible — chunk identity is defined by array position, not download metadata.
- File count is bounded: one store per `(source, variable, year)`, regardless of how many incremental downloads were made.
- `GetterWeather` opens one store and queries any sub-region or time window without knowing download history.
- Chunking can be tuned for the query access pattern (time-first for point queries).
- Zarr is the standard format for cloud-native geospatial data (compatible with S3, GCS).

### Cons
- Requires regridding each CDS download onto the store's fixed coordinate grid before
  writing chunks — adds processing complexity and a `scipy` or `pyresample` dependency.
- Significant rewrite of `SaverWeather` and `GetterWeather`.
- `weather_layers` DB table becomes mostly redundant (coverage is intrinsic to the
  Zarr store); either the table is removed or its role changes to a lightweight index.
- `zarr` becomes a required dependency.

### When to prefer
When the project grows to continental or global coverage and file-count scalability
becomes a real bottleneck.  Also the natural choice if the data store is eventually
moved to cloud object storage (S3/GCS).

---

## Strategy 5 — Kerchunk / VirtualiZarr manifest

**Category:** virtual aggregation layer (no file format change)  
**Files affected:** `saver_weather.py` (manifest rebuild step), `getter_weather.py` (manifest open)

### How it works

Keep all downloaded `.nc` files exactly as they are — no merging, no format change.
After each download, rebuild a JSON reference manifest (Kerchunk format) that maps
the chunk layout of all files into a single virtual Zarr view:

```
data/ERA5_land_t2m_2024.kerchunk.json   ← points into ERA5_land_t2m_202406_a1b2.nc,
                                            ERA5_land_t2m_202407_c3d4.nc, ...
```

`GetterWeather` opens the manifest with `xarray.open_dataset(manifest, engine="kerchunk")`
and sees all files as one unified array — no copies made, no data moved.

### Pros
- Zero data duplication — files are never rewritten.
- Read performance equivalent to Zarr because the chunk index is pre-computed.
- Strategy 1 (unique filenames) must be in place first but that is a one-line change.
- Compatible with cloud storage (manifests work with S3 URLs).

### Cons
- Manifest must be regenerated after every `save()` call — adds a step to `SaverWeather`.
- Manifest becomes a critical dependency; if it diverges from disk state the getter
  silently reads stale or missing data.
- `kerchunk` / `VirtualiZarr` is a newer library with a less stable API than `xarray`
  or `zarr`.
- Does not reduce file count — just hides it from the reader.

### When to prefer
When the goal is fast multi-file reads without rewriting data.  A good middle ground
between the minimal fix (Strategy 1) and full Zarr adoption (Strategy 4).

---

## Strategy 6 — Parquet + DuckDB as query engine

**Category:** full stack replacement (format + query layer)  
**Files affected:** `saver_weather.py`, `getter_weather.py`, DB schema, interpolation layer

### How it works

Convert every ERA5 download to columnar Parquet at save time — one row per
`(lat, lon, time, variable, value)`.  Replace the `weather_layers` SQLite table and
`GetterWeather`'s file-path lookup with a single DuckDB query over a glob:

```sql
SELECT lat, lon, time, value
FROM 'data/ERA5_land/*.parquet'
WHERE variable = '2m_temperature'
  AND lat BETWEEN 51.0 AND 52.0
  AND time BETWEEN '2024-06-01' AND '2024-06-30'
```

DuckDB's predicate pushdown reads only the relevant row groups.  `CoverageManager`
becomes unnecessary — DuckDB can compute covered ranges from the data itself.

### Pros
- File count is irrelevant — DuckDB handles the glob.
- No separate DB infrastructure needed for coverage or path lookup.
- Appending new data is trivial: just write another Parquet file.
- DuckDB is fast and has zero-copy integration with pandas/numpy.

### Cons
- Columnar row-per-point layout is highly inefficient for gridded raster data:
  a single ERA5 year for Germany at 0.1° resolution is ~10⁸ rows.
- Loses the spatial locality that NetCDF/Zarr chunk layouts provide — all spatial
  queries become full column scans across potentially large files.
- Bilinear spatial interpolation (`interpolate_netcdf`) assumes a grid; row-based
  Parquet would require reconstructing the grid in memory first.
- Complete rewrite of the saver, getter, and interpolation layer.

### When to prefer
Better suited to station observation data (DWD Parquet files already use this pattern)
than to gridded reanalysis.  Not recommended as a replacement for the NetCDF pipeline.

---

## Overall comparison

| Strategy | Fixes collision | Reduces file count | New dependencies | Code change scope | Biggest risk |
|---|---|---|---|---|---|
| 1 — Unique filename | ✅ | ❌ | none | 1 function | none |
| 2 — Tile grid | ✅ | partially | none | `CoverageManager` + stem | tile size choice |
| 3 — Maintainer class | ❌ (needs 1 first) | ✅ (post-hoc) | none | new class | coordinate misalignment on merge |
| 4 — Zarr store | ✅ | ✅ | `zarr` | saver + getter + schema | regridding cost |
| 5 — Kerchunk manifest | ❌ (needs 1 first) | ❌ | `kerchunk` | saver + getter | manifest staleness |
| 6 — Parquet + DuckDB | ✅ | ✅ | `duckdb`, `pyarrow` | full stack | grid → row layout mismatch |

---

## Recommended path

1. **Now:** Apply Strategy 1 (unique filename) — one function, zero risk, unblocks the
   pipeline immediately.
2. **Short term:** Apply Strategy 2 (tile grid) on top to simplify `CoverageManager`
   and make the tile cache reusable across projects.
3. **Medium term:** Add Strategy 3 (maintainer) for `audit_consistency`,
   `check_integrity`, and `detect_gaps` — low-risk diagnostics first, then
   `merge_fragments` once coordinate alignment is validated on real data.
4. **Long term:** Evaluate Strategy 4 (Zarr) if the data store grows to continental
   scale or moves to cloud object storage.
