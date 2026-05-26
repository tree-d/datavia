# Weather Pipeline — Next Steps

> **Purpose:** Ordered action plan for remaining work.
>
> **Current state (2026-05-08):** Steps 1–7 are **done**.  Step 8 is
> **partially done** (`get_config` / `reconfigure` implemented; `rename_all`
> and `check_pipelines` deferred).  The ERA5 `valid_time` dimension bug is
> **fixed**.  Step 9 (ERA5 request chunking) is **done**.

---

## Open bug — ERA5 `valid_time` dimension ~~(blocks Step 9 real-world testing)~~

**Status:** ✅ fixed 2026-05-08.

ERA5 files retrieved via the CDS API and decoded with `cfgrib` use **`valid_time`**
as the time dimension name, not `"time"`.  `extract_netcdf_layer_metadata()` in
`datavia/library/formats.py` only checked `if "time" in ds.coords`, so ERA5
files landed with `valid_from = None`, `valid_until = None`, and a stem ending
in `_unknown`.  Any time-range query against these rows returned nothing.

**Fix applied** in `datavia/library/formats.py`:
- `extract_netcdf_layer_metadata()` now resolves the time coordinate as
  `ds.coords["time"] if "time" in ds.coords else ds.coords.get("valid_time")`,
  so ERA5 `valid_time` files produce correct `valid_from` / `valid_until`.
- `_build_dest_stem()` in `saver_weather.py` inherits the fix automatically
  because it delegates entirely to `extract_netcdf_layer_metadata()`.
- Two unit tests added to `TestExtractNetcdfLayerMetadata` in
  `tests/test_weather_pipeline_unit.py`:
  - `test_valid_time_coord_metadata` — ERA5-shaped dataset with only
    `valid_time` → correct `valid_from` and `valid_until`.
  - `test_valid_time_coord_stem` — same dataset → stem
    `ERA5_land_2m_temperature_2024` (not `ERA5_land_2m_temperature_unknown`).

**Files changed:** `datavia/library/formats.py`,
`tests/test_weather_pipeline_unit.py`.

---

## Dependency map

```
Step 8 remainder (rename_all, check_pipelines)
    │
    ├── valid_time bug fix  ──────────────────────────┐
    │                                                  │
    └──────────────────────────────────────────────────▼
                                              Step 9 (ERA5 chunking + progress)
                                              (also requires CDS credentials)
```

---

## ✅ Steps 1–7 — done

| Step | Item | Done |
|---|---|---|
| 1 | BUG-06 — year-end `valid_until` rounded to 23:59:59 | 2026-04-28 |
| 2 | BUG-01/09/10 — `sync_files_and_database`, `source_name` hygiene | pre-2026-04-28 |
| 3 | BUG-07 — deterministic filenames via `_build_dest_stem()` | 2026-04-28 |
| 4 | BUG-05/08 — batch coordinate interpolation + NaN fill | 2026-04-28 |
| 5 | Enh. 1 — CRS-agnostic `get_data` (`input_crs` param) | 2026-04-29 |
| 6 | Enh. 2 — `"temporal_resolution"` config key (daily/hourly) | 2026-04-29 |
| 6.5 | ERA5 `nc_variable_map` (`t2m`→`2m_temperature` etc.) | 2026-05-05 |
| 7 | Enh. 3 — `CoverageManager` + incremental downloads | 2026-04-29 |

---

## ⚠️ Step 8 — Enhancement 4: pipeline lifecycle *(partial — 2026-05-06)*

### Done

- `WeatherPipeline.__init__` gains `replace: bool = False`.
- `get_config()` returns a copy of the effective config.
- `reconfigure(config_updates, replace=False)` applies delta or full
  replacement, validates, rebuilds downloader and getter.
  - Guards against changing `"source"` (immutable after construction).
  - State is unchanged if validation fails.
- `SoilPipeline.configure()` renamed to `reconfigure()` for API consistency
  across pipelines.
- 8 new tests in `TestWeatherPipelineLifecycle`.

**Files changed:** `packages/weather/datavia/weather/pipeline.py`,
`packages/soil/datavia/soil/pipeline.py`,
`tests/test_weather_pipeline_unit.py`,
`tests/test_soil_pipeline_unit.py`,
`tests/test_soil_e2e.py`,
`packages/soil/README.md`

### Remaining — lower priority than Step 9

**A — `SaverWeather.rename_all(old_source_name, new_source_name)`**

Atomically renames all managed files on disk and updates every matching
`weather_layers` row in a single SQLite transaction.  Needed when a pipeline
is renamed after data is already on disk.

- Open a SQLite transaction.
- Fetch all `uri` values for `source_name = old_source_name`.
- For each: `os.rename(old_path, new_path)`.  Roll back the whole transaction
  if any rename fails.
- `UPDATE weather_layers SET source_name = new, uri = new_path WHERE uri = old_path`.

**Files:** `packages/weather/datavia/weather/saver_weather.py`.

**B — `Datavia.check_pipelines()`**

Read-only orchestrator audit.  Raises `DataviaConsistencyError` on:
- name collisions between registered pipelines,
- DB rows whose file paths no longer exist on disk,
- declared coverage with no corresponding DB rows.

**Files:** `datavia/core/datavia.py`, `datavia/core/interfaces.py`
(new `DataviaConsistencyError`).

---

## Step 9 — Enhancement 5: ERA5 request chunking and progress *(done 2026-05-08)*

**Prerequisite:** `valid_time` bug fix (done).

### What was implemented

**1. `ERA5Downloader._iter_monthly_chunks(date_start, date_end)`**

Static method that splits a date range into `(chunk_start, chunk_end)` pairs,
one per calendar month, clipping the first and last months to the requested
boundaries.  Returns a sorted list.

**2. Monthly chunking in `ERA5Downloader.download()`**

`download()` now:
- Applies `buffer_days` to the effective start before chunking.
- Iterates monthly chunks with a `tqdm` outer bar (`[N/total months]`) when
  `tqdm` is installed; falls back silently to a plain iterator if not.
- Submits one `reanalysis-era5-land` CDS job per chunk.
- Returns a **newline-joined string of all produced paths** (one per month),
  which is the same multi-path convention already used by
  `CompositeWeatherDownloader`, so `WeatherPipeline.update_data()` needs no
  changes.

**3. Queue-wait logging via `ERA5Downloader._wait_for_cds_job(job)`**

Polls the job every 60 s while `status == "queued"`, logging:
```
ERA5Downloader: job 922cc8da queued — waiting 60 s (total 0:01:00)
```
When the job transitions to `"running"` that is logged once, then control
returns to the caller for the file download.

**4. `cds_queue_timeout` config key**

Optional integer (seconds).  When the CDS queue wait for a single chunk
exceeds the limit, the job is cancelled and `TimeoutError` is raised with
the job ID.  Added to `_KNOWN_CONFIG_KEYS` in `pipeline.py` and forwarded
through `CompositeWeatherDownloader` to `ERA5Downloader`.

**5. Module-level `cdsapi` import**

`cdsapi` is now imported at module level (with a graceful `None` fallback if
not installed) so that `unittest.mock.patch` can replace it in tests without
`ImportError`.

**6. `tqdm` added to `packages/weather/pyproject.toml`**

```toml
"tqdm>=4.66,<5",
```

### Files changed

| File | What changed |
|---|---|
| `packages/weather/datavia/weather/era5_downloader.py` | Monthly chunking, queue logging, tqdm, timeout, module-level `cdsapi` |
| `packages/weather/datavia/weather/composite_downloader.py` | Forward `cds_queue_timeout`; update docstring for multi-path ERA5 |
| `packages/weather/datavia/weather/pipeline.py` | Add `cds_queue_timeout` to `_KNOWN_CONFIG_KEYS` |
| `packages/weather/pyproject.toml` | Add `tqdm>=4.66,<5` dependency |
| `tests/test_weather_pipeline_unit.py` | 16 new tests across 3 new test classes |

### New test classes (all pass without CDS credentials)

- `TestERA5DownloaderMonthlyChunking` (9 tests) — chunking logic for all
  boundary conditions, year crossings, leap years, and single days.
- `TestERA5DownloaderDownloadMocked` (5 tests) — mocked `cdsapi.Client`
  verifying job count, multi-path return, buffer-day month expansion, and
  `TimeoutError` on queue timeout.
- `TestWeatherPipelineCdsQueueTimeout` (2 tests) — config key acceptance and
  forwarding to the grid downloader.

---


### Why this matters

The current `ERA5Downloader` issues a single CDS job for the entire date range
and bounding box.  CDS queues large jobs with an unpredictable wait time (can
exceed 30 min).  There is no progress feedback and no way to resume a partial
download after a timeout.  Monthly chunking keeps individual jobs small (1–5 min
queue time) and allows resuming from the last completed month.

### Design

**1. Monthly chunking in `ERA5Downloader`**

Split the requested date range into one CDS job per calendar month per variable:

```python
# One request: variable=["2m_temperature"], year="2024", month="01"
# One request: variable=["2m_temperature"], year="2024", month="02"
# ...
```

`download()` returns a list of file paths (one per month-chunk) rather than a
single path string.  `CompositeWeatherDownloader` is updated to concatenate
these lists from all sub-downloaders.

**2. Queue-wait logging**

While a CDS job is queued, log the job ID and elapsed wait time every 60 s:

```
ERA5Downloader: job 922cc8da queued — waiting 60 s (total 0:01:00)
ERA5Downloader: job 922cc8da queued — waiting 60 s (total 0:02:00)
ERA5Downloader: job 922cc8da running
```

**3. `tqdm` progress bar**

- Outer bar: job count (e.g. `[3/12] months`).
- Inner bar: byte transfer progress for the active download.

Add `tqdm` to `packages/weather/pyproject.toml` dependencies.

**4. `cds_queue_timeout` config key**

Optional integer (seconds).  When the CDS queue wait for a single job exceeds
this value, cancel the job and raise `TimeoutError` with the job ID.

```python
WeatherPipeline(config={
    "source": "ERA5_land",
    ...
    "cds_queue_timeout": 1800,   # cancel after 30 min in queue
})
```

Add `"cds_queue_timeout"` to `_KNOWN_CONFIG_KEYS` in `pipeline.py`.

**5. `CompositeWeatherDownloader` — accept `list[str]` from sub-downloaders**

Currently the downloader contract is `download() → str` (newline-joined paths
or `"failed"`).  Change to `download() → list[str]` for all sub-downloaders
and update `WeatherPipeline.update_data()` to iterate the list directly.

### Files to change

| File | What changes |
|---|---|
| `packages/weather/datavia/weather/era5_downloader.py` | Monthly chunking, queue logging, tqdm, timeout |
| `packages/weather/datavia/weather/composite_downloader.py` | Accept `list[str]` from sub-downloaders |
| `packages/weather/datavia/weather/pipeline.py` | Add `cds_queue_timeout` to known keys; iterate `list[str]` |
| `packages/weather/pyproject.toml` | Add `tqdm` dependency |
| `tests/test_weather_pipeline_unit.py` | Unit tests for chunking logic and timeout |

### Test additions (no CDS credentials needed)

- `ERA5Downloader` with a mocked CDS client → date range 2024-01 to 2024-03
  produces exactly 3 job calls (one per month).
- Queue-wait logger emits a message every 60 s (mock `time.sleep`).
- `cds_queue_timeout` exceeded → `TimeoutError` raised with job ID.
- `CompositeWeatherDownloader.download()` returns a flat `list[str]` when
  sub-downloaders return multi-element lists.

---

## Summary table

| Step | Item | Effort | Status |
|---|---|---|---|
| 1–7 | Bugs + Enhancements 1–3 | — | ✅ done |
| ERA5 `valid_time` bug | `formats.py` time-coord fallback | S | ✅ 2026-05-08 |
| 8a | `get_config` + `reconfigure` | M | ✅ 2026-05-06 |
| 8b | `rename_all` | M | deferred |
| 8c | `check_pipelines` | M | deferred |
| **9** | **ERA5 chunking + progress** | **L** | **✅ 2026-05-08** |

**Effort key:** S = hours · M = 1–2 days · L = 3–5 days

