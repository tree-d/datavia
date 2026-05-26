# Weather Data Sources for Tree Modelling
> Germany-wide projects · Free & open · No API key required  
> Tested: April 2026 · Demo location: lat 51.4218, lon 12.2415 (Leipzig)

---

## TL;DR — Recommendation

| Priority | Source | Why |
|---|---|---|
| **1 · Primary raster** | DWD HYRAS | 5 km obs-based grid, all 3 tree vars, 1951–present |
| **2 · Fallback / hourly** | Open-Meteo / ERA5 | Global, simple REST, hourly data available |
| **3 · Radiation check** | NASA POWER | Independent satellite PAR, daily |
| **4 · Site validation** | Bright Sky (DWD) | Real station measurements at specific points |

---

## Source Overview

### Data Type Classification

```
RASTER  — every grid cell has a value, no spatial gaps
           → suitable for Germany-wide spatial analysis

STATION — measurements only where instruments exist
           → gaps of 10–50 km between stations; NOT suitable as a raster
```

---

## 1 · Open-Meteo Historical API

| Property | Value |
|---|---|
| **Type** | **RASTER** — ERA5 / ERA5-Land reanalysis |
| **Grid** | 0.1° × 0.1° ≈ 9 km, global |
| **Period** | 1940 – present (≈ 5-day lag) |
| **Temporal res.** | Hourly + daily |
| **API key** | None |
| **Licence** | CC BY 4.0 (non-commercial free) |
| **Endpoint** | `https://archive-api.open-meteo.com/v1/archive` |

### Tree-relevant variables

| Parameter | Unit | API name |
|---|---|---|
| Temperature mean/max/min | °C | `temperature_2m_mean/max/min` |
| Precipitation sum | mm/day | `precipitation_sum` |
| Shortwave radiation sum | MJ/m²/day | `shortwave_radiation_sum` |
| FAO ET₀ | mm/day | `et0_fao_evapotranspiration` |
| Daylight duration | s | `daylight_duration` |

> Note: `shortwave_radiation_sum` is in MJ/m²/day.  
> Convert to W/m²: multiply by `1e6 / 86400`.

### Quick example

```python
import requests, pandas as pd

r = requests.get("https://archive-api.open-meteo.com/v1/archive", params={
    "latitude": 51.42, "longitude": 12.24,
    "start_date": "2023-01-01", "end_date": "2023-12-31",
    "daily": "temperature_2m_mean,precipitation_sum,shortwave_radiation_sum,et0_fao_evapotranspiration",
    "timezone": "Europe/Berlin",
})
df = pd.DataFrame(r.json()["daily"])
```

---

## 2 · Bright Sky (DWD station observations)

| Property | Value |
|---|---|
| **Type** | **STATION** — nearest DWD station auto-selected |
| **Coverage** | Germany only |
| **Period** | ≈ 2010 – present |
| **Temporal res.** | Hourly |
| **API key** | None |
| **Endpoint** | `https://api.brightsky.dev/weather` |

### Tree-relevant variables

| Parameter | Unit | Field name |
|---|---|---|
| Temperature | °C | `temperature` |
| Precipitation | mm | `precipitation` |
| Sunshine duration | min | `sunshine` |
| Solar irradiance | W/m² | `solar` |
| Relative humidity | % | `relative_humidity` |

### Limitations for Germany-wide use

- Data exists **only at DWD station locations** — ~600 stations across Germany
- Typical station spacing: 10–50 km → significant spatial gaps
- **Do not use as a raster input** for spatial modelling
- Best use: ground-truth validation of gridded sources at a known site

### Quick example

```python
import requests, pandas as pd

r = requests.get("https://api.brightsky.dev/weather", params={
    "lat": 51.42, "lon": 12.24,
    "date": "2023-06-01", "last_date": "2023-06-30",
})
data = r.json()
print("Station:", data["sources"][0]["station_name"])
df = pd.DataFrame(data["weather"])
```

---

## 3 · NASA POWER

| Property | Value |
|---|---|
| **Type** | **RASTER** — satellite-derived + model |
| **Grid** | 0.5° × 0.5° ≈ 55 km, global |
| **Period** | 1981 – present |
| **Temporal res.** | Daily only |
| **API key** | None |
| **Endpoint** | `https://power.larc.nasa.gov/api/temporal/daily/point` |

### Tree-relevant variables

| Parameter | Unit | API name |
|---|---|---|
| Temperature mean | °C | `T2M` |
| Temperature max/min | °C | `T2M_MAX` / `T2M_MIN` |
| Precipitation (corrected) | mm/day | `PRECTOTCORR` |
| All-sky SW radiation | kWh/m²/day | `ALLSKY_SFC_SW_DWN` |
| Clear-sky SW radiation | kWh/m²/day | `CLRSKY_SFC_SW_DWN` |
| **Photosynthetically active radiation (PAR)** | **W/m²** | **`ALLSKY_SFC_PAR_TOT`** |

> PAR (`ALLSKY_SFC_PAR_TOT`) is especially valuable for tree/canopy models —
> it is provided directly, no conversion factor needed.

### Quick example

```python
import requests, pandas as pd

r = requests.get("https://power.larc.nasa.gov/api/temporal/daily/point", params={
    "parameters": "T2M,T2M_MAX,T2M_MIN,PRECTOTCORR,ALLSKY_SFC_SW_DWN,ALLSKY_SFC_PAR_TOT",
    "community": "AG",
    "longitude": 12.24, "latitude": 51.42,
    "start": "20230101", "end": "20231231",
    "format": "JSON",
})
params = r.json()["properties"]["parameter"]
df = pd.DataFrame(params).rename_axis("date").reset_index()
```

---

## 4 · DWD CDC GeoServer (WFS)

| Property | Value |
|---|---|
| **Type** | **STATION** — 10-min DWD obs. as vector point features |
| **Coverage** | Germany only |
| **Period** | Near-realtime |
| **Temporal res.** | 10 minutes |
| **API key** | None |
| **Endpoint** | `https://cdc.dwd.de/geoserver/ows` (WFS 2.0) |

### Available observation layers (climate-relevant selection)

| WFS Layer name | Variable |
|---|---|
| `CDC:OBS_DEU_PT10M_T2M` | Air temperature at 2 m |
| `CDC:OBS_DEU_PT10M_T5CM` | Air temperature at 5 cm |
| `CDC:OBS_DEU_PT10M_RAD-G` | Global radiation |
| `CDC:OBS_DEU_PT10M_RAD-F` | Diffuse radiation |
| `CDC:OBS_DEU_PT10M_RR` | Precipitation |
| `CDC:OBS_DEU_PT10M_SD` | Sunshine duration |
| `CDC:OBS_DEU_PT10M_TD` | Dewpoint temperature |

> **Important:** These are *point features* (station locations), not gridded rasters.  
> The GeoServer also exposes a WCS endpoint for gridded data but the naming  
> is not user-friendly. For gridded DWD data, use HYRAS (section 5).

### Quick example — list stations in a bounding box

```python
import requests

r = requests.get("https://cdc.dwd.de/geoserver/ows", params={
    "service": "WFS", "version": "2.0.0", "request": "GetFeature",
    "typeNames": "CDC:OBS_DEU_PT10M_T2M",
    "outputFormat": "application/json",
    "BBOX": "11.5,51.0,13.0,52.0,EPSG:4326",
    "count": 10,
})
features = r.json()["features"]
for f in features:
    print(f["properties"])
```

---

## 5 · DWD HYRAS (opendata direct download)

| Property | Value |
|---|---|
| **Type** | **RASTER — observation-interpolated grid** |
| **Grid** | **5 km × 5 km**, Germany + border region |
| **Period** | T: 1951–present · Precip: 1931–present · Rad: 1951–2024 |
| **Temporal res.** | Daily |
| **API key** | None |
| **Format** | NetCDF (.nc), one file per year |
| **Base URL** | `https://opendata.dwd.de/climate_environment/CDC/grids_germany/daily/hyras_de/` |

HYRAS is produced by DWD through **kriging interpolation of actual station measurements**
onto a regular 5 km grid. It is *not* a numerical weather model output — it is
observation-based, making it the most accurate historical gridded dataset for Germany.

### Variables and file naming

| Variable | Subdirectory | Filename pattern | Unit | File size |
|---|---|---|---|---|
| T mean | `air_temperature_mean/` | `tas_hyras_1_{year}_v6-1_de.nc` | °C | ~67 MB/year |
| T max | `air_temperature_max/` | `tasmax_hyras_1_{year}_v6-1_de.nc` | °C | ~78 MB/year |
| T min | `air_temperature_min/` | `tasmin_hyras_1_{year}_v6-1_de.nc` | °C | ~76 MB/year |
| Precipitation | `precipitation/` | `pr_hyras_1_{year}_v6-1_de.nc` | mm/day | ~62 MB/year |
| Global radiation | `radiation_global/` | `rsds_hyras_5_{year}_v4-0_de.nc` | W/m² | ~19 MB/year |
| Relative humidity | `humidity/` | `hurs_hyras_1_{year}_v6-1_de.nc` | % | ~112 MB/year |

> Version numbers (`v6-1`, `v5-0`, etc.) can change when DWD releases updates.  
> Use directory listing to auto-discover the latest version for a given year  
> (see `_hyras_latest_file()` in `test_weather_sources.py`).

### Quick example — extract a time series at one point

```python
import xarray as xr

year = 2023
base = "https://opendata.dwd.de/climate_environment/CDC/grids_germany/daily/hyras_de"

# Download locally first (recommended for large files):
# wget {base}/air_temperature_mean/tas_hyras_1_2023_v6-1_de.nc

ds = xr.open_dataset(f"tas_hyras_1_{year}_v6-1_de.nc")

# Extract one point (nearest grid cell)
t_series = ds["tas"].sel(x=12.24, y=51.42, method="nearest")

# Extract a spatial bbox
t_box = ds["tas"].sel(x=slice(10.0, 15.0), y=slice(47.0, 55.0))

# Convert to pandas
df = t_series.to_dataframe().reset_index()[["time", "tas"]]
```

### Quick example — extract all tree variables for a single year

```python
import xarray as xr, pandas as pd

LAT, LON, YEAR = 51.42, 12.24, 2023
BASE = "https://opendata.dwd.de/climate_environment/CDC/grids_germany/daily/hyras_de"

files = {
    "T_mean":  (f"{BASE}/air_temperature_mean/tas_hyras_1_{YEAR}_v6-1_de.nc",    "tas"),
    "T_max":   (f"{BASE}/air_temperature_max/tasmax_hyras_1_{YEAR}_v6-1_de.nc",  "tasmax"),
    "T_min":   (f"{BASE}/air_temperature_min/tasmin_hyras_1_{YEAR}_v6-1_de.nc",  "tasmin"),
    "precip":  (f"{BASE}/precipitation/pr_hyras_1_{YEAR}_v6-1_de.nc",            "pr"),
    "rad":     (f"{BASE}/radiation_global/rsds_hyras_5_{YEAR}_v4-0_de.nc",       "rsds"),
}

series = {}
for label, (path, var) in files.items():
    ds = xr.open_dataset(path)
    series[label] = ds[var].sel(x=LON, y=LAT, method="nearest").values

df = pd.DataFrame(series)
# df has columns T_mean, T_max, T_min, precip, rad — one row per day
```

---

## Source Comparison Table

| Source | Type | Resolution | Coverage | Period | T | Precip | Radiation |
|---|---|---|---|---|:---:|:---:|:---:|
| **DWD HYRAS** | Raster | 5 km | Germany | 1951–now | ✓ | ✓ | ✓ |
| Open-Meteo / ERA5 | Raster | 9 km | Global | 1940–now | ✓ | ✓ | ✓ |
| NASA POWER | Raster | 55 km | Global | 1981–now | ✓ | ✓ | ✓ (PAR) |
| Bright Sky (DWD) | Station | point | Germany | 2010–now | ✓ | ✓ | ✗ |
| DWD GeoServer WFS | Station | point | Germany | realtime | ✓ | ✓ | ✓ |

---

## Required Python Packages

```bash
pip install requests pandas xarray netCDF4
```

| Package | Used for |
|---|---|
| `requests` | All REST API calls |
| `pandas` | DataFrame handling of JSON responses |
| `xarray` + `netCDF4` | Reading HYRAS NetCDF files |

---

## Notes on Coordinates in HYRAS

HYRAS files use **ETRS89 LAEA Europe (EPSG:3035)** — not a rotated-pole projection
and not WGS84 lat/lon.
The `x` and `y` dimensions are in **metres** on that projected grid.
Passing WGS84 longitude/latitude directly to `xarray.Dataset.interp()` or
`sel(x=LON, y=LAT)` gives silently wrong results or a `ValueError`.

To extract a value at a WGS84 point you must first reproject with `pyproj`:

```python
import pyproj, xarray as xr

ds = xr.open_dataset("tas_hyras_1_2023_v6-1_de.nc")
crs_file = pyproj.CRS.from_cf(ds["crs"].attrs)          # EPSG:3035
transformer = pyproj.Transformer.from_crs(
    "EPSG:4326", crs_file, always_xy=True
)
x_proj, y_proj = transformer.transform(lon_wgs84, lat_wgs84)
value = ds["tas"].interp(x=x_proj, y=y_proj)
```

When using the `datavia` weather pipeline, `interpolate_netcdf` performs this
reprojection automatically — no manual conversion is needed.

---

*Sources verified April 2026. See `test_weather_sources.py` for live API tests.*
