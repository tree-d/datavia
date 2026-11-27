# Coding-standards checklist

The project standard is `docs/development/coding-standards.md`; this list
makes its code-relevant items concrete for datavia (section and AC level in
brackets) and adds the conventions the repo's tooling and reviewers enforce.

It is used twice:

1. **Before planning and writing code** (develop step 1): read it so the
   plan and the code meet it from the start, and so you recognise violations
   in the code you read.
2. **In the review** (develop step 6): go through every item against all new
   and changed code and record one verdict each:
   - **pass**: checked, conforms;
   - **fixed**: did not conform, now fixed (say what);
   - **N/A**: does not apply (say why).

Task skills may add their own checklist (e.g.
`skills/add-data-source/references/pipeline-contract-checklist.md`); review
those items the same way.

Tools catch part of this (ruff `N`, `D10x`, `E/W/F/B/SIM/UP/RUF/PLC/PLW`;
mypy strict defs), but a passing linter is not a passing review: ruff checks
only that public docstrings *exist*, not what they say, and the `type-check`
task does not scan `packages/`.

## 1. Naming [Naming Conventions]

- [ ] 1.1 Names are self-descriptive first, pronounceable second, brief third. [AC1+]
- [ ] 1.2 English only; words in grammatical order (`download_layer`, not `layer_download_do`). [AC1+]
- [ ] 1.3 PEP 8 casing: `snake_case` functions, variables and modules,
      `CapWords` classes, `UPPER_SNAKE` constants. [AC1+]
- [ ] 1.4 Abbreviations only where they are community consensus and
      unambiguous (`crs`, `bbox`, `dem`, `nc` are fine; `tmpv`, `dl_res` are not). [AC1+]
- [ ] 1.5 No numbers, meaningless suffixes (`_new`, `_2`, `_final`) or
      developer names in identifiers. [AC1+]
- [ ] 1.6 Access is signalled by naming: internal helpers, constants and
      attributes start with `_`; only the intended API is public. [AC2+]

## 2. Formatting [Formatting]

- [ ] 2.1 `ruff format` leaves the code unchanged (line length 88, double
      quotes, spaces). [AC1+]
- [ ] 2.2 Indentation, line breaks and operator spacing are consistent; no
      manual alignment that the formatter would undo. [AC1+]
- [ ] 2.3 Imports sorted (ruff `I`); no unused imports or variables. [AC1+]

## 3. Code structure [Code Structure]

- [ ] 3.1 No top-level logic; modules only define constants, classes and
      functions (scripts keep logic in `main()`). [AC1+]
- [ ] 3.2 Each function does one task; longer flows are split into helpers
      that form a readable call hierarchy. [AC1+]
- [ ] 3.3 No duplicated logic: existing helpers in `datavia/core/`,
      `datavia/library/` and the packages are reused where they fit;
      repetition within the change is factored out. [AC1+]
- [ ] 3.4 No non-constant module globals (no module-level caches, sessions,
      DataFrames, mutable defaults). Constants are truly constant and typed
      (`_BASE_URL: str = ...`, `frozenset` for fixed sets). [AC2+]
- [ ] 3.5 State lives on objects (config on the pipeline, request parameters
      on the downloader), not in loose variables passed around. [AC2+]
- [ ] 3.6 No redundant derived state that can go out of sync; derived values
      are computed, or private and updated together. [AC2+]
- [ ] 3.7 Self-contained functionality sits in its own module / package. [AC2+]
- [ ] 3.8 Only the necessary API is exposed: `__all__` lists the public
      names; everything else is `_private`. [AC3 Libraries]

## 4. Typing, errors, logging

- [ ] 4.1 Every function and method, private ones and tests included, has
      full type hints (mypy `disallow_untyped_defs`).
- [ ] 4.2 No bare `except:` or broad `except Exception:` that hides bugs;
      catch the specific exceptions the call can raise.
- [ ] 4.3 Invalid input raises a clear `ValueError` / `TypeError` naming the
      offending value; typical use returns the expected result. [Testing AC2+]
- [ ] 4.4 `logging` (`logger = logging.getLogger(__name__)`), never `print`,
      in library code.
- [ ] 4.5 Every `noqa`, `type: ignore`, `nosec` and `pragma: no cover` has a
      specific code and a comment saying why.
- [ ] 4.6 No credentials, tokens or personal data in code, logs, exceptions
      or test fixtures.

## 5. Documentation in code [Documentation]

- [ ] 5.1 Code is self-explaining; non-obvious lines (quirks, unit factors,
      time offsets, magic numbers) have a short comment saying *why*. [AC1+]
- [ ] 5.2 No comments that restate the code. [AC1+]
- [ ] 5.3 File names say what the module does. [AC1+]
- [ ] 5.4 Every module, class, function and method (public **and** private)
      has a NumPy-style docstring: what it does, every non-obvious parameter,
      the return value, and `Raises` for every exception it raises. [AC3]
- [ ] 5.5 Docstrings state units, CRS and array shapes where relevant;
      module constants have a `#:` comment.
- [ ] 5.6 No measured run times or speed promises in docstrings or docs;
      describe what drives cost (data volume, number of requests, area,
      date range).
- [ ] 5.7 Docstrings render under Sphinx `-W` (valid RST: blank lines before
      lists, matching backticks, correct section underlines).

## 6. Testing and testability [Testing & Testability]

- [ ] 6.1 Functions can be called without hidden context and don't modify
      globals. [AC2+]
- [ ] 6.2 New and changed logic is covered by unit tests without network;
      error paths are tested, not just the happy path. [AC3+]
- [ ] 6.3 Anything that needs the network is gated by `DATAVIA_E2E=1`.
- [ ] 6.4 Tests are deterministic: no real network, no wall-clock or random
      dependence without a fixed seed; temp files under `tmp_path`; the DB
      via the `sqlite_db` fixture.
- [ ] 6.5 Tests follow the style of the existing suite: a module docstring
      listing what is covered, descriptive test names, assertions on
      behaviour (not just "runs without error").

## 7. Interoperability and packaging [Interoperability]

- [ ] 7.1 Data is stored in standardized open formats (GeoTIFF, NetCDF,
      Zarr, Parquet). [AC2+]
- [ ] 7.2 New dependencies are declared in the right `pyproject.toml`
      (core vs package vs extra) and heavy or credentialled ones are optional
      and imported lazily. [AC2+]
- [ ] 7.3 Public API changes are documented (`docs/api/index.rst`, user
      guide) and versioning is respected. [AC3]

## 8. Version control [Version Control]

- [ ] 8.1 No generated files (data, `.tif`, `.nc`, caches, coverage output,
      `bandit-report.json`) added to Git; `.gitignore` covers any new kind. [AC2+]
- [ ] 8.2 `docs/changelog.rst` entry under *Unreleased* for user-visible
      changes. [AC3]
- [ ] 8.3 Commit only when the user asks: one topic per commit, English
      present-tense headline, body says *why*. [AC2+]
