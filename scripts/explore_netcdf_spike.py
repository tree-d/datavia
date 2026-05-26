"""
NetCDF Investigation Spike — HYRAS vs ERA5

Purpose: Understand the coordinate structure of HYRAS and ERA5 NetCDF files
before implementing Phase D (HYRASDownloader) and fixing Phase A3 (interpolate_netcdf).

Key questions to answer:
  1. What coordinate/dimension names does HYRAS use? (x/y? lat/lon? auxiliary arrays?)
  2. Does HYRAS use a rotated-pole CRS — and does xarray expose it?
  3. Can xarray.interp() handle HYRAS coordinates with WGS84 lat/lon input as-is?
  4. What minimal fix does interpolate_netcdf need to support both sources?

Test location: Munich (lat=48.137, lon=11.576) — easy to sanity-check values.

Run with:
    pixi run python scripts/explore_netcdf_spike.py
"""

import os
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import requests
import xarray as xr

# Make the local datavia library importable when run from any directory.
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

# Download destination — safe scratch space, not committed.
DATA_DIR = Path("/tmp/datavia_spike")
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Test location: Munich
LAT = 48.137
LON = 11.576
TEST_DATE = datetime(2022, 7, 15, 12, 0)  # mid-summer, should be warm & sunny

print(f"Repo root : {repo_root}")
print(f"Data dir  : {DATA_DIR}")
print(f"xarray    : {xr.__version__}")


# ---------------------------------------------------------------------------
# Section 1 — Download one HYRAS file
# ---------------------------------------------------------------------------
# Using relative humidity 2022 — one of the smaller HYRAS variables (~20 MB).
# No credentials needed; DWD Open Data is public.
#
# Plan variable table (Section 7):
#   config name            subdir      filename prefix   NC variable
#   relative_humidity_2m   humidity    hurs_hyras_1      hurs

HYRAS_BASE = "https://opendata.dwd.de/climate_environment/CDC/grids_germany/daily/hyras_de/"
HYRAS_SUBDIR = "humidity/"
YEAR = 2022

listing_url = HYRAS_BASE + HYRAS_SUBDIR
print(f"\n[1] Fetching directory listing: {listing_url}")

resp = requests.get(listing_url, timeout=30)
resp.raise_for_status()

# Discover the exact filename — mirrors HYRASDownloader._discover_latest_filename.
# Actual DWD filename format: hurs_hyras_1_<year>_v<X>-<Y>_de.nc
pattern = re.compile(rf"(hurs_hyras_1_{YEAR}_v[\d-]+_de\.nc)")
matches = pattern.findall(resp.text)
print(f"    Matching filenames: {matches}")

if not matches:
    # Fallback: show everything that looks like a .nc file so we can adjust the pattern.
    all_nc = re.findall(r'href="([^"]+\.nc)"', resp.text)
    print("    No match — all .nc hrefs found:")
    for f in all_nc:
        print("     ", f)
    raise RuntimeError("Pattern did not match. Adjust 'pattern' above.")

# Pick the latest version (last alphabetically is fine for vX-Y suffixes).
filename = sorted(matches)[-1]
print(f"    Selected: {filename}")

hyras_path = DATA_DIR / filename

if hyras_path.exists():
    print(f"    Already downloaded: {hyras_path} ({hyras_path.stat().st_size / 1e6:.1f} MB)")
else:
    file_url = listing_url + filename
    print(f"    Downloading {file_url} ...")
    with requests.get(file_url, stream=True, timeout=300) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        print(f"    Size: {total / 1e6:.1f} MB")
        downloaded = 0
        with open(hyras_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                downloaded += len(chunk)
    print(f"    Done: {downloaded / 1e6:.1f} MB written to {hyras_path}")


# ---------------------------------------------------------------------------
# Section 2 — Inspect the HYRAS file structure
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
print("[2] HYRAS Dataset overview")
print("=" * 60)

ds_hyras = xr.open_dataset(hyras_path)
print(ds_hyras)

print("\n--- .dims ---")
print(dict(ds_hyras.dims))

print("\n--- .coords (names) ---")
print(list(ds_hyras.coords))

print("\n--- .data_vars ---")
print(list(ds_hyras.data_vars))

print("\n--- Global attributes ---")
for k, v in ds_hyras.attrs.items():
    print(f"  {k}: {v}")

print("\n--- Coordinate details ---")
for name, coord in ds_hyras.coords.items():
    print(f"\n[{name}]  dtype={coord.dtype}  shape={coord.shape}")
    print(f"  dims : {coord.dims}")
    print(f"  attrs: {dict(coord.attrs)}")
    if coord.size <= 10:
        print(f"  values: {coord.values}")
    else:
        print(f"  first 3: {coord.values.flat[:3]}  ...  last 3: {coord.values.flat[-3:]}")

# Check for 2D auxiliary lat/lon arrays and grid_mapping.
has_2d_lat = any(
    c in ds_hyras.coords and ds_hyras.coords[c].ndim == 2
    for c in ("lat", "latitude")
)
has_2d_lon = any(
    c in ds_hyras.coords and ds_hyras.coords[c].ndim == 2
    for c in ("lon", "longitude")
)
print(f"\n2-D auxiliary lat array present: {has_2d_lat}")
print(f"2-D auxiliary lon array present: {has_2d_lon}")

# Check the climate variable specifically — time_bnds has no grid_mapping.
var = "hurs"
gm = ds_hyras[var].attrs.get("grid_mapping", "(none)")
print(f"grid_mapping attribute on '{var}': {gm}")
if gm in ds_hyras:
    print("Grid mapping variable attributes:")
    for k, v in ds_hyras[gm].attrs.items():
        print(f"  {k}: {v}")


# ---------------------------------------------------------------------------
# Section 3 — Try interpolate_netcdf as-is on HYRAS
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
print("[3] interpolate_netcdf as-is on HYRAS")
print("=" * 60)

from datavia.library.interpolation import interpolate_netcdf  # noqa: E402

print(f"  File : {hyras_path}")
print(f"  Point: lat={LAT}, lon={LON}")
print(f"  Time : {TEST_DATE}")

try:
    result = interpolate_netcdf(
        nc_path=str(hyras_path),
        lat=LAT,
        lon=LON,
        variable="hurs",
        datetime_utc=TEST_DATE,
    )
    print(f"  Result: {result}")
    check = "PASS" if 40 <= result <= 90 else "SUSPICIOUS — check value"
    print(f"  Sanity check (expected ~40-90 % for Munich July): {check}")
except Exception as e:
    print(f"  ERROR ({type(e).__name__}): {e}")
    print("  -> This is the bug we need to fix in Phase A3.")


# ---------------------------------------------------------------------------
# Section 4 — Manual interpolation experiments on HYRAS
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
print("[4] Manual interpolation experiments")
print("=" * 60)

# Use the actual climate variable, not the first data_var (which is time_bnds).
var = "hurs"
da = ds_hyras[var]
spatial_dims = [d for d in da.dims if d != "time"]
print(f"\n4a) DataArray '{var}' dims: {da.dims}")
print(f"    spatial dims: {spatial_dims}")

# 4b — naive: pass WGS84 lat/lon directly to the spatial dims.
print("\n4b) Naive interp with raw WGS84 lat/lon on HYRAS spatial dims:")
try:
    da_t = da.sel(time=TEST_DATE, method="nearest")
    print(f"    After time sel shape: {da_t.shape}")
    if len(spatial_dims) == 2:
        dim0, dim1 = spatial_dims
        val = da_t.interp({dim0: LAT, dim1: LON}, method="linear").values
        check = "PLAUSIBLE" if 40 <= float(val) <= 90 else "IMPLAUSIBLE or NaN"
        print(f"    interp({dim0}={LAT}, {dim1}={LON}) = {val}  [{check}]")
    else:
        print(f"    Unexpected number of spatial dims: {spatial_dims}")
except Exception as e:
    print(f"    ERROR: {type(e).__name__}: {e}")

# 4c — 2D auxiliary lat/lon arrays (xarray trick for curvilinear grids).
print("\n4c) Nearest-grid-point via 2D auxiliary lat/lon (if present):")
lat_coord_name = next((c for c in ("lat", "latitude") if c in ds_hyras.coords), None)
lon_coord_name = next((c for c in ("lon", "longitude") if c in ds_hyras.coords), None)

if lat_coord_name and lon_coord_name:
    lat_arr = ds_hyras.coords[lat_coord_name].values
    lon_arr = ds_hyras.coords[lon_coord_name].values
    print(f"    Found: lat='{lat_coord_name}' shape={lat_arr.shape}, lon='{lon_coord_name}' shape={lon_arr.shape}")
    if lat_arr.ndim == 2:
        dist = np.sqrt((lat_arr - LAT) ** 2 + (lon_arr - LON) ** 2)
        idx = np.unravel_index(np.argmin(dist), dist.shape)
        print(f"    Nearest grid index: {idx}, dist={dist[idx]:.4f} deg")
        da_t = da.sel(time=TEST_DATE, method="nearest")
        val = da_t.values[idx]
        check = "PLAUSIBLE" if 40 <= float(val) <= 90 else "IMPLAUSIBLE or NaN"
        print(f"    Value at nearest grid point: {val}  [{check}]")
    else:
        print("    1D lat/lon — standard interp should work.")
else:
    print("    No auxiliary lat/lon coordinates found — need pyproj reprojection.")

# 4d — pyproj reprojection as last resort.
print("\n4d) pyproj WGS84 -> rotated-pole reprojection:")
try:
    import pyproj  # noqa: E402

    print(f"    pyproj available: {pyproj.__version__}")
    gm_name = ds_hyras[var].attrs.get("grid_mapping")
    if gm_name and gm_name in ds_hyras:
        gm_attrs = dict(ds_hyras[gm_name].attrs)
        print(f"    Grid mapping '{gm_name}': {gm_attrs}")
        try:
            crs_rotpole = pyproj.CRS.from_cf(gm_attrs)
            print(f"    CRS from CF: {crs_rotpole.name}")
            transformer = pyproj.Transformer.from_crs("EPSG:4326", crs_rotpole, always_xy=True)
            x_rot, y_rot = transformer.transform(LON, LAT)
            print(f"    Munich WGS84 ({LON}, {LAT}) -> rotated ({x_rot:.4f}, {y_rot:.4f})")
            if len(spatial_dims) == 2:
                dim0, dim1 = spatial_dims
                da_t = da.sel(time=TEST_DATE, method="nearest")
                val = da_t.interp({dim0: y_rot, dim1: x_rot}, method="linear").values
                check = "PLAUSIBLE" if 40 <= float(val) <= 90 else "IMPLAUSIBLE or NaN"
                print(f"    After reprojection interp: {val}  [{check}]")
        except Exception as e:
            print(f"    CRS build/transform failed: {e}")
    else:
        print("    No grid_mapping found — skip.")
except ImportError:
    print("    pyproj NOT available. Install with: pixi add pyproj")


# ---------------------------------------------------------------------------
# Section 5 — ERA5 (optional, requires ~/.cdsapirc)
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
print("[5] ERA5 file structure (requires ~/.cdsapirc)")
print("=" * 60)

cdsapirc = Path(os.path.expanduser("~/.cdsapirc"))
print(f"~/.cdsapirc exists: {cdsapirc.exists()}")

try:
    import cdsapi  # noqa: E402

    print("cdsapi available")
    CDS_AVAILABLE = True
except ImportError:
    print("cdsapi NOT available — skipping ERA5 download")
    CDS_AVAILABLE = False

era5_path = DATA_DIR / "era5_spike_2022_07.nc"

# Maximum time to wait in the CDS queue before giving up and skipping ERA5.
# The CDS queue can have thousands of jobs; 5 minutes is a reasonable limit for
# an interactive spike. Raise it (or set to None) if running unattended overnight.
CDS_TIMEOUT_SECONDS = 300


def _download_era5(path: Path) -> None:
    """Submit and wait for a small ERA5-Land request via the CDS API."""
    import cdsapi as _cdsapi  # noqa: PLC0415 — imported inside thread

    c = _cdsapi.Client()
    c.retrieve(
        "reanalysis-era5-land",
        {
            "variable": ["2m_temperature"],
            "year": "2022",
            "month": "07",
            "day": ["14", "15", "16"],
            "time": ["00:00", "06:00", "12:00", "18:00"],
            "area": [49.0, 11.0, 47.5, 12.5],  # N W S E
            "format": "netcdf",
        },
        str(path),
    )


if not CDS_AVAILABLE or not cdsapirc.exists():
    print("Skipping ERA5 download (no credentials). Using HYRAS results only.")
elif era5_path.exists():
    print(f"Already downloaded: {era5_path}")
else:
    import concurrent.futures  # noqa: E402

    print(
        f"Submitting ERA5 request (timeout={CDS_TIMEOUT_SECONDS}s — "
        "raise CDS_TIMEOUT_SECONDS or set to None to wait longer)..."
    )
    # The new CDS API returns a zip archive — download to a temp path first.
    era5_zip = era5_path.with_suffix(".zip")
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_download_era5, era5_zip)
        try:
            future.result(timeout=CDS_TIMEOUT_SECONDS)
            print(f"  Downloaded zip: {era5_zip.stat().st_size / 1e6:.2f} MB")
            # Extract the first .nc file from the zip.
            import zipfile  # noqa: E402
            if zipfile.is_zipfile(era5_zip):
                with zipfile.ZipFile(era5_zip) as zf:
                    nc_names = [n for n in zf.namelist() if n.endswith(".nc")]
                    if not nc_names:
                        raise RuntimeError(f"No .nc file found inside {era5_zip}")
                    zf.extract(nc_names[0], DATA_DIR)
                    extracted = DATA_DIR / nc_names[0]
                    extracted.rename(era5_path)
                era5_zip.unlink(missing_ok=True)
                print(f"Done: {era5_path.stat().st_size / 1e6:.1f} MB")
            else:
                # Some CDS versions return raw netcdf — rename directly.
                era5_zip.rename(era5_path)
                print(f"Done (raw nc): {era5_path.stat().st_size / 1e6:.1f} MB")
        except concurrent.futures.TimeoutError:
            print(
                f"  TIMED OUT after {CDS_TIMEOUT_SECONDS}s — CDS queue is too long right now."
                " Re-run later or increase CDS_TIMEOUT_SECONDS."
            )
            # Clean up any partial files so the next run retries cleanly.
            era5_path.unlink(missing_ok=True)
            era5_zip.unlink(missing_ok=True)
        except Exception as exc:
            print(f"  ERA5 download failed: {exc}")
            era5_path.unlink(missing_ok=True)
            era5_zip.unlink(missing_ok=True)

if era5_path.exists():
    ds_era5 = xr.open_dataset(era5_path)
    print("\n--- ERA5 dims ---")
    print(dict(ds_era5.dims))
    print("--- ERA5 coords ---")
    for name, coord in ds_era5.coords.items():
        print(f"  [{name}]  dtype={coord.dtype}  shape={coord.shape}  dims={coord.dims}")

    print("\nTesting interpolate_netcdf on ERA5 (2m_temperature / t2m):")
    print("  NOTE: new CDS API uses 'valid_time' not 'time' — interpolate_netcdf")
    print("        currently only checks for 'time', so it returns all time steps.")
    try:
        result_era5 = interpolate_netcdf(
            nc_path=str(era5_path),
            lat=LAT,
            lon=LON,
            variable="t2m",
            datetime_utc=TEST_DATE,
        )
        # result may be a scalar or an array if 'valid_time' was not matched.
        values = np.atleast_1d(result_era5)
        print(f"  Result shape: {values.shape} (expect scalar or (1,))")
        celsius = float(values[0]) - 273.15
        check = "PASS" if 20 <= celsius <= 35 else "SUSPICIOUS"
        print(f"  First value (K): {float(values[0]):.2f}  ->  {celsius:.1f}°C  [{check}]")
        if values.shape[0] > 1:
            print(f"  WARNING: got {values.shape[0]} values — 'valid_time' not handled in interpolate_netcdf.")
    except Exception as e:
        print(f"  ERROR ({type(e).__name__}): {e}")
else:
    ds_era5 = None


# ---------------------------------------------------------------------------
# Section 6 — Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
print("[6] SUMMARY")
print("=" * 60)

print("\nHYRAS:")
print(f"  dims        : {dict(ds_hyras.dims)}")
print(f"  coord names : {list(ds_hyras.coords)}")
print(f"  data_vars   : {list(ds_hyras.data_vars)}")
print(f"  grid_mapping: {ds_hyras[list(ds_hyras.data_vars)[0]].attrs.get('grid_mapping', 'none')}")

if ds_era5 is not None:
    print("\nERA5:")
    print(f"  dims        : {dict(ds_era5.dims)}")
    print(f"  coord names : {list(ds_era5.coords)}")
    print(f"  data_vars   : {list(ds_era5.data_vars)}")

print("\nKey question answers:")
print("  Q1 HYRAS coord names    -> see Section 2 output")
print("  Q2 Rotated-pole CRS     -> see grid_mapping in Section 2")
print("  Q3 interp works as-is   -> see Section 3 result")
print("  Q4 Minimal fix needed   -> see Section 4 experiments")


# ---------------------------------------------------------------------------
# FINDINGS — recorded after running this spike on 2026-04-20
# ---------------------------------------------------------------------------
#
# HYRAS file: hurs_hyras_1_2022_v6-1_de.nc  (113 MB, relative humidity)
#
# Q1 — Coordinate/dimension names
#   Dims   : time(365), y(890), x(665)
#   x/y    : projection coordinates in METRES, not degrees (values ~4e6 / ~2.7e6)
#   Also present: 2D auxiliary arrays lat(y,x) and lon(y,x) in WGS84 degrees
#   Conclusion: NOT rotated-pole as the plan assumed — the plan note in A3 is wrong.
#
# Q2 — Projection
#   grid_mapping = 'crs'  →  ETRS89-extended / LAEA Europe  (EPSG:3035)
#   Lambert Azimuthal Equal Area, origin lat=52°N lon=10°E
#   Full CRS WKT and proj4 string embedded in the file.
#
# Q3 — Does interpolate_netcdf work as-is on HYRAS?
#   NO — raises ValueError: Dimensions {'lat', 'lon'} do not exist.
#   The function tries to .interp() on 'latitude'/'longitude' or 'lat'/'lon',
#   but HYRAS only has 'x'/'y' as dimension coordinates.
#   (lat/lon exist only as non-dimension 2D auxiliary arrays.)
#
# Q4 — What minimal fix does interpolate_netcdf need?
#   Winning strategy: pyproj reproject WGS84 -> EPSG:3035, then interp(y=..., x=...)
#     Munich (48.137°N, 11.576°E) -> LAEA (x=4438346m, y=2781635m)
#     Result: 43.60 %  [PLAUSIBLE for Munich July 2022]
#   pyproj 3.7.1 is already in the pixi env — no new dependency.
#
#   Alternative (nearest-only, no bilinear): argmin on 2D lat/lon auxiliary arrays
#     Result: 43.70 %  [PLAUSIBLE] — but nearest-neighbor only, not interpolated.
#
#   Naive interp(y=48.137, x=11.576): NaN (coordinate units are metres, not degrees).
#
# FILENAME PATTERN (important for HYRASDownloader):
#   Format: {prefix}_{year}_v{X}-{Y}_de.nc
#   The plan's regex in Section 7 is missing the '_de' suffix — must be fixed.
#
# ERA5 — downloaded 2026-04-20, new CDS API (cdsapi >= 0.7 / ecmwf-datastores)
#   File size: ~13kB (small bbox 47.5-49N, 11-12.5E; 3 days; 4 times/day; 1 var)
#   dims      : valid_time(12), latitude(16), longitude(16)
#   coords    : valid_time, latitude, longitude, expver, number
#   data_vars : t2m  (float32, Kelvin)
#
#   KEY FINDING: new CDS API uses 'valid_time' NOT 'time'.
#   interpolate_netcdf checks only for 'time' in point.coords -> skips time
#   selection -> returns all 12 time steps as a 1D array instead of a scalar.
#   This is a second bug to fix in Phase A3 alongside the HYRAS x/y issue.
#
#   Otherwise interpolate_netcdf works for ERA5: latitude/longitude dims are
#   found correctly and bilinear interp runs without error.
#   First time-step value: ~300K (~27°C) for Munich 2022-07-14 — PLAUSIBLE.
#
# NEXT STEPS:
#   1. Fix A3 note in weather_source_architecture_plan.md:
#        "HYRAS uses ETRS89 LAEA (EPSG:3035), not rotated-pole."
#        "new CDS ERA5 uses valid_time coordinate, not time."
#   2. Implement interpolate_netcdf fix (two sub-fixes):
#        a) HYRAS: detect x/y dims + grid_mapping -> pyproj reproject -> interp(y, x)
#        b) ERA5 new CDS: detect 'valid_time' as fallback time coordinate name
#   3. Fix HYRASDownloader filename pattern: add '_de' before '.nc'
# ---------------------------------------------------------------------------
