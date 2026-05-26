# Weather Pipeline — Further Enhancements

> **Purpose:** Detailed design for the next round of improvements to the
> weather pipeline: flexible input CRS, configurable temporal resolution,
> incremental spatial/temporal downloads, and full pipeline lifecycle
> management (auto-merge, reconfigure, rename, orchestrator audit).
>
> **Status:** Design only — not yet implemented.
> **Prerequisite:** Phases A–G from `weather_source_architecture_plan.md` are
> **complete as of April 2026** — this plan is now unblocked.
> Note: Enhancement 1 is partially started; `crs_coords` is already accepted as
> a parameter in `GetterWeather.get_data()` but is currently a stub (raises
> `ValueError` for anything other than `"EPSG:4326"` and is not forwarded to
> `interpolate_netcdf`).

---

## Table of Contents

0. [Known bugs](#0-known-bugs)
1. [Motivation](#1-motivation)
2. [Enhancement 1 — Flexible input CRS in `get_data`](#2-enhancement-1--flexible-input-crs-in-get_data)
3. [Enhancement 2 — Configurable temporal resolution](#3-enhancement-2--configurable-temporal-resolution)
4. [Enhancement 3 — Incremental spatial and temporal downloads](#4-enhancement-3--incremental-spatial-and-temporal-downloads)
5. [Enhancement 4 — Pipeline lifecycle management](#5-enhancement-4--pipeline-lifecycle-management)
6. [Enhancement 5 — ERA5 request chunking and download progress](#6-enhancement-5--era5-request-chunking-and-download-progress)
7. [Affected files](#7-affected-files)
8. [Verification checklist](#8-verification-checklist)
9. [Known risks and open questions](#9-known-risks-and-open-questions)

---

## 0. Known bugs

### BUG-01 — `sync_files_and_database` is placed at the wrong abstraction level

**Introduced:** April 2026 (during example-script testing)  
**Status:** Partially worked around — root cause is a systemic interface
contradiction described in [section 9](#9-known-risks-and-open-questions).

#### Surface symptom

When the database is reset, previously downloaded files are no longer
registered.  A call to `update_data()` re-downloads everything instead of
detecting the files already on disk.  To recover, `sync_files_and_database()`
was needed, but `SaverWeather` never implemented it, so a workaround was
added directly to `WeatherPipeline`:

```python
# packages/weather/datavia/weather/pipeline.py  (current workaround)
def sync_files_and_database(self) -> bool:
    ...
    assert isinstance(self.saver, SaverWeather)   # ← concrete cast
    return self.saver.sync_files_and_database()    # ← raises NotImplementedError
```

`WeatherPipeline.update_data()` also does not call `sync_files_and_database()`
before querying existing layers, so the DB is never automatically reconciled.

#### Root cause — a contradiction in `interfaces.py`

`datavia/core/interfaces.py` contains two statements that directly contradict
each other:

1. `Saver` declares `sync_files_and_database()` as an **abstract method**,
   which any concrete saver must implement.
2. The docstring on `Getter.get_existing_layers()` states: *"Only the Getter
   may read from the database; callers should use this method instead of
   asking the Saver."*

Any implementation of `Saver.sync_files_and_database()` **must** read from the
database to detect orphan rows — which violates statement 2.  `TiffSaver`
already violates it: `check_data_exists()` queries `raster_layers` directly.
The weather pipeline not having an implementation just made the contradiction
visible earlier.

#### Short-term workaround (acceptable until the interface is redesigned)

1. Implement `sync_files_and_database()` on `SaverWeather` following the
   `TiffSaver` pattern — this perpetuates the interface violation but at
   least makes the weather pipeline consistent with the others.
2. Remove the concrete cast and the override from `WeatherPipeline`.
3. Call `self.saver.sync_files_and_database()` at the top of
   `WeatherPipeline.update_data()` before querying existing layers.

**Files to change:** `packages/weather/datavia/weather/saver_weather.py`,
`packages/weather/datavia/weather/pipeline.py`.

**The correct long-term fix** requires redesigning the `Saver` and `Pipeline`
base interfaces so that reconciliation belongs at the pipeline level — see the
design issue in [section 9](#9-known-risks-and-open-questions).

---

## 1. Motivation

After the source-architecture refactor (phases A–G) the pipeline has clean
source isolation and correct unit conversions.  The next set of improvements
focuses on two areas:

- **Query flexibility** — callers currently must convert all coordinates to
  WGS84 before querying and can only request daily values; both constraints
  should be lifted.
- **Download efficiency and correctness** — `update_data()` re-downloads the
  full declared extent on every call and has no concept of what is already on
  disk.  Reusing the same pipeline name with a different config silently
  corrupts the DB.  Both problems need a proper incremental download manager
  and a pipeline lifecycle model.

---

## 2. Enhancement 1 — Flexible input CRS in `get_data`

### Current behaviour

`GetterWeather.get_data()` accepts only `crs_coords="EPSG:4326"` and requires
callers to supply coordinates in WGS84 longitude/latitude degrees.  When the
source data is natively in another projection (e.g. HYRAS uses EPSG:3035), the
caller has to reproject manually before querying and then interpret the result
without any CRS context.

### Planned behaviour

`get_data()` should accept coordinates in any CRS that `pyproj` can resolve and
internally reproject them to whatever CRS the source file uses before
interpolating.  `pyproj` is already in the pixi environment (added for phase
A3).

```python
# Proposed future API — not yet implemented

# Input already in ETRS89-LAEA metres — no manual reprojection needed
values = pipeline.get_data(
    coords=np.array([[4_438_345, 2_781_634]]),
    crs_coords="EPSG:3035",
    variable="relative_humidity_2m",
    datetime_utc="2024-06-15T12:00:00",
)

# Input in a national grid — also valid
values = pipeline.get_data(
    coords=np.array([[5_000_000, 3_200_000]]),
    crs_coords="EPSG:25832",          # UTM zone 32N
    variable="2m_temperature",
    datetime_utc="2024-06-15T12:00:00",
)
```

### Implementation notes

**File:** `packages/weather/datavia/weather/getter_weather.py`
**File:** `datavia/library/interpolation.py`

- `get_data()` gains a `crs_coords: str = "EPSG:4326"` parameter (already
  present as a stub; wire it through to `interpolate_netcdf`).
- `interpolate_netcdf` already detects the file CRS from the `grid_mapping`
  attribute (phase A3 fix).  Extend it to accept an optional
  `input_crs: str | None` argument and, when provided and different from the
  file CRS, reproject the input coordinates before interpolating:

  ```python
  if input_crs and input_crs != file_crs_str:
      transformer = pyproj.Transformer.from_crs(
          input_crs, file_crs, always_xy=True
      )
      x_proj, y_proj = transformer.transform(x_in, y_in)
  else:
      x_proj, y_proj = x_in, y_in
  ```

- For ERA5 (WGS84 lat/lon) the existing path is unchanged when
  `crs_coords="EPSG:4326"` (default).

### Test additions

`tests/test_weather_pipeline_unit.py`:

- Query with `crs_coords="EPSG:3035"` on a mock HYRAS dataset → verify the
  correct reprojected coordinates are passed to `xr.Dataset.interp`.
- Query with `crs_coords="EPSG:4326"` (default) → verify no reprojection
  transformer is created.

---

## 3. Enhancement 2 — Configurable temporal resolution

### Current behaviour

All sources return daily values.  HYRAS is daily by design.  ERA5 is
downloaded at hourly resolution but `interpolate_netcdf` selects the single
nearest time step, effectively returning one value regardless of what the caller
requests.

### Planned behaviour

A `"temporal_resolution"` config key controls the granularity of both the
download and the query response.

```python
# Proposed future API — not yet implemented
WeatherPipeline(config={
    "source":               "ERA5_land",
    "variables":            ["2m_temperature"],
    "date_start":           "2024-01-01",
    "date_end":             "2024-12-31",
    "temporal_resolution":  "hourly",   # "daily" is the default
})
```

| Value | ERA5 | HYRAS | DWD stations |
|---|---|---|---|
| `"daily"` (default) | nearest time step to noon UTC | native daily value | daily mean from hourly station data |
| `"hourly"` | all hours in the requested range | ❌ raises `ValueError` | native hourly observation |

### Implementation notes

**File:** `packages/weather/datavia/weather/pipeline.py`

- Add `"temporal_resolution"` to the known config keys in
  `validate_pipeline_config()`.
- Pass the value through to `CompositeWeatherDownloader` (controls what time
  steps are requested from CDS) and `GetterWeather` (controls what
  `interpolate_netcdf` returns).

**File:** `packages/weather/datavia/weather/downloader_era5.py`

- `ERA5Downloader.download()` already requests hourly data from CDS.  When
  `temporal_resolution="daily"` the saver/getter just uses noon UTC; no
  download-side change required.

**File:** `datavia/library/interpolation.py`

- Add a `temporal_resolution: str = "daily"` parameter to
  `interpolate_netcdf`.
- `"daily"` → select the single nearest time step (existing behaviour).
- `"hourly"` → return all time steps within the requested day as a
  `xr.DataArray` keyed by `valid_time`.

**File:** `packages/weather/datavia/weather/hyras_downloader.py`

- `HYRASDownloader.download()` must raise `ValueError` early if
  `temporal_resolution="hourly"` is requested, since HYRAS files are
  daily-only.

### Test additions

`tests/test_weather_pipeline_unit.py`:

- `temporal_resolution="hourly"` with ERA5 mock → `get_data` returns an array
  of 24 values for one calendar day.
- `temporal_resolution="hourly"` with HYRAS source → `WeatherPipeline.__init__`
  raises `ValueError`.
- `temporal_resolution="daily"` (default) → existing tests unchanged.

---

## 4. Enhancement 3 — Incremental spatial and temporal downloads

### Current behaviour

`update_data()` passes the full declared bounding box and date range to the
downloader on every call.  It never checks what is already in `weather_layers`.
Calling it twice is safe only because the downloader checks for existing files
on disk — but no spatial or temporal diffing is done.

### Planned behaviour

An incremental download manager sits between `WeatherPipeline.update_data()`
and the downloader.  Before any request is issued it:

1. Queries `weather_layers` for all rows matching the current source and
   variables.
2. Computes the **uncovered** spatial and temporal portions by subtracting the
   known coverage from the declared config extent.
3. Issues requests only for the uncovered portions.

#### Sub-region bounding box

Callers should be able to limit downloads to a smaller area:

```python
WeatherPipeline(config={
    "source":     "ERA5_land",
    "variables":  ["2m_temperature"],
    "date_start": "2024-01-01",
    "date_end":   "2024-12-31",
    "era5_bbox":  [52.7, 13.1, 52.3, 13.6],  # Berlin only, not all Germany
})
```

#### Superseding region logic

If Leipzig (bbox A) and Berlin (bbox B) have been downloaded separately and
Sachsen (bbox C, which contains both A and B) is requested:

```
coverage = [bbox_A, bbox_B]
requested = bbox_C
missing   = bbox_C  minus  union(bbox_A, bbox_B)
```

Only `missing` is fetched.  The existing Leipzig and Berlin files are reused.

> **Note:** exact 2-D bbox subtraction is non-trivial (the result is often an
> L-shaped polygon, not a rectangle).  A pragmatic first implementation may
> tile the missing area into two axis-aligned rectangles and issue two
> requests; a later pass can use `shapely.Polygon` for exact coverage.

#### Combined spatial and temporal overlap (2-D coverage)

Spatial and temporal extents are **not independent** — each downloaded chunk
covers a **(bbox × time range)** cell.  Treating them as two separate 1-D
problems produces wrong results whenever the spatial and temporal extents of
existing chunks differ from each other.

Example:

```
Chunk 1: Berlin,       1990–2000
Chunk 2: East Germany, 1980–2010   ← new request
```

If the missing portion is computed as:

```
missing_temporal → 1980–1990 and 2000–2010   (before/after Berlin chunk)
missing_spatial  → East Germany minus Berlin  (area outside Berlin)
```

…and these two 1-D results are combined naively (e.g. as a cross product), the
implementation would either skip 1990–2000 for the non-Berlin part of East
Germany or re-download the Berlin/1990–2000 tile that already exists.  Both are
wrong.

The correct decomposition of the missing 2-D area for chunk 2 is:

```
Missing piece 1: East Germany,              1980–1990  (entirely before Berlin chunk)
Missing piece 2: East Germany minus Berlin, 1990–2000  (spatial gap during overlap period)
Missing piece 3: East Germany,              2000–2010  (entirely after Berlin chunk)
```

`CoverageManager.missing_spatiotemporal(bbox, date_start, date_end)` must
therefore iterate over all existing coverage cells, split the requested
(bbox × time) rectangle along both axes simultaneously, and return the list of
non-overlapping (bbox, date_start, date_end) tuples that still need to be
fetched.  Each returned tuple becomes one downloader call.

A first implementation can approximate non-rectangular bbox remainders as
axis-aligned rectangles (same caveat as above); `shapely` is the upgrade path
for exact polygon arithmetic.

#### Download priority queue *(low priority)*

A configurable download queue would let short, time-critical requests complete
before large background bulk downloads.  Not urgent — design separately when
the incremental manager is stable.

#### Compressed bulk downloads

For large extents a single zip archive may be faster than many small files.
Benchmark before implementing; the CDS API supports `download_format="zip"`.

### Implementation notes

**New file:** `packages/weather/datavia/weather/coverage_manager.py`

Responsibilities:

- `CoverageManager(db_path, source_name, variables)` — loads existing coverage
  cells (each a `(bbox, date_start, date_end)` tuple) from `weather_layers`
  on init.
- `missing_spatiotemporal(bbox, date_start, date_end) -> list[CoverageCell]`
  — computes the set of `(bbox, date_start, date_end)` cells that are not yet
  covered by any existing chunk, accounting for the combined 2-D
  (spatial × temporal) overlap.  Each returned cell becomes one downloader
  call.  See the **Combined spatial and temporal overlap** section above for
  the algorithm rationale.
- `register(bbox, date_start, date_end, file_paths)` — writes new rows to
  `weather_layers` after a successful download.

> The old independent `missing_temporal` / `missing_spatial` helpers are **not**
> implemented as public methods — they would give wrong answers for overlapping
> (bbox × time) chunks and should not be exposed.

**File:** `packages/weather/datavia/weather/pipeline.py`

- `update_data()` instantiates `CoverageManager` and passes the missing
  portions to the downloader instead of the raw config values.

### Test additions

`tests/test_weather_pipeline_unit.py`:

- `missing_spatiotemporal` — same bbox, same time range: returns empty list
  (already covered).
- `missing_spatiotemporal` — wider time range, same bbox: returns one cell
  for each uncovered date gap.
- `missing_spatiotemporal` — wider bbox, same time range: returns one or two
  cells covering the spatial remainder.
- `missing_spatiotemporal` — **2-D overlap**: Berlin 1990–2000 on disk, East
  Germany 1980–2010 requested → returns exactly three cells: East Germany
  1980–1990, (East Germany minus Berlin) 1990–2000, East Germany 2000–2010.
  No Berlin/1990–2000 tile is re-fetched.
- `update_data()` calls the downloader once per cell returned by
  `missing_spatiotemporal` (mock `CoverageManager`).
- Superseding region: after Leipzig + Berlin downloads, Sachsen request issues
  exactly one (or two) partial requests covering only the spatial remainder
  for the already-covered time range.

---

## 5. Enhancement 4 — Pipeline lifecycle management

### Current problem

Pipeline instances are identified only by the name supplied in `config["source"]`.
If the same name is reused with a changed config (e.g. a different date range
or bounding box), the DB record is overwritten while existing files on disk are
untouched.  `GetterWeather` then silently serves data from the old config with
no warning.

### Planned behaviour

#### Auto-merge of existing coverage on init

On `WeatherPipeline.__init__`, before downloading anything, the pipeline merges
all coverage already registered in `weather_layers` for the same source and
variables into the declared config.  The result is the **effective config** —
the union of what was declared and what already exists on disk.

Only the portion declared in the current config but absent from the DB is
downloaded:

```python
# Jan–Jun was downloaded in a previous run.
pipeline = WeatherPipeline(config={
    "source":     "ERA5_land",
    "variables":  ["2m_temperature"],
    "date_start": "2024-07-01",   # user only declares Jul–Dec
    "date_end":   "2024-12-31",
})

# Auto-merge detects Jan–Jun on disk.
# Effective config now spans Jan–Dec.
# Only Jul–Dec is downloaded.
print(pipeline.get_config()["date_start"])  # "2024-01-01"
```

`replace=True` discards all existing coverage and starts clean — useful when
old data was a mistake:

```python
pipeline = WeatherPipeline(config={
    "source":     "ERA5_land",
    "variables":  ["2m_temperature"],
    "date_start": "2024-01-01",
    "date_end":   "2024-06-30",
}, replace=True)
# All previous ERA5_land temperature rows deleted from weather_layers.
# Jan–Jun downloaded fresh.
```

#### `get_config()` — inspect the effective config

```python
effective = pipeline.get_config()
# Returns the merged config dict, including coverage auto-detected from disk.
# Keys: same as the constructor config dict, with date_start/date_end and
# era5_bbox reflecting the full covered extent.
```

#### Live reconfiguration via `reconfigure()`

After construction, callers can extend the date range or adjust the bounding
box without restarting:

```python
# Extend the time range — downloads only the new months
pipeline.reconfigure({"date_end": "2025-03-31"})

# Replace all coverage and start fresh
pipeline.reconfigure({"date_start": "2025-01-01", "date_end": "2025-06-30"},
                     replace=True)
```

Renaming is also supported.  `SaverWeather` renames all managed files on disk
and updates every matching `weather_layers` row atomically (SQLite transaction
+ `os.rename`) so no data is orphaned and no path references break:

```python
pipeline.reconfigure({"name": "era5_germany_2024", "date_end": "2025-03-31"})
# Files moved:  ERA5_land_2024*.nc  →  era5_germany_2024_2024*.nc
# DB updated:   source_name = "ERA5_land" → "era5_germany_2024"
```

`reconfigure()` runs the same auto-merge logic as `__init__`, so it is safe to
call repeatedly without duplicating downloads.

#### Orchestrator-level consistency check via `dv.check_pipelines()`

The `Datavia` orchestrator holds references to all registered pipelines and can
therefore cross-check them against the DB in one pass:

```python
dv = Datavia(pipelines=[pipeline_a, pipeline_b])
dv.check_pipelines()
# Compares each pipeline's effective config against weather_layers.
# Reports (but does not fix) any of:
#   - name collisions between pipelines
#   - DB rows whose file paths no longer exist on disk (orphaned files)
#   - declared coverage with no corresponding DB rows (unregistered files)
```

`check_pipelines()` is read-only and raises `DataviaConsistencyError` (or
logs warnings, TBD) on any finding.

> **Known limitation:** if two `Datavia` instances share the same DB (same
> process or two processes pointing at the same SQLite file), each has an
> incomplete in-memory view.  A DB-level write lock (`BEGIN EXCLUSIVE`) or a
> singleton guard on `Datavia` should be evaluated before multi-process use
> is supported.

### Implementation notes

**File:** `packages/weather/datavia/weather/pipeline.py`

- `WeatherPipeline.__init__` gains `replace: bool = False` parameter.
- After `validate_pipeline_config()`, call `CoverageManager.merge_existing()`
  to build the effective config (see Enhancement 3 for `CoverageManager`).
- Store effective config as `self._effective_config`; expose via `get_config()`.
- `reconfigure(config_updates, replace=False)` — validates the delta, applies
  it to `self._effective_config`, calls `CoverageManager` for the new missing
  portion, triggers download, and if `"name"` changed calls
  `SaverWeather.rename_all(old_name, new_name)`.

**File:** `packages/weather/datavia/weather/saver_weather.py`

- Add `rename_all(old_source_name, new_source_name)`:
  - Opens a SQLite transaction.
  - Fetches all `file_path` values for `source_name = old_source_name`.
  - For each path calls `os.rename(old, new)` (derive new path from naming
    convention).
  - Updates all rows in one `UPDATE` statement.
  - Commits; rolls back on any `OSError`.

**File:** `datavia/core/datavia.py`

- `check_pipelines()` — iterates `self.pipelines`, for each calls
  `pipeline.get_config()` and compares against DB rows; collects all findings;
  raises `DataviaConsistencyError` with a summary if any findings exist.

### Test additions

`tests/test_weather_pipeline_unit.py`:

- `replace=False` (default): second init with same source → DB rows from first
  run are detected and merged; only missing dates are downloaded.
- `replace=True`: second init → all previous DB rows deleted; full range
  downloaded fresh.
- `get_config()` returns merged date range spanning both runs.
- `reconfigure({"date_end": "2025-06-30"})` → only the new months are
  downloaded; existing coverage untouched.
- `reconfigure({"name": "new_name"})` → `SaverWeather.rename_all` is called
  with correct old/new names; DB rows updated.
- `dv.check_pipelines()` raises `DataviaConsistencyError` when a pipeline's
  DB rows are missing or a file on disk is absent.

---

## 6. Enhancement 5 — ERA5 request chunking and download progress

### Current behaviour

`ERA5Downloader.download()` submits a **single CDS job** that covers the
entire configured date range, all requested variables, and the full bounding
box in one call:

```python
client.retrieve(
    "reanalysis-era5-land",
    {
        "variable": self.variables,   # all variables in one request
        "year":     years,            # all years at once
        "month":    months,
        ...
    },
    output_path,
)
```

This causes two real problems in production:

1. **CDS queue latency** — large jobs (multi-year, many variables, large bbox)
   can sit in the Copernicus queue for hours to **multiple days** before
   processing begins.  The process blocks with no feedback during this time.
2. **Single point of failure** — if the download is interrupted after hours of
   queuing, the entire job must be resubmitted.  Nothing is saved until the
   full file arrives.

### Planned behaviour

#### Request splitting by month (primary axis)

`ERA5Downloader` splits the declared date range into **one CDS job per
calendar month per variable**.  Each job is smaller, enters the queue faster,
and its result is saved to disk independently — so a restart only re-downloads
the months that are missing:

```
Config: 2022-01-01 → 2024-12-31, variables=["2m_temperature", "total_precipitation"]

CDS jobs submitted:
  2022-01  temperature    → era5_HYRAS_2022-01_2m_temperature.nc
  2022-01  precipitation  → era5_HYRAS_2022-01_total_precipitation.nc
  2022-02  temperature    → era5_HYRAS_2022-02_2m_temperature.nc
  ...      (36 months × 2 variables = 72 jobs total)
```

The month boundary is the natural unit because:
- One month of hourly ERA5-Land data for Germany (~1 km, full day range) is
  typically 50–150 MB — a manageable file that exits the queue in minutes
  rather than hours.
- `SaverWeather` already supports multiple files per source; one row per
  monthly file is inserted into `weather_layers` automatically.
- `GetterWeather` queries by `valid_from`/`valid_until` and picks the
  correct monthly file without any change to the query API.

A `chunk_size` config key controls the granularity:

```python
WeatherPipeline(config={
    "source":      "ERA5_land",
    "variables":   ["2m_temperature", "total_precipitation"],
    "date_start":  "2022-01-01",
    "date_end":    "2024-12-31",
    "era5_chunk":  "monthly",   # default — one CDS job per variable per month
    # "era5_chunk": "yearly"   # coarser — one job per variable per year
    # "era5_chunk": "none"     # original single-job behaviour (not recommended
    #                          #   for ranges > ~3 months)
})
```

#### Download progress feedback

CDS jobs have two waiting phases:

1. **Queue wait** — the job is accepted but not yet processed.  Duration is
   unpredictable (seconds to days depending on CDS load).
2. **Transfer** — the server streams the file after processing.  Duration
   depends on file size and network speed.

Both phases should surface progress to the user:

- **Queue phase** — log the CDS job ID and elapsed waiting time every
  60 seconds so the user knows the process is alive:

  ```
  INFO  datavia.weather.era5_downloader — CDS job abc-123 queued.
        Status: queued (elapsed: 00:02:10)
  INFO  datavia.weather.era5_downloader — CDS job abc-123 queued.
        Status: queued (elapsed: 00:04:20)
  INFO  datavia.weather.era5_downloader — CDS job abc-123 processing.
  ```

- **Transfer phase** — a `tqdm` progress bar showing bytes transferred,
  total expected size, transfer speed, and estimated time remaining.  `tqdm`
  is already in the pixi environment:

  ```
  Downloading ERA5 2022-01 2m_temperature:
    42%|████████░░░░░░░░░| 58.3M/138.6M [00:23<00:31, 2.54MB/s]
  ```

  The progress bar is written to `stderr` (standard `tqdm` default) so it
  does not pollute log files or stdout data pipelines.

- **Multi-job overview** — when multiple monthly chunks are downloaded
  sequentially, a second outer `tqdm` bar tracks overall progress:

  ```
  ERA5 2022–2024 (2m_temperature):  17%|███░░░░░░░░| 12/72 jobs [14:32<1:08:00]
  ```

#### Wall-clock timeout for CDS queue waiting

A configurable `cds_queue_timeout` prevents the process from blocking
indefinitely when the Copernicus queue is saturated:

```python
WeatherPipeline(config={
    ...
    "cds_queue_timeout": 3600,   # abort after 1 h of queue waiting per job
                                  # (default: None → wait forever)
})
```

When the timeout is exceeded the job is cancelled on the CDS side (via
`cdsapi.Client.delete(job_id)`) and a `TimeoutError` is raised with the
job ID so the user can resume or inspect the job on the CDS web UI.  Because
the request was split into monthly chunks, already-completed chunks are
not lost — only the timed-out chunk needs to be retried.

### Implementation notes

**File:** `packages/weather/datavia/weather/era5_downloader.py`

- Add `_iter_monthly_chunks(date_start, date_end) -> Iterator[tuple[str, str]]`
  — yields `(chunk_start, chunk_end)` pairs for each calendar month in the
  range, respecting partial months at the boundaries.
- `download()` iterates over chunks and calls a new `_download_chunk(start,
  end, variable) -> str` for each, accumulating returned file paths.
- `_download_chunk` wraps the existing `client.retrieve()` call, adds the
  queue-wait logging loop (polling `client.status(job_id)` every 60 s), and
  wraps the byte-transfer with a `tqdm` bar.
- Honour `cds_queue_timeout`: track elapsed queue time; call
  `client.delete(job_id)` and raise `TimeoutError` if the limit is exceeded.
- Return a `list[str]` of file paths instead of a single `str`; the
  composite downloader and pipeline must handle the list accordingly.

**File:** `packages/weather/datavia/weather/composite_downloader.py`

- `download()` must accept that each sub-downloader may return a `list[str]`
  and flatten all results before passing to `SaverWeather`.

**File:** `packages/weather/datavia/weather/pipeline.py`

- `update_data()` must iterate over the list of paths and call
  `self.saver.save()` for each one.
- Add `"era5_chunk"` and `"cds_queue_timeout"` to `_KNOWN_CONFIG_KEYS`.

### Test additions

`tests/test_weather_pipeline_unit.py`:

- `_iter_monthly_chunks("2024-01-15", "2024-03-20")` → yields
  `("2024-01-15", "2024-01-31")`, `("2024-02-01", "2024-02-29")`,
  `("2024-03-01", "2024-03-20")`.
- Mock `client.retrieve` to return instantly; verify `download()` is called
  once per month (3 times for a 3-month range).
- Mock `client.status` to always return `"queued"`; verify `TimeoutError`
  is raised after `cds_queue_timeout` seconds.
- Verify that already-saved monthly files are skipped when `update_data()`
  is called a second time (integration with Enhancement 3 / `CoverageManager`).

---

## 7. Affected files

| File | Change type |
|---|---|
| `datavia/library/interpolation.py` | Add `input_crs` parameter; add `temporal_resolution` parameter |
| `packages/weather/datavia/weather/getter_weather.py` | Wire `crs_coords` through to `interpolate_netcdf` |
| `packages/weather/datavia/weather/pipeline.py` | Add `temporal_resolution` config key; add `replace` param; add `get_config()`; add `reconfigure()` |
| `packages/weather/datavia/weather/hyras_downloader.py` | Raise `ValueError` for `temporal_resolution="hourly"` |
| `packages/weather/datavia/weather/coverage_manager.py` | **New file** — incremental download manager |
| `packages/weather/datavia/weather/saver_weather.py` | ✅ `list_managed_files()` + `delete_registration()` implemented (BUG-01 done); add `rename_all()` |
| `datavia/core/datavia.py` | Add `check_pipelines()` |
| `packages/weather/datavia/weather/era5_downloader.py` | Add monthly chunking, queue-wait logging, `tqdm` transfer bar, `cds_queue_timeout` |
| `packages/weather/datavia/weather/composite_downloader.py` | Handle `list[str]` return from sub-downloaders |
| `tests/test_weather_pipeline_unit.py` | New test cases (all enhancements) |

---

## 8. Verification checklist

```bash
# Unit tests — no network required
pytest tests/test_weather_pipeline_unit.py -v

# CRS reprojection smoke test (in REPL, requires a HYRAS .nc file)
from datavia.weather import WeatherPipeline
import numpy as np

pipeline = WeatherPipeline(config={
    "source": "HYRAS", "variables": ["2m_temperature"],
    "date_start": "2024-06-01", "date_end": "2024-06-30",
})
# Query in EPSG:3035 metres — should not raise
values = pipeline.get_data(
    coords=np.array([[4_500_000, 3_000_000]]),
    crs_coords="EPSG:3035",
    variable="2m_temperature",
    datetime_utc="2024-06-15T12:00:00",
)

# Temporal resolution guard
from datavia.weather import WeatherPipeline
try:
    WeatherPipeline(config={
        "source": "HYRAS", "variables": ["2m_temperature"],
        "date_start": "2024-01-01", "date_end": "2024-01-31",
        "temporal_resolution": "hourly",
    })
except ValueError as exc:
    print(exc)   # HYRAS does not support hourly resolution

# Incremental download smoke test — second call should issue no requests
pipeline = WeatherPipeline(config={
    "source": "ERA5_land", "variables": ["2m_temperature"],
    "date_start": "2024-01-01", "date_end": "2024-06-30",
})
pipeline.update_data()   # downloads Jan–Jun
pipeline2 = WeatherPipeline(config={
    "source": "ERA5_land", "variables": ["2m_temperature"],
    "date_start": "2024-01-01", "date_end": "2024-06-30",
})
pipeline2.update_data()  # should issue zero CDS requests (already on disk)

# Reconfigure + rename
pipeline.reconfigure({"name": "era5_de_2024", "date_end": "2024-12-31"})
# Jul–Dec downloaded; files renamed to era5_de_2024_*.nc; DB updated

# Orchestrator audit
from datavia import Datavia
dv = Datavia(pipelines=[pipeline])
dv.check_pipelines()  # should pass silently
```

---

## 9. Known risks and open questions

### ⚠️ Bbox subtraction is non-trivial

Exact 2-D bounding-box difference can produce an L-shaped or otherwise
non-rectangular result.  The first implementation should tile into at most two
axis-aligned rectangles and document the approximation.  If exact coverage
tracking is needed, introduce `shapely` as an optional dependency.

### ⚠️ SQLite concurrency

`weather_layers` is a single SQLite file.  Concurrent writes from two processes
(or two `Datavia` instances in threads) can cause `database is locked` errors.
`BEGIN EXCLUSIVE` in `rename_all` and `register` is a safe first step;
full multi-writer support would require migrating to PostgreSQL.

### ⚠️ `reconfigure()` mid-download

If `update_data()` is running in a background thread when `reconfigure()` is
called, the in-progress download and the new config may conflict.  An
asyncio-safe cancellation token or a threading lock should guard
`self._effective_config` writes.

### ℹ️ `replace=True` and disk cleanup

`replace=True` removes DB rows but does **not** delete files from disk (they
may be shared by another pipeline or needed for debugging).  Document this
clearly; add a separate `pipeline.purge()` method if physical deletion is
needed.

### ℹ️ `check_pipelines()` severity levels

Decide whether each finding type (name collision, orphaned file, unregistered
file) raises an exception, logs a warning, or returns a structured report.
A structured report (`list[ConsistencyFinding]`) is the most flexible; raising
on collisions and warning on orphaned files is a pragmatic default.

### ✅ Design issue — disk↔DB reconciliation belongs in the Pipeline, not the Saver (RESOLVED)

**Previously:** a systemic contradiction across all pipelines and the core
interface (root cause of BUG-01).

**Fixed in April 2026** via the Option B refactor.

The component responsibility model is now enforced:

| Component | Responsibility | DB reads | DB writes |
|---|---|---|---|
| **Saver** | File writes + file inventory | never | INSERT/DELETE |
| **Getter** | DB reads + file-data extraction | SELECT only | never |
| **Pipeline** | Orchestration; disk↔DB reconciliation | via Getter | via Saver |

Key primitives added:
- `Saver.list_managed_files()` — pure disk scan, no DB
- `Saver.delete_registration(uri)` — DB DELETE only, no filesystem
- `Saver.save(..., register_only=True)` — registers without copying
- `Getter.get_registered_uris()` — DB SELECT returning file paths
- `Pipeline.sync_files_and_database()` — coordinator (concrete, base class)

`TiffSaver.check_data_exists()` is the only remaining legacy violation (it
SELECT from `raster_layers`); it is marked deprecated and will be removed once
all callers migrate to `Pipeline.sync_files_and_database()`.

