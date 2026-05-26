# ERA5-Land Pipeline Issue Report

> **Status:** A ✅ fixed 2026-05-05 · B ✅ fixed 2026-05-08 · C ⚠️ open — see [weather_file_management_strategies.md](weather_file_management_strategies.md)

Observed while running `use_case_8_era5_winter_grid.py` on 2026-04-29.

---

## ✅ A — Variable name mismatch (fixed 2026-05-05)

ERA5 NetCDF files store `2m_temperature` under the short ECMWF name **`t2m`**.
`_resolve_variables()` registered `t2m` in the DB but the getter queried
`2m_temperature` — no match.

**Fix:** `nc_variable_map` added to `SOURCE_REGISTRY["ERA5_land"]` mapping
`t2m → 2m_temperature`, `tp → total_precipitation`,
`ssrd → surface_solar_radiation_downwards`.

**File:** `packages/weather/datavia/weather/source_registry.py`

---

## ✅ B — NULL temporal metadata (`valid_time` coordinate, fixed 2026-05-08)

ERA5 files from the CDS API use **`valid_time`** as the time dimension name,
not `"time"`.  `extract_netcdf_layer_metadata()` in `datavia/library/formats.py`
only checked `if "time" in ds.coords`, so saved rows got `valid_from = NULL`,
`valid_until = NULL`, and a stem ending in `_unknown`.

**Fix:** time coordinate resolved as
`ds.coords["time"] if "time" in ds.coords else ds.coords.get("valid_time")`.

**File:** `datavia/library/formats.py`

---

## ⚠️ C — Filename collision: bbox and month not encoded (open)

`_build_dest_stem()` in `saver_weather.py` generates stems as
`{source}_{variable}_{year}` — e.g. `ERA5_land_t2m_2024.nc`.  Two downloads
for the same variable and year but different bounding boxes or months produce
the same filename; the second silently overwrites the first.

The `CoverageManager`'s spatial-remainder logic is correct but relies on each
downloaded tile having a unique filename.  As long as filenames collide,
incremental spatial coverage is broken.

**File:** `packages/weather/datavia/weather/saver_weather._build_dest_stem()`

**Strategies and long-term options:** [weather_file_management_strategies.md](weather_file_management_strategies.md)

