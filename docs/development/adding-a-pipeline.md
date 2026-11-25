# Adding a New Pipeline

This guide shows how to add a new data source to Datavia as a pipeline
package, like `datavia-elevation`, `datavia-soil` and `datavia-weather`.
It covers the component contract, two worked examples, a skeleton for
custom storage, and every file outside your package that has to change.

The [checklist](#checklist) at the end lists every step.

## Contents

1. [Do you need a new pipeline?](#1-do-you-need-a-new-pipeline)
2. [How a pipeline works](#2-how-a-pipeline-works)
3. [Naming: one `source` string ties it together](#3-naming-one-source-string-ties-it-together)
4. [Example A: reuse the GeoTIFF components](#4-example-a-reuse-the-geotiff-components)
5. [Example B: custom downloader, generic storage](#5-example-b-custom-downloader-generic-storage)
6. [Custom saver and getter](#6-custom-saver-and-getter)
7. [Packaging](#7-packaging)
8. [CLI wiring (optional)](#8-cli-wiring-optional)
9. [Tests](#9-tests)
10. [Documentation](#10-documentation)
11. [Checklist](#checklist)

---

## 1. Do you need a new pipeline?

Not always. If the data is **one GeoTIFF at one URL** (a WCS `GetCoverage`
request, or a direct file link that returns `image/tiff`),
`ElevationPipeline` already handles it. The class has nothing
elevation-specific apart from its default URL:

```python
from datavia.elevation import ElevationPipeline

landcover = ElevationPipeline(
    config={
        "source": "landcover",  # file prefix, DB key and dv.<attribute>
        "url": "https://example.org/wcs?SERVICE=WCS&REQUEST=GetCoverage&...",
    }
)
landcover.update_data()
values = landcover.get_data(coords, crs_coords="EPSG:4326")
```

Write a new pipeline when you need one of:

- a different download step (archives, APIs with parameters, several files,
  authentication) → [Example B](#5-example-b-custom-downloader-generic-storage);
- a format other than GeoTIFF, or time-dependent data →
  [custom saver and getter](#6-custom-saver-and-getter);
- a pipeline-specific `get_data` (named properties, dict results, time
  ranges) → see `SoilPipeline` and `WeatherPipeline`;
- a separately installable package with its own dependencies →
  [packaging](#7-packaging).

## 2. How a pipeline works

A pipeline is three components plus an orchestrator. The abstract classes,
with the full contract in their docstrings, are in
[`datavia/core/interfaces.py`](../../datavia/core/interfaces.py).

| Component    | Job                                         | DB reads    | DB writes      |
|--------------|---------------------------------------------|-------------|----------------|
| `Downloader` | Fetch data, write it to a local file         | never       | never          |
| `Saver`      | Copy the file into the data directory, register it; list its own files | **never**   | INSERT/DELETE  |
| `Getter`     | Find files via the DB, return values at coordinates | SELECT only | **never**      |
| `Pipeline`   | Orchestrate; reconcile disk and DB           | via Getter  | via Saver      |

The read/write split is a firm rule. Reviewers will reject a `Saver` that
runs a `SELECT` or a `Getter` that writes.

### Lifecycle

```python
class Pipeline:
    def __init__(self, name, downloader, saver, getter, url=None): ...
```

1. **`__init__` takes classes, not instances.** It stores them and does no
   I/O. This keeps `import` and `Datavia([...])` cheap.
2. **`__call__` builds the instances** (and runs `initialize_database()`):
   - `self.downloader = downloader_class(self.url)` when `url` is set,
     otherwise `downloader_class(*args, **kwds)`;
   - `self.saver = saver_class(self.name)`;
   - `self.getter = getter_class(self.name)`.

   `update_data`, `get_data` and `sync_files_and_database` call `self()`
   automatically if the components don't exist yet. If your downloader needs
   more than a URL, override `__call__` to pass it a config dict (see
   [Example B](#5-example-b-custom-downloader-generic-storage)).
3. **`update_data()`**: the base class downloads and saves unconditionally.
   Pipelines override it to skip work already done. The standard flow
   (`ElevationPipeline.update_data`) is:

   ```text
   sync_files_and_database()          # reconcile disk <-> DB first
   getter.get_existing_layers()       # what is already stored?
     └─ nothing missing → return True
   downloader.download()              # → local path, or "failed"
     └─ "failed" → return False
   saver.save(path)                   # → True / False
   ```

4. **`sync_files_and_database()`** compares `saver.list_managed_files()`
   (disk) with `getter.get_registered_uris()` (DB). It removes DB rows whose
   file is gone (`saver.delete_registration(uri)`) and registers files that
   have no row (`saver.save(path, register_only=True)`). It only works if
   both methods describe the same set of **absolute paths**.
5. **`get_data(coords, crs_coords="EPSG:4326", interpolation_order=3,
   band=1, **kwargs)`** forwards to the getter. `coords` has shape `(N, 2)`
   (`[lon, lat]` for EPSG:4326, `[x, y]` for projected CRSs). The result has
   shape `(N,)`, or is a `dict[str, ndarray]` for multi-property pipelines.
   Points outside the data give `nan`.

## 3. Naming: one `source` string ties it together

Every pipeline takes a `config` dict with a required `"source"` key. That one
string becomes:

- `pipeline.name`, and so the attribute on the controller:
  `Datavia([...])()` exposes it as `dv.<source>`;
- the file prefix in `get_config().data_directory`: `TiffSaver` writes
  `<source>_<stem>.tif` and `list_managed_files()` matches `<source>_*`;
- the `source_name` column in the metadata tables, which the getter filters
  on.

Two pipelines with the same `source` share files and DB rows, and the second
overwrites the first on the controller. Pick a name that is unique and a
valid Python identifier.

Validate the config at `__init__` time with
`Pipeline.validate_pipeline_config`. It reports every missing and every
unknown key in one `ValueError`, so a typo such as `"ulr"` fails at once
instead of being silently ignored. Define the key sets as module constants
(`validate_pipeline_config` is annotated `set[str]`), as in [`packages/elevation/datavia/elevation/pipeline.py`](../../packages/elevation/datavia/elevation/pipeline.py):

```python
_REQUIRED_CONFIG_KEYS: set[str] = {"source"}
_KNOWN_CONFIG_KEYS: set[str] = _REQUIRED_CONFIG_KEYS | {"url"}
```

`config=None` should mean "all defaults", so `MyPipeline()` works.

## 4. Example A: reuse the GeoTIFF components

The core package ships generic components for single GeoTIFFs:

| Class            | Module                         | Behaviour |
|------------------|--------------------------------|-----------|
| `TiffDownloader` | `datavia.core.downloader_url`  | `TiffDownloader(url)`; streams the URL to a temp file with retries; rejects responses whose content type isn't a TIFF. |
| `TiffSaver`      | `datavia.core.saver_tiff`      | Copies to `<data_dir>/<source>_<stem>.tif`, optional reprojection, writes `raster_layers` (+ `raster_band_metadata` for multi-band files). |
| `GetterTiff`     | `datavia.core.getter_tiff`     | Reads paths from `raster_layers`, samples with `spatial_interpolate`; `get_band_mapping()` for multi-band files. |

A complete pipeline built only from these, for a hypothetical canopy-height
GeoTIFF. It is `ElevationPipeline` with a different default:

```python
"""Canopy height pipeline: one GeoTIFF from a WCS endpoint."""

import logging
from typing import Any

from datavia.core.downloader_url import TiffDownloader
from datavia.core.getter_tiff import GetterTiff
from datavia.core.interfaces import Pipeline
from datavia.core.saver_tiff import TiffSaver

logger = logging.getLogger(__name__)

#: Default WCS GetCoverage request (EPSG:25832, Germany).
_DEFAULT_URL: str = "https://example.org/wcs?SERVICE=WCS&REQUEST=GetCoverage&..."

_REQUIRED_CONFIG_KEYS: set[str] = {"source"}
_KNOWN_CONFIG_KEYS: set[str] = _REQUIRED_CONFIG_KEYS | {"url"}


class CanopyPipeline(Pipeline):
    """Canopy height in metres from a single GeoTIFF.

    Parameters
    ----------
    config : dict[str, Any], optional
        ``source`` (str, required) and ``url`` (str, optional).  ``None``
        means ``{"source": "canopy"}`` with the default URL.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Validate *config* and register the generic TIFF components."""
        if config is not None:
            Pipeline.validate_pipeline_config(
                config, _REQUIRED_CONFIG_KEYS, _KNOWN_CONFIG_KEYS, "CanopyPipeline"
            )
            name: str = config["source"]
            url: str = config.get("url", _DEFAULT_URL)
        else:
            name, url = "canopy", _DEFAULT_URL
        super().__init__(name, TiffDownloader, TiffSaver, GetterTiff, url=url)

    def update_data(
        self, reproject: bool = False, resolution_m: int | None = None
    ) -> bool:
        """Download and register the GeoTIFF unless it is already stored.

        Parameters
        ----------
        reproject : bool, optional
            Reproject the saved file to ``get_config().default_crs``.
        resolution_m : int, optional
            Target resolution in metres when reprojecting.

        Returns
        -------
        bool
            ``True`` if the data is stored (now or already), else ``False``.
        """
        self.sync_files_and_database()  # also builds the components on first use
        if self.getter is not None and self.getter.get_existing_layers():
            logger.info("Canopy data already stored.")
            return True
        return super().update_data(reproject=reproject, resolution_m=resolution_m)
```

The skip check in `update_data` is what makes repeated calls cheap: once a
layer is registered, nothing is downloaded. To force a fresh download, delete
the `<source>_*.tif` file. The next `update_data()` removes the stale row and
downloads again.

## 5. Example B: custom downloader, generic storage

The most common case: the data needs a special download step but ends up as
a GeoTIFF. Write a `Downloader` and keep `TiffSaver` and `GetterTiff`.

The downloader contract
([`Downloader.download`](../../datavia/core/interfaces.py)):

- return the **path of a local file** on success;
- return the string **`"failed"`** on failure, and log why. The base
  `Pipeline.update_data` checks for that value. Don't let network errors
  escape;
- put the file somewhere temporary (`tempfile`). The saver copies it into the
  data directory. Never write into the data directory yourself, or
  `sync_files_and_database` will register a half-written file;
- the file **stem becomes the layer name**: `TiffSaver` stores
  `foo.tif` as layer `<source>_foo`. Choose a stable, meaningful stem
  (`TiffDownloader` uses `downloaded_file_<timestamp>`, which gives layer
  names like `canopy_downloaded_file_1795624824`).

Example: the provider ships the GeoTIFF inside a zip archive.

```python
"""Pipeline for a GeoTIFF delivered inside a zip archive."""

import logging
import os
import tempfile
import zipfile
from typing import Any

import requests

from datavia.config import get_config
from datavia.core.getter_tiff import GetterTiff
from datavia.core.interfaces import Downloader, Pipeline
from datavia.core.saver_tiff import TiffSaver

logger = logging.getLogger(__name__)

_REQUIRED_CONFIG_KEYS: set[str] = {"source", "url", "member"}
_KNOWN_CONFIG_KEYS: set[str] = _REQUIRED_CONFIG_KEYS


class ZippedTiffDownloader(Downloader):
    """Download a zip archive and extract one GeoTIFF from it.

    Parameters
    ----------
    config : dict[str, Any]
        ``url`` of the archive and ``member``, the GeoTIFF's path inside it.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Store the archive URL and member name."""
        self.url: str = config["url"]
        self.member: str = config["member"]

    def download(self) -> str:
        """Fetch the archive and extract the GeoTIFF to a temporary directory.

        Returns
        -------
        str
            Path to the extracted GeoTIFF, or ``"failed"`` on any error.
        """
        try:
            response = requests.get(self.url, timeout=get_config().api_timeout)
            response.raise_for_status()
            tmp_dir = tempfile.mkdtemp(prefix="datavia_")
            archive = os.path.join(tmp_dir, "archive.zip")
            with open(archive, "wb") as fh:
                fh.write(response.content)
            with zipfile.ZipFile(archive) as zf:
                path = zf.extract(self.member, tmp_dir)
            return path
        except (requests.RequestException, zipfile.BadZipFile, KeyError) as exc:
            logger.error("Download of %s failed: %s", self.url, exc)
            return "failed"


class ZippedTiffPipeline(Pipeline):
    """A GeoTIFF from a zip archive, stored and queried with the generic components.

    Parameters
    ----------
    config : dict[str, Any]
        ``source``, ``url`` and ``member`` (all required).
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Validate *config* and register the components."""
        Pipeline.validate_pipeline_config(
            config, _REQUIRED_CONFIG_KEYS, _KNOWN_CONFIG_KEYS, "ZippedTiffPipeline"
        )
        self.config = config
        # url=None: the base __call__ then forwards its arguments to the
        # downloader instead of passing a single URL.
        super().__init__(
            config["source"], ZippedTiffDownloader, TiffSaver, GetterTiff, url=None
        )

    def __call__(self, *args: Any, **kwds: Any) -> "ZippedTiffPipeline":
        """Build the components, giving the downloader this pipeline's config."""
        super().__call__(self.config, *args, **kwds)
        return self

    def update_data(
        self, reproject: bool = False, resolution_m: int | None = None
    ) -> bool:
        """Download and register the GeoTIFF unless it is already stored.

        Parameters
        ----------
        reproject : bool, optional
            Reproject the saved file to ``get_config().default_crs``.
        resolution_m : int, optional
            Target resolution in metres when reprojecting.

        Returns
        -------
        bool
            ``True`` if the data is stored (now or already), else ``False``.
        """
        self.sync_files_and_database()  # also builds the components on first use
        if self.getter is not None and self.getter.get_existing_layers():
            return True
        return super().update_data(reproject=reproject, resolution_m=resolution_m)
```

The `__call__` override is the same pattern `SoilPipeline` uses to hand its
property/depth configuration to `CompositeDownloader`.

**Several files from one source.** If one pipeline fetches several layers
(soil properties, weather variables), the base `update_data` is too simple
because it calls `download()` and `save()` once. Look at:

- `CompositeDownloader` in `datavia/core/interfaces.py`: one `Downloader`
  that delegates to sub-downloaders, so the pipeline sees a single
  component. Used by `datavia.soil.composite_downloader` and
  `datavia.weather.composite_downloader`.
- `SoilPipeline.update_data` (`packages/soil/datavia/soil/pipeline.py`):
  compares `get_existing_layers()` with the requested layers, then downloads
  and saves only the missing ones, one file per layer.

## 6. Custom saver and getter

Write your own `Saver`/`Getter` when the data isn't a GeoTIFF (NetCDF, Zarr,
point tables) or has a time dimension. The weather package is the reference
implementation: `datavia.weather.saver_weather.SaverWeather` and
`datavia.weather.getter_weather.GetterWeather`.

### Metadata tables

Tables are defined with SQLAlchemy Core in
[`datavia/library/database/schema.py`](../../datavia/library/database/schema.py)
and created by `initialize_database()` (idempotent; it also adds tables and
indexes that are new in an existing database). Reuse an existing table if it
fits:

| Table                  | For                         | Key columns |
|------------------------|-----------------------------|-------------|
| `raster_layers`        | one row per raster file      | `layer_name`, `source_name`, `uri`, `bbox` (WKT, EPSG:4326), `crs`, resolution |
| `raster_band_metadata` | per-band descriptions        | `layer_name`, `source_name`, `band_index`, `description` |
| `weather_layers`       | time-sliced gridded data     | `source_name`, `variable`, `valid_from`, `valid_until`, `uri`, `file_format` |

Every row needs `source_name` and `uri` (absolute path), otherwise sync and
source isolation break. To add a table, define it in `schema.py` and add a
case to `tests/test_database_schema.py`. Keep it portable between SQLite
(default) and PostgreSQL, which `initialize_database()` both supports.

Ready-made SELECT helpers for getters are in
`datavia/library/database/query.py` (`get_raster_paths`,
`get_raster_metadata`, `get_weather_paths`, `get_weather_metadata`, …). For
writes, open a session with
`datavia.library.database.connection.session_local()`, then commit or roll
back and close it. `TiffSaver._delete_layer_metadata` shows the pattern.

### Skeleton

```python
"""Saver and getter for a custom format (skeleton)."""

import os
from typing import Any

import numpy as np

from datavia.config import get_config
from datavia.core.interfaces import Getter, Saver


class MySaver(Saver):
    """File writes and inventory for the ``<source>`` data.  Never SELECTs."""

    def __init__(self, source_name: str) -> None:
        """Remember the source name and resolve the data directory."""
        self.source_name = source_name
        self.data_dir = str(get_config().data_directory)

    def save(
        self,
        data_path: str,
        reproject: bool = False,
        resolution_m: int | None = None,
        register_only: bool = False,
    ) -> bool:
        """Copy *data_path* into the data directory and INSERT its row(s).

        With ``register_only=True`` *data_path* is already inside the data
        directory (sync found it without a DB row): skip the copy and only
        INSERT.  Must be idempotent: delete existing rows for the URI before
        inserting.  Return ``False`` and log on failure; don't raise.
        """
        raise NotImplementedError

    def list_managed_files(self) -> list[str]:
        """Return absolute paths of this source's files (filesystem only).

        Match exactly the files ``save`` writes, e.g. the ``<source>_``
        prefix and your extension.  Return ``[]`` if the directory is missing.
        """
        prefix = self.source_name + "_"
        if not os.path.isdir(self.data_dir):
            return []
        return [
            os.path.join(self.data_dir, f)
            for f in os.listdir(self.data_dir)
            if f.startswith(prefix) and f.endswith(".nc")
        ]

    def delete_registration(self, uri: str) -> None:
        """DELETE every row for *uri* and this source.  Never touch the file."""
        raise NotImplementedError


class MyGetter(Getter):
    """DB reads and value extraction.  Never writes."""

    def __init__(self, source_name: str) -> None:
        """Remember the source name."""
        self.source_name = source_name

    def get_existing_layers(self) -> set[str]:
        """Return logical layer ids (not paths) stored for this source.

        ``update_data`` compares these with what the user asked for, so use
        the same identifiers as the pipeline's config (variable names,
        coverage ids, ...).  Return ``set()`` if nothing is stored.
        """
        raise NotImplementedError

    def get_registered_uris(self) -> set[str]:
        """Return the absolute ``uri`` of every row for this source.

        Must use the same path form as ``MySaver.list_managed_files`` so that
        ``sync_files_and_database`` can compare the two sets.
        """
        raise NotImplementedError

    def get_data(
        self,
        coords: np.ndarray,
        crs_coords: str = "EPSG:4326",
        interpolation_order: int = 3,
        band: int = 1,
        **kwargs: Any,
    ) -> np.ndarray | dict[str, np.ndarray]:
        """Return values at *coords* (shape ``(N, 2)``) as ``(N,)``, ``nan`` outside.

        Transform coordinates with
        ``datavia.library.coordinate_transforms.transform_coordinates``;
        reuse ``datavia.library.interpolation`` where it fits.
        """
        raise NotImplementedError
```

Then pass the classes to the base constructor:
`super().__init__(name, MyDownloader, MySaver, MyGetter)`.

## 7. Packaging

Each pipeline is its own distribution (`datavia-<name>`) that adds the
subpackage `datavia.<name>` to the shared `datavia` namespace.

### Layout

```text
packages/<name>/
├── LICENSE
├── README.md
├── pyproject.toml
└── datavia/              # NO __init__.py here: implicit namespace
    └── <name>/
        ├── __init__.py   # public exports + extend_path line
        └── pipeline.py
```

`packages/<name>/datavia/<name>/__init__.py`:

```python
"""Datavia <name> namespace package."""

from .pipeline import MyPipeline

__all__ = ["MyPipeline"]

__path__ = __import__("pkgutil").extend_path(__path__, __name__)
```

Do **not** add `packages/<name>/datavia/__init__.py`. It would shadow the
core `datavia/__init__.py` (which defines `Datavia`).

### `packages/<name>/pyproject.toml`

Copy [`packages/elevation/pyproject.toml`](../../packages/elevation/pyproject.toml)
and change `name`, `description`, `keywords` and `dependencies`. Keep:

- `version` equal to the core version (the release workflow rewrites all of
  them together);
- `"datavia>=1.0.0.dev0,<2.0"` in `dependencies`;
- the hatch settings, with your package name:

  ```toml
  [tool.hatch.build]
  include = ["datavia/<name>/**/*.py"]

  [tool.hatch.build.targets.wheel]
  packages = ["datavia"]

  [tool.hatch.build.targets.editable]
  mode = "strict"
  ```

Optional heavy or credentialled dependencies can be extras of your package
(see `era5` in `packages/weather/pyproject.toml`), imported lazily inside a
function. Ruff's `PLC0415` is disabled under `packages/` for that reason.

### Root `pyproject.toml`

1. `[project.optional-dependencies]`: add
   `<name> = ["datavia-<name>>=1.0.0.dev0,<2.0"]` and add
   `"datavia[<name>]"` to the `pipelines` group.
2. `[tool.pixi.pypi-dependencies]`: add
   `datavia-<name> = { path = "./packages/<name>/", editable = true }`.
   Then run `pixi install`.

The core wheel excludes `packages/**`, so no change is needed in
`[tool.hatch.build]`.

### Release workflow

`.github/workflows/release-pypi.yml` lists the packages by hand. Add your
package:

- the `sed -i "s/version = ...` line in **"Update version in pyproject.toml"**;
- a **"Build <name> package"** step (copy the elevation one, change
  `working-directory`);
- the `sed` line and the `git add` path in the post-release step that bumps
  to the next dev version.

The pull-request CI (`ci-pr-check`: format, lint, tests, docs) already covers
`packages/` and `tests/` without changes.

## 8. CLI wiring (optional)

The `datavia` CLI loads pipelines from a user's `datavia_config.py`, but the
`update` subcommands are written out per pipeline:

- `datavia/cli.py`: add an `@update.command()` function named after your
  source that calls `update_pipeline("<name>", config_file)`; copy
  `elevation`. `datavia update all` needs no change.
- `datavia/cli.py` `config init`: add a `--<name>` flag if users should be
  able to select it.
- `datavia/cli_config.py`: add a `<NAME>_EXAMPLE` snippet and its branch in
  `_generate_usage_examples`.
- `tests/test_cli.py`: cover the new subcommand.

## 9. Tests

Tests live in the root `tests/` directory (`test_<name>_pipeline_unit.py`,
`test_<name>_e2e.py`).

- **Unit tests, no network.** Build the pipeline with `__init__` only, then
  attach `unittest.mock.MagicMock` components to test `update_data` and
  `get_data` logic: skip when stored, download only what's missing, `False`
  on `"failed"`. Pattern: `tests/test_soil_pipeline_unit.py`.
- **Component tests with a real database.** The `sqlite_db` fixture in
  `tests/conftest.py` gives each test a fresh in-memory SQLite database.
  Point the data directory at `tmp_path`, write a tiny GeoTIFF/NetCDF with
  rasterio/xarray, call `saver.save(...)`, and check the getter and
  `sync_files_and_database()` (delete the file → row removed; delete the
  row → file re-registered). Pattern: `tests/test_sync_adventure.py`.
- **End-to-end tests** that hit the real service are gated:

  ```python
  @pytest.mark.skipif(
      os.getenv("DATAVIA_E2E") != "1",
      reason="DATAVIA_E2E not set; skipping integration test",
  )
  def test_<name>_pipeline_e2e(sqlite_db): ...
  ```

  Pattern: `tests/test_elevation_pipeline.py`.

Run them with:

```bash
pixi run -e dev test-light   # what CI runs (e2e tests skip themselves)
pixi run -e dev test-e2e     # includes network tests (DATAVIA_E2E=1)
pixi run -e dev ci-check     # format-check, lint, type-check, test-light
```

`type-check` (mypy) only covers the core `datavia/` directory. Run
`mypy packages/<name>/datavia/<name> --ignore-missing-imports` yourself; the
coding standards require full type hints there too.

## 10. Documentation

- `packages/<name>/README.md`: installation, a usage example, the data
  source, units and CRS.
- `docs/api/index.rst`: add `.. automodule:: datavia.<name>.pipeline` (and
  your downloader/saver/getter modules) under **Pipeline Packages**.
  `pixi run docs` builds with `-W`, so docstring formatting errors fail the
  build.
- `docs/index.rst`: add the pipeline to *Available pipeline packages*,
  *Current Data Sources*, and **Data Licensing & Attribution**, with the
  provider's licence and the exact citation/attribution text it requires.
  This is required for every new data source.
- `README.md`: *Available Pipelines*, the *Project Structure* tree, and the
  storage figures in *System Requirements*.
- `docs/changelog.rst`: an entry under *Unreleased → Added*.

Follow the [coding standards](coding-standards.md): NumPy-style docstrings,
type hints, and describe what drives runtime cost (data volume, number of
requests) rather than quoting measured times.

## Checklist

- [ ] Checked that `ElevationPipeline({"source": ..., "url": ...})` isn't
      enough
- [ ] `packages/<name>/` with `pyproject.toml`, `README.md`, `LICENSE`,
      `datavia/<name>/__init__.py` (with `extend_path`), no
      `datavia/__init__.py`
- [ ] Pipeline class: `config` dict with `source`, validated with
      `validate_pipeline_config`; `config=None` gives defaults
- [ ] Downloader returns a temp-file path or `"failed"`; never raises for
      network errors
- [ ] Saver never SELECTs; `save` handles `register_only=True`;
      `list_managed_files` and `get_registered_uris` return the same
      absolute paths
- [ ] Getter never writes; `get_existing_layers` returns layer ids, not
      paths
- [ ] `update_data` calls `sync_files_and_database()` and skips layers
      already stored
- [ ] New DB table (if any) in `schema.py` + `tests/test_database_schema.py`
- [ ] Root `pyproject.toml`: extra, `pipelines` group, pixi editable
      dependency
- [ ] `.github/workflows/release-pypi.yml`: version bumps + build step
- [ ] CLI: `datavia update <name>`, `config init --<name>`, example
      snippet (optional)
- [ ] Unit tests (mocks), component tests (`sqlite_db`), gated e2e test
- [ ] Docs: package README, `docs/api/index.rst`, `docs/index.rst`
      (incl. licence/attribution), `README.md`, changelog
- [ ] `pixi run -e dev ci-check` and `pixi run -e dev docs` pass
