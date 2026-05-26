# ERA5-Land Pipeline Issue Report

> **Status:** Issue A ✅ **FIXED** (2026-05-05).  Issue B confirmed and tracked
> in [weather_next_steps.md](weather_next_steps.md) — fix required in
> `datavia/library/formats.py`.

Observed while running `use_case_8_era5_winter_grid.py` on 2026-04-29.

---

## What happened

ERA5 data downloaded successfully from CDS (request `922cc8da`, ~50 s).  
The file was saved to `.datavia/data/ERA5_land_t2m_unknown.nc`.  
The subsequent `get_data()` call failed with:

```
No weather files found for source='ERA5_land', variable='2m_temperature',
time=[2024-01-18T00:00:00, 2024-01-18T23:00:00]. Run the pipeline update first.
```

---

## Root causes

### ✅ A — Variable name mismatch (fixed 2026-05-05)

ERA5 NetCDF files store `2m_temperature` under the short ECMWF name **`t2m`**.
`_resolve_variables()` registered `t2m` in the DB but the getter queried
`2m_temperature` — no match.

**Fix:** `nc_variable_map` added to `SOURCE_REGISTRY["ERA5_land"]` mapping
`t2m → 2m_temperature`, `tp → total_precipitation`,
`ssrd → surface_solar_radiation_downwards`.

**File:** `packages/weather/datavia/weather/source_registry.py`

---

### ⚠️ B — NULL temporal metadata (`valid_time` coordinate)

ERA5 files from the CDS API use **`valid_time`** as the time dimension name,
not `"time"`:

```
Dimensions:  (valid_time: 1464, latitude: 6, longitude: 8)
Coordinates:
  * valid_time  (valid_time) datetime64[ns] 2024-03-01 … 2024-04-30T23:00:00
```

`extract_netcdf_layer_metadata()` in `datavia/library/formats.py` only checks
`if "time" in ds.coords`, so it never finds the time dimension.  The saved
row has `valid_from = NULL`, `valid_until = NULL`, and the file stem ends with
`_unknown` — making any time-range query return nothing.

**Fix required:** fall back to `valid_time` when `time` is absent.  See
[weather_next_steps.md](weather_next_steps.md) for the implementation plan.

---

## Combined effect

Both issues compound: even if the variable name matched, the NULL timestamps
prevent the row from being returned by any time-range query.  Workaround in
use-case scripts: fall back to synthetic data when `get_data()` raises
`RuntimeError`.

