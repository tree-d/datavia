# Weather Pipeline — Next Steps

> **Purpose:** Ordered action plan for fixing confirmed bugs and implementing
> design enhancements.  Cross-references `weather_pipeline_status_report_2026-04.md`
> (bugs) and `weather_further_enhancements.md` (features).
>
> **Current state:** Pipeline is functionally correct and produces
> climatologically plausible results.  Steps 1–7 are **done**: all blocking
> bugs have been fixed, the first two enhancements (CRS-agnostic input,
> configurable temporal resolution) are implemented, and `CoverageManager`
> provides incremental downloads.  Steps 8 → 9 are the remaining work.

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

## ✅ Step 1 — Fix BUG-06: year-end timestamp boundary *(done — 2026-04-28)*

**Fix:** `valid_until` now rounded up to `23:59:59` in
`extract_netcdf_layer_metadata()` so year-end queries succeed.

**Files changed:** `datavia/library/formats.py`

---

## ✅ Step 2 — Fix BUG-01 / BUG-09 / BUG-10: sync and source_name hygiene *(done — pre-2026-04-28)*

**Fix:** `Pipeline.sync_files_and_database()` implemented; `update_data()`
calls it before downloading. Duplicate-row accumulation stopped.

**Known issue (non-blocking):** DESIGN-01 — composite pipelines register DWD
files under the grid source name (e.g. `"HYRAS"`) rather than
`"DWD_stations"`. Does not break current functionality; document in
Enhancement 3 if per-source incremental logic is needed.

**Files changed:** `packages/weather/datavia/weather/pipeline.py`,
`datavia/core/interfaces.py`

---

## ✅ Step 3 — Fix BUG-07: deterministic filenames *(done — 2026-04-28)*

**Fix:** `SaverWeather._build_dest_stem()` derives the filename from NC
metadata before copying. `HYRAS_tas_2024.nc` instead of `HYRAS_tmpXXX.nc`.

**Decision:** `CoverageManager` (Step 7) uses DB as source of truth, not
filenames. Deterministic names remain for human readability only.

**Files changed:** `packages/weather/datavia/weather/saver_weather.py`

---

## ✅ Step 4 — Fix BUG-05 + BUG-08: batch interp + NaN fill *(done — 2026-04-28)*

**Fix:** `interpolate_netcdf` accepts `np.ndarray` for lats/lons; opens NC
once, interpolates all points in one call. NaN fill via `ffill/bfill` before
bilinear.

**Performance:** 365-day × 8-station query: ~5 min → <5 s.

**Files changed:** `datavia/library/interpolation.py`,
`packages/weather/datavia/weather/getter_weather.py`

---

## ✅ Step 5 — Enhancement 1: CRS-agnostic input *(done — 2026-04-29)*

**Fix:** `interpolate_netcdf` and `GetterWeather.get_data()` accept
`input_crs` parameter. Both projected (HYRAS EPSG:3035) and geographic (ERA5)
branches reproject input correctly.

**Files changed:** `datavia/library/interpolation.py`,
`packages/weather/datavia/weather/getter_weather.py`

---

## ✅ Step 6 — Enhancement 2: temporal resolution config key *(done — 2026-04-29)*

**Fix:** `"temporal_resolution": "daily" | "hourly"` pipeline config key.
`interpolate_netcdf` returns 24-element array for hourly. HYRAS raises
`ValueError` at init for hourly (daily-only source).

**Files changed:** `packages/weather/datavia/weather/pipeline.py`,
`datavia/library/interpolation.py`,
`packages/weather/datavia/weather/hyras_downloader.py`,
`packages/weather/datavia/weather/getter_weather.py`,
`packages/weather/datavia/weather/composite_downloader.py`

---

## ✅ Step 6.5 — ERA5 `nc_variable_map` *(done — 2026-05-05)*

**Issue:** ERA5 NetCDF files store `2m_temperature` under short name `t2m`;
`_resolve_variables()` registered `t2m` in DB but getter queried
`2m_temperature` → no match.

**Fix:** Added `"nc_variable_map"` to `SOURCE_REGISTRY["ERA5_land"]` mapping
`t2m → 2m_temperature`, `tp → total_precipitation`,
`ssrd → surface_solar_radiation_downwards`. Same pattern already used for
HYRAS.

**Files changed:** `packages/weather/datavia/weather/source_registry.py`

---

## ✅ Step 7 — Enhancement 3: `CoverageManager` and incremental downloads *(done — 2026-04-29)*

**Implementation:** `CoverageManager(source_name, variables)` loads existing
`weather_layers` rows at init. `missing_spatiotemporal(bbox, date_start,
date_end)` runs 2-D (spatial × temporal) subtraction using Shapely for bbox
intersection and the *4-strip decomposition* for exact axis-aligned remainder
strips. Returns `list[CoverageCell]`; each cell triggers one downloader call.

`WeatherPipeline.update_data()` instantiates `CoverageManager`, gets missing
cells, and creates one `CompositeWeatherDownloader` per cell. When fully
covered logs "nothing to download" and returns `True` with zero network calls.

**Tests added:** `TestCoverageManager` (6 tests): fully covered → empty list,
wider time range → 2 temporal gaps, wider bbox → spatial strip, 2-D overlap
(Berlin 1990-2000 + East Germany 1980-2010 → 6 cells), invalid date order →
`ValueError`, `update_data()` with full coverage → downloader never instantiated.

**Files changed:** `packages/weather/datavia/weather/coverage_manager.py`
(new), `packages/weather/datavia/weather/pipeline.py`,
`packages/weather/datavia/weather/__init__.py`,
`packages/weather/pyproject.toml` (added `shapely>=2.0.4` dependency)

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

| Step | Bug / Enhancement | Effort | Prerequisite | Unblocks | Status |
|---|---|---|---|---|
| **1** | BUG-06 — year-end `valid_until` | S | — | Correct annual queries | ✅ 2026-04-28 |
| **2** | BUG-01 / BUG-09 / BUG-10 — sync + source_name | S | — | Clean DB state | ✅ pre-2026-04-28 |
| **3** | BUG-07 — deterministic filenames | M | — | Enhancement 3 | ✅ 2026-04-28 |
| **4** | BUG-05 + BUG-08 — batch interp + NaN fill | M | — | Enhancement 1 | ✅ 2026-04-28 |
| **5** | Enh. 1 — wire CRS stub | S | Step 4 | CRS-agnostic queries | ✅ 2026-04-29 |
| **6** | Enh. 2 — temporal resolution config key | S | — | Hourly ERA5 queries | ✅ 2026-04-29 |
| **6.5** | ERA5 `nc_variable_map` | XS | — | ERA5 queries work | ✅ 2026-05-05 |
| **7** | Enh. 3 — `CoverageManager`, incremental DL | L | Steps 1–4 | Enhancement 4 | ✅ 2026-04-29 |
| **8** | Enh. 4 — pipeline lifecycle (merge, rename) | L | Step 7 | Full lifecycle API | |
| **9** | Enh. 5 — ERA5 chunking + progress | L | Steps 7–8, CDS creds | Production ERA5 use | |

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
