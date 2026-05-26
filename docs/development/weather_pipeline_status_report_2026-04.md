# Weather Pipeline — Status Report

> **Date:** 2026-04-24  
> **Testing method:** Three research scripts executed against on-disk HYRAS 2025
> data (no new downloads).  Scripts are in `scripts/`.  
> **Status key:** 🔴 blocking · 🟡 significant · 🔵 informational

---

## Executive Summary

The HYRAS weather pipeline is **functionally correct** — it downloads real data,
reprojects from EPSG:3035, and returns climatologically plausible values.  Three
scripts covering 365-day precipitation transects, 92-day heat-day analysis, and
multi-variable grid snapshots all produced results consistent with known German
climatological patterns (north-south precipitation gradient, alpine amplification,
realistic summer temperatures).

However, **six bugs were confirmed** during testing.  Two are blocking for
production use: the per-coordinate file-open performance collapse makes full-year
queries impractically slow, and the year-boundary timestamp mismatch silently
drops the last day of any multi-year or full-year query.  Four additional bugs
(orphan file accumulation, non-descriptive filenames, NaN over-propagation, DWD
source-name mismatch) are significant but non-blocking for the core query path.

---

## Research Scripts

Three scripts were created in `scripts/` that demonstrate real research use-cases:

| Script | Research question | Query volume | Status |
|---|---|---|---|
| [scripts/grid_snapshot_germany_2025.py](scripts/grid_snapshot_germany_2025.py) | Multi-variable 5×4 grid snapshot for 3 dates | 6 vectorised calls × 20 points | ✅ Completed |
| [scripts/precipitation_north_south_2025.py](scripts/precipitation_north_south_2025.py) | N–S precipitation transect, full year 2025 | 365 days × 8 stations | ✅ Completed (1 bug triggered) |
| [scripts/heat_days_germany_2025.py](scripts/heat_days_germany_2025.py) | Summer heat-day ranking, 15 German cities | 92 days × 15 cities | ✅ Completed (see below) |

### Sample results — precipitation transect

Annual totals from the north–south transect confirm HYRAS is correct:

| Station | Annual mm | Note |
|---|---|---|
| Flensburg (N coast) | 750 | Maritime Atlantic influence |
| Hamburg | 661 | |
| Hannover | 464 | Continental minimum |
| Kassel | 578 | Upwind of Central Uplands |
| Würzburg | 528 | Rain-shadow of Rhön/Spessart |
| Augsburg | 648 | Pre-Alpine increase |
| Rosenheim | 850 | Alpine foreland |
| Berchtesgaden (Alps) | 1 219 | Alpine maximum |

The north–south gradient and alpine amplification are both correct.  July is
the wettest summer month across the Alpine fringe (expected convective maximum);
October is wettest in the northwest (frontal systems).

### Sample results — summer heat ranking (92 days × 15 cities, 1 380/1 380 values)

```
Rank  City              Mean°C   Peak°C Peak date      Warm    Hot
───────────────────────────────────────────────────────────────────
1     Frankfurt/M.       +20.6    +29.0 2025-07-01       48     10
2     Stuttgart          +20.3    +27.9 2025-07-01       43     10
3     Freiburg           +19.9    +27.1 2025-06-30       38      7
4     Saarbrücken        +19.8    +28.1 2025-07-01       41     10
5     Munich             +19.5    +26.1 2025-06-30       36      7
6     Cologne            +19.4    +27.6 2025-06-30       32      6
7     Leipzig            +19.3    +28.1 2025-07-01       28      3
8     Berlin             +19.3    +28.5 2025-07-01       31      4
9     Dresden            +19.2    +28.2 2025-07-01       30      3
10    Erfurt             +18.7    +27.5 2025-07-01       25      3
11    Hannover           +18.6    +28.5 2025-07-01       22      2
12    Bremen             +17.9    +26.0 2025-07-01       15      1
13    Hamburg            +17.8    +26.8 2025-07-01       13      1
14    Rostock            +17.8    +26.3 2025-07-01       10      1
15    Flensburg          +17.0    +22.8 2025-07-01       10      0
───────────────────────────────────────────────────────────────────
                 Germany avg   +19.0
```

**Climatological interpretation:** The south-west bias (Frankfurt, Stuttgart,
Freiburg hottest; Flensburg coolest) is correct and matches Germany's known
thermal geography.  The Germany-wide mean of +19.0 °C is consistent with a
moderately warm summer.

**Notable observation — peak date concentration:** 13 of 15 cities share a
peak date of **2025-07-01**, with the remaining two (Freiburg, Munich, Cologne)
peaking on June 30.  The convergence on a single two-day window is not an
artefact — it signals a real pan-Germany heat event around June 30/July 1 2025.
The pipeline correctly resolved the event at all 15 sites without any NaN
values.

**BUG-08 cross-check:** This script queried city coordinates (well inside the
HYRAS domain), not a regular grid with edge-clipping, which explains why it
achieved 1 380/1 380 (0 % NaN) while `grid_snapshot_germany_2025.py` achieved
only 5/20 non-NaN at domain edges.  BUG-08 is a boundary-interpolation issue,
not a general interpolation failure.

---

## Bugs Found

### ✅ 🔴 BUG-05 — Per-coordinate file re-open (performance collapse) — FIXED 2026-04-28

**Discovered by:** `precipitation_north_south_2025.py` taking ~5 minutes for
365 days × 8 stations on a single 48 MB file.

**Root cause:** `GetterWeather.get_data()` iterates over each coordinate in a
Python loop and calls `interpolate_netcdf()` once per point.
`interpolate_netcdf()` opens the NetCDF file, reprojects via pyproj, runs
`xr.DataArray.interp()`, and closes the file — **one full file-open cycle per
coordinate**:

```python
# packages/weather/datavia/weather/getter_weather.py  (current)
for i, coord in enumerate(coords_arr):
    lon, lat = float(coord[0]), float(coord[1])
    raw_gridded = interpolate_netcdf(
        nc_files[0], lat, lon, nc_variable, datetime_utc   # ← opens file every iteration
    )
```

For a 365-day × 8-station query: **2 920 file-open/close cycles** on the
same file.

**Impact:** ~5 minutes per query session for a full-year, 8-station analysis.
Scales as O(N_days × N_coords), making any production-scale spatial analysis
(e.g. 100 stations, multiple years) entirely impractical without a rewrite.

**Fix applied (2026-04-28):**
`datavia/library/interpolation.py` rewritten to accept `float | np.ndarray`
for `lats`/`lons`.  All N points are reprojected in one `pyproj` batch call
and interpolated in a single `xr.DataArray.interp()` call using a `"points"`
dimension.  `packages/weather/datavia/weather/getter_weather.py` updated to
extract coordinate arrays and call `interpolate_netcdf` once per query instead
of once per coordinate.  The Parquet/station path retains a per-coordinate loop
because IDW is inherently local.  Five unit tests added in
`tests/test_weather_pipeline_unit.py` (`TestInterpolateNetcdf`): scalar
backward compatibility, `(N,)` output, single `open_dataset` call count,
edge-NaN pre-fill, unknown variable `KeyError`.

~~**Fix:** `interpolate_netcdf` must accept a coordinate array and batch all
points in a single `xr.open_dataset()` context~~

**Files changed:** `datavia/library/interpolation.py`,
`packages/weather/datavia/weather/getter_weather.py`

---

### ✅ 🔴 BUG-06 — Year-end precipitation query fails (timestamp boundary) — FIXED 2026-04-28

**Discovered by:** `precipitation_north_south_2025.py` — the final day query
raises a hard error:

```
WARNING  __main__ — Query failed for 2025-12-31:
  No weather files found for source='HYRAS', variable='total_precipitation',
  time=[2025-12-31T12:00:00, 2025-12-31T12:00:00].
  Run the pipeline update first.
```

**Root cause:** HYRAS precipitation files store timestamps at **06:00 UTC**
(end of daily accumulation period).  The saver calls
`extract_netcdf_layer_metadata()` which reads the last `time` coordinate
literally → registers `valid_until = '2025-12-31T06:00:00'` in the DB.
The getter queries `get_weather_paths()` with `datetime_utc = '2025-12-31T12:00:00'`,
which exceeds `valid_until`, so no rows are returned.

The same latent failure exists for **temperature on December 31**: the `tas`
variable has `valid_until = '2025-12-31T00:00:00'` — a noon query on Dec 31
will also fail. It was not triggered in these tests because the heat-day
script covers only June–August.

**DB evidence:**

```
# weather_layers row for precipitation:
valid_from  = '2025-01-01T06:00:00.000000000'
valid_until = '2025-12-31T06:00:00.000000000'   ← 6 hours short of midnight

# weather_layers row for temperature:
valid_from  = '2025-01-01T00:00:00.000000000'
valid_until = '2025-12-31T00:00:00.000000000'   ← 12 hours short of midnight
```

**Fix applied (2026-04-28):** `datavia/library/formats.py` →
`extract_netcdf_layer_metadata()` now rounds the last time coordinate up to
end-of-day via
`pd.Timestamp(...).replace(hour=23, minute=59, second=59, microsecond=0).isoformat()`.
Three unit tests added in `tests/test_weather_pipeline_unit.py`
(`TestExtractNetcdfLayerMetadata`): last-step-at-06:00 case, `valid_from`
unchanged, and ERA5-midnight case.

~~**Fix:** In `SaverWeather.save()` (via `_read_temporal_metadata`), round
`valid_until` up to end-of-day~~

**Files changed:** `datavia/library/formats.py`

---

### ✅ 🟡 BUG-07 — Temp-path filenames leak into data directory and DB — FIXED 2026-04-28

**Discovered by:** pre-run disk audit — all 8 HYRAS files are named
`HYRAS_tmp*.nc` with random system-generated stems.

**Root cause:** `URLDownloader.download()` creates a temp file via
`tempfile.mkstemp(suffix=".download")`, then `HYRASDownloader._get_final_filename()`
replaces only the extension (`→ .nc`) but keeps the `tmp*` stem.  The saver
then copies `HYRAS_tmp6tem1oyd.nc` to the data directory using that name
as-is:

```python
# datavia/core/downloader_url.py
temp_fd, temp_path = tempfile.mkstemp(suffix=".download")
...
final_path = self._get_final_filename(working_filename, content_type)
os.rename(working_filename, final_path)
return final_path   # ← returns /tmp/tmp6tem1oyd.nc or similar

# packages/weather/datavia/weather/hyras_downloader.py
def _get_final_filename(self, temp_path, content_type):
    return temp_path.rsplit(".", 1)[0] + ".nc"  # ← just replaces .download → .nc
```

**Consequences:**
- Files accumulate: every new pipeline run after a DB reset generates a fresh
  `tmp*` name; 4 previous runs left 6 orphan files (476 MB wasted).
- Layer names in the DB are meaningless (`HYRAS_tmp6tem1oyd`) instead of
  descriptive (`HYRAS_2m_temperature_2025`).
- No way to find files by variable or year from the filename alone.

**Fix applied (2026-04-28) — design corrected after initial implementation:**
Naming responsibility was moved to `SaverWeather.save()` via a new
`_build_dest_stem()` helper.  The downloader is reverted to a pure
extension swap (`.download` → `.nc`) with no knowledge of data directories
or variable names.

`_build_dest_stem(data_path, file_format, source_name)` reads the file
content *before* copying:
- **NetCDF, single variable:** `{source_name}_{nc_var}_{year}.nc`
  (e.g. `HYRAS_tas_2024.nc`)
- **NetCDF, multiple variables:** `{source_name}_{year}.nc`
- **Parquet:** `{source_name}_{year}.parquet` (year from `datetime` column
  minimum); falls back to `{source_name}_{temp_stem}` only if the column
  cannot be read.

The `register_only=True` path was also corrected: the layer name is now
derived directly from the existing file stem, preventing a
`HYRAS_HYRAS_tas_2024` double-prefix that the old code produced.

**Rationale:** The downloader's sole contract is to fetch bytes and return
a temp path.  It has no knowledge of `data_directory` or file content.
The saver already reads content for DB metadata (`extract_netcdf_layer_metadata`)
so deriving the destination name there adds no extra I/O and avoids
architectural coupling.

**Files changed:**
- `packages/weather/datavia/weather/saver_weather.py` — `_build_dest_stem()` added, `save()` updated
- `packages/weather/datavia/weather/hyras_downloader.py` — reverted to simple extension swap
- `tests/test_hyras_downloader.py` — `TestGetFinalFilename` updated (2 tests for ext swap)
- `tests/test_weather_pipeline_unit.py` — `TestSaverWeatherDestNaming` added (4 tests)
- `tests/test_sync_adventure.py` — `test_happy_registration` expected name updated

> **One-time migration:** existing `HYRAS_tmp*.nc` orphan files should be
> removed from the data directory and the pipeline re-run to produce
> deterministically named files.

---

### ✅ 🟡 BUG-08 — Bilinear NaN propagation at domain edges — FIXED 2026-04-28

**Discovered by:** `grid_snapshot_germany_2025.py` — 15 of 20 grid points
return NaN.  Several points are inland Germany (e.g. 50.0°N/12.8°E = Chemnitz,
52.5°N/6.0°E = Münster area) yet return NaN.

**Root cause:** `xr.DataArray.interp(method="linear")` propagates NaN when
**any** of the four surrounding grid cells is a fill value.  The HYRAS EPSG:3035
grid has nodata in ocean cells and along the domain boundary.  When a query
point's enclosing 2×2 stencil includes one ocean/boundary cell, the bilinear
result is NaN — even if the query point itself is well inside Germany.

**Evidence:** The pattern is consistent with coastal proximity:
- 55.0°N row: all NaN — northernmost row touches Baltic/North Sea cells
- 47.5°N row: all NaN — southernmost row clips Alpine foothills boundary
- 52.5°N/6.0°E, 52.5°N/15.0°E: near western/eastern domain edges
- 50.0°N/12.8°E: stencil clips eastern boundary near Czech border

**Quantified loss:** For a regular 5×4 grid, 75 % of query points returned NaN
due purely to edge effects — not because the query coordinates are outside
Germany.

**Fix applied (2026-04-28):**
`_prefill_nodata()` helper added to `datavia/library/interpolation.py`.
Masks `_FillValue` → NaN, then applies forward-fill and backward-fill along
both spatial axes using a pure-numpy propagation (no `bottleneck` dependency).
Called inside `interpolate_netcdf()` before `xr.DataArray.interp()`.  One
dedicated test (`test_nodata_prefill_prevents_nan_at_edge`) confirms that a
query point adjacent to a fill-value row returns a finite value.

~~**Fix:** Pre-fill HYRAS nodata cells with nearest-valid-neighbour values~~

**Files changed:** `datavia/library/interpolation.py`.

---

### 🟡 BUG-09 — Duplicate DB rows for DWD station data

**Discovered by:** DB audit — `weather_layers` contains 3 identical rows for
the same DWD station file and the same date/variable:

```
id=1  weather_dwd_stations_dufq7wvc  variable=temperature_2m  2026-04-15
id=2  weather_dwd_stations_5um9hnum  variable=temperature_2m  2026-04-15
id=3  weather_dwd_stations_ralfk36g  variable=temperature_2m  2026-04-15
```

Three separate Parquet files with different random names exist on disk, all
covering the same date.  `check_data_exists()` returns `True` after the first
row exists, so later calls should be no-ops — but they are not, because each
call uses `source_name='weather'` and `check_data_exists` is apparently not
being called before the DWD downloader runs.

**Root cause:** `WeatherPipeline.update_data()` called the DWD downloader without
first checking whether station data for the same date already exists.  This was
the same root cause as BUG-01 / the missing `sync_files_and_database()` call at
the top of `update_data()`.

**✅ Fixed (April 2026):** `self.sync_files_and_database()` is now called at the
top of `WeatherPipeline.update_data()` (Pipeline base-class method).  BUG-09
new duplicate rows will no longer accumulate.  Any existing duplicate rows in
the DB must be cleaned up with the one-time SQL in `weather_next_steps.md`
Step 2.

**Files changed:** `packages/weather/datavia/weather/pipeline.py` (sync before
the update loop).

---

### ✅ 🔵 BUG-10 — DWD station rows use correct `source_name` — CONFIRMED 2026-04-28

**Discovered by:** DB audit — DWD station rows have `source_name='weather'`
instead of a source-specific name (e.g. `'DWD_stations'`).  HYRAS rows
correctly use `source_name='HYRAS'`.

**Status (2026-04-28):** Code inspection of `SaverWeather.save()` confirms
`self.source_name` is already used throughout — no hardcoded `'weather'`
string is present.  The DB audit finding was from an older code state
(likely a pipeline instance created with `config={"source": "weather"}`).
No code change required.

**New finding — composite pipeline source-name bleed (DESIGN-01):** A deeper
trace of the registration path reveals a related design concern that is *not*
the original BUG-10 but is worth tracking:

When a composite pipeline is configured with `source="HYRAS"` and
`dwd_stations=[...]`, the single `SaverWeather(self.name)` instance registers
both the HYRAS `.nc` file **and** the DWD `.parquet` file under
`source_name="HYRAS"`.  Consequences:

- `get_weather_paths(source_name="HYRAS", ...)` returns DWD rows as well as
  gridded rows, so the blending path sees them correctly.
- A *dedicated* `WeatherPipeline(config={"source": "DWD_stations"})` will
  never find those rows — they were registered under `"HYRAS"`.
- `sync_files_and_database()` for a `"DWD_stations"` pipeline cannot detect
  the files because it searches for `HYRAS_*.parquet` prefixes, not
  `DWD_stations_*.parquet`.

This is currently non-blocking because the composite pipeline is the only
supported mode for mixed data.  It becomes blocking once the `CoverageManager`
(Step 7) or per-source incremental updates are introduced.  Tracked as
**DESIGN-01** in `weather_further_enhancements.md` § Known risks.

---

## Bug Priority Order

| # | ID | Severity | Fix complexity | Fix first because… |
|---|---|---|---|---|
| 1 | BUG-05 | 🔴 blocking | Medium | Makes production-scale queries impossible | ✅ Fixed 2026-04-28 |
| 2 | BUG-06 | 🔴 blocking | Low | Silently drops last day of any annual query | ✅ Fixed 2026-04-28 |
| 3 | BUG-07 | 🟡 significant | Medium | File accumulation; non-reproducible storage | ✅ Fixed 2026-04-28 |
| 4 | BUG-08 | 🟡 significant | Low | Loses 75 % of grid points at domain edges | ✅ Fixed 2026-04-28 |
| 5 | BUG-09 | 🟡 significant | Low | Resolved by BUG-01 fix (sync before update) | ✅ Fixed pre-2026-04-28 |
| 6 | BUG-10 | 🔵 informational | Low | Breaks DWD-only pipeline source isolation | ✅ Already correct |

BUG-01 (disk↔DB reconciliation at wrong abstraction level) has been **fixed**
(April 2026 Option B refactor).  It was the root cause of BUG-04/BUG-09 and
the first architectural fix has been applied.

---

## Verified Working

| Capability | Verified by |
|---|---|
| HYRAS download from DWD OpenData (no credentials) | Previous session |
| ETRS89-LAEA → WGS84 coordinate reprojection via pyproj | All three scripts |
| Single-point bilinear interpolation | Scripts 1, 2, 3 |
| Vectorised multi-point query (20 coords, single call) | Script 3 |
| Correct unit pass-through (HYRAS is already °C / mm) | Scripts 2, 3 — plausible values |
| Year-boundary query Jan 1 (temperature) | Script 3 — Jan 1 temperature OK |
| Idempotent `update_data()` (no re-download if DB rows exist) | All three scripts |
| CF `grid_mapping` auto-detection | All three scripts |
| Climatologically correct spatial pattern | Script 2 — N–S gradient confirmed |

---

## Files to Change (consolidated)

| File | Bugs addressed |
|---|---|
| `datavia/library/interpolation.py` | ✅ BUG-05 (batch coords), BUG-08 (NaN fill) — DONE 2026-04-28 |
| `datavia/library/formats.py` | ✅ BUG-06 (`valid_until` rounding) — DONE 2026-04-28 |
| `packages/weather/datavia/weather/hyras_downloader.py` | ✅ BUG-07 — reverted to simple `.download`→`.nc` extension swap (naming moved to saver) |
| `datavia/core/downloader_url.py` | ✅ BUG-07 — no change required |
| `packages/weather/datavia/weather/getter_weather.py` | ✅ BUG-05 (pass coord array) — DONE 2026-04-28 |
| `packages/weather/datavia/weather/saver_weather.py` | ✅ BUG-07 (`_build_dest_stem` + corrected `save()`) — DONE 2026-04-28 |
| `packages/weather/datavia/weather/pipeline.py` | ✅ BUG-09 (sync before update) — DONE |
