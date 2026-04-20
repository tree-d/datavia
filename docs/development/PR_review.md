# PR Review — SQLite Migration: Remaining Issues

## Background

This PR migrates Datavia's metadata backend from Docker-managed PostGIS to a
local SQLite database.  The core migration work — rewriting `connection.py`,
`start.py`, `init.sql`, `config.py`, and `conftest.py` — is complete and
correct.  Issues flagged in review remain open.  They are grouped below
by file, each with a root-cause description and concrete fix steps.

### Migration checklist status (cross-reference with `SQLite_Migration.md`)

| Item from migration plan | Status |
|---|---|
| Rewrite `init.sql` (bbox TEXT, no GIST) | Done — but see issue 1 (AUTOINCREMENT) |
| Rewrite `connection.py` (SQLite detection, thread safety) | Done |
| Rewrite `start.py` (`inspect.has_table`, no `AUTOCOMMIT`) | Done |
| Rewrite `config.py` (`database_url` fallback chain) | Done — but see issues 2 & 3 (stale docstrings) |
| Update `saver_tiff.py` (plain WKT, no `ST_GeomFromText`) | Done (not flagged in review) |
| Update `query.py` (remove `ST_AsText`) | Done (not flagged in review) |
| Create `conftest.py` with `sqlite_db` fixture | Done — but elevation tests do not use it (issues 4 & 5) |
| `cli_utils.py` start/stop are now no-ops | Done |
| Docs: remove Docker/PostGIS references | Partial — see issues 6, 7, 8 |
| GitHub Actions tag filter | Broken — see issue 9 |
| `.datavia/.gitignore` two sources of truth | Not resolved — see issue 10 |
| `scripts/test_docs_with_io.py` secret leakage | Not resolved — see issue 11 |

---

## Issues by file

---

### 1 · `datavia/library/database/init.sql` — AUTOINCREMENT breaks PostgreSQL

**Root cause.**  The migration correctly dropped the PostGIS `SERIAL` type and
the GIST index, but replaced `SERIAL PRIMARY KEY` with
`INTEGER PRIMARY KEY AUTOINCREMENT`.  The `AUTOINCREMENT` keyword is
SQLite-specific and will cause a syntax error if a user configures a
PostgreSQL URL and `initialize_database()` runs the file against it.
`INTEGER PRIMARY KEY` alone is sufficient for SQLite: it is an alias for the
rowid, which auto-increments by definition — `AUTOINCREMENT` only adds an
extra monotonicity guarantee that is rarely needed and has a small overhead.

**Fix.**

```sql
-- Before
id INTEGER PRIMARY KEY AUTOINCREMENT,

-- After (works in both SQLite and PostgreSQL)
id INTEGER PRIMARY KEY,
```

Apply the same change to both `raster_layers` and `raster_band_metadata`.
The `DOUBLE PRECISION` type is already cross-backend, so no other DDL change
is needed.

---

### 2 · `datavia/config.py` — `base_directory` docstring contradicts actual default

**Root cause.**  The `base_directory` property docstring lists the resolution
order as:

> 3. `storage = global` **(default)** -> `~/.datavia/`

But `_set_defaults()` sets `"storage": "project"`, making `<cwd>/.datavia/`
the actual default.  The word "default" is attached to the wrong entry.

**Fix.**  Swap the "(default)" label in the docstring:

```
2. ``storage = project`` **(default)** -> ``<cwd>/.datavia/`` — per-project, self-contained.
3. ``storage = global`` -> ``~/.datavia/`` — shared across all projects.
```

---

### 3 · `datavia/config.py` — `project_name` and `_derive_project_defaults` carry Docker-era language

**Root cause.**  Two members still reference Docker concepts that were removed:

- `project_name` property docstring: *"Return the Docker Compose project name"*
  and *"every project directory owns its own isolated container"*.
- `_derive_project_defaults` docstring: explains the port derivation in detail,
  but the returned port integer is now unused (unpacked as `_` in
  `_set_defaults`).

**Fix.**

For `project_name`, replace the docstring opening:

```python
"""Return the stable per-project identifier derived from the working directory.

The value is a short hash of the absolute CWD path, giving each project
directory a unique, reproducible name without any persistent state or user
configuration.  Override with ``[project] name = my_project`` in
``datavia.conf``.
"""
```

For `_derive_project_defaults`: either remove the port return value entirely
and simplify the call site in `_set_defaults`, or update the docstring to note
that the returned port is a legacy value no longer used and will be removed in
a future version.

---

### 4 · `tests/test_elevation_pipeline.py` — no in-memory DB isolation

**Root cause.**  The test comment says *"initialise in-memory SQLite
database"*, but the test body just calls `initialize_database()` with no
prior URL injection or `reset_engine()`.  The engine therefore points to the
file-backed default path derived from `data_directory`.  The `finally: pass`
block and the *"no teardown needed"* comment are incorrect for a file-backed
DB.

The shared `sqlite_db` fixture in `conftest.py` already does this correctly:
it injects `sqlite:///:memory:`, calls `reset_engine()`, initialises the
schema, and restores state on teardown.

**Fix.**  Replace the inline setup with the fixture:

```python
# Before (manual, incomplete)
def test_elevation_pipeline_e2e():
    initialize_database()
    try:
        ...
    finally:
        pass  # SQLite is in-memory; no teardown needed.

# After (uses shared fixture)
def test_elevation_pipeline_e2e(sqlite_db):
    try:
        ...
    finally:
        pass  # sqlite_db fixture handles teardown.
```

Remove the `initialize_database` import if it is no longer used elsewhere in
the file.

---

### 5 · `tests/test_elevation_pipeline_inside_datavia.py` — same isolation problem

**Root cause.**  Identical to issue 4.  `initialize_database()` is called
without URL injection or engine reset, so the test uses the file-backed DB
and the *"no teardown needed"* comment is inaccurate.

**Fix.**  Same pattern as issue 4: accept `sqlite_db` as a fixture parameter
and remove the manual `initialize_database()` call and its import.

---

### 6 · `docs/user_guide/basic_usage.rst` — stale PostGIS reference

**Root cause.**  The doctest comment inside the namespace-package architecture
example still reads:

```rst
>>> # dv() connects to the PostGIS database and instantiates each pipeline's
>>> # Downloader / Saver / Getter components. Does NOT download data.
```

**Fix.**

```rst
>>> # dv() connects to the metadata database (SQLite by default) and
>>> # instantiates each pipeline's Downloader / Saver / Getter components.
>>> # Does NOT download data.
```

---

### 7 · `docs/user_guide/quick_start.rst` — stale PostGIS reference in doctest

**Root cause.**  The "Step 2" doctest comment mirrors the same stale wording:

```rst
>>> # dv() connects to the PostGIS database and instantiates each pipeline's
>>> # Downloader / Saver / Getter components. It does NOT download data.
```

**Fix.**

```rst
>>> # dv() connects to the configured metadata backend and instantiates each
>>> # pipeline's Downloader / Saver / Getter components. It does NOT download data.
```

---

### 8 · `docs/user_guide/quick_start.rst` — Docker references in Prerequisites and Setup

**Root cause.**  The Prerequisites section still lists Docker as a
requirement, and the "Setup Database" section still instructs users to run
`datavia start/stop`, which are now no-ops.

**Current text (to remove/replace):**

```rst
Prerequisites
-------------
* Docker (for PostGIS database)

Setup Database
--------------
Start/Stop the PostGIS database:

.. code-block:: bash

    datavia start/stop
```

**Fix.**  Remove the Docker bullet from Prerequisites entirely.  Replace the
"Setup Database" section with a plain note:

```rst
Database
--------
No database setup is required.  A SQLite metadata file (``datavia.db``) is
created automatically in the configured data directory on first use.
To use PostgreSQL instead, set ``[database] url = postgresql://...`` in
``datavia.conf``.
```

---

### 9 · `.github/workflows/release-pypi.yml` — regex pattern used where glob is required

**Root cause.**  GitHub Actions tag filters use **glob** syntax, not regex.
The pattern `v[0-9]+.[0-9]+.[0-9]+` is treated as a glob, where `[0-9]`
matches exactly one character from the set `0-9`, and `+` is a literal `+`.
The pattern therefore never matches a real semver tag like `v1.2.3`.

```yaml
# Current (broken — reverted from the original v*.*.* glob)
tags:
  - 'v[0-9]+.[0-9]+.[0-9]+'
```

**Fix.**

```yaml
# Fixed — restore the original glob
tags:
  - 'v*.*.*'
```

---

### 10 · `.datavia/.gitignore` — two sources of truth for runtime gitignore rules

**Root cause.**  `DataviaConfig.ensure_directories()` already writes a
`.gitignore` into the computed base directory on first use (confirmed in
`config.py`).  Tracking a `.datavia/.gitignore` in the repository creates a
second source of truth and commits an application-state directory into the
repo.  The two files can drift independently.

**Fix (two steps):**

1. Delete `.datavia/.gitignore` and the now-empty `.datavia/` directory from
   the repository.
2. Add `.datavia/` to the root `.gitignore` so the runtime-generated directory
   is never accidentally committed:

```gitignore
# Datavia runtime directory — created automatically on first use
.datavia/
```

The `ensure_directories()` runtime path remains unchanged.

---

### 11 · `scripts/test_docs_with_io.py` — unconditional `.env` copy leaks secrets

**Root cause.**  `process_rst_files()` copies both `datavia.conf` and `.env`
into the temporary docs directory without any guard:

```python
for config_file in ["datavia.conf", ".env"]:
    src = project_root / config_file
    if src.exists():
        shutil.copy2(src, temp_docs_dir / config_file)
```

A `.env` file may contain database passwords or API keys.  Copying it into a
temp directory used by Sphinx doctests means those values are available to any
doctest that triggers environment-variable reading, and they could appear in
captured output, CI logs, or build artefacts.

**Fix.**  Guard the `.env` copy behind an explicit opt-in environment variable:

```python
copy_env = os.getenv("DATAVIA_DOCTEST_COPY_ENV", "0") == "1"

for config_file in ["datavia.conf"]:
    src = project_root / config_file
    if src.exists():
        shutil.copy2(src, temp_docs_dir / config_file)

if copy_env:
    env_src = project_root / ".env"
    if env_src.exists():
        shutil.copy2(env_src, temp_docs_dir / ".env")
```

Document in the script's module docstring that `.env` is not copied by default
and that `DATAVIA_DOCTEST_COPY_ENV=1` is required for live-credential doctest
runs.
