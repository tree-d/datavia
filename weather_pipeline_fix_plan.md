# Weather Pipeline Fix Plan — Bugs 3, 4, 5, 6

**Source:** `weather_pipeline_issue_report_2.md`
**Date:** 2026-05-29
**Branch:** `weather`

---

## Overview

Four bugs causing silent data corruption, invisible failures, and partial data loss. All
receive proper root-cause fixes. Phases can start independently; steps within a phase are
sequential unless noted.

---

## Phase 1 — Bug 3: ERA5 descending latitude → all-NaN silently

### Step 1 — `datavia/library/interpolation.py` · `interpolate_netcdf()` (~line 348)

Insert two `sortby` calls immediately **before** the existing `interpolate_na` pair:

```python
da = da.sortby(x_dim)   # no-op when already ascending (HYRAS); fixes ERA5 N→S
da = da.sortby(y_dim)
da = da.interpolate_na(dim=x_dim, method="nearest", fill_value="extrapolate")
da = da.interpolate_na(dim=y_dim, method="nearest", fill_value="extrapolate")
```

Update the docstring to note that dimension orientation is normalised before gap-filling.

### Step 2 — `packages/weather/datavia/weather/getter_weather.py` · `get_data()` (~line 265)

In the `except Exception as exc` block:

- Change `logger.warning` → `logger.error(..., exc_info=True)`
- Always re-raise the exception — no silent all-NaN return.

**Decision:** always re-raise. Silent wrong data (all-NaN) is worse than a surfaced error.

### Step 3 — New unit test · `TestInterpolateNetcdf`

`test_descending_latitude_produces_non_nan_result` — construct a 5×5 `DataArray`
with latitude in descending order (55.0 → 47.0), call `interpolate_netcdf`, assert result
is a finite float.

---

## Phase 2 — Bug 4: HYRAS variable names in ERA5 pipeline + invisible update failures

### Step 4 — `packages/weather/datavia/weather/source_registry.py`

Add a new helper function:

```python
def get_valid_variables(source_name: str) -> frozenset[str] | None:
```

- Returns `frozenset(nc_variable_map.values())` for sources that have a `nc_variable_map`
  (ERA5\_land, HYRAS).
- Returns `None` for `DWD_stations` (no variable map → no validation possible).

### Step 5 — `packages/weather/datavia/weather/pipeline.py` · `WeatherPipeline.__init__()`

After the existing config-key validation, call `get_valid_variables(source)`:

- If the result is not `None`, cross-reference each requested variable against it.
- Raise `ValueError` at construction time listing the bad variables and the valid set.
- `DWD_stations` → `None` → no check → unchanged behaviour.

### Step 6 — `packages/weather/datavia/weather/pipeline.py` · `update_data()`

Replace `return False` on failure:

- All cells are **always** attempted (never abort early).
- Collect failed cell identifiers into a list throughout the loop.
- After the loop: if any cell failed, raise `RuntimeError` naming the failed cells.
- Successfully registered cells remain in the DB so the scientist can query partial data.

### Step 7 — Updated / new tests

- `test_invalid_variable_for_era5_raises_at_init` — passing `"temperature_2m_max"` in an
  ERA5 config raises `ValueError` at `__init__`.
- Update `test_update_data_returns_false_on_download_failure` → now expects `RuntimeError`.

---

## Phase 3 — Bug 5: Only `nc_files[0]` used for multi-file queries

**Decision:** extend `interpolate_netcdf()` to accept `list[str]` — keeps interpolation
self-contained; the caller passes the full list without knowing about the concat strategy.

### Step 8 — `datavia/library/interpolation.py` · `interpolate_netcdf()`

Change signature: `nc_path: str` → `nc_path: str | list[str]`

Internally:

```python
if isinstance(nc_path, list):
    ds = xr.open_mfdataset(nc_path, combine="by_coords")
else:
    ds = xr.open_dataset(nc_path)
```

Single-string behaviour is unchanged. Update docstring.

### Step 9 — `packages/weather/datavia/weather/getter_weather.py` · `get_data()`

Change:

```python
raw_batch = interpolate_netcdf(nc_files[0], ...)
```

to:

```python
raw_batch = interpolate_netcdf(nc_files, ...)
```

`nc_files` is already a `list[str]`; when it has one element `open_mfdataset` is equivalent
to `open_dataset`.

### Step 10 — New unit test · `TestInterpolateNetcdf`

`test_multi_file_nc_returns_all_timestamps` — create two 1-step monthly DataArrays (June,
July), save as separate `.nc` files, call `interpolate_netcdf([june_path, july_path], ...)`
with a time range spanning both months — assert all returned values are finite.

---

## Phase 4 — Bug 6: DWD Parquet filename collision

### Step 11 — `packages/weather/datavia/weather/saver_weather.py` · `_build_dest_stem()` Parquet branch

Replace the year-only stem:

```python
# Before
return f"{source_name}_{year}"

# After
df = pd.read_parquet(data_path, columns=["datetime", "station_id"])
df["datetime"] = pd.to_datetime(df["datetime"])
year_start = str(df["datetime"].min().year)
year_end   = str(df["datetime"].max().year)
station_ids = sorted(df["station_id"].unique().tolist())
station_hash = hashlib.md5(
    str(station_ids).encode(), usedforsecurity=False
).hexdigest()[:6]
return f"{source_name}_{year_start}_{year_end}_{station_hash}"
```

Fallback: if `station_id` column is absent, fall back to `{source_name}_{year}` to preserve
compatibility with files that pre-date this fix. Update docstring.

### Step 12 — New unit test · `TestSaverWeatherDestNaming`

`test_parquet_different_station_sets_produce_unique_stems` — create two in-memory Parquet
DataFrames with different `station_id` sets but the same year → call `_build_dest_stem` for
each → assert the returned stems are different.

---

## Affected files

| File | What changes |
|---|---|
| `datavia/library/interpolation.py` | `interpolate_netcdf()`: `sortby` before `interpolate_na`; accept `list[str]` |
| `packages/weather/datavia/weather/getter_weather.py` | `get_data()`: always re-raise on NC failure; pass full `nc_files` list |
| `packages/weather/datavia/weather/source_registry.py` | Add `get_valid_variables()` |
| `packages/weather/datavia/weather/pipeline.py` | `__init__`: variable validation; `update_data`: raise with partial-success report |
| `packages/weather/datavia/weather/saver_weather.py` | `_build_dest_stem()` Parquet branch: station hash + date range |
| `tests/test_weather_pipeline_unit.py` | 4 new tests; 1 updated |

---

## Verification

1. `pixi run pytest tests/test_weather_pipeline_unit.py -x` — all existing tests pass.
2. All 4 new tests and 1 updated test pass.
3. `pixi run pytest tests/test_coordinate_transforms.py tests/test_spatial_ops.py` — no
   regressions from the `sortby` addition.
4. Run `weather_exp.py` against an ERA5 source — result is non-NaN.
5. `pixi run UC3` → `pixi run UC5` → `pixi run UC3` — UC3 does not return wrong stations
   on the second run.

---

## Decisions recorded

| # | Decision |
|---|---|
| Bug 3 re-raise | Always re-raise on NC interpolation failure; no silent all-NaN. |
| Bug 4 partial success | Iterate all cells; raise `RuntimeError` after the loop naming failed cells; DB keeps successful cells. |
| Bug 5 fix location | Extend `interpolate_netcdf()` signature to accept `list[str]`; caller passes full list. |
