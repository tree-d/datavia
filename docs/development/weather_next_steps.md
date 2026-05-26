# Weather Pipeline — Next Steps

> **Purpose:** Ordered action plan for fixing confirmed bugs and implementing
> design enhancements.  Cross-references `weather_pipeline_status_report_2026-04.md`
> (bugs) and `weather_further_enhancements.md` (features).
>
> **Current state:** Pipeline is functionally correct and produces
> climatologically plausible results.  Two blocking bugs must be fixed before
> any research-scale use.  Enhancements build on top of a clean bug-free base.

---

## Why the order matters — dependency map

The bugs and features are not independent.  The diagram below shows which
steps unblock which:

```
BUG-06  ──────────────────────────────────────────────── (standalone)
BUG-01 / BUG-09 / BUG-10 ───────┐
                                 │
BUG-07 ──────────────────────────┼──► Step 3 (clean storage state)
                                 │         │
BUG-05 + BUG-08 ─────────────────┘         │
  (both in interpolation.py)               │
                                           ▼
                            Enhancement 1 (CRS)    ← stub already wired
                            Enhancement 2 (hourly) ← simple config key
                                           │
                                           ▼
                            Enhancement 3 (CoverageManager / incremental)
                                           │
                                           ▼
                            Enhancement 4 (lifecycle: merge, reconfigure)
                                           │
                                           ▼
                            Enhancement 5 (ERA5 chunking + progress)
                                       (requires CDS credentials)
```

Key dependency insights:

- **BUG-05 and BUG-08** both live inside `interpolate_netcdf()`.  Rewriting
  that function once to batch coordinates also makes adding the nearest-neighbour
  NaN fill trivial.  Doing them separately means touching the same function
  twice and maintaining two PRs that conflict on the same lines.
- **BUG-01 / BUG-09 / BUG-10** are all in `saver_weather.py` +
  `pipeline.py`.  They share a code review session and can land as one commit.
- **BUG-07** (deterministic filenames) is logically independent but must be
  done **before** Enhancement 3 (`CoverageManager`), because the coverage
  manager needs to find files by name to determine what is already cached.
  A `HYRAS_tmp*.nc` file cannot be reliably matched to a variable or year
  without reading the file, making incremental logic fragile.
- **Enhancements 3 and 4** (incremental downloads, pipeline lifecycle) depend
  on a clean, consistent DB state — meaning all bugs should be fixed first.

---

## Step 1 — Fix BUG-06: year-end timestamp boundary *(fix first, lowest effort)*

**Why first:** One-line change, zero risk, immediately unblocks annual queries.

**Symptom:** `precipitation_north_south_2025.py` raised `RuntimeError` for
`2025-12-31`.  HYRAS `pr` stores its last daily value at `06:00 UTC`; `tas`
at `00:00 UTC`.  The saver writes those raw values into `valid_until`, so any
query after those times on December 31 fails.

**Where:** `datavia/library/formats.py` → `extract_netcdf_layer_metadata()`
(line 291).  After reading the last time coordinate, replace the raw value with
end-of-day:

```python
# After this block (current code reads valid_until from last time step):
valid_until = valid_until.replace(hour=23, minute=59, second=59, microsecond=0)
```

**Alternatively** the rounding can live in `SaverWeather._read_temporal_metadata()`
in `saver_weather.py` — either location is fine.  The library function is
preferred because it is the canonical source of truth for NC metadata and any
future saver will benefit automatically.

**Test to add:** `tests/test_weather_pipeline_unit.py` — mock a NetCDF with
last time stamp `2025-12-31T06:00:00`; assert `valid_until` returned by
`extract_netcdf_layer_metadata` equals `2025-12-31T23:59:59`.

**Files changed:** `datavia/library/formats.py` (or `saver_weather.py`)

---

## Step 2 — Fix BUG-01 / BUG-09 / BUG-10: sync and source_name hygiene

**Why together:** All three share `saver_weather.py` and `pipeline.py`.  A
single focused PR touches both files once.

### ✅ BUG-01 fix (sync before update) — DONE

`Pipeline.sync_files_and_database()` is now a concrete method on the base
`Pipeline` class.  `WeatherPipeline.update_data()` calls
`self.sync_files_and_database()` at the top.  `SaverWeather` exposes
`list_managed_files()` and `delete_registration(uri)` as the disk- and
DB-side primitives; `GetterWeather.get_registered_uris()` provides the DB
read side.  No concrete casts, no `sync_files_and_database()` on any Saver.

### BUG-09 consequence (duplicate rows fixed by BUG-01)

Once sync runs before `update_data()`, the DWD downloader checks what is
already in the DB and skips existing station files.  The 3 duplicate Parquet
rows will stop accumulating on new runs.  The existing duplicate rows in
the DB should be cleaned up manually via a one-time migration:

```sql
DELETE FROM weather_layers
WHERE source_name = 'weather'
  AND id NOT IN (
      SELECT MIN(id) FROM weather_layers
      WHERE source_name = 'weather'
      GROUP BY variable, valid_from
  );
```

### BUG-10 (DWD rows use wrong source_name)

In `SaverWeather.save()`, when saving a Parquet file, use the actual
`self.source_name` (e.g. `"DWD_stations"`) instead of a hardcoded
`"weather"`:

```python
# Current (wrong):
source_name = "weather"

# Correct — use the instance attribute set in __init__:
source_name = self.source_name
```

**Test to add:** After `save()` inserts a Parquet row, assert that
`weather_layers.source_name == self.source_name`, not `"weather"`.

**Files changed:** `packages/weather/datavia/weather/pipeline.py`,
`packages/weather/datavia/weather/saver_weather.py`

---

## Step 3 — Fix BUG-07: deterministic filenames

> ### ⛔ DESIGN DISCUSSION REQUIRED before implementing Step 7 (`CoverageManager`)
>
> Step 3 fixes the filename bug independently, but the deterministic naming
> scheme chosen here **directly constrains the design of `CoverageManager`**
> (Step 7).  Before writing any `CoverageManager` code, the following must be
> agreed on:
>
> - **Filename convention:** `HYRAS_<variable>_<year>.nc` works for annual
>   HYRAS files, but ERA5 will be chunked monthly (Enhancement 5:
>   `ERA5_land_2m_temperature_2024-06.nc`).  The `CoverageManager` must parse
>   both patterns — or rely solely on the DB rather than filenames.
> - **Single source of truth:** should `CoverageManager` derive coverage from
>   filenames on disk (fast, no DB needed) or from `weather_layers` rows (more
>   reliable, already contains `valid_from`/`valid_until`)?  These two
>   approaches lead to very different implementations.
> - **Who owns the filename schema?** If the DB is the source of truth,
>   deterministic filenames are still good practice (BUG-07 is worth fixing
>   regardless), but `CoverageManager` never needs to parse them.  If the
>   filesystem is the source of truth, the naming convention becomes a formal
>   contract that must be documented and tested.
>
> **Recommended decision:** use the DB as the source of truth for
> `CoverageManager`.  Deterministic filenames remain important for
> human-readability and orphan detection, but coverage logic reads
> `weather_layers` rows, not filenames.  This decouples the two concerns and
> makes `CoverageManager` independent of source-specific naming patterns.
>
> **Action:** discuss and record this decision before starting Step 7.

**Why here:** Before implementing incremental downloads (Enhancement 3), the
storage layer must produce predictable, stable filenames.  `CoverageManager`
cannot reliably map `HYRAS_tmpc242n79f.nc` to `variable=pr, year=2025`
without opening the file — defeating the purpose of a lightweight coverage
check.

**Current behaviour:** `URLDownloader` creates a temp file via
`tempfile.mkstemp(suffix=".download")`.  `HYRASDownloader._get_final_filename()`
strips `.download` and keeps the `tmp*` stem.  The saver copies this into the
data directory, leaving names like `HYRAS_tmp6tem1oyd.nc`.

**Fix — two parts:**

#### Part A: `HYRASDownloader._get_final_filename()` returns a descriptive path

```python
# packages/weather/datavia/weather/hyras_downloader.py
def _get_final_filename(self, temp_path: str, content_type: str) -> str:
    """Build a stable, descriptive destination path for a HYRAS download.

    Returns a path in the configured data directory of the form
    ``HYRAS_<variable>_<year>.nc``, independent of the temporary filename.

    Parameters
    ----------
    temp_path : str
        Path of the temporary download file (not used except to extract
        the extension as a fallback).
    content_type : str
        HTTP Content-Type header; not used for HYRAS files.

    Returns
    -------
    str
        Absolute destination path.
    """
    name = f"HYRAS_{self._current_variable}_{self._current_year}.nc"
    return os.path.join(get_config().data_directory, name)
```

`self._current_variable` and `self._current_year` must be set as instance
attributes just before each `download()` call in the download loop.

#### Part B: `URLDownloader` keeps the temp file in `/tmp` until rename

`URLDownloader.download()` already does `os.rename(working_filename, final_path)`.
No change needed there — `_get_final_filename()` now returns a path outside
`/tmp`, so the rename moves the file directly into the data directory.

#### Migration of existing orphan files

After the fix is deployed, the 6 orphan `HYRAS_tmp*.nc` files should be
deleted (or moved to a backup location), and `sync_files_and_database()`
re-run so the two registered files are re-associated with their
deterministic names:

```bash
# One-time cleanup — run after deploying the fix
rm .datavia/data/HYRAS_tmp*.nc   # removes all tmp-named copies
# Then call pipeline.sync_files_and_database() to re-register the two
# remaining files under their new names after re-downloading
```

> Note: The two DB-registered files will also need to be re-downloaded once
> to get their deterministic names.  This is a one-time cost.

**Files changed:** `packages/weather/datavia/weather/hyras_downloader.py`
(no change needed to `datavia/core/downloader_url.py` — the hook is in
`_get_final_filename`)

---

## Step 4 — Fix BUG-05 + BUG-08 together: rewrite `interpolate_netcdf`

**Why together:** Both bugs live in `datavia/library/interpolation.py` →
`interpolate_netcdf()`.  The performance fix (batch coordinates) and the
NaN fill (pre-fill before bilinear) are separate concerns but modify the same
function body.  A single rewrite avoids double-churn on a central library
function.

### BUG-05: Batch coordinate support

**Current signature:**
```python
def interpolate_netcdf(nc_path, lat: float, lon: float, variable, datetime_utc)
```

**New signature** (backwards compatible — scalar float still accepted):
```python
def interpolate_netcdf(
    nc_path: str,
    lats: float | np.ndarray,
    lons: float | np.ndarray,
    variable: str,
    datetime_utc: Any,
) -> float | np.ndarray:
```

Inside the function, open the dataset **once** and interpolate all coordinates
in a single vectorised call:

```python
with xr.open_dataset(nc_path) as ds:
    lats_arr = np.atleast_1d(np.asarray(lats, dtype=float))
    lons_arr = np.atleast_1d(np.asarray(lons, dtype=float))
    # reproject all lons/lats → x_arr, y_arr in one pyproj call (N points)
    transformer = pyproj.Transformer.from_crs("EPSG:4326", file_crs, always_xy=True)
    x_arr, y_arr = transformer.transform(lons_arr, lats_arr)
    # xarray vectorised interp over N points
    point = ds[variable].interp(
        x=xr.DataArray(x_arr, dims="points"),
        y=xr.DataArray(y_arr, dims="points"),
        method="linear",
    )
    # temporal selection unchanged
    ...
```

`GetterWeather.get_data()` is updated to build `lats_arr` and `lons_arr` from
all `coords_arr` rows and pass them in a single call, removing the per-coordinate
loop entirely.

### BUG-08: Nearest-neighbour NaN fill before bilinear

After opening the dataset and selecting the variable, fill nodata cells before
calling `.interp()`:

```python
da = ds[variable]
# Replace fill value with NaN, then propagate nearest valid value
# outward so that bilinear stencils at the domain boundary don't collapse.
fill_val = da.attrs.get("_FillValue", None)
if fill_val is not None:
    da = da.where(da != fill_val)
# Forward-fill then back-fill along both spatial axes.
# This is a simple O(n) approximation; for exact nearest-neighbour the
# distance_transform_edt approach (used in interpolate_tiff) is the
# upgrade path.
da = da.ffill("x").ffill("y").bfill("x").bfill("y")
```

The combination of BUG-05 and BUG-08 fixes means:
- A 365-day × 8-station query drops from ~5 minutes to < 5 seconds.
- A 5×4 Germany grid query drops from 5/20 non-NaN to all 20 non-NaN
  (for inland points).

**Performance note:** The `ffill/bfill` approach is fast (no scipy import
needed beyond what is already present) but is an approximation — it fills
diagonals incorrectly in some corner-clipping cases.  For exact nearest-
neighbour the `scipy.ndimage.distance_transform_edt` approach already used in
`interpolate_tiff` should be ported here as a follow-up (documented in
Enhancement roadmap, not blocking for the bug fix).

**Files changed:** `datavia/library/interpolation.py`,
`packages/weather/datavia/weather/getter_weather.py`

---

## Step 5 — Enhancement 1: wire the CRS stub *(quick win)*

The `crs_coords` parameter already exists on `GetterWeather.get_data()` but
raises `ValueError` for anything other than `"EPSG:4326"`.  After the BUG-05
rewrite, `interpolate_netcdf` already has all coordinates flowing through a
pyproj transformer.  Wiring the stub requires:

1. Remove the guard in `get_data()` that rejects non-EPSG:4326 input.
2. Pass `crs_coords` to `interpolate_netcdf` as a `input_crs` parameter.
3. Inside `interpolate_netcdf`, reproject from `input_crs → file_crs` instead
   of always assuming `EPSG:4326 → file_crs`.

This is a small change on top of the BUG-05 rewrite.

**Files changed:** `packages/weather/datavia/weather/getter_weather.py`,
`datavia/library/interpolation.py`

---

## Step 6 — Enhancement 2: configurable temporal resolution *(simple config key)*

Add `"temporal_resolution"` as a config key with two values: `"daily"`
(default, current behaviour) and `"hourly"`.

- `"daily"`: no change to existing code path.
- `"hourly"`: `interpolate_netcdf` returns all time steps in the day as an
  `xr.DataArray` keyed by `valid_time` rather than selecting the nearest.
- `HYRASDownloader` raises `ValueError` immediately on `"hourly"` since HYRAS
  is daily-only.

This is self-contained and does not interact with any other pending change.

**Files changed:** `packages/weather/datavia/weather/pipeline.py`,
`datavia/library/interpolation.py`,
`packages/weather/datavia/weather/hyras_downloader.py`

---

## Step 7 — Enhancement 3: `CoverageManager` and incremental downloads

**Prerequisite:** Steps 1–4 complete (clean DB state, stable filenames, fast
queries).

The `CoverageManager` (new file `coverage_manager.py`) computes which
`(bbox, date_start, date_end)` cells are already registered and returns only
the uncovered portions to the downloader.  This is the largest single piece of
new code in the plan.

Key design decision from the enhancements doc (§4): coverage is inherently
2-D (spatial × temporal).  Do **not** compute missing spatial and missing
temporal extents independently and cross-product them — that produces both
gaps and duplicated downloads.  The correct algorithm decomposes the missing
2-D rectangle into a list of `(bbox, start, end)` tuples by splitting along
both axes simultaneously.

**Implementation order within this step:**

1. `CoverageManager.__init__` + `missing_spatiotemporal()` — with unit tests
   (the four test cases in the enhancements doc §4).
2. Wire `CoverageManager` into `WeatherPipeline.update_data()`.
3. Integration test: run `update_data()` twice for the same HYRAS year →
   assert that the second call issues zero downloader calls.

**Files changed:** `packages/weather/datavia/weather/coverage_manager.py`
(new), `packages/weather/datavia/weather/pipeline.py`

---

## Step 8 — Enhancement 4: pipeline lifecycle (merge, reconfigure, rename)

**Prerequisite:** Step 7 complete (`CoverageManager.merge_existing()` is
reused here).

- `WeatherPipeline.__init__` gains `replace: bool = False`.
- `get_config()` exposes the effective (merged) config.
- `reconfigure(config_updates, replace=False)` applies delta + re-runs
  coverage logic.
- `SaverWeather.rename_all(old, new)` — atomic rename of files + DB rows in
  a SQLite transaction.
- `Datavia.check_pipelines()` — read-only audit, raises
  `DataviaConsistencyError` on name collisions or missing files.

**Files changed:** `packages/weather/datavia/weather/pipeline.py`,
`packages/weather/datavia/weather/saver_weather.py`,
`datavia/core/datavia.py`

---

## Step 9 — Enhancement 5: ERA5 request chunking and progress

**Prerequisite:** Steps 7 and 8 complete.  Also requires CDS credentials for
any real testing.

- Split ERA5 downloads into one CDS job per calendar month per variable.
- Queue-wait logging every 60 s with job ID.
- `tqdm` transfer bar (outer bar for job count, inner for bytes).
- `cds_queue_timeout` config key cancels and raises `TimeoutError` when
  exceeded.
- `CompositeWeatherDownloader` flattened to accept `list[str]` from
  sub-downloaders.

This step is last because it has an external dependency (CDS API / network)
that cannot be tested with cached data and requires real Copernicus credentials.

**Files changed:** `packages/weather/datavia/weather/era5_downloader.py`,
`packages/weather/datavia/weather/composite_downloader.py`,
`packages/weather/datavia/weather/pipeline.py`

---

## Summary table

| Step | Bug / Enhancement | Effort | Prerequisite | Unblocks |
|---|---|---|---|---|
| **1** | BUG-06 — year-end `valid_until` | S | — | Correct annual queries |
| **2** | BUG-01 / BUG-09 / BUG-10 — sync + source_name | S | — | Clean DB state |
| **3** | BUG-07 — deterministic filenames | M | — | Enhancement 3 |
| **4** | BUG-05 + BUG-08 — batch interp + NaN fill | M | — | Enhancement 1 |
| **5** | Enh. 1 — wire CRS stub | S | Step 4 | CRS-agnostic queries |
| **6** | Enh. 2 — temporal resolution config key | S | — | Hourly ERA5 queries |
| **7** | Enh. 3 — `CoverageManager`, incremental DL | L | Steps 1–4 | Enhancement 4 |
| **8** | Enh. 4 — pipeline lifecycle (merge, rename) | L | Step 7 | Full lifecycle API |
| **9** | Enh. 5 — ERA5 chunking + progress | L | Steps 7–8, CDS creds | Production ERA5 use |

**Effort key:** S = hours · M = 1–2 days · L = 3–5 days

Steps 1–4 are all independent of each other and can be parallelised across
developers.  Steps 5 and 6 are also independent of each other.  Steps 7 → 8 → 9
must be done in order.

---

## Files to change — consolidated view

| File | Steps | What changes |
|---|---|---|
| `datavia/library/formats.py` | 1 | Round `valid_until` up to 23:59:59 |
| `datavia/library/interpolation.py` | 4, 5, 6 | Batch coords, NaN fill, CRS param, temporal_resolution param |
| `packages/weather/datavia/weather/pipeline.py` | 2, 6, 7, 8, 9 | Call sync before update; config keys; lifecycle API; ERA5 paths |
| `packages/weather/datavia/weather/saver_weather.py` | 2, 8 | Fix source_name; add rename_all() |
| `packages/weather/datavia/weather/hyras_downloader.py` | 3, 6 | Deterministic filenames; hourly guard |
| `packages/weather/datavia/weather/getter_weather.py` | 4, 5 | Remove per-coord loop; pass CRS |
| `packages/weather/datavia/weather/coverage_manager.py` | 7 | New file |
| `datavia/core/datavia.py` | 8 | Add check_pipelines() |
| `packages/weather/datavia/weather/era5_downloader.py` | 9 | Chunking + progress |
| `packages/weather/datavia/weather/composite_downloader.py` | 9 | Handle list[str] |
| `tests/test_weather_pipeline_unit.py` | 1–9 | One new test class per step |
