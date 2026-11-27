# Code skeleton

Minimal templates to start from. They follow
`docs/development/adding-a-pipeline.md` (the "guide"); the guide has the full
worked examples, and this file points to them instead of copying them.
Replace `<name>` (package / module, lower case), `<Name>` (class prefix) and
`<source>` (default `source` string, a valid Python identifier).

Before you copy a template, open the closest **existing** implementation and
mirror its conventions:

| You are writing | Mirror |
|-----------------|--------|
| GeoTIFF pipeline, default components | `packages/elevation/datavia/elevation/pipeline.py`, guide §4 |
| Custom downloader, GeoTIFF storage | guide §5 (`ZippedTiffDownloader`), `packages/soil/datavia/soil/hihydrosoil_downloader.py` |
| Several layers from one source | `packages/soil/datavia/soil/composite_downloader.py`, `SoilPipeline.update_data` |
| Custom saver / getter, time axis | `packages/weather/datavia/weather/saver_weather.py`, `getter_weather.py`, guide §6 skeleton |
| Weather downloader (path C) | `packages/weather/datavia/weather/hyras_downloader.py`, `era5_downloader.py` |
| Fake provider for tests | `tests/cdc_fake.py` |

## Package layout (path E)

```text
packages/<name>/
├── LICENSE                  # copy from packages/elevation/
├── README.md                # install, usage example, source, units, CRS, licence
├── pyproject.toml           # copy packages/elevation/pyproject.toml, see guide §7
└── datavia/                 # NO __init__.py here (implicit namespace)
    └── <name>/
        ├── __init__.py
        ├── pipeline.py
        └── <provider>_downloader.py   # if the download step is custom
```

### `datavia/<name>/__init__.py`

```python
"""Datavia <name> namespace package."""

from .pipeline import <Name>Pipeline

__all__ = ["<Name>Pipeline"]

__path__ = __import__("pkgutil").extend_path(__path__, __name__)
```

### `datavia/<name>/<provider>_downloader.py`

```python
"""Downloader for <provider> <dataset>."""

import logging
import os
import tempfile
from typing import Any

import requests

from datavia.config import get_config
from datavia.core.interfaces import Downloader

logger = logging.getLogger(__name__)

#: Base URL of the <provider> API (documented at <docs URL>).
_BASE_URL: str = "https://..."


class <Name>Downloader(Downloader):
    """Download <dataset> from <provider> into a temporary file.

    Parameters
    ----------
    config : dict[str, Any]
        Pipeline config; uses ``<key>`` (...) and ``<key>`` (...).
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Store the request parameters; does no I/O."""
        self.config = config

    def download(self) -> str:
        """Fetch the data and write it to a temporary file.

        Returns
        -------
        str
            Path of the downloaded file, or ``"failed"`` on any error (the
            reason is logged).  The file stem becomes the layer name, so it is
            stable and meaningful.
        """
        try:
            response = requests.get(
                _BASE_URL, params=self._build_params(), timeout=get_config().api_timeout
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.error("<provider> download failed: %s", exc)
            return "failed"
        tmp_dir = tempfile.mkdtemp(prefix="datavia_<name>_")
        path = os.path.join(tmp_dir, "<stable_stem>.tif")
        with open(path, "wb") as fh:
            fh.write(response.content)
        return path

    def _build_params(self) -> dict[str, str]:
        """Translate the pipeline config into the provider's query parameters."""
        raise NotImplementedError
```

Contract (guide §5): return a temp-file path or `"failed"`; never let network
errors escape; never write into the data directory; validate the response
(content type, non-empty, expected variables) before returning the path.

### `datavia/<name>/pipeline.py`

```python
"""<Name> pipeline: <one line on what data, from where>."""

import logging
from typing import Any

from datavia.core.getter_tiff import GetterTiff
from datavia.core.interfaces import Pipeline
from datavia.core.saver_tiff import TiffSaver

from .<provider>_downloader import <Name>Downloader

logger = logging.getLogger(__name__)

#: Keys that must be present in the config dict.
_REQUIRED_CONFIG_KEYS: frozenset[str] = frozenset({"source"})

#: All valid config keys (required + optional).
_KNOWN_CONFIG_KEYS: frozenset[str] = _REQUIRED_CONFIG_KEYS | frozenset({...})

#: Config used when ``config`` is ``None``.
_DEFAULT_CONFIG: dict[str, Any] = {"source": "<source>", ...}


class <Name>Pipeline(Pipeline):
    """<What the pipeline delivers: quantity, unit, extent, resolution, CRS>.

    Parameters
    ----------
    config : dict[str, Any], optional
        ``source`` (str, required) and ... ``None`` means the defaults
        (``{"source": "<source>", ...}``).

    Raises
    ------
    ValueError
        If *config* has missing or unknown keys.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Validate *config* and register the component classes (no I/O)."""
        if config is not None:
            Pipeline.validate_pipeline_config(
                config,
                set(_REQUIRED_CONFIG_KEYS),
                set(_KNOWN_CONFIG_KEYS),
                "<Name>Pipeline",
            )
        self.config: dict[str, Any] = {**_DEFAULT_CONFIG, **(config or {})}
        super().__init__(
            self.config["source"], <Name>Downloader, TiffSaver, GetterTiff, url=None
        )

    def __call__(self, *args: Any, **kwds: Any) -> "<Name>Pipeline":
        """Build the components, giving the downloader this pipeline's config."""
        super().__call__(self.config, *args, **kwds)
        return self

    def update_data(
        self, reproject: bool = False, resolution_m: int | None = None
    ) -> bool:
        """Download and register the data unless it is already stored.

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
            logger.info("%s data already stored.", self.name)
            return True
        return super().update_data(reproject=reproject, resolution_m=resolution_m)
```

For several layers, compare `get_existing_layers()` with the requested set and
download only the missing ones (`SoilPipeline.update_data`). For custom
storage, write `Saver` / `Getter` subclasses from the guide §6 skeleton and
pass them to `super().__init__`.

## Wiring (path E)

Guide §7–§8. In the root `pyproject.toml`:

```toml
[project.optional-dependencies]
<name> = ["datavia-<name>>=1.0.0.dev0,<2.0"]
pipelines = [..., "datavia[<name>]"]

[tool.pixi.pypi-dependencies]
datavia-<name> = { path = "./packages/<name>/", editable = true }
```

Run `pixi install` afterwards, then
`pixi run -e dev python -c "from datavia.<name> import <Name>Pipeline"`.
Add the version `sed` lines, the build step and the `git add` path to
`.github/workflows/release-pypi.yml`.

## Tests

One file per concern in the root `tests/` directory.

### `tests/test_<name>_pipeline_unit.py`: logic with mocks, no network, no DB

```python
"""Unit tests for <Name>Pipeline: config validation and update/get logic."""

from unittest.mock import MagicMock

import pytest
from datavia.<name> import <Name>Pipeline


@pytest.fixture()
def pipeline() -> <Name>Pipeline:
    """Return a pipeline with mocked components (``__init__`` only, no I/O)."""
    pipe = <Name>Pipeline()
    pipe.downloader = MagicMock()
    pipe.saver = MagicMock()
    pipe.getter = MagicMock()
    return pipe


def test_unknown_config_key_raises() -> None:
    """A misspelt key is rejected at construction time."""
    with pytest.raises(ValueError, match="ulr"):
        <Name>Pipeline(config={"source": "x", "ulr": "..."})


def test_update_skips_when_stored(pipeline: <Name>Pipeline) -> None:
    """No download happens when the layer is already registered."""
    pipeline.getter.get_existing_layers.return_value = {"<layer>"}
    pipeline.saver.list_managed_files.return_value = []
    pipeline.getter.get_registered_uris.return_value = set()
    assert pipeline.update_data() is True
    pipeline.downloader.download.assert_not_called()


def test_update_returns_false_on_failed_download(pipeline: <Name>Pipeline) -> None:
    """A ``"failed"`` download is reported as ``False`` and nothing is saved."""
    ...
```

### `tests/test_<name>_downloader.py`: downloader against a fake server

Patch `requests.get` / `requests.Session` (or a fake like `tests/cdc_fake.py`)
to return small in-memory payloads. Cover success, HTTP error → `"failed"`,
wrong content type / empty payload → `"failed"`, parameter building.

### Component test with a real DB: `sqlite_db` + `tmp_path`

```python
def test_save_get_sync_roundtrip(sqlite_db: None, tmp_path: Path) -> None:
    """Saved files are queryable, and sync drops rows whose file is gone."""
    pipe = <Name>Pipeline()
    pipe()
    pipe.saver.data_dir = str(tmp_path)  # keep file I/O inside the test dir
    src = tmp_path / "incoming" / "<stable_stem>.tif"
    ...  # write a 10x10 GeoTIFF / NetCDF with rasterio / xarray
    assert pipe.saver.save(str(src))
    values = pipe.get_data(np.array([[lon, lat]]), crs_coords="EPSG:4326")
    ...
```

Pattern: `tests/test_sync_adventure.py` (`_create_minimal_tiff`,
`_build_tiff_pipeline`).

### `tests/test_<name>_e2e.py`: real service, gated

```python
@pytest.mark.skipif(
    os.getenv("DATAVIA_E2E") != "1",
    reason="DATAVIA_E2E not set; skipping integration test",
)
def test_<name>_pipeline_e2e(sqlite_db: None, tmp_path: Path) -> None:
    """Download a small real extent and check values at known points."""
```

Use the smallest request the provider allows, and assert plausible ranges at
named locations (e.g. Berlin `[13.405, 52.520]`), not exact values that the
provider may revise.
