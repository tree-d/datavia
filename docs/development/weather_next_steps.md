# Weather Pipeline — Next Steps

> **Current state (2026-05-08):** Steps 1–9 complete.  One open bug (filename collision).
> Two deferred Step-8 items (low priority).

---

## Plan

This is the current execution plan for the review feedback still open on PR #20.
The order is chosen to reduce correctness risk first, then clean up integration
and documentation issues.

1. ✅ Refactor `datavia/library/interpolation.py` — removed `_fill_nan_along_axis`
  and `_prefill_nodata`; inlined `DataArray.interpolate_na` directly inside
  `interpolate_netcdf` (2026-05-27).
2. ✅ Fix the runtime issues in the weather pipeline path (2026-05-27):
  - `datavia/cli_config.py` — added `"source"`, `"date_start"`, `"date_end"`;
    switched variable name to `"2m_temperature"`
  - `packages/weather/datavia/weather/composite_downloader.py` — filter
    `"failed"` sentinel and flatten multi-path outputs in `download()`
  - `packages/weather/datavia/weather/getter_weather.py` — coerce
    single-element datetime lists to scalar before `is_multi_time` check
  - `docs/user_guide/examples/example_weather_query.py` — renamed station
    dict key `"id"` → `"name"`
3. ✅ Normalize weather variable naming (2026-05-27):
  - `packages/weather/datavia/weather/dwd_downloader.py` — added
    `_PIPELINE_TO_OPEN_METEO` mapping; `DWDStationDownloader` now accepts
    pipeline-level names (e.g. `2m_temperature`) and translates them to
    Open-Meteo API names internally, renaming response columns back before
    writing Parquet; fixed docstring typo
  - `tests/test_weather_e2e.py` — updated `_VARIABLE` constant from
    `"temperature_2m"` to `"2m_temperature"` to match pipeline naming
4. ✅ Update stale documentation (2026-05-27):
  - `docs/development/known_issues.md` — Bug C marked fixed; description updated
    to match current `_build_dest_stem()` implementation
  - This file — Bug C section updated to ✅
5. ✅ Align lint and style-only feedback (2026-05-27):
  - `pyproject.toml` — added `I001` to `tests/**` per-file ignores
  - `packages/weather/LICENSE` — removed trailing `s` from `***Contact:***s`
  - `datavia/core/downloader_api.py` — "Initialise" → "Initialize"
  - `datavia/core/downloader_url.py` — "Initialise" → "Initialize"; removed
    redundant init-level URL log; fixed double-skip bug (gate file-open mode on
    HTTP 206 vs 200); replaced `while`/`retry_count` loop with `for`/`else`;
    used `continue` for empty chunks

---

## ✅ Fixed — Bug C: filename collision in `_build_dest_stem()`

Fixed in BUG-07 (2026-04-28).  `_build_dest_stem()` now encodes
`{source_name}_{variable}_{YYYYmm_start}_{YYYYmm_end}_{bbox_hash}`, making
filenames unique per spatial region and time window.

---

## Deferred — Step 8 remainder (low priority)

**A — `SaverWeather.rename_all(old_source_name, new_source_name)`**

Atomically rename all managed files and update every matching `weather_layers`
row in a single SQLite transaction.  Needed when a pipeline is renamed after
data is already on disk.

**File:** `packages/weather/datavia/weather/saver_weather.py`

**B — `Datavia.check_pipelines()`**

Read-only orchestrator audit — raises `DataviaConsistencyError` on name
collisions, missing files, or declared coverage with no DB rows.

**Files:** `datavia/core/datavia.py`, `datavia/core/interfaces.py`

---

## ✅ Done — Steps 1–9

| Step | Item | Date |
|---|---|---|
| 1 | BUG-06 — year-end `valid_until` rounded to 23:59:59 | 2026-04-28 |
| 2 | BUG-01/09/10 — `sync_files_and_database`, `source_name` hygiene | pre-2026-04-28 |
| 3 | BUG-07 — deterministic filenames via `_build_dest_stem()` | 2026-04-28 |
| 4 | BUG-05/08 — batch coordinate interpolation + NaN fill | 2026-04-28 |
| 5 | Enh. 1 — CRS-agnostic `get_data` (`input_crs` param) | 2026-04-29 |
| 6 | Enh. 2 — `"temporal_resolution"` config key (daily/hourly) | 2026-04-29 |
| 6.5 | ERA5 `nc_variable_map` (`t2m`→`2m_temperature` etc.) | 2026-05-05 |
| 7 | Enh. 3 — `CoverageManager` + incremental downloads | 2026-04-29 |
| 8 | Enh. 4 — pipeline lifecycle (`get_config`, `reconfigure`) | 2026-05-06 |
| 9 | Enh. 5 — ERA5 monthly chunking + CDS queue progress + timeout | 2026-05-08 |
| — | BUG-B — `valid_time` coordinate in ERA5 NetCDF files | 2026-05-08 |

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

