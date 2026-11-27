# Pipeline contract checklist

Datavia-specific rules for data-source code, from
`docs/development/adding-a-pipeline.md` (the "guide"). Review these **in
addition to** `skills/develop/references/standards-checklist.md`, with the
same verdicts (**pass** / **fixed** / **N/A** with a reason).

## Components [guide §2, §5, §6]

- [ ] P1 `__init__` of the pipeline and its components does **no I/O** (no
      network, DB or file access); components are built in `__call__`.
- [ ] P2 Config validated with `Pipeline.validate_pipeline_config` at
      `__init__`; key sets are module constants; `config=None` gives working
      defaults.
- [ ] P3 The `source` name is unique, a valid Python identifier, and used
      consistently as file prefix, DB `source_name` and `dv.<source>`. [guide §3]
- [ ] P4 The downloader returns a temp-file path or `"failed"`, logs the
      reason, never lets network or parse errors escape, and never writes
      into the data directory; the file stem is stable and meaningful.
- [ ] P5 The downloader validates the response (status, content type,
      non-empty payload, expected variables / bands) before returning a path.
- [ ] P6 Network calls pass `timeout=get_config().api_timeout`.
- [ ] P7 The saver never SELECTs; `save` supports `register_only=True`, is
      idempotent (deletes rows for the URI before inserting), and returns
      `False` and logs instead of raising.
- [ ] P8 `Saver.list_managed_files()` and `Getter.get_registered_uris()`
      return the **same absolute path form** for the same files.
- [ ] P9 The getter never writes; `get_existing_layers()` returns logical
      layer ids (not paths); `get_data` takes `(N, 2)` coords and returns
      `(N,)` (or a documented dict), with `nan` outside the data.
- [ ] P10 `update_data` calls `sync_files_and_database()` first and skips
      layers already stored; it returns `True` / `False` as documented.
- [ ] P11 Provider quirks from the research brief are handled explicitly:
      fill / nodata values → `nan`, pixel registration, scale factors and
      units, time-stamp convention (UTC, interval start or end).
- [ ] P12 A new DB table (if any) is defined in `schema.py`, portable between
      SQLite and PostgreSQL, has `source_name` and `uri`, and is tested in
      `tests/test_database_schema.py`.

## Tests [guide §9]

- [ ] P13 Unit tests with mocked components: config validation,
      skip-when-stored, download only what's missing, `"failed"` → `False`,
      `get_data` shapes and `nan` outside.
- [ ] P14 Component test with `sqlite_db`: save → get → sync (file deleted →
      row removed; row deleted → file re-registered).
- [ ] P15 End-to-end test against the real service, gated by
      `DATAVIA_E2E=1`, with the smallest request possible and plausible-range
      assertions at named places.

## Packaging and wiring [guide §7, §8]

- [ ] P16 Package layout: no `packages/<name>/datavia/__init__.py`;
      `extend_path` line present; `pyproject.toml` version equals the core
      version, depends on `datavia>=1.0.0.dev0,<2.0`, hatch settings name the
      package.
- [ ] P17 Root `pyproject.toml`: extra, `pipelines` group, pixi editable
      dependency; `.github/workflows/release-pypi.yml` updated.
- [ ] P18 CLI wiring done or explicitly N/A.
