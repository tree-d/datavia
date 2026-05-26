# Weather Pipeline Report — ERA5-Land via CDS API (Copernicus)

> **Purpose:** Guidance, examples, and processing tricks for implementing the datavia `weatherPipeline`.  
> **Source analysed:** `copernicus/src/` — an existing pipeline from a related project (BioDT / Grasslands PDT).  
> **Note:** This report is for inspiration and learning only. Do not copy code from the source project.

---

## Table of Contents

1. [Data Source](#1-data-source)
2. [Download Architecture](#2-download-architecture)
3. [Reading Raw Files](#3-reading-raw-files)
4. [Unit Conversions](#4-unit-conversions)
5. [Hourly → Daily Aggregation — Special Cases](#5-hourly--daily-aggregation--special-cases)
6. [Output Format — Hourly](#6-output-format--hourly)
7. [Output Format — Daily](#7-output-format--daily)
8. [Datavia Pipeline — Recommended Output Shape](#8-datavia-pipeline--recommended-output-shape)
9. [Required Python Dependencies](#9-required-python-dependencies)
10. [Summary of Non-Obvious Tricks](#10-summary-of-non-obvious-tricks-quick-reference)

---

## 1. Data Source

### What it is

**ERA5-Land** is a reanalysis dataset produced by ECMWF. It provides hourly estimates of
land-surface variables at 0.1° × 0.1° (≈ 9 km) resolution globally from 1950 to present
(with ≈ 3-month lag).

The three variables used in the reference project are:

| Logical name | ERA5 variable name | Short name | Raw unit |
|---|---|---|---|
| Precipitation | `total_precipitation` | `tp` | m (accumulative since UTC midnight) |
| Air temperature | `2m_temperature` | `t2m` | K (instantaneous) |
| Solar radiation downward | `surface_solar_radiation_downwards` | `ssrd` | J/m² (accumulative since UTC midnight) |

Additional variables needed for FAO-56 Penman-Monteith PET
(infrastructure present in the reference code but not yet active):

| ERA5 variable name | Purpose |
|---|---|
| `10m_u_component_of_wind` / `10m_v_component_of_wind` | Wind speed |
| `2m_dewpoint_temperature` | Actual vapour pressure |
| `surface_pressure` | Psychrometric constant |
| `surface_latent_heat_flux` | Latent heat for energy balance |

---

### How to access — CDS API

All downloads go through the **Copernicus Climate Data Store (CDS)** Python client.

**Setup steps:**

1. Create a free **ECMWF account**: <https://www.ecmwf.int/>
2. Accept the ERA5-Land licence at: <https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land>
3. Get your **Personal Access Token**: <https://cds.climate.copernicus.eu/how-to-api>
4. Save credentials to `$HOME/.cdsapirc`:

   ```ini
   url: https://cds.climate.copernicus.eu/api
   key: <your-personal-access-token>
   ```

5. Install the client:

   ```bash
   pip install cdsapi
   ```

**Minimal download example:**

```python
import cdsapi

client = cdsapi.Client()
client.retrieve(
    "reanalysis-era5-land",
    {
        "variable": [
            "2m_temperature",
            "total_precipitation",
            "surface_solar_radiation_downwards",
        ],
        "product_type": "reanalysis",
        "year": "2020",
        "month": "03",
        "day": [f"{d:02d}" for d in range(1, 32)],
        "time": [f"{h:02d}:00" for h in range(24)],
        "area": [52.0, 11.0, 51.0, 13.0],   # N, W, S, E bounding box
        "grid": "0.1/0.1",
        "data_format": "grib",
        "download_format": "unarchived",
    },
    "era5_2020_03.grib"
)
```

---

### How to cite

```
Muñoz Sabater, J. (2019):
  ERA5-Land hourly data from 1950 to present.
  Copernicus Climate Change Service (C3S) Climate Data Store (CDS).
  DOI: 10.24381/cds.e2161bac
```

**Useful links:**

- Dataset page: <https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land>
- Full licence: <https://apps.ecmwf.int/datasets/licences/copernicus/>
- Technical documentation: <https://confluence.ecmwf.int/display/CKB/ERA5-Land%3A+data+documentation>

**Secondary method references:**

- **FAO-56** (Penman-Monteith, SVP formulas): Allen et al. 1998, FAO Irrigation and Drainage Paper 56.
- **Thornthwaite PET**: Pereira & Pruitt 2004, Agricultural Water Management 66, 251–257.
  <https://doi.org/10.1016/j.agwat.2003.11.003>

---

## 2. Download Architecture

### Month grouping with buffer months

The pipeline requests **one extra month before the first** and **one after the last** requested month. This is because:

- ERA5-Land accumulative variables reset to zero at UTC midnight.
- At the very start of the first local-time day of the requested range, the UTC midnight of the previous month is needed to reconstruct the accumulation correctly.
- Full-year requests are batched into four-month ranges `01-04`, `05-08`, `09-12` to reduce the number of CDS API calls.

### Area vs point download

| Mode | When to use | CDS `area` parameter | Notes |
|---|---|---|---|
| **Area (bounding box)** | Multiple locations or GRIB format | `[lat_max, lon_min, lat_min, lon_max]` | Single request covers all coordinates; each location is extracted later by spatial interpolation |
| **Point (single location)** | Single location, NetCDF only | 4 nearest grid cells centred on the point | GRIB format does not support point download via CDS |

**Grid snapping:** Area boundaries must be aligned to the ERA5 0.1° grid. Snap each coordinate:

```python
resolution = 0.1
lat_snapped = round(lat * round(1 / resolution)) / round(1 / resolution)
# use floor for south/west boundaries, ceil for north/east boundaries
```

### OPeNDAP caching layer (reference project only)

Before hitting the CDS API, the reference code checks an OPeNDAP HTTP server
(`http://opendap.biodt.eu/grasslands-pdt/`) and the local `weatherDataRaw/` folder for
an already-downloaded file covering the required area and time. If a larger cached bounding
box exists it is reused directly — avoiding redundant CDS downloads when collaborating on
overlapping study areas.

> **For the datavia weather pipeline:** This caching layer is an organisational optimisation
> and is not needed in an initial implementation.

---

## 3. Reading Raw Files

### GRIB format (recommended)

```python
import xarray as xr

ds = xr.open_dataset(
    "era5_2020_03.grib",
    engine="cfgrib",
    backend_kwargs={"indexpath": ""},
)
# Dimensions: valid_time, latitude, longitude

# Interpolate linearly to target coordinates
t2m_at_point = ds["t2m"].interp(latitude=51.42, longitude=12.24)
# Result: DataArray with dimension valid_time
```

> **Critical GRIB artifact:** The first **23 values** and the last **1 value** of every
> monthly GRIB file downloaded from CDS are `NaN`. Always trim them:
>
> ```python
> data = t2m_at_point.values[23:-1]
> ```

### NetCDF format (alternative)

```python
import netCDF4
import numpy as np
from scipy.interpolate import griddata

ds   = netCDF4.Dataset("era5_2020_03.nc")
times = netCDF4.num2date(ds.variables["valid_time"][:],
                         units=ds.variables["valid_time"].units)
lats = ds.variables["latitude"][:]
lons = ds.variables["longitude"][:]
t2m  = ds.variables["t2m"][:]   # shape: (time, lat, lon)

# Bilinear interpolation to a single point
target = (51.42, 12.24)
points = np.array([(la, lo) for la in lats for lo in lons])
t2m_at_point = np.array([
    griddata(points, t2m[i].flatten(), [target], method="linear")[0]
    for i in range(len(times))
])
```

---

## 4. Unit Conversions

All conversions are applied immediately after extraction, before any daily aggregation.

| Variable | Raw ERA5 unit | Target unit | Formula |
|---|---|---|---|
| Temperature | K | °C | `T_C = T_K − 273.15` |
| Precipitation | m (accumulative) | mm (accumulative) | `P_mm = P_m × 1000` |
| SSRD | J/m² (accumulative) | J/m²/day (daily total) | aggregation (see §5c) |
| SSRD → PAR | J/m²/day | µmol/m²/s | `PAR = SSRD × 4.57 × 0.5 / 86400` |
| Wind speed 10 m → 2 m | m/s at 10 m | m/s at 2 m | log-profile (see below) |

**Wind profile correction (log-law, roughness length z₀ = 0.03 m for grass):**

```python
import numpy as np

def wind_speed_height_change(wind_speed, initial_height=10, target_height=2, z0=0.03):
    return wind_speed * np.log(target_height / z0) / np.log(initial_height / z0)
```

---

## 5. Hourly → Daily Aggregation — Special Cases

This is the most intricate part of the pipeline. The sections below document each
non-obvious subtlety.

---

### 5a. Timezone handling — no DST!

The timezone offset is always the **winter (standard) UTC offset**, with no daylight-saving
time applied. Compute it once per location using `timezonefinder`:

```python
from timezonefinder import TimezoneFinder
from zoneinfo import ZoneInfo
import datetime

tz_name = TimezoneFinder().timezone_at(lat=51.42, lng=12.24)
tz = ZoneInfo(tz_name)

# Use January 1 to guarantee the winter (standard) offset:
offset = datetime.datetime(2020, 1, 1, tzinfo=tz).utcoffset()
tz_offset_hours = int(offset.total_seconds() / 3600)
```

> **Reason:** DST would change the offset during summer, which would break the logic that
> maps a fixed offset to a fixed number of rows to trim from the start/end of the data.

---

### 5b. Accumulative variable reconstruction (precipitation & SSRD)

ERA5 precipitation and SSRD **reset to zero at UTC midnight every day**, not at local
midnight. For non-UTC timezones you must reconstruct daily local-calendar totals from the
UTC-based accumulation.

**For UTC zones (`tz_offset_hours == 0`):**

```python
# The accumulated value at 00:00 UTC of day D+1 == total for UTC day D
daily_total = accumulated_utc_midnight_values[1:]   # skip the initial 00:00
```

**For UTC+ zones (e.g. UTC+1, `k = 1`):**

```python
# Local day D runs from 23:00 UTC(D-1) to 23:00 UTC(D).
# It straddles two UTC accumulation cycles.
# Reconstruct by combining the tail of UTC day D with the head of UTC day D+1.

def daily_accumulated_nonzero_offset(values_hourly, k):
    """
    values_hourly: 1-D array, length = N_days*24 + 1, starting at 00:00 local.
    k: UTC offset in hours (positive = east of UTC).
    """
    n_days = (len(values_hourly) - 1) // 24
    result = np.empty(n_days)
    for d in range(n_days):
        # hours 00:00...(k-1):00 local belong to UTC day D+1 cycle
        tail = values_hourly[d*24 + (24 - k)]        # accumulated at UTC midnight of day D+1
        head = values_hourly[(d+1)*24 + (24 - k)]    # accumulated at UTC midnight of day D+2
        result[d] = tail + (head - values_hourly[(d+1)*24])
    return result
```

---

### 5c. Midnight-inclusive daily temperature statistics

ERA5 hourly temperature covers `00:00, 01:00, …, 23:00` for each calendar day.
To compute a true midnight-to-midnight mean, minimum, or maximum you need the `00:00` of the
**next** day as the 25th point — this is why the hourly array has length `N_days×24 + 1`.

```python
import numpy as np
import statistics

def daily_mean_00_24(values_hourly):
    """
    25-point window [00:00 … 24:00] per day.
    The midnight value is averaged with the next day's 00:00 before computing the mean
    to avoid double-counting the boundary.
    """
    n_days = (len(values_hourly) - 1) // 24
    result = np.empty(n_days)
    for d in range(n_days):
        window = list(values_hourly[d*24 : d*24 + 25])
        # Replace 00:00 with the average of today's and tomorrow's midnight
        window[0] = statistics.mean([window[0], window[24]])
        result[d] = np.mean(window[:24])
    return result

def daily_min_00_24(values_hourly):
    n_days = (len(values_hourly) - 1) // 24
    return np.array([np.min(values_hourly[d*24 : d*24 + 25]) for d in range(n_days)])

def daily_max_00_24(values_hourly):
    n_days = (len(values_hourly) - 1) // 24
    return np.array([np.max(values_hourly[d*24 : d*24 + 25]) for d in range(n_days)])
```

---

### 5d. Daylight-only temperature mean

For canopy / phenology models, a temperature mean weighted to daylight hours is more
representative than a 24-hour mean. Sunrise and sunset are computed with `astral`:

```python
from astral import LocationInfo
from astral.sun import sunrise, sunset
import datetime, numpy as np

def daily_mean_daylight(values_hourly, dates, lat, lon):
    """
    values_hourly: 1-D array length N_days*24 + 1
    dates: list of 'YYYY-MM-DD' strings, length N_days
    """
    loc = LocationInfo(latitude=lat, longitude=lon)
    n_days = len(dates)
    result = np.empty(n_days)

    for d, date_str in enumerate(dates):
        date = datetime.date.fromisoformat(date_str)
        sr = sunrise(loc.observer, date=date)
        ss = sunset(loc.observer,  date=date)

        # Add 30 min to account for ERA5 hour-centre convention
        sr_min = sr.hour * 60 + sr.minute + 30
        ss_min = ss.hour * 60 + ss.minute + 30

        hours = values_hourly[d*24 : d*24 + 24]
        weights = np.zeros(24)
        for h in range(24):
            h_start = h * 60
            h_end   = h_start + 60
            # fractional overlap of this hour with [sr_min, ss_min]
            overlap = max(0, min(h_end, ss_min) - max(h_start, sr_min))
            weights[h] = overlap / 60.0

        result[d] = np.dot(hours, weights) / weights.sum() if weights.sum() > 0 else np.nan

    return result
```

---

### 5e. SSRD → PAR conversion

```python
import numpy as np

def par_from_ssrd(ssrd_daily_Jm2):
    """
    ssrd_daily_Jm2: daily total SSRD in J/m²
    Returns: PAR in µmol/m²/s
      4.57  — energy to photon flux (µmol/J)
      0.5   — PAR fraction of total solar radiation
      86400 — seconds per day
    """
    return np.maximum(0.0, ssrd_daily_Jm2 * 4.57 * 0.5 / 86400)
```

> **Always clamp to ≥ 0.** Numerical noise in the SSRD accumulation can produce tiny
> negative values after differencing.

---

### 5f. PET — Thornthwaite method

Thornthwaite PET requires complete calendar years. Return `NaN` for any partial year.

```python
import numpy as np

def heat_index(temperature_monthly):
    """temperature_monthly: array shape (n_years, 12) in °C"""
    return np.sum(np.maximum(0, 0.2 * temperature_monthly) ** 1.514, axis=1)

def exponent_a(I):
    return 6.75e-7 * I**3 - 7.71e-5 * I**2 + 1.7912e-2 * I + 0.49239

def get_pet_thornthwaite(T_eff_daily, day_length_h, dates):
    """
    T_eff_daily: effective temperature per day (°C)
    day_length_h: day length per day (hours)
    dates: list of 'YYYY-MM-DD' strings
    Returns: PET in mm/day
    """
    # Effective temperature (Pereira & Pruitt 2004, k=0.69)
    # T_eff = 0.69/2 * (3*T_max - T_min)   — computed upstream

    # Require full years
    years = sorted(set(d[:4] for d in dates))
    # ... group T_eff by month → monthly means → heat_index per year ...

    correct_to_daily = day_length_h / 360.0   # Eq.5

    pet = np.empty(len(dates))
    for i, (T, c) in enumerate(zip(T_eff_daily, correct_to_daily)):
        if T <= 0:
            pet[i] = 0.0
        elif T > 26:
            pet[i] = c * (-415.85 + 32.24 * T - 0.43 * T**2)   # Eq.4
        else:
            # I and a are year-specific — look them up from precomputed arrays
            pet[i] = c * 16 * (10 * T / I_year) ** a_year        # Eq.1
    return pet
```

**Effective temperature (Pereira & Pruitt 2004, k = 0.69):**

```python
T_eff = 0.69 / 2 * (3 * T_max_daily - T_min_daily)
```

---

## 6. Output Format — Hourly

Tab-separated `.txt` file, `float_format="%.6f"`, missing values as `"nan"`.

**Columns:**

```
Valid time | Local time | Precipitation[mm] (acc.) | Temperature[degC] | SSRD[Jm-2] (acc.)
```

**Example rows:**

```
Valid time              Local time              Precipitation[mm] (acc.)  Temperature[degC]  SSRD[Jm-2] (acc.)
2020-03-01T00:00+00:00  2020-03-01T01:00+01:00  0.000000                  -1.834228          0.000000
2020-03-01T01:00+00:00  2020-03-01T02:00+01:00  0.012540                  -2.011300          0.000000
```

**Filename pattern:**

```
weatherDataPrepared/lat51.421813_lon12.241529__2020-03-01_2020-11-30__hourly__grib.txt
```

A companion **data query protocol** file is also written with columns
`year | month | data_source | time_stamp | info` to document exactly which CDS requests
produced the data.

---

## 7. Output Format — Daily

Tab-separated `.txt`, same float format.

**Columns:**

```
Date | Precipitation[mm] | Temperature[degC] | Temperature_Daylight[degC] | PAR[µmolm-2s-1] | Daylength[h] | PET[mm]
```

**Filename pattern:**

```
weatherDataPrepared/lat51.421813_lon12.241529__2020-03-01_2020-11-30__weather.txt
```

---

## 8. Datavia Pipeline — Recommended Output Shape

The datavia convention uses `np.ndarray` arrays or plain `dict` objects.
For the weather pipeline, the natural delivery unit is **one location, one year** as a dict
of 1-D daily arrays:

```python
import numpy as np

weather_data = {
    # shape (N_days,) — ISO date strings
    "dates":                     np.array(["2020-01-01", "2020-01-02", ...], dtype="U10"),

    # all float64, shape (N_days,)
    "precipitation_mm":          np.ndarray,   # daily total, mm
    "temperature_degC":          np.ndarray,   # daily mean 00:00–24:00, °C
    "temperature_daylight_degC": np.ndarray,   # daylight-weighted mean, °C
    "par_umol_m2_s":             np.ndarray,   # photosynthetically active radiation
    "day_length_h":              np.ndarray,   # hours of daylight
    "pet_mm":                    np.ndarray,   # potential evapotranspiration, mm
}
```

For multiple coordinates, key the dict by `(lat, lon)` tuples:

```python
all_locations = {
    (51.42, 12.24): weather_data_location_1,
    (48.13,  11.58): weather_data_location_2,
}
```

---

## 9. Required Python Dependencies

| Package | Purpose |
|---|---|
| `cdsapi` | CDS download client |
| `xarray` | GRIB reading |
| `cfgrib` | GRIB backend for xarray |
| `eccodes` | C library required by cfgrib — install via conda: `conda install -c conda-forge eccodes` |
| `netCDF4` | NetCDF reading (alternative format) |
| `numpy` | Array operations |
| `pandas` | DataFrame I/O for txt files |
| `scipy` | `griddata` spatial interpolation (NetCDF area mode) |
| `astral` | Sunrise/sunset for daylight calculations |
| `timezonefinder` | IANA timezone lookup from lat/lon |
| `paramiko` | SFTP upload (optional, only if caching layer is implemented) |
| `python-dotenv` | `.env` credential loading (optional) |

---

## 10. Summary of Non-Obvious Tricks — Quick Reference

| # | Trick | Why it matters |
|---|---|---|
| 1 | Download one extra month before and after the requested range | Covers local-time boundary; needed to reconstruct the first and last local calendar day |
| 2 | GRIB files: skip first 23 values and last 1 value (NaN artifact) | These positions are always NaN in CDS GRIB downloads |
| 3 | Accumulative vars (rain, SSRD) reset at **UTC** midnight, not local midnight | Daily totals in non-UTC zones must be reconstructed by combining two UTC cycles |
| 4 | Use a 25-point window `[00:00 … 24:00]` for temperature mean/min/max | Achieves true midnight-to-midnight statistics |
| 5 | Average `00:00_today` and `00:00_tomorrow` before computing the window mean | Avoids double-counting the midnight boundary |
| 6 | Use **standard-time (winter)** UTC offset only — no DST | A fixed offset maps to a fixed number of array rows to trim; DST would break this |
| 7 | Add 30 min to sunrise/sunset for fractional hour weighting | Accounts for ERA5's hour-centre convention; improves daylight mean accuracy |
| 8 | Clamp PAR to ≥ 0 after SSRD conversion | Numerical noise in accumulated values can yield tiny negatives after differencing |
| 9 | Thornthwaite PET requires full calendar years; return NaN for partial years | Prevents silently wrong PET values caused by an incomplete heat index |
| 10 | Snap area boundaries to `floor/ceil(coord × 10) / 10` before the CDS request | Ensures area bounds align to the ERA5 0.1° grid |
