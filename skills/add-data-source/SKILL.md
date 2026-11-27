---
name: add-data-source
description: Integrate a new data source into datavia end to end — a new provider, dataset, pipeline package, weather source, soil property or weather variable. Use when asked to "add", "integrate", "support" or "wire up" a data source or variable. Runs triage and API research, plans with the user, implements from a skeleton, reviews against the coding standards and pipeline contract, updates the docs (incl. docs/user_guide/variables.rst) and iterates the quality gates until clean, following the develop skill.
---

# Add a data source to datavia

This skill is a specialisation of **`skills/develop/SKILL.md`**. Read that
skill first; all its rules apply throughout: plan gate, standards before
code, asking about violations and pre-existing errors, scope guard with
rollback, review, gates. The steps below say where each develop step fits.

Work from the datavia repository root, do the steps **in order**, and meet
each **exit condition** before moving on. Steps 1 and 2 are read-only and
need no plan; from step 4 on, nothing is written without the plan approved
in step 3.

Background, read at the start:

- `docs/development/adding-a-pipeline.md` (the "guide"): component contract,
  worked examples, packaging, CLI, tests, docs, checklist;
- `datavia/core/interfaces.py`: abstract `Downloader`, `Saver`, `Getter`,
  `Pipeline`, with the full contract in their docstrings.

If the request is ambiguous about **what** to add (which variable, region or
provider), ask the user, or start with the `research-data-source` skill.

---

## Step 1: Triage: which integration steps are needed

Read `references/integration-paths.md` and classify the request:

| Path | When |
|------|------|
| **A** Config only | One GeoTIFF at one URL / WCS request, no time axis |
| **B** New weather variable | The source already exists in `SOURCE_REGISTRY`, only a variable is new |
| **C** New weather source | Gridded time series that fits `WeatherPipeline` (Zarr store, `variable` + `datetime_utc` queries) |
| **D** New soil property / soil provider | Static soil raster that fits `SoilPipeline` |
| **E** New pipeline package | New domain, or nothing above fits |

Then take the guide's **Checklist** plus the path-specific items from
`integration-paths.md` and mark every item **needed** or **N/A —
<reason>**.

**Exit:** the path is chosen and justified in one or two sentences, and the
checklist has no unmarked items. If two paths fit, ask the user.

## Step 2: API lookup: how to talk to the source

You need verified facts about the provider before planning code. If a
research brief for this source exists (from `research-data-source`), check
that it answers everything below. Otherwise, or if anything is missing or the
source itself is unclear, **run the `research-data-source` skill** now. It
probes the API with a temporary script outside the repo.

The brief must give:

- the endpoint and one **verified** sample request (actually run), plus how
  to fetch the full extent needed;
- auth (none / key file / env var), rate limits, terms of use;
- file format, CRS, resolution, pixel registration (cell centre vs corner),
  nodata / fill values, dimension order;
- units and any scale factor; for time series the time-stamp convention
  (UTC? interval start or end? accumulation window?);
- the licence and the **verbatim** attribution / citation text.

**Exit:** every bullet answered, with the sample request verified.

## Step 3: Plan with the user (develop steps 0–2)

1. Read the standards **before designing anything**: develop step 1, plus
   `references/pipeline-contract-checklist.md`. Ask about the standards
   violations you find in the code you read, as develop step 1 describes.
2. Draft the plan with `skills/develop/references/plan-template.md`:
   - context: the need, the chosen source, the integration path and why;
   - approach: components reused vs written (see
     `references/code-skeleton.md`), storage, the `get_data` interface;
   - **change inventory**: turn every "needed" triage item into concrete
     entries (each class, function, method, constant, test group, and each
     doc / config / CI file with its section). Include the doc entries
     from step 6 below, `docs/user_guide/variables.rst` always among them;
   - verification: the gates, the modules that need ≥ 90 % coverage, the
     e2e test.
3. Get the plan approved (Claude Code: plan mode → ExitPlanMode).

**Exit:** the plan is approved, with a complete inventory.

## Step 4: Write the code (develop steps 3–4)

Do develop step 3 first: checkpoint, baseline gates, and the question about
pre-existing errors. Then implement the inventory, following
`references/code-skeleton.md` for your path. Reuse before you write:

- GeoTIFF sources: `TiffDownloader` (`datavia.core.downloader_url`),
  `TiffSaver` (`datavia.core.saver_tiff`), `GetterTiff`
  (`datavia.core.getter_tiff`);
- several files from one source: `CompositeDownloader`
  (`datavia/core/interfaces.py`);
- DB reads: helpers in `datavia/library/database/query.py`; DB writes:
  `datavia.library.database.connection.session_local()`;
- coordinates and sampling: `datavia.library.coordinate_transforms.transform_coordinates`,
  `datavia.library.interpolation`, `datavia.library.spatial_ops`;
- units: `datavia.library.unit_conversions` and the weather registry's
  conversion table;
- timeouts: `get_config().api_timeout`; config validation:
  `Pipeline.validate_pipeline_config`.

Do the wiring the inventory lists: root `pyproject.toml` (extra, `pipelines`
group, pixi editable dependency, then `pixi install`),
`.github/workflows/release-pypi.yml`, CLI.

Write the tests **with** the code: unit tests with mocks, component tests with
`sqlite_db`, network-free provider fakes (style of `tests/cdc_fake.py`), and
one e2e test gated by `DATAVIA_E2E=1`.

Anything you need that the inventory doesn't list → develop's **scope
guard** (stop, explain, offer to extend the plan or roll back to plan mode).

**Exit:** `pixi run -e dev python -c "import datavia.<module>"` works and the
new test files run (they may still fail; step 7 makes them pass).

## Step 5: Standards review, every point (develop step 5)

Review all new and changed code against
`skills/develop/references/standards-checklist.md` **and**
`references/pipeline-contract-checklist.md`, item by item, with a verdict
for each (**pass** / **fixed** / **N/A** + reason).

**Exit:** every item has a verdict, all fixes are made within the plan, and
the filled checklists are kept for the report.

## Step 6: Documentation

Update everything the inventory lists, at least:

1. **`docs/user_guide/variables.rst`** (always):
   - install line in *Using Pipelines → 1.* for a new package or extra;
   - examples in *2.* / *4.* if the request style is new;
   - CLI line if there is a new `datavia update` command;
   - a row in **How each pipeline takes a request** for a new pipeline class;
   - a row in the **Pipelines** table: class + ``source`` / ``dv.<name>``,
     install, data source, coverage and grid (extent, resolution, CRS), time,
     credentials;
   - **Variables**: new rows in the weather or soil table, or a new
     subsection with a list-table (variable, unit, scale factor / "divide
     by", notes, default), keeping column counts and `:widths:` consistent;
   - doctest outputs that list variables or properties (e.g.
     `get_valid_variables(...)`, `SoilPipeline().properties`) must match the
     new code exactly.
2. `docs/index.rst`: *Available pipeline packages*, *Current Data Sources*,
   and **Data Licensing & Attribution** with the verbatim licence and
   attribution text from the brief.
3. `docs/api/index.rst`: `.. automodule::` for every new module.
4. `README.md` (*Available Pipelines*, *Project Structure*, storage figures),
   the package `README.md` (path E), `docs/changelog.rst` under
   *Unreleased → Added*.

Doc examples that need the network get `# doctest: +SKIP`. Describe what
drives cost (area, number of variables, date range, request count), never
measured run times.

**Exit:** every doc entry in the inventory is done.

## Step 7: Quality gates and report (develop steps 6–7)

Run the gates from `skills/develop/references/quality-gates.md` until one
full pass is clean. Include `mypy` for the touched package(s), coverage
≥ 90 % for every new or changed module, the docs build and the doctests.
Run the e2e test if the network and credentials are available.

Report as develop step 7 describes, and add:

- the integration path and why;
- the research brief's key facts (source, licence) and any **unverified**
  points.
