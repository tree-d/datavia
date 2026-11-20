# Datavia Kriging

External drift kriging (EDK) and ordinary kriging (OK) of station data, with
the empirical variogram and the variogram fit, computed in Mojo on all CPU
cores. Import name: `datavia.kriging`.

datavia uses it to interpolate the daily DWD station observations onto the
HYRAS 1 km grid (weather source `"DWD_stations"`). The package itself is
self-contained: it depends only on numpy at the Python level and on Mojo/MAX
at run time, not on the rest of datavia. It can be moved into its own
repository unchanged (see [Extracting the package](#extracting-the-package)).

---

## Installation

The package needs **Mojo** and **MAX** at run time. Get them either way:

| How | Command |
|---|---|
| pip | `pip install "datavia-kriging[mojo]"` (PyPI packages `mojo` and `max`) |
| conda / pixi | install `mojo` and `max` from the channel `https://conda.modular.com/max`, then the package without the extra |

In the datavia repository, the pixi feature `kriging` (part of the `default`
and `dev` environments) does this:

```toml
[tool.pixi.feature.kriging]
channels = ["conda-forge", "https://conda.modular.com/max"]

[tool.pixi.feature.kriging.dependencies]
mojo = ">=1.2,<2"
max = ">=26.6,<27"

[tool.pixi.feature.kriging.pypi-dependencies]
datavia-kriging = { path = "./packages/kriging/", editable = true }
```

Use the conda package `max`, not `max-all`: `max-all` pulls in pandas 3,
which conflicts with datavia's `pandas<3`.

The `.mojo` sources are installed with the package. `mojo.importer` compiles
them on the first call into `__mojocache__/` next to the sources, so the first
call takes a few seconds and **the package directory must be writable**.

Do not install this package next to `datavia-extensions` (the repository
this code was copied from): both provide `datavia.kriging`.

---

## Quick start

```python
import numpy as np
from datavia.kriging import krige, krige_grid, Variogram

# Stations: coordinates [m] in a projected CRS, elevation [m], and values
# (n_stations, n_days); a missing value equals `nodata`.
rng = np.random.default_rng(0)
sx, sy = rng.uniform(0, 5e4, 60), rng.uniform(0, 5e4, 60)
sh = rng.uniform(0, 800, 60)
sz = 5 - 0.0065 * sh[:, None] + rng.normal(0, 0.3, (60, 10))

# At points: estimates (n_points, n_days)
r = krige(np.array([1e4, 4e4]), np.array([2e4, 3e4]), np.array([100.0, 700.0]),
          sx, sy, sh, sz, nodata=-9999.0, max_dist=1e5)
r.estimates        # float32 (2, 10)
r.variogram        # Variogram(nugget, sill, range, model_type), fitted to all days
r.bins             # the empirical variogram it was fitted to
r.converged        # whether the fit converged

# On a grid: easting, northing and DEM are 2-D (ny, nx); estimates (n_days, ny, nx)
easting, northing = np.meshgrid(np.arange(0, 5e4, 1e3), np.arange(0, 4e4, 1e3))
dem = rng.uniform(0, 800, easting.shape)
g = krige_grid(easting, northing, dem, sx, sy, sh, sz,
               nodata=-9999.0, dem_nodata=-9999.0, max_dist=1e5)

# Precipitation (rain: (n_stations, n_days) gauge values): no negative
# estimates, and 0 where only a distant gauge reports
p = krige_grid(easting, northing, dem, sx, sy, sh, rain,
               nodata=-9999.0, dem_nodata=-9999.0, max_dist=1e5,
               correct_negative=True, dist_zero=True)
```

### Keep and reuse a variogram

```python
r.save_variogram("temperature.json")              # fitted: with bins and `converged`
v = Variogram.load("temperature.json")
r2 = krige(x, y, h, sx, sy, sh, new_values,       # later days, other targets, ...
           nodata=-9999.0, max_dist=1e5, variogram=v)
```

With `variogram=` given, nothing is fitted and `r2.bins` / `r2.converged` are
`None`. The file holds the model by name, the nugget and sill, the range in
metres, and for a fitted variogram the empirical bins:

```json
{
 "version": 1,
 "model": "exponential",
 "nugget": 0.173,
 "sill": 0.945,
 "range_m": 40632.5,
 "converged": true,
 "bins": {"lag_m": [...], "semivariance": [...], "pair_count": [...]}
}
```

Floats are written at full precision, so a reloaded variogram gives
bit-identical estimates.

---

## The method

**Kriging** estimates a target value as a weighted sum of station values,
`z(s0) = sum_i w_i z_i`. The weights make the estimate unbiased and its
error variance smallest, given the **variogram** `gamma(h)`, which is half the
expected squared difference of two values `h` metres apart. For ordinary
kriging they solve

```
| Gamma  1 | | w  |   | gamma_0 |     Gamma_ij  = gamma(|s_i - s_j|)
| 1^T    0 | | mu | = |    1    |     gamma_0,i = gamma(|s_i - s0|)
```

**External drift kriging** (the default, `drift=True`) also makes the weights
reproduce the target's elevation, `sum_i w_i h_i = h0`, so the local mean
follows the terrain:

```
| Gamma  1  h | | w   |   | gamma_0 |
| 1^T    0  0 | | mu1 | = |    1    |
| h^T    0  0 | | mu2 |   |   h0    |
```

### Variogram estimation (unless `variogram=` is given)

1. **Empirical variogram.** Every station pair within `max_lag` adds
   `(z_i - z_j)^2` to the bin of its distance, on every day both report.
   Pairs with `|h_i - h_j| / distance > max_elevation_gradient` are left out.
   Bins with fewer than `min_pairs_per_bin` pairs are merged into a
   neighbour. Semivariance of a bin: `sum / (2 * pairs)`.
2. **Fit.** A model is fitted to the bins (normalised to (0, 1]) by a bounded,
   deterministic Nelder-Mead search minimising
   `sum_k pairs_k * |gamma_k - gamma(lag_k)|`:
   - exponential (`EXPONENTIAL`, default): `c0 + c (1 - exp(-h / a))`
   - spherical (`SPHERICAL`): `c0 + c (1.5 h/a - 0.5 (h/a)^3)` for `h < a`, else `c0 + c`

   The range `a` is in metres. Nugget `c0` and sill `c` stay in normalised
   units, which does not change the kriging weights. `gamma(0)` is the nugget.

### Estimator per target and day

Only stations within `max_dist` that report a value take part:

| Reporting stations | Estimate |
|---|---|
| none, or all exactly 0 | 0 |
| one | its value, or 0 with `dist_zero` if it is farther than `threshold_dist` |
| two | inverse squared distance weighting |
| three or more | kriging; the weights are reused while the same stations report |
| singular kriging system | `OUT_NODATA` (-9999), counted in `singular_count` |

"None → 0" suits zero-inflated variables like precipitation. For other
variables, choose `max_dist` so that every target has stations in range.

---

## Settings

| Argument | Default | Effect |
|---|---|---|
| `nodata` | (required) | Marks a missing station value; NaN is not supported |
| `max_dist` | (required) | Search radius [m] around each target |
| `dem_nodata` | (required, `krige_grid`) | Cells whose rounded elevation equals it get `OUT_NODATA` |
| `drift` | `True` | `True`: EDK with elevation as drift; `False`: ordinary kriging |
| `variogram` | `None` | Use this `Variogram` instead of fitting one |
| `model_type` | `EXPONENTIAL` | Model to fit (`EXPONENTIAL` or `SPHERICAL`) |
| `correct_negative` | `False` | Set negative estimates to 0 |
| `dist_zero` | `False` | Single reporting station farther than `threshold_dist` → 0 |
| `threshold_dist` | 0 (`krige`); half a cell diagonal (`krige_grid`) | Distance [m] for `dist_zero` |
| `bin_width` | 1000 | Width [m] of the variogram bins |
| `max_lag` | 80 000 | Largest pair distance [m] in the variogram |
| `max_elevation_gradient` | 0.2 | Pairs steeper than this are left out of the variogram |
| `min_pairs_per_bin` | 30 | Sparser bins are merged into a neighbour |

Coordinates must be in one projected CRS in metres (e.g. EPSG:3035 or UTM).
Stations must not share a position.

---

## Results

`krige` and `krige_grid` return a `KrigingResult`:

| Field | Meaning |
|---|---|
| `estimates` | float32; `(n_points, n_days)` / `(n_days, ny, nx)`, no day axis for 1-D input; `OUT_NODATA` where there is no estimate |
| `variogram` | the variogram used |
| `bins` | `EmpiricalBins(lag, semivariance, pair_count)` in physical units, or `None` if given |
| `converged` | whether the fit converged, or `None` |
| `singular_count` | target-days with a singular system (e.g. EDK with all stations at one elevation) |
| `sum_warning_count` | weight sets not summing to 1 within 1e-4 (used anyway) |

With empty target arrays, `krige` only estimates the variogram. This is how
datavia fits its monthly variograms.

---

## Performance

All work runs in Mojo with the GIL released. The empirical variogram is
parallel over station rows, and the kriging over target cells (with chunks
interleaved, so dense and sparse regions spread evenly). The inner loops are
SIMD-vectorised. Results do not depend on the number of cores. To use fewer
cores, restrict the process before the first call (e.g. `taskset`).

The cost per target grows with the cube of the number of reporting stations
within `max_dist`, times the number of distinct sets of reporting stations
over the days (the weights are reused while the same stations report). Dense
networks with gaps that change from day to day, such as rain gauges, are
therefore much more expensive than climate stations that report every day.

---

## Layout

```
packages/kriging/
├── pyproject.toml            # distribution datavia-kriging; numpy; extra [mojo]
├── datavia/kriging/          # no datavia/__init__.py (namespace package portion)
│   ├── __init__.py           # public API and method overview
│   ├── _types.py             # Variogram, EmpiricalBins, KrigingResult, file format
│   ├── _krige.py             # krige, krige_grid: arguments -> Mojo extension
│   ├── _core.mojo            # the Python extension module (one function: krige)
│   └── kernels/              # Mojo package with the algorithms
│       ├── empirical_variogram.mojo
│       ├── variogram.mojo                # models, misfit, bounded Nelder-Mead
│       ├── ordinary_kriging.mojo         # neighbourhood, estimators, solve, cell loop
│       └── external_drift_kriging.mojo   # the bordered EDK system
└── tests/
    ├── test_api.py           # Python API on synthetic data
    ├── test_variogram_io.py  # saving and loading variograms
    └── mojo/                 # kernel tests against scalar reference implementations
```

## Tests

From the datavia repository root:

```sh
pixi run test-kriging        # Python API (pytest)
pixi run test-kriging-mojo   # Mojo kernels against scalar references
```

## Extracting the package

To move `datavia.kriging` into its own repository:

1. Copy `packages/kriging/` to the new repository root.
2. Add a pixi section to its `pyproject.toml`: workspace channels
   `conda-forge` and `https://conda.modular.com/max`, dependencies `python`,
   `numpy`, `mojo` and `max`, the package itself as an editable
   pypi-dependency, and `pytest` plus the test tasks above with
   `-I datavia/kriging` instead of `-I packages/kriging/datavia/kriging`.
3. In datavia, replace the path dependency of the `kriging` feature with the
   new repository (`datavia-kriging = { git = "...", rev = "..." }`) and
   delete `packages/kriging/`.

No code changes are needed. Nothing in the package imports datavia, and
datavia uses only the public API (`krige`, `Variogram`, `EmpiricalBins`,
`OUT_NODATA`, the model constants).
