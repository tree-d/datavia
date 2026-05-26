# HYRAS & ERA5 NetCDF Spike Findings

> Recorded 2026-04-20.
> Investigation script: `scripts/explore_netcdf_spike.py`
> Purpose: establish the real NetCDF structure of HYRAS and ERA5 files before
> implementing Phase A3 (`interpolate_netcdf` fix) and Phase D (`HYRASDownloader`).

---

## HYRAS — `hurs_hyras_1_2022_v6-1_de.nc` (113 MB, relative humidity)

| Property | Value |
|---|---|
| Dimensions | `time(365)`, `y(890)`, `x(665)` |
| `x` / `y` unit | **metres** — ETRS89 LAEA Europe (EPSG:3035) |
| 2D auxiliary coords | `lat(y, x)` and `lon(y, x)` in WGS84 degrees |
| CRS source | `grid_mapping = 'crs'` — variable `crs` carries full `crs_wkt` and `proj4` strings |
| `interpolate_netcdf` result | **Fails** — `ValueError: Dimensions {'lat','lon'} do not exist` |

> **The plan's A3 note is wrong.**  The `x`/`y` coordinate system is
> **ETRS89 LAEA (EPSG:3035)**, not rotated-pole.  `xarray`'s `sel` /
> `interp` cannot be called directly with WGS84 lat/lon.

### Verified fix

Reproject WGS84 → EPSG:3035 with `pyproj`, then use `xarray.Dataset.interp`:

```python
import pyproj

crs_file = pyproj.CRS.from_cf(ds["crs"].attrs)
transformer = pyproj.Transformer.from_crs("EPSG:4326", crs_file, always_xy=True)
x_proj, y_proj = transformer.transform(lon_wgs84, lat_wgs84)
value = ds[variable].interp(y=y_proj, x=x_proj)
```

Munich (lat=48.137, lon=11.576), 2022-07-15: **43.6 % RH** — plausible.

`pyproj 3.7.1` is already present in the pixi environment — no new dependency needed.

### Filename pattern

Format: `{prefix}_{year}_v{X}-{Y}_de.nc`

The `_de` suffix is present in every HYRAS file.  The plan's original regex
was missing it; the regex for `HYRASDownloader` must include `_de`.

---

## ERA5 — new CDS API (cdsapi ≥ 0.7 / `ecmwf-datastores`)

| Property | Value |
|---|---|
| Download format | `.zip` archive (not raw `.nc`) — must be extracted |
| Dimensions | `valid_time`, `latitude`, `longitude` |
| Time coord name | **`valid_time`** — the old name `time` no longer exists |
| Data variable | `t2m` (float32, Kelvin) |
| `interpolate_netcdf` result | Returns **all 12 time steps as a 1-D array** instead of a scalar, because the function checks only for `"time"` in `ds.coords` |

> The `latitude` / `longitude` dimension names and spatial interpolation path
> work correctly.  Only the time-selection branch is broken.

### Verified fix

Detect `valid_time` as a fallback when `time` is absent:

```python
time_dim = "time" if "time" in ds.coords else "valid_time"
```

Munich (lat=48.137, lon=11.576), first slot 2022-07-14 00:00:
**291.44 K → 18.3 °C** — plausible for midnight.

---

## Impact on the implementation plan

| Phase | Change required |
|---|---|
| **A3** `interpolate_netcdf` | Sub-fix a) HYRAS `x`/`y` pyproj path; sub-fix b) `valid_time` fallback |
| **D** `HYRASDownloader` | Filename regex must include `_de`; HTML-scrape auto-discovery must match `_de` suffix |
| **A3 note in plan** | Correct "rotated-pole" to "ETRS89 LAEA (EPSG:3035)"; add `valid_time` ERA5 issue |
