# Integration paths: triage

Decide which path applies, then build the task list: the guide's
**Checklist** (`docs/development/adding-a-pipeline.md`) plus the path-specific
items below. Mark every item **needed** or **N/A — <reason>**.

## Decision tree

```text
Does the source already exist in datavia, and only a variable/property is new?
├─ weather source in SOURCE_REGISTRY ............................ → B
├─ soil provider (SoilGrids / HiHydroSoil) ...................... → D
└─ no
   Is it one GeoTIFF at one URL or WCS GetCoverage request, static, no auth?
   ├─ yes .......................................................... → A
   └─ no
      Is it a gridded time series (weather / climate) queried by
      variable + datetime, storable as a per-variable Zarr store?
      ├─ yes ....................................................... → C
      └─ no
         Is it a static soil raster delivered per property/depth?
         ├─ yes .................................................... → D
         └─ no (new domain, vector/point data, own query API) ...... → E
```

If two paths fit, prefer the one that touches less code, and say why. Ask the
user if the choice changes what they can query (e.g. A gives one layer and no
named variables; E can expose named properties).

---

## A. Config only (`ElevationPipeline` with another URL)

Guide §1. No new package; the user (or an example) builds
`ElevationPipeline(config={"source": "<name>", "url": "<GetCoverage URL>"})`.

- [ ] Verify the URL returns `image/tiff` (`TiffDownloader` rejects other
      content types) and covers the needed extent.
- [ ] Optional: an example script under `docs/user_guide/examples/` (add it to
      the `check-examples` pixi task if added).
- [ ] A component or gated e2e test only if new code exists (usually none).
- [ ] Docs: `variables.rst` (*Pipelines* table row, a short *Variables*
      subsection or note: unit, CRS, resolution, `nan` outside), `docs/index.rst`
      *Current Data Sources* + **Data Licensing & Attribution**, changelog.
- Guide checklist items for packaging, release workflow, CLI, DB tables: N/A.

## B. New variable for an existing weather source

All in `packages/weather/datavia/weather/source_registry.py` unless the
downloader has to request the variable explicitly.

- [ ] `SOURCE_REGISTRY[<source>]["nc_variable_map"]`: native NetCDF name →
      pipeline variable name. Reuse an existing pipeline name if the quantity
      already exists for another source (same name = same meaning and unit).
- [ ] `"conversions"` if the raw unit differs from the target unit; add a
      converter to `datavia/library/unit_conversions.py` and the
      `_FROM_TO_CONVERSION_MAP` only if the `(from, to)` pair is new.
- [ ] Time semantics: `accumulated_variables`, `day_start_hour`,
      `interval_end_variables`, `daily_aggregation` (see
      `datavia/library/temporal.py` and the registry docstrings).
- [ ] Station-backed sources (`DWD_stations`): `native_units`,
      `daily_aggregation`, and the station product / column mapping in
      `dwd_cdc.py` / `dwd_kriging_downloader.py`.
- [ ] The downloader: does it request variables by name (ERA5 CDS request,
      HYRAS URL per variable)? Add the mapping there.
- [ ] Tests: `tests/test_weather_source_registry.py` (valid variables,
      conversion), the source's downloader test, `tests/test_unit_conversions.py`
      for a new converter.
- [ ] Docs: `variables.rst` weather table (new row, ✓/– per source, unit,
      daily value, native name), the `get_valid_variables` doctest outputs, the
      notes under the table if conversions or time windows are special;
      `WeatherPipeline` docstring table in `packages/weather/datavia/weather/pipeline.py`;
      changelog.
- Guide checklist items for packaging, release, CLI, DB tables: N/A.

## C. New weather source (gridded time series)

New downloader in `packages/weather/datavia/weather/`, stored and queried by
the existing `SaverWeather` / `GetterWeather` / Zarr store.

- [ ] Downloader module `<source>_downloader.py` (copy the structure of
      `hyras_downloader.py` for file-server sources or `era5_downloader.py` for
      request-based APIs). It returns a temp NetCDF path or `"failed"`.
- [ ] `SOURCE_REGISTRY["<Source>"]` entry: `grid_downloader`, `conversions`,
      `nc_variable_map`, `coverage_bbox` (if the provider cannot subset),
      `zarr_grid` (crs, time_freq, dtype, fill_value, chunks, codec), time
      semantics keys. Use the `HYRAS` entry as the template.
- [ ] Find every hard-coded source branch and decide whether the new source
      needs one:
      `grep -rn '"HYRAS"\|"ERA5_land"\|"DWD_stations"' datavia packages tests`.
      Typical places: `pipeline.py` (config validation, bbox handling),
      `composite_downloader.py`, `coverage_manager.py`, `zarr_store_manager.py`,
      `datavia/library/{temporal,formats,interpolation}.py`, `datavia/cli*.py`.
- [ ] Optional dependencies (credentials, heavy clients) as an extra in
      `packages/weather/pyproject.toml`, imported lazily (see `era5`).
- [ ] Tests: downloader unit tests with a fake server (pattern:
      `tests/test_hyras_downloader.py`, `tests/cdc_fake.py`), registry tests,
      a `WeatherPipeline` unit test with the new source, a gated e2e test in
      `tests/test_weather_e2e.py` or its own file.
- [ ] CLI: `datavia update weather --source <Source>` usually works already;
      check `datavia/cli.py` / `cli_config.py` for source lists or examples.
- [ ] Docs: `variables.rst` *Pipelines* table row, weather variable table
      (new column for the source, ✓/– per variable), a `get_valid_variables`
      doctest line, *How each pipeline takes a request* notes if behaviour
      differs (e.g. hourly support); `WeatherPipeline` docstring source table;
      `docs/index.rst` sources + licensing; `docs/api/index.rst` automodule;
      `README.md`; changelog.

## D. New soil property or soil provider

- [ ] Property of an existing provider: add the API name (and canonical
      alias, if any) to `SoilPipeline._KNOWN_PROPERTIES` and the alias maps in
      `packages/soil/datavia/soil/pipeline.py`; for HiHydroSoil also
      `HiHydroSoilDownloader._CANONICAL_TO_PREFIX`.
- [ ] Decide whether it belongs in the default `properties` list (changes
      the default download volume; ask the user if unsure).
- [ ] New provider: a new downloader module next to `soilgrids_downloader.py`
      / `hihydrosoil_downloader.py`, routed in `composite_downloader.py`
      (`CompositeDownloader`), one GeoTIFF per coverage ID
      `<property>_<depth>_<statistic>`, stored with `TiffSaver`, read with
      `SoilGetterTiff`.
- [ ] Depth and statistic semantics: map the provider's depths onto the
      existing depth labels or document why not.
- [ ] Tests: `tests/test_soil_pipeline_unit.py` (validation, routing,
      skip-when-stored), the downloader test, `tests/test_soil_e2e.py` (gated).
- [ ] Docs: `variables.rst` soil table (property, source, description, raw
      unit, divide-by, unit, default) and the `SoilPipeline().properties`
      doctest output; `docs/index.rst` licensing for a new provider; changelog.

## E. New pipeline package

Follow the guide end to end (§2–§10); every checklist item is likely needed.
Additional decisions to record:

- [ ] Storage: GeoTIFF components (guide §4/§5) or custom `Saver` / `Getter`
      (guide §6)? Custom storage needs a table in
      `datavia/library/database/schema.py` only if no existing table fits.
- [ ] `get_data` return shape: `(N,)` array or `dict[str, ndarray]`; which
      keyword arguments select variables.
- [ ] Source name: unique, a valid Python identifier, not used by another
      pipeline.
- [ ] CLI wiring (guide §8): needed if users run updates from the command
      line.
