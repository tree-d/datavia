# Zarr Store — Design Review

> **Context (2026-05-18):** Condensed record of the review discussion that shaped
> the revised [zarr_store_implementation_plan.md](zarr_store_implementation_plan.md).
> Points marked **→ accepted** were incorporated into the plan;
> **→ rejected** were consciously left out with rationale.

---

**Dev:** Phase D says *"pass the Dataset directly to `interpolate_netcdf()` — no
signature change needed."*  I just checked: `interpolate_netcdf` takes
`nc_path: str`.  It opens the file itself with `xr.open_dataset`.  It does not
accept a Dataset.  If we pass a Zarr-backed Dataset the whole function breaks.

**DS:** Right, so we need to refactor `interpolate_netcdf` to accept either a path
or an already-open Dataset.  That's actually an improvement — currently the
function opens and closes the file on every call, which is expensive for point
queries across large time windows.

**Dev:** That is a significant scope expansion.  The plan says Phase D is "1–2 days";
with `interpolate_netcdf` refactored it's 2–3 days and it touches a shared library
function used by both the NetCDF and the Zarr code paths.

**DS:** Agreed, but there's no way around it.  We can't have `GetterWeather` write the
Zarr Dataset to a temp `.nc` file just to pass a path — that defeats the entire point.

**Dev:** → **accepted**.  `interpolate_netcdf` must gain an overload that accepts
`xr.Dataset`.  Keep the `nc_path: str` signature working for backward compatibility
(existing `.nc` rows in the DB).

---

**Dev:** Phase C says `delete_registration()` removes the store from disk
*"after user confirmation via a dry_run flag"*.  A `dry_run` flag is a CLI/UI
concern.  A saver method should not be responsible for user confirmation — that
belongs in the CLI command or the pipeline's public API.

**DS:** Agreed.  The saver should expose `delete_store(variable, year)` with a plain
boolean `confirmed: bool` parameter.  The CLI passes `confirmed=True`; tests pass it
directly.  No interactive prompts inside library code.

**Dev:** → **accepted**.

---

**DS:** The plan pins `zarr>=2.18,<3`.  Zarr v3 has been stable since early 2025 and
xarray now recommends it.  Zarr v2 uses a different internal format (`zarr.storage`)
than v3 (`zarr.store`).  If we pin v2 now we'll need a store migration when we
eventually upgrade.

**Dev:** The project's `xarray>=2024.0` dependency is already compatible with Zarr v3.
Pinning v2 is defensive engineering for no concrete reason.

**DS:** → **accepted** — pin `zarr>=3.0,<4` (v3 stable).  The v3 API is what xarray's
`open_zarr` and `to_zarr` use by default in 2025+.  Update the dependency and all
store creation code accordingly.

---

**DS:** The DB stays as a download-avoidance cache, but it is a second source of truth.
If the DB row is missing (corrupt SQLite, manual delete, migration error), `CoverageManager`
re-downloads data that already exists in the Zarr store, wastes CDS quota, and then
writes duplicate values into the store chunks.  The store is the ground truth; the DB
should be derivable from it.  `rebuild_from_store` in Phase E is marked *optional* —
it should be **required**.

**Dev:** Making it required adds another 0.5 days and delays the initial release.
The DB loss scenario is rare.  `rebuild_from_store` is a maintenance tool, not a
daily-use path.

**DS:** The risk is not just DB loss.  The *first time* someone downloads data on a
fresh checkout with an existing Zarr store and an empty DB (e.g. after `git clean -fd`
accidentally removes the SQLite file), every month is re-downloaded.

**Dev:** → **accepted** — `rebuild_from_store` is promoted to a required part of
Phase E, not optional.  It runs automatically when `CoverageManager` detects a Zarr
store exists for a `(source_name, variable, year)` triple that has no DB rows.

---

**Dev:** The chunk layout `{"time": 24, "latitude": 41, "longitude": 46}` is
presented without rationale.  24 hourly steps × 41 lat × 46 lon = ~45 k cells per
chunk.  Is that optimised for point queries (we want large time chunks, small spatial
chunks) or spatial snapshots (we want small time chunks, large spatial chunks)?  The
main use case throughout the codebase is point queries: single lat/lon, time series.

**DS:** For point queries we want chunks that are long in the time dimension and small
in space — something like `{"time": 744, "latitude": 5, "longitude": 5}` gives a full
month of hourly data for a ~0.5° × 0.5° patch.  That means reading a time series at
one location loads ≈ 1 chunk per month instead of ≈ 30.

**Dev:** → **accepted** — chunk layout revised to `{"time": 720, "latitude": 5,
"longitude": 5}` as the default.  Add a note that spatial-snapshot use cases can
rechunk offline.

---

**DS:** No compression codec is specified.  ERA5 float32 data compresses 4–6× with
Blosc+Zstd at level 3.  For a full year of Germany at 0.1° that is ~600 MB raw →
~120 MB stored.  Not specifying a codec means zarr uses its default (no compression in
v3, zlib in v2).

**Dev:** Adding Blosc as a required codec means adding `numcodecs` as a direct
dependency.  It is already a transitive dependency of zarr, but we should not rely
on transitive deps for things we configure explicitly.

**DS:** → **accepted** — add `numcodecs>=0.12,<1` as a direct dependency; specify
`blosc2` with `zstd` level 3 in the `zarr_grid` codec entry.

---

**Dev:** Phase C mentions `_build_dest_stem()` is removed.  The `.nc` backward
compatibility path in `GetterWeather` still needs that function — it reads `.nc`
files registered in the DB as `file_format="netcdf"`.

**DS:** No — the backward compat path opens `.nc` files via their `uri` column
(already an absolute path), not via stem reconstruction.  `_build_dest_stem` is only
called at save time, not at read time.  It is safe to remove.

**Dev:** → **accepted**.

---

**DS:** The `storage_backend` config key (section 3, backward compat table) is a
permanent feature flag.  Feature flags accumulate.  In two years nobody will
remember what `"netcdf"` does.

**Dev:** I actually agree.  The `file_format` column in the DB already distinguishes
`"netcdf"` from `"zarr"`.  `GetterWeather` routes based on that.  The config key is
redundant.

**DS:** → **accepted** — `storage_backend` config key removed.  Routing is 100%
based on the `file_format` DB column.

---

**Dev:** Phase B mentions `fasteners.InterProcessLock` is *"a transitive dependency
via zarr"*.  In zarr v3 the locking model changed — `zarr.storage` no longer bundles
`fasteners`.  We cannot rely on it as a transitive dep.

**DS:** → **accepted** — `fasteners>=0.19` added as a direct dependency.

---

**DS:** One thing the plan does not address: what happens to `sync_files_and_database()`?
That method is defined in the base `Pipeline` class and reconciles on-disk files with
DB rows by comparing `list_managed_files()` against `get_registered_uris()`.  For
Zarr stores, a single directory is referenced by multiple DB rows (one per download
tile).  The current reconciliation logic deletes DB rows for URIs with no
corresponding file.  A missing `.zarr` directory would delete *all* rows for that
store — correct.  But a present directory whose DB rows have been manually deleted
would not be detected as orphaned data — it would just silently accumulate untracked
chunks.

**Dev:** `rebuild_from_store` (now required in Phase E) is the answer.  It repopulates
the DB from store contents.  `sync_files_and_database` stays unchanged.

**DS:** → **accepted**, with a note added to Phase E that `sync_files_and_database`
is safe for Zarr stores as long as each store directory exists as a whole unit.

---

---

# Implementation Review — Round 2

> **Context (2026-05-18):** Pre-implementation dialogue focused on `ZarrStoreManager`
> (Phase B).  Three reviewers: **Pragma** (pragmatist — ships fast, hates scope
> creep), **Ops** (reliability/ops engineer — obsessed with failure modes),
> **Test** (testing purist — if it can't run in CI, it doesn't exist).

---

**Pragma:** Let's define what Phase B actually ships.  I want the smallest
`ZarrStoreManager` that makes Phase C and D work.  Four public methods:
`create_store`, `write_dataset`, `open_multi_year`, `delete_store`.  Done.
No extras.

**Ops:** Before we talk about methods, talk about failure.  What happens if
`write_dataset` crashes after `to_zarr(region=...)` has written 3 of 8 time
chunks?  The store now contains partial data for a year.  The next call to
`write_dataset` with `region="auto"` happily overwrites the same chunks again —
but the last 5 chunks still have fill-value NaN masquerading as "no data yet"
rather than "write failed".

**Pragma:** The DB row is only inserted after `write_dataset` returns
successfully.  A partial write means no DB row.  The next download attempt
re-downloads the whole month and re-writes all chunks.  That is idempotent.

**Ops:** Only if the re-write covers exactly the same chunk boundaries.  ERA5
CDS downloads by calendar month.  A month starts at hour 0 of day 1 and ends
at hour 23 of the last day.  If `searchsorted` on the time axis is off by one
due to a DST edge case or a leap-second artefact, the re-write lands on
different chunk boundaries and you now have two overlapping partial writes with
a seam of doubled or missing hours in the middle.

**Test:** I can write a test for that — but only if `ZarrStoreManager` accepts
a `zarr.storage.LocalStore` (or any zarr store object) instead of constructing
its own path from config.  If the path is built internally from
`settings.data_dir / source_name / variable / year`, I cannot inject a temp
directory in tests.

**Pragma:** That is a constructor argument, not an API change.  `ZarrStoreManager`
takes an optional `store_root: Path | None = None`; defaults to the config path;
tests pass `tmp_path`.

**Test:** → **accepted as minimum**.  I also need a way to create a synthetic
ERA5-shaped Dataset without downloading anything.  That means the Germany grid
definition (lat axis, lon axis, hourly time axis) must be importable from
`source_registry.py` as a plain data structure — not locked inside
`create_store`.

**Pragma:** It already is.  The `zarr_grid` key in each source entry is a dict
with `lat`, `lon`, `time_freq`, `dtype`, `fill_value`, `chunks`, `codec`.
`create_store` reads it.  Your test just builds a Dataset from the same dict.

**Test:** Good.  Then the test matrix I need is: (1) clean write to empty store,
(2) idempotent re-write of same region, (3) partial-write-then-complete (i.e.
simulate a crash), (4) write to wrong year store raises `ValueError`.  All four
run in CI with no CDS credentials.

---

**Ops:** Back to the crash scenario.  The plan says `to_zarr(compute=False)`
creates the skeleton on `create_store`.  `write_dataset` then calls
`to_zarr(region="auto")`.  There is no transactional guarantee.  If the
process is killed between writing chunk 3 and chunk 4, the store is in an
indeterminate state.

**Pragma:** Write a `.write_in_progress` sentinel file at the start of
`write_dataset`, remove it at the end.  `open_multi_year` refuses to open any
store that has that sentinel.  That is three lines of code and covers
the entire failure surface.

**Ops:** A sentinel file does not tell you *which* time region is corrupt.
`rebuild_from_store` would need to scan chunk metadata to determine actual
coverage — not just list the `.zarr` directory.

**Test:** That scan is Phase E scope.  The sentinel is Phase B scope.
Pragma is right — accept the sentinel, document that `rebuild_from_store`
uses it to flag stores that need manual inspection.

**Ops:** → **accepted** — `.write_in_progress` sentinel in `write_dataset`.
`create_store` also writes it during skeleton creation (a skeleton with no
data is as dangerous as a partial write if something reads it).  Remove
sentinel only after `write_dataset` returns without exception.

---

**Pragma:** `open_multi_year` — the plan says "opens and concatenates multiple
`.zarr/` directories."  Concat on which dimension?  `xr.open_mfdataset` with
`engine="zarr"` can do this lazily.  Or we open each year separately and call
`xr.concat`.

**Test:** `xr.open_mfdataset` with `engine="zarr"` is the right call — it
returns a single lazy Dataset without loading anything.  But it needs a
`preprocess` function if the coordinate arrays don't align perfectly across
years (e.g. a leap year has a different time axis length than a non-leap year).

**Ops:** The time coordinates *will* differ between years — 8760 hours for a
common year, 8784 for a leap year.  `open_mfdataset` concatenates on `time` by
default if `combine="by_coords"`.  As long as each store's time axis has no
gaps and no overlaps with adjacent years, the concat is safe.

**Pragma:** That is enforced at write time.  `create_store` builds the time
axis from `pd.date_range(f"{year}-01-01", periods=hours_in_year, freq="1h")`.
If a download partially covers a month, `region="auto"` only touches those
hours.  The time axis is always fully specified in the skeleton; only values
may be NaN.

**Test:** → **accepted** — `open_multi_year` uses `xr.open_mfdataset(paths,
engine="zarr", combine="by_coords", chunks={})`.  Tests must cover leap-year
boundary (year 2024 → 2025 concat).

---

**Ops:** `delete_store(variable, year, confirmed: bool)` — the plan says this
removes the store directory.  Does it also deregister the DB rows?  Or does
the caller do that?

**Pragma:** `delete_store` removes the directory only.  The DB is managed by
`CoverageManager`, not `ZarrStoreManager`.  Separation of concerns.  The
caller (CLI or `WeatherSaver`) removes the DB rows before or after calling
`delete_store`.

**Ops:** Before, please.  If you remove the directory first and then the DB
deregistration fails, you have orphaned DB rows pointing at a missing store.
`sync_files_and_database` will then delete those rows on next run — so it
self-heals, but there is a window of inconsistency.  Remove DB rows first,
then directory.

**Test:** This ordering must be a test case.  Simulate DB deregistration
succeeding but `shutil.rmtree` failing — the DB rows should be re-creatable
via `rebuild_from_store` because the directory still exists.  Simulate
`shutil.rmtree` succeeding — now the DB rows are gone and the directory is
gone, which is the correct terminal state.

**Pragma:** → **accepted** — `delete_store` is a pure filesystem operation.
The caller removes DB rows before calling it.  Documented in the docstring.

---

**Test:** One more thing.  The `BloscCodec` import path is
`from zarr.codecs import BloscCodec`.  If `zarr` is not installed (e.g. a
developer running just the core `datavia` package without the `weather`
extras), that import fails at module load time, not at call time.  If
`zarr_store_manager.py` is imported anywhere in the core package, it breaks
the whole import chain for users who don't need weather data.

**Pragma:** `zarr_store_manager.py` lives in `packages/weather/datavia/weather/`.
It is never imported by the core `datavia` package.  The weather package
declares `zarr` as a hard dependency.  If you install the weather extras,
zarr is present.  If you don't, you never touch this file.

**Test:** Correct.  But `datavia/library/interpolation.py` lives in core and
will gain a `Dataset` overload in Phase D.  That overload must not import
xarray at module level if xarray is optional in core.

**Pragma:** xarray is already a core dependency (`xarray>=2024.0` is in the
top-level `pyproject.toml`).  The concern is moot.

**Test:** → **retracted**, confirmed xarray is a core dep.

---

## Conclusions and decisions for Phase B

| Decision | Resolution |
|---|---|
| `ZarrStoreManager` injectable `store_root` | `store_root: Path \| None = None`, defaults to config |
| Sentinel file for partial writes | `.write_in_progress` written at start of `create_store` and `write_dataset`, removed on clean exit |
| `open_multi_year` implementation | `xr.open_mfdataset(paths, engine="zarr", combine="by_coords", chunks={})` |
| `delete_store` scope | Filesystem only; caller removes DB rows first |
| CI test matrix (no CDS) | (1) clean write, (2) idempotent re-write, (3) crash recovery sentinel, (4) wrong-year `ValueError`, (5) leap-year concat |
| `zarr_grid` as importable data | Already in `source_registry.py`; tests build synthetic Datasets from it |
| DB row removal ordering | Remove DB rows before calling `delete_store` |

## Open questions raised in this review

**OQ-1 — DST / leap-second alignment:** ⚠️ *Monitor in practice.*
`pd.date_range(f"{year}-01-01", periods=hours, freq="1h")` produces a
UTC-anchored sequence; ERA5 coordinates are also UTC throughout, so no
DST offset can enter the time axis from the CDS side.  The theoretical
risk is a floating-point or rounding artefact in how `xarray` encodes
the `time` coordinate when reading from NetCDF (which uses CF `units =
"hours since 1900-01-01"` integers) vs. the Zarr skeleton (which stores
`numpy.datetime64[ns]` directly).  Implement `create_store` and
`write_dataset` first; run one real download covering October 31 (the
EU summer→winter DST boundary) and inspect the `searchsorted` alignment
result before trusting the seam.  Add a CI assertion that the last
timestamp of a common-year store is exactly `{year}-12-31T23:00`.

**OQ-2 — `write_in_progress` on crash vs. clean concurrent access:** ⚠️ *Accept for now; note for future.*
For the current single-process, sequential-download use case the sentinel
is safe and correct.  If the project later moves to parallel download
workers (e.g. multiple `WeatherSaver` instances via Dask distributed or
a task queue), the per-store sentinel becomes a bottleneck: a long write
for one year would block concurrent reads of a *different* year's store
if both happen to be in the same `open_multi_year` call.  At that point
the sentinel should be replaced with per-chunk write tracking or a proper
`fasteners.ReaderWriterLock`.  Document this limitation clearly in the
`ZarrStoreManager` class docstring so the constraint is visible when
parallelism is introduced.

**OQ-3 — `rebuild_from_store` chunk scan strategy:** ✅ *Option (b) — infer coverage from written chunks.*
When `rebuild_from_store` encounters a store that carries a
`.write_in_progress` sentinel, it should not raise or delete.  Instead
it reads the zarr array metadata to determine which time chunks have been
written (i.e. have corresponding chunk files on disk) and reconstructs
DB rows only for calendar months where all expected chunks are present
and non-empty.  Months with missing or partial chunks are logged as
warnings but not registered — they remain available for re-download.
The sentinel is removed after the scan completes, regardless of how many
months were successfully registered.  This preserves all already-good
data, costs one filesystem scan, and leaves the store in a consistent
state without wasting CDS quota.
