---
name: research-data-source
description: Research a data source for datavia without changing the repo — find and compare candidate providers when the source is unclear, read the API documentation, and probe the provider's API live with a temporary Python script. Produces a research brief (verified sample request, format, CRS, units, time stamps, licence, recommended integration path). Use when asked to find, evaluate or research a dataset, provider or API, or when add-data-source needs API facts.
---

# Research a data source

Output: a **research brief** (`references/research-brief-template.md`) that
the `add-data-source` skill uses in its step 2. This skill **never edits the
datavia repository**; all scripts and downloads go to a temporary directory
outside it.

Because it's read-only, the plan gate of `skills/develop/SKILL.md` doesn't
apply here. As soon as the work moves on to changing repository files, it
does: the brief feeds the plan, and no code is written before the plan is
approved.

## Step 1: Pin down the need

Write down, and ask the user about anything you can't infer:

- quantity / variable and the unit the user expects;
- region (default: Germany, as for the existing pipelines), spatial
  resolution;
- static, or time series: period, temporal resolution (hourly / daily),
  latency (historical vs near-real-time);
- constraints: licence (open data only?), credentials acceptable?
- whether a named provider is given, or the source is open.

**Exit:** the need is a short, concrete spec.

## Step 2: Find and compare candidates

Skip to step 3 if the user named the provider and the dataset is unambiguous.

1. Check what datavia already offers. A new variable for an existing source
   is the cheapest path: `docs/user_guide/variables.rst`,
   `packages/weather/datavia/weather/source_registry.py`,
   `packages/soil/datavia/soil/pipeline.py`.
2. Search (WebSearch / WebFetch) for official, authoritative providers
   first: national agencies (DWD, BKG, UBA, state survey offices), European
   services (Copernicus CDS / CLMS / EEA), established scientific providers
   (ISRIC, ...). Read the provider's own docs, not third-party summaries.
3. Compare the candidates in a table:

   | | Candidate 1 | Candidate 2 |
   |---|---|---|
   | Variable(s), unit | | |
   | Coverage, resolution, CRS | | |
   | Period, time step, update cadence | | |
   | Format | | |
   | Access (WCS / OGC API / REST / file server / CDS / STAC) | | |
   | Subsetting (bbox / time) supported? | | |
   | Auth, rate limits, terms | | |
   | Licence, attribution | | |
   | Python client needed? | | |

4. Recommend one with the reason. If two are close, or the choice trades
   off something the user cares about (resolution vs credentials, coverage vs
   licence), ask the user before probing.

**Exit:** one source chosen, alternatives recorded.

## Step 3: Probe the API with a temporary script

Never rely on the documentation alone; make a real request.

1. **Set up a throwaway workspace outside the repo**, with separate
   folders for scripts and downloaded data (downloads are untrusted):

   ```bash
   PROBE_DIR=$(mktemp -d)            # or a session scratchpad directory
   mkdir -p "$PROBE_DIR/scripts" "$PROBE_DIR/downloads"
   cp skills/research-data-source/references/api-probe-template.py "$PROBE_DIR/scripts/probe.py"
   ```

2. **Edit `probe.py`**: fill in the endpoint and the smallest meaningful
   request (a few km bbox, a single time step, one variable). Prefer
   discovery calls first (`GetCapabilities` / `DescribeCoverage`, OpenAPI
   spec, STAC collection, directory listing), then one data request.

3. **Run it from the datavia root with the dev environment**, isolated
   (`-I`) so files in the download folder can't shadow imports:

   ```bash
   pixi run -e dev python -I "$PROBE_DIR/scripts/probe.py" "$PROBE_DIR/downloads"
   ```

   The dev environment has `requests`, `rasterio`, `xarray`, `netCDF4`,
   `pandas`. If the provider needs a client that isn't installed, note it
   in the brief as a dependency; don't add it to the repo.

4. **Iterate** until one data request succeeds, and record what the
   script prints:
   - exact URL and parameters, HTTP status, content type, payload size;
   - format; for rasters: CRS, transform, resolution, **pixel registration**
     (does the first coordinate mark the cell centre or the corner? check
     against the provider's stated grid), shape, dtype, **nodata / fill
     values** (e.g. `-9999`, `-32768`, `_FillValue`, `missing_value`) and
     whether they are declared in the metadata;
   - for NetCDF / Zarr: dimensions and their order, variable names,
     `units`, `scale_factor` / `add_offset`, `long_name`;
   - time: coordinate values, time zone (UTC?), whether a stamp marks the
     **start or end** of the interval, accumulation windows (e.g.
     precipitation 06–06 UTC), calendar;
   - a sanity value at a known place (e.g. Berlin 13.405 E, 52.520 N) and
     whether it's plausible in the stated unit.

5. **Rules while probing**:
   - keep requests tiny and few; respect rate limits and terms of use;
     identify with a generic `User-Agent` (no personal data);
   - read credentials only from their standard location (e.g.
     `~/.cdsapirc`) or environment variables; never write them into the
     script, never print them, never put them in the brief;
   - don't run code or binaries that came with downloaded data;
   - stop and ask the user before anything that costs money, needs an
     account the user hasn't set up, or downloads more than a few MB.

6. **Clean up**: `rm -rf "$PROBE_DIR"` once the brief is written, unless
   the user wants to keep the script. If you keep it, give its full path.

**Exit:** at least one data request verified end to end, with the facts
above recorded.

## Step 4: Write the research brief

Fill in `references/research-brief-template.md`, including:

- the **recommended integration path (A–E)** from
  `skills/add-data-source/references/integration-paths.md`, with a reason;
- the licence and **verbatim** attribution or citation text from the
  provider's page (with its URL);
- every gotcha found while probing, and open questions.

Show the brief to the user. When this skill runs as step 2 of
`add-data-source`, continue there with the brief as input. Mark anything you
couldn't verify (e.g. no credentials available) as **unverified**.
