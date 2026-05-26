# Weather Pipeline — Next Steps

> **Purpose:** Ordered action plan for remaining work.
>
> **Current state (2026-05-06):** Steps 1–7 are **done**.  Step 8 is
> **partially done** (`get_config` / `reconfigure` implemented; `rename_all`
> and `check_pipelines` deferred).  One open ERA5-specific bug (`valid_time`
> dimension name) blocks real CDS end-to-end testing.  Step 9 (ERA5 request
> chunking) is the highest-priority remaining item.

---

## Open bug — ERA5 `valid_time` dimension (blocks Step 9 real-world testing)

**Status:** confirmed, not yet fixed.

ERA5 files retrieved via the CDS API and decoded with `cfgrib` use **`valid_time`**
as the time dimension name, not `"time"`.  `extract_netcdf_layer_metadata()` in
`datavia/library/formats.py` only checks `if "time" in ds.coords`, so ERA5
files land with `valid_from = None`, `valid_until = None`, and a stem ending
in `_unknown`.  Any time-range query against these rows returns nothing.

**Fix required** in `datavia/library/formats.py`:
- In `extract_netcdf_layer_metadata()` resolve the time coordinate as
  `ds.coords.get("time") or ds.coords.get("valid_time")` before reading
  `valid_from` / `valid_until`.
- Same guard in `_build_dest_stem()` so the year suffix is extracted correctly.
- Add a unit test: ERA5-shaped dataset with only `valid_time` → correct
  `valid_from`, `valid_until`, and stem `ERA5_land_2m_temperature_2024`.

**Files to change:** `datavia/library/formats.py`,
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

## Step 9 — Enhancement 5: ERA5 request chunking and progress *(highest priority)*

**Prerequisite:** `valid_time` bug fix (above).  Real testing also requires
CDS credentials.

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
| ERA5 `valid_time` bug | `formats.py` time-coord fallback | S | open |
| 8a | `get_config` + `reconfigure` | M | ✅ 2026-05-06 |
| 8b | `rename_all` | M | deferred |
| 8c | `check_pipelines` | M | deferred |
| **9** | **ERA5 chunking + progress** | **L** | **next** |

**Effort key:** S = hours · M = 1–2 days · L = 3–5 days

