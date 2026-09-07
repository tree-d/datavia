# Zarr Store Implementation Plan

> **Context (2026-05-18):** This plan supersedes the multi-strategy comparison in
> [weather_file_management_strategies.md](weather_file_management_strategies.md).
> It implements **Strategy 4 — Zarr store** as the definitive storage backend for
> gridded weather data, replacing the per-download `.nc` file model.
>
> The motivating insight is that ERA5 (and HYRAS) data is already on a regular
> WGS84 (EPSG:4326) lat/lon grid.  Because the Zarr store is defined on the same
> native grid and resolution, each downloaded tile maps directly to a contiguous
> slice of array indices — **no regridding is needed.**  The previously listed
> "regridding cost" con is eliminated.
>
> **Scope:** `SaverWeather`, `GetterWeather`, `CoverageManager`, `ZarrStoreManager`
> (new), `weather_layers` schema, `pyproject.toml`, and associated tests.
>
> **Branch:** `zarrstore`

---

## 1 · Architecture overview

### 1.1 Store layout on disk

```
<data_dir>/
├── ERA5_land/
│   ├── 2m_temperature/
│   │   ├── 2023.zarr/
│   │   │   ├── .zgroup
│   │   │   └── t2m/
│   │   │       ├── .zarray    ← shape=(8760,797,92), chunks=(24,40,40), dtype=float32
│   │   │       ├── .zattrs    ← CF metadata: units, long_name, grid_mapping
│   │   │       ├── 0.0.0      ← time[0:24], lat[0:40], lon[0:40]
│   │   │       └── ...
│   │   └── 2024.zarr/
│   └── total_precipitation/
│       └── 2024.zarr/
└── HYRAS/
    └── precipitation/
        └── 2024.zarr/
```

One Zarr store per `(source_name, variable, year)`.  Each store holds a
single data variable (identical name to the Datavia variable key) plus
the three coordinate arrays `time`, `latitude`, `longitude`.

### 1.2 Coordinate grids

| Source | Lat resolution | Lon resolution | Time axis | Fill value |
|---|---|---|---|---|
| ERA5-Land | 0.1° | 0.1° | hourly (UTC) | `NaN` (float32) |
| ERA5 (standard) | 0.25° | 0.25° | hourly (UTC) | `NaN` (float32) |
| HYRAS | 5 km ≈ 0.045° | 5 km ≈ 0.045° | daily (00:00 UTC) | `NaN` (float32) |

The full lat/lon axis for each source is defined once in `source_registry.py`
(`ZARR_GRID`) and is read when initialising a new store.  This guarantees
that coordinate values are identical across all downloads and across all
years, so `xr.open_mfdataset` can concatenate stores without alignment errors.

### 1.3 Why the DB stays (but its role changes)

The `weather_layers` table is **not** removed.  It continues to serve as the
write-log consumed by `CoverageManager` to avoid redundant CDS downloads.
One row is inserted per write operation, with the `uri` pointing to the Zarr
store directory (not to a per-download file).

`GetterWeather` no longer queries `weather_layers` for file paths.  Instead
it constructs the store path directly from `(source_name, variable, year)`
and opens it with `xr.open_zarr`.  The coverage tracking in the DB therefore
becomes purely a *download-avoidance cache* — it can be rebuilt at any time
by introspecting the store.

> **Design review:** The design decisions reflected in this plan were reached through
> a structured review between a developer (conservative scope) and a data scientist
> (data-flow focused).  The full dialogue is in
> [zarr_design_review.md](zarr_design_review.md).

---

## 2 · Phased implementation

### Phase A — Dependency and store constants (1 day)

**Goal:** add `zarr` as a dependency; define native grids in `source_registry.py`.

#### A.1 `packages/weather/pyproject.toml`

Add `zarr>=3.0,<4` (Zarr v3, stable since early 2025 and the default used by
xarray's `open_zarr` / `to_zarr`), `numcodecs>=0.12,<1` (Blosc codec;
explicit direct dep — do not rely on transitive zarr dep), and
`fasteners>=0.19` (store write locking; explicit direct dep — not bundled
by zarr v3) to `dependencies`.

```toml
dependencies = [
    ...
    "zarr>=3.0,<4",
    "numcodecs>=0.12,<1",
    "fasteners>=0.19",
]
```

#### A.2 `packages/weather/datavia/weather/source_registry.py`

Add a `ZARR_GRID` key to each entry in `SOURCE_REGISTRY`:

```python
import numpy as np

SOURCE_REGISTRY: dict[str, dict[str, Any]] = {
    "ERA5_land": {
        "grid_downloader": ERA5Downloader,
        "conversions": { ... },               # unchanged
        "nc_variable_map": { ... },           # unchanged
        "zarr_grid": {
            "latitude":  np.arange(55.2, 47.1, -0.1).round(1),   # 82 steps
            "longitude": np.arange(5.9,  15.1,  0.1).round(1),   # 92 steps
            "time_freq": "1h",      # pandas offset alias
            "dtype":     "float32",
            "fill_value": float("nan"),
            # Point-query optimised: large time axis, small spatial footprint.
            # 720 h = 1 month of hourly data; 5×5 cells ≈ 0.5°×0.5° patch.
            # Loading a 1-year time series at one location reads ≤ 12 chunks.
            "chunks":    {"time": 720, "latitude": 5, "longitude": 5},
            "codec":     {"id": "blosc2", "cname": "zstd", "clevel": 3},
        },
    },
    "HYRAS": {
        ...
        "zarr_grid": {
            "latitude":  np.arange(55.1, 47.2, -0.045).round(3),
            "longitude": np.arange(5.9,  15.1,  0.045).round(3),
            "time_freq": "1D",
            "dtype":     "float32",
            "fill_value": float("nan"),
            # HYRAS daily data: 365 days × 10×10 cells (spatial snapshot optimised).
            "chunks":    {"time": 365, "latitude": 10, "longitude": 10},
            "codec":     {"id": "blosc2", "cname": "zstd", "clevel": 3},
        },
    },
}
```

> **Grid bounds rationale (ERA5-Land):** Germany's standard ERA5-Land bounding
> box used throughout the codebase is `(west=5.9, south=47.3, east=15.0,
> north=55.1)`.  The store grid is extended by one cell on each edge so that
> bilinear interpolation does not degrade at the exact boundary.  The lat axis
> runs north-to-south to match ERA5's native array layout and avoid a flip.
>
> **Chunk rationale:** The primary access pattern in this codebase is a point
> time-series query (single lat/lon, full time range).  Chunks are therefore
> large in the time dimension and small in space.  Spatial-snapshot workloads
> (all grid cells, single timestamp) can rechunk offline via
> `ZarrStoreManager.rechunk(variable, year, mode="spatial")`.

---

### Phase B — `ZarrStoreManager` (2–3 days)

**Goal:** encapsulate all Zarr I/O in a single, independently testable class.

**New file:** `packages/weather/datavia/weather/zarr_store_manager.py`

#### Public interface

```python
class ZarrStoreManager:
    """Create, write to, and read from per-(source, variable, year) Zarr stores.

    Parameters
    ----------
    data_dir : str
        Root data directory (from ``get_config().data_directory``).
    source_name : str
        Source identifier, e.g. ``"ERA5_land"``.
    """

    def store_path(self, variable: str, year: int) -> str:
        """Return the filesystem path to the Zarr store directory.

        Parameters
        ----------
        variable : str
            Datavia variable name, e.g. ``"2m_temperature"``.
        year : int
            Calendar year.

        Returns
        -------
        str
            Absolute path: ``<data_dir>/<source_name>/<variable>/<year>.zarr``.
        """

    def ensure_store(self, variable: str, year: int) -> None:
        """Create the Zarr store for the given variable and year if it does not exist.

        Initialises a full-year time axis (hourly or daily, depending on
        ``zarr_grid["time_freq"]``), the lat/lon axes from ``zarr_grid``, and a
        single data array filled entirely with ``fill_value``.  Writes ``zarr_grid``
        CRS and grid-mapping attributes to ``.zattrs``.

        Safe to call multiple times — a no-op when the store already exists.

        Parameters
        ----------
        variable : str
            Datavia variable name.
        year : int
            Calendar year for which to create the store.

        Raises
        ------
        KeyError
            If ``source_name`` is not registered in ``SOURCE_REGISTRY`` or has
            no ``zarr_grid`` entry.
        """

    def write_dataset(self, ds: xr.Dataset, variable: str) -> None:
        """Write an xarray Dataset into the appropriate Zarr region(s).

        Determines the target year(s) from ``ds.time`` (or ``ds.valid_time``),
        calls :meth:`ensure_store` for each year, aligns ``ds`` coordinates
        to the store's axis values, and calls ``ds.to_zarr(store_path,
        region={...})``.

        Cross-year datasets (e.g. a December–January download) are split and
        written to two separate stores automatically.

        Parameters
        ----------
        ds : xr.Dataset
            Dataset returned by the downloader.  Must contain the data variable
            under its ERA5 short name (e.g. ``t2m``); the method resolves it to
            the canonical Datavia name via ``nc_variable_map``.
        variable : str
            Datavia variable name to write (selects the correct data variable
            from ``ds`` via ``nc_variable_map``).

        Raises
        ------
        ValueError
            If ``ds`` coordinates cannot be aligned to the store grid
            (tolerance: half a grid cell).
        """

    def open_store(self, variable: str, year: int) -> xr.Dataset:
        """Open an existing Zarr store as a lazy xarray Dataset.

        Parameters
        ----------
        variable : str
            Datavia variable name.
        year : int
            Calendar year.

        Returns
        -------
        xr.Dataset
            Lazy dataset backed by the Zarr store.  Caller is responsible
            for closing it (use as context manager).

        Raises
        ------
        FileNotFoundError
            If the store does not exist on disk.
        """

    def open_multi_year(self, variable: str, years: list[int]) -> xr.Dataset:
        """Open and concatenate multiple year stores along the time axis.

        Parameters
        ----------
        variable : str
            Datavia variable name.
        years : list[int]
            Ordered list of calendar years to open.

        Returns
        -------
        xr.Dataset
            Concatenated lazy dataset.  Missing years (no store on disk) are
            silently skipped.  Returns an empty Dataset when no stores exist.
        """

    def covered_bbox_for_period(
        self, variable: str, date_start: str, date_end: str
    ) -> tuple[float, float, float, float] | None:
        """Return the spatial bbox covered by non-fill data for the given period.

        Reads the store(s) for the years spanned by ``[date_start, date_end]``
        and computes the axis-aligned bbox of all lat/lon cells that have at
        least one non-NaN value in that time window.  Used by
        :class:`~datavia.weather.coverage_manager.CoverageManager` to cross-check
        DB coverage against actual store contents.

        Parameters
        ----------
        variable : str
            Datavia variable name.
        date_start : str
            ISO date string (``YYYY-MM-DD``).
        date_end : str
            ISO date string (``YYYY-MM-DD``).

        Returns
        -------
        tuple[float, float, float, float] | None
            ``(west, south, east, north)`` in EPSG:4326, or ``None`` if the
            store does not exist or contains only fill values for that period.
        """

    def migrate_nc_file(self, nc_path: str, variable: str) -> None:
        """Import an existing NetCDF file into the Zarr store.

        Opens the file with :func:`xarray.open_dataset`, calls
        :meth:`write_dataset`, then optionally removes the original file.
        Used by the CLI migration command ``datavia migrate-to-zarr``.

        Parameters
        ----------
        nc_path : str
            Absolute path to a previously saved ``.nc`` file.
        variable : str
            Datavia variable name of the data in the file.
        """
```

#### Key implementation details

**Coordinate alignment (`write_dataset`)**

```python
# Map downloaded lat/lon to store index positions using np.searchsorted.
# ERA5 guarantees values on the 0.1° grid, so exact match is expected.
# Tolerance check: abs(ds_lat - store_lat[idx]) < 0.05 (half a cell).

lat_idx_start = np.searchsorted(store_lat[::-1], ds_lat.max()) ...
lon_idx_start = np.searchsorted(store_lon, ds_lon.min())
# Build region dict and call ds_aligned.to_zarr(store, region=region)
```

**Time coordinate normalization**

ERA5 uses `valid_time` instead of `time`.  Before writing, rename and cast
to the store's `time` axis (full-year hourly `DatetimeIndex`):

```python
if "valid_time" in ds.coords and "time" not in ds.coords:
    ds = ds.rename({"valid_time": "time"})
ds["time"] = ds.time.astype("datetime64[ns]")
```

**Atomic write safety**

`zarr`'s region write is not atomic at the filesystem level.  The write is
wrapped with a lock file (`<store_path>.lock`) using `fasteners.InterProcessLock`
to prevent concurrent region writes from the CLI and background scripts.
`fasteners` is listed as a direct dependency in `pyproject.toml` — it is no
longer bundled by zarr v3 and must not be relied on as a transitive dep.

---

### Phase C — Refactor `SaverWeather` (1–2 days)

**Files:** `packages/weather/datavia/weather/saver_weather.py`

`SaverWeather.save()` currently copies the temp `.nc` file to disk and inserts
a DB row.  Under the Zarr model it instead:

1. Opens the downloaded temp `.nc` file as an `xr.Dataset`.
2. Calls `ZarrStoreManager.write_dataset(ds, variable)` for each requested
   variable.
3. Inserts one `weather_layers` row per variable written, with:
   - `uri` = Zarr store path (e.g. `.../ERA5_land/2m_temperature/2024.zarr`)
   - `valid_from` / `valid_until` = time range extracted from `ds`
   - `bbox` = WKT POLYGON of the downloaded tile
   - `file_format` = `"zarr"`

The private helpers `_build_dest_stem()`, `_infer_file_format()` (for `.nc`),
and the `shutil.copy2` call are removed.  The `register_only` path is retained
for the migration command.

`list_managed_files()` is updated to list `.zarr` store directories instead
of `.nc` files.

`delete_store(variable, year, confirmed: bool)` is added: when `confirmed=True`
removes all DB rows for that store and deletes the store directory.  The
`confirmed` flag is set by the caller (CLI or test); no interactive prompt
inside the library.  `delete_registration()` remains for removing DB rows
without touching disk.

---

### Phase D — Refactor `GetterWeather` (1–2 days)

**Files:** `packages/weather/datavia/weather/getter_weather.py`

`GetterWeather.get_data()` currently calls `get_weather_paths()` to look up
`.nc` file paths from the DB, then passes them to `interpolate_netcdf()`.

Under the Zarr model:

1. Determine the set of years spanned by `datetime_utc`.
2. Call `ZarrStoreManager.open_multi_year(variable, years)` — returns a lazy
   concatenated Dataset.
3. Pass the Dataset to `interpolate_netcdf()`.  **Important:** the current
   signature of `interpolate_netcdf` is `(nc_path: str, ...)` — it opens the
   file itself.  Phase D requires adding a second overload:
   `interpolate_netcdf(ds: xr.Dataset, ...)` that skips the `open_dataset`
   call and uses the already-open Dataset directly.  The `nc_path: str`
   signature is retained unchanged for the backward-compatible `.nc` path.
   Both overloads share the same interpolation body.
4. Apply source-aware unit conversions as before.

The `get_weather_paths()` DB query is no longer called from `get_data()`.
`get_existing_layers()` is updated to scan for existing Zarr store directories
instead of querying the DB:

```python
def get_existing_layers(self) -> set[str]:
    mgr = ZarrStoreManager(self.data_dir, self.source_name)
    return mgr.list_available_variables()
```

`ZarrStoreManager.list_available_variables()` scans
`<data_dir>/<source_name>/` for sub-directories containing at least one
`*.zarr` directory.

---

### Phase E — Simplify `CoverageManager` (1 day)

**Files:** `packages/weather/datavia/weather/coverage_manager.py`

`CoverageManager` currently loads `weather_layers` rows and runs the
4-strip spatial subtraction algorithm to find uncovered fragments.  This logic
remains **unchanged** — the DB rows still record `bbox` and `valid_from` /
`valid_until` per download, so the algorithm still works correctly.

The only change is the removal of the filename-uniqueness constraint:
`CoverageManager` no longer needs to care what the `uri` value looks like.
Multiple rows may share the same Zarr store `uri`; that is expected and correct.

**Required (not optional):** `CoverageManager.rebuild_from_store(variable)`
repopulates `weather_layers` from Zarr store contents by calling
`ZarrStoreManager.covered_bbox_for_period()` in a sliding monthly window.
This method is called **automatically** when `missing_spatiotemporal()` detects
that a Zarr store exists on disk for a `(source_name, variable, year)` triple
that has zero DB rows — preventing redundant CDS re-downloads after DB loss
or a fresh checkout.

**Note on `sync_files_and_database`:** The base `Pipeline.sync_files_and_database()`
compares `list_managed_files()` (store directories) against `get_registered_uris()`
(DB `uri` values).  For Zarr stores a single directory has multiple DB rows; a
missing directory correctly deletes all its rows.  A present directory with no DB
rows is recovered by `rebuild_from_store` rather than by the sync.  No changes
to `sync_files_and_database` are needed.

---

### Phase F — Database schema migration (0.5 days)

**Files:** `datavia/library/database/init.sql`

No column additions are strictly required.  The `file_format` column already
accepts arbitrary strings; the value `"zarr"` is added alongside `"netcdf"` and
`"parquet"` in the saver logic.

The `uri` column will now sometimes hold a directory path (Zarr store) rather
than a file path.  No SQL constraint change is needed — the column is `TEXT`
with no uniqueness constraint.

Add one new index to speed up per-variable store lookups in `CoverageManager`:

```sql
CREATE INDEX IF NOT EXISTS idx_weather_source_variable_format
    ON weather_layers (source_name, variable, file_format);
```

This index is additive and safe to apply to existing databases via the
existing idempotent `initialize_database()` mechanism.

---

### Phase G — CLI migration command (0.5 days)

**Files:** `datavia/cli.py` or `datavia/cli_utils.py`

Add a `migrate-to-zarr` sub-command:

```
datavia migrate-to-zarr --source ERA5_land --variable 2m_temperature [--dry-run]
```

Internally:

1. Calls `SaverWeather.list_managed_files()` for `.nc` files only.
2. For each file, calls `ZarrStoreManager.migrate_nc_file(path, variable)`.
3. Removes the `.nc` file and its DB row, inserts a new Zarr DB row.
4. Prints a summary: `Migrated N files → 2 Zarr stores`.

`--dry-run` prints what would happen without modifying anything.

---

### Phase H — Tests (2 days)

**Files:** `tests/test_zarr_store_manager.py` (new),
`tests/test_weather_pipeline_unit.py` (extend),
`tests/test_weather_e2e.py` (extend)

#### H.1 Unit tests for `ZarrStoreManager`

| Test | What it verifies |
|---|---|
| `test_ensure_store_creates_directory` | Store dir and `.zarray` are written. |
| `test_ensure_store_is_idempotent` | Second call does not reset data. |
| `test_write_dataset_fills_correct_region` | Written values are readable at correct lat/lon/time indices. |
| `test_write_dataset_cross_year_split` | A Dec–Jan dataset is split into two stores. |
| `test_write_dataset_raises_on_misaligned_coords` | `ValueError` when coord tolerance exceeded. |
| `test_open_store_raises_when_missing` | `FileNotFoundError` when store absent. |
| `test_open_multi_year_skips_missing` | Missing years are silently skipped. |
| `test_covered_bbox_for_period` | Correct bbox returned for partially filled store. |
| `test_migrate_nc_file` | `.nc` data transferred and queryable from store. |

#### H.2 `SaverWeather` integration tests

Extend `TestSaverWeather` to:

- Verify that `save()` on a mock ERA5 NetCDF creates a `.zarr` directory.
- Verify that `weather_layers` row has `file_format="zarr"` and `uri` pointing
  to the store directory.
- Verify that a second `save()` for an adjacent tile appends to the same store
  (DB inserts second row, store directory is the same).

#### H.3 `GetterWeather` integration tests

Extend `TestGetterWeather` to:

- Verify that `get_data()` reads from the Zarr store rather than `.nc` files
  (no `.nc` file on disk; only a pre-populated `.zarr` store).
- Verify that multi-year queries open two stores and concatenate correctly.
- Verify that unit conversions (K → °C) are applied identically to the
  NetCDF path.

#### H.4 End-to-end smoke test

Add `test_zarr_full_round_trip` to `tests/test_weather_e2e.py`:

1. Create a minimal mock CDS response (a tiny NetCDF for a 2° × 2° German bbox,
   3 days of hourly data).
2. Run `WeatherPipeline.update_data()`.
3. Assert that a `.zarr` store exists on disk.
4. Run `WeatherPipeline.get_weather_data()` and assert a numeric result.
5. Run `WeatherPipeline.update_data()` a second time for an adjacent bbox.
6. Assert no new store was created (same store, second DB row).
7. Assert both tiles are queryable via `get_weather_data()`.

---

## 3 · Backward compatibility

| Item | Behaviour |
|---|---|
| Existing `.nc` files | Read by `GetterWeather` as before.  No forced migration. |
| `file_format="netcdf"` rows | Routed to the `nc_path: str` overload of `interpolate_netcdf`. |
| `file_format="zarr"` rows | Routed to `ZarrStoreManager.open_store()` + Dataset overload of `interpolate_netcdf`. |
| `WeatherPipeline` config | No new required key.  No `storage_backend` flag — routing is determined solely by the `file_format` DB column.  A project with a mix of `.nc` and `.zarr` rows works correctly during the migration period. |

---

## 4 · Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| ERA5 coordinate values differ by floating-point rounding across CDS requests | Low | Alignment check with `round(1)` before `searchsorted`; tolerance of 0.05° raises `ValueError` with diagnostic message. |
| Zarr region write leaves partial data on crash | Medium | Chunk-level region writes are not file-atomic; the lock file prevents concurrent corruption.  A partially written chunk is detectable as a NaN region — `rebuild_from_store` can identify and flag it. |
| Concurrent writes from two pipeline instances | Low | `fasteners.InterProcessLock` (direct dep) on `<store_path>.lock`. |
| HYRAS grid does not align with ERA5 at 0.1° | N/A | HYRAS uses a separate store with its own `zarr_grid`; no cross-source writes. |
| zarr v3 API incompatibility | Low | Pin `zarr>=3.0,<4`.  Zarr v3 format is stable; stores are forward-compatible within the v3 series. |
| DB/store divergence after DB loss | Medium | `rebuild_from_store` (Phase E, required) auto-detects and repairs on next `missing_spatiotemporal()` call. |

---

## 5 · Dependency summary

| Package | Version constraint | Why |
|---|---|---|
| `zarr` | `>=3.0,<4` | Store creation and I/O (v3 stable API) |
| `numcodecs` | `>=0.12,<1` | Blosc2/Zstd compression codec |
| `fasteners` | `>=0.19` | Store write locking (direct dep; not bundled by zarr v3) |
| `xarray` | `>=2024.0,<2027` (already present) | `open_zarr`, `to_zarr`, `open_mfdataset` |
| `numpy` | `>=2.0.0,<3` (already present) | Coordinate alignment arithmetic |

---

## 6 · Implementation order and effort

| Phase | Owner | Effort | Blocker |
|---|---|---|---|
| A — Dependencies + grid constants | — | 0.5 d | — |
| B — `ZarrStoreManager` | — | 2–3 d | A |
| C — `SaverWeather` refactor | — | 1–2 d | B |
| D — `GetterWeather` + `interpolate_netcdf` overload | — | 2–3 d | B |
| E — `CoverageManager` + `rebuild_from_store` (required) | — | 1 d | C |
| F — DB schema migration | — | 0.5 d | — |
| G — CLI migration command | — | 0.5 d | B, C |
| H — Tests | — | 2–3 d | B, C, D |
| **Total** | | **~10–14 d** | |

Phases A, F, and E can be worked on in parallel with B.
Phases C and D depend on B completing first but can be developed in parallel
with each other.
Phase G and H can start as soon as B is stable.

---

## 7 · Open questions

*All five questions have now been researched and resolved.*

---

**Q1 — Is `xr.Dataset.to_zarr(store, region={...})` safe for partial writes?**

✅ **Resolved — Yes, with the correct two-step workflow.**

The xarray documentation confirms the region-write pattern explicitly
([xarray I/O docs — Distributed writes](https://docs.xarray.dev/en/stable/user-guide/io.html#distributed-writes)):

1. Create the store skeleton first using `compute=False` (writes metadata and
   coordinate arrays, but no data values):
   ```python
   ds_template.to_zarr(store_path, compute=False, consolidated=False)
   ```
2. Fill in regions with subsequent `to_zarr(region=...)` calls:
   ```python
   ds_slice.to_zarr(store_path, region="auto", consolidated=False)
   # or explicitly:
   ds_slice.to_zarr(store_path, region={"time": slice(t0, t1)}, consolidated=False)
   ```

**Safety guarantee:** "Concurrent writes with `region` are safe as long as
they modify distinct chunks in the underlying Zarr arrays (or use an
appropriate `lock`)." Sequential writes to non-overlapping time slices (one
CDS download at a time) are fully safe without a lock.

**Important caveat:** xarray does not check coordinate alignment during
region writes. It is the caller's responsibility to verify that the slice
indices in the download Dataset map correctly onto the store's fixed coordinate
arrays before issuing the write. The `ZarrStoreManager.write_dataset()` method
(Phase B) must perform this alignment check using `searchsorted` before
calling `to_zarr(region=...)`.

**Coordinate arrays:** Coordinate variables (latitude, longitude, time) cannot
be written via `region`; they must be written separately with `mode='a'` or
included in the initial `compute=False` skeleton. The skeleton approach
(writing coordinates at store creation time) is the correct pattern.

---

**Q2 — Does ERA5-Land always deliver coordinates on exact 0.1° multiples?**

✅ **Resolved — Yes, confirmed by ECMWF official documentation.**

From [ERA5-Land: data documentation (ECMWF)](https://confluence.ecmwf.int/display/CKB/ERA5-Land%3A+data+documentation):

> "Currently, the data can only be downloaded on a regular latitude/longitude
> grid of 0.1°×0.1° via the CDS catalogue."

The native resolution is 9 km (TCo1279 reduced Gaussian), but the CDS
**always** interpolates to 0.1°×0.1° before delivery. No other resolution is
available via CDS. ECMWF member states with MARS access can retrieve the
native grid, but this project uses CDS exclusively.

**Implication:** The `round(1)` + `searchsorted` coordinate alignment
approach in `ZarrStoreManager.write_dataset()` is safe. A tolerance of
0.05° is more than sufficient to absorb any floating-point representation
noise in the delivered coordinate values.

---

**Q3 — What is the correct Zarr v3 API for Blosc compression?**

✅ **Resolved — `zarr.codecs.BloscCodec`; the xarray encoding key is
`"compressors"` (plural list), not `"compressor"` (singular v2 style).**

From the [zarr v3 codecs API reference](https://zarr.readthedocs.io/en/stable/api/zarr/codecs/)
and confirmed by the [xarray Zarr compressors example](https://docs.xarray.dev/en/stable/user-guide/io.html#zarr-compressors-and-filters):

```python
from zarr.codecs import BloscCodec

compressor = BloscCodec(cname="zstd", clevel=3, shuffle="shuffle")

ds.to_zarr(
    store_path,
    consolidated=False,
    encoding={
        "temperature": {
            "compressors": [compressor],   # ← plural list, zarr v3 style
            "chunks": (720, 5, 5),
        }
    },
)
```

**Key differences from zarr v2:**
- v2 used `encoding={"var": {"compressor": numcodecs.Blosc(...)}}` (singular, `numcodecs` object)
- v3 uses `encoding={"var": {"compressors": [zarr.codecs.BloscCodec(...)]}}` (plural list, zarr object)
- `numcodecs.Blosc` objects are **not** directly usable in v3 encoding dicts
- Import path: `from zarr.codecs import BloscCodec` (part of zarr package itself)

**BloscCodec constructor defaults (zarr v3):**
```
BloscCodec(*, typesize=None, cname='zstd', clevel=5, shuffle=None, blocksize=0)
```
Our target settings: `cname="zstd", clevel=3, shuffle="shuffle"`.

**Zarr v3 fill_value decoupling (additional finding):**
In zarr v3, `fill_value` (used for unwritten chunks) and `_FillValue` (xarray's
CF masking sentinel) are decoupled. Both must be set explicitly:
```python
encoding={
    "temperature": {
        "fill_value": float("nan"),    # zarr store default for unwritten chunks
        "_FillValue": float("nan"),    # CF convention masking value
        "compressors": [compressor],
        "chunks": (720, 5, 5),
    }
}
```

**`numcodecs` direct dependency:** zarr v3 still calls `numcodecs.blosc`
internally inside `BloscCodec` (it sets `numcodecs.blosc.use_threads = False`
at import time for async safety). Keep `numcodecs>=0.12,<1` as a direct
dependency in `packages/weather/pyproject.toml` for explicit version pinning.

---

**Q4 — Does the `interpolate_netcdf` + lazy zarr Dataset path avoid loading
the full store into memory?**

✅ **Resolved — Yes, zarr-backed xarray is lazy by default; only touched
chunks are loaded.**

From the xarray documentation and zarr architecture:

- `xr.open_zarr()` returns all data variables as `dask.array` objects —
  fully lazy by default. Example from docs:
  ```
  foo (x, y) float64 dask.array<chunksize=(4, 5), meta=np.ndarray>
  ```
- A `.sel()` or `.isel()` call produces a new lazy array; no data is fetched
  from disk until `.compute()` or `.values` is called.
- Only the chunk files that intersect the selected slice are read from the
  store. A point query on a 10-year store fetches at most
  `ceil(n_points / chunk_lat) × ceil(n_points / chunk_lon)` lat/lon chunks
  times the time chunks covering the requested period.
- Unwritten chunks (all-NaN regions) are **not stored on disk**; zarr returns
  the `fill_value` (NaN) for them without any disk I/O.

**Implication for Phase D:** The new `interpolate_netcdf` Dataset overload
should call `xr.open_zarr()` and pass the lazy Dataset directly. The
`method="nearest"` temporal selection and spatial interpolation both operate
lazily; `.compute()` is triggered only when the final interpolated scalar
values are extracted. This is safe for multi-year stores spanning multiple
`.zarr/` directories when combined with `xr.open_mfdataset(..., engine="zarr")`
or manual `xr.concat()` before passing to the overload.

---

**Q5 — Should the Zarr store cover a fixed Germany grid, or the CDS
download bbox?**

✅ **Resolved — Fixed Germany grid is correct; sparse stores do not waste disk
space.**

The concern was: "a small test download of just Neuss creates a nearly-empty
store with ~92 × 82 × 8760 NaN chunks occupying disk."

**This concern is unfounded.** Zarr (v2 and v3) only writes chunk files for
chunks that have been explicitly written. A chunk that has never been written
returns `fill_value` without any corresponding file on disk. A download
covering only Neuss (≈ 4 × 4 grid cells) creates only the chunk files
intersecting those cells; the rest of the Germany grid simply does not exist
on disk until data is downloaded for those cells.

**Confirmed:** "Zarr arrays have a `fill_value` that is used for chunks that
were never written to disk." (xarray docs, Fill Values section)

This makes the fixed Germany grid approach strictly superior to the dynamic
bbox approach for this use case: the store has a well-defined, stable
coordinate space, deduplication of overlapping downloads is trivial (just
write to the same fixed address), and disk usage is proportional to data
actually downloaded — not to the store's spatial extent.

---

## 8 · Note on VirtualiZarr / Kerchunk

VirtualiZarr (formerly Kerchunk) provides a complementary capability: it
builds a virtual Zarr-compatible view over a collection of existing `.nc`
files *without* rewriting any data.  It is a good fit for read-heavy scenarios
where the `.nc` files already exist and must not be modified.

In the context of this project it is **not adopted as the primary strategy**
because:

1. It requires the `.nc` collision bug (Bug C) to be fixed first to guarantee
   unique source files.
2. The manifest must be regenerated after every `save()` call and becomes a
   critical single point of failure.
3. The Zarr native write model (Phase B–C above) is simpler and eliminates the
   manifest staleness risk entirely.

VirtualiZarr remains a valid **read-layer complement** if future requirements
demand serving existing archival `.nc` files via a unified Zarr interface
without migration.  Evaluate when such a requirement arises.

> This section was formerly Section 7.  Renumbered to Section 8 after the
> open questions section was added.
