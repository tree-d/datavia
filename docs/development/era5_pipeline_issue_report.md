# ERA5-Land Pipeline Issue Report

> **Status:** Issue A ✅ **FIXED** (2026-05-05).  Issue B remains under investigation.

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

### 1. Variable name mismatch

The CDS/ERA5 NetCDF file stores `2m_temperature` under the short ECMWF name **`t2m`**.  
`saver_weather._resolve_variables()` reads the raw NC variable names and registers them
as-is when no `nc_variable_map` is present.

| What the file contains | What gets registered in DB | What the getter queries |
|---|---|---|
| `t2m` | `t2m` | `2m_temperature` |

The DB row therefore never matches the query.

`SOURCE_REGISTRY["ERA5_land"]` has a `conversions` dict keyed by `"2m_temperature"` but
**no `nc_variable_map`**, unlike `SOURCE_REGISTRY["HYRAS"]` which maps `"tas"` →
`"2m_temperature"`.

Other affected variables: `tp` (total_precipitation), `ssrd`
(surface_solar_radiation_downwards).

---

### 2. NULL temporal metadata

The saved row has `valid_from = NULL` and `valid_until = NULL`:

```python
{'variable': 't2m', 'valid_from': None, 'valid_until': None,
 'uri': '...ERA5_land_t2m_unknown.nc', ...}
```

`get_weather_paths()` filters with `valid_from <= to_dt AND valid_until >= from_dt`,
so a row with NULL times is always excluded.

The file stem also ends with `_unknown` instead of the expected `_<year>`, indicating
that `_build_dest_stem()` could not extract the year from the time dimension —
probably because the time values failed to parse during `_read_temporal_metadata()`.

---

### 3. Combined effect

Both issues compound: even if the variable name matched, the NULL timestamps would
prevent the row from being returned by any time-range query.

---

## Fix applied — 2026-05-05

### ✅ A — `nc_variable_map` added to `SOURCE_REGISTRY["ERA5_land"]`

[packages/weather/datavia/weather/source_registry.py](../../../packages/weather/datavia/weather/source_registry.py)
now maps ECMWF short names to pipeline variable names:

```python
"nc_variable_map": {
    "t2m":  "2m_temperature",
    "tp":   "total_precipitation",
    "ssrd": "surface_solar_radiation_downwards",
},
```

Same pattern already used for HYRAS. `_resolve_variables()` applies this
mapping before registering rows, so DB entries now use `2m_temperature` and
match the getter's query.

### ⚠️ B — Temporal metadata NULL issue (under investigation)

The reported `valid_from = None` / `valid_until = None` requires a real ERA5
file to debug. `extract_netcdf_layer_metadata()` in
`datavia/library/formats.py` should decode ERA5's `hours since 1900-01-01`
time encoding automatically via xarray's default `decode_times=True`, but the
original report shows this failed.

**Next step:** Download a small ERA5 file and inspect its raw time coordinate
values with `ncdump -v time <file>.nc` to confirm the encoding. If xarray
fails to decode it, add explicit `use_cftime=True` or a manual decode step.

**Workaround:** Until fixed, affected use cases fall back to synthetic data.

---

## Original suggestions (2026-04-29)

### A — Add `nc_variable_map` to `SOURCE_REGISTRY["ERA5_land"]` ✅ DONE

```python
"ERA5_land": {
    "grid_downloader": ERA5Downloader,
    "conversions": { ... },
    "nc_variable_map": {
        "t2m":  "2m_temperature",
        "tp":   "total_precipitation",
        "ssrd": "surface_solar_radiation_downwards",
    },
},
```

Same pattern already used for HYRAS — zero new infrastructure needed.

---

### B — Fix temporal metadata extraction for ERA5

ERA5 time is encoded as `hours since 1900-01-01` (ECMWF epoch).  
Check that `_read_temporal_metadata()` in `saver_weather.py` decodes this correctly
(e.g. by calling `xr.open_dataset(..., decode_times=True)` or using
`cftime_range` decoding) and that the resulting datetimes are serialised as
ISO-8601 strings before being stored.

---

### C — Guard against `_unknown` file stems

If `_build_dest_stem()` cannot parse the year, it falls back to `_unknown`.
Consider raising an error (or at minimum a warning) so the problem is visible
rather than silently producing a file that can never be queried.

---

### D — Add an integration test for ERA5 save → query round-trip

A test that downloads a tiny ERA5 slice, saves it, and immediately queries
`get_weather_paths()` for the same variable and time window would catch
both issues above automatically.

---

## Workaround for this project (no package changes)

Until the package is fixed, UC8/UC9/UC10 fall back to clearly-labelled
synthetic data and still produce charts — the fallback logic in the scripts
already handles `RuntimeError` from `get_data()`.
