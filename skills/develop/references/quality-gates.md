# Quality gates

Run everything from the **datavia repository root** with the repo's own pixi
environment (`pixi run -e dev ...`). Other environments (e.g. a parent
workspace with datavia installed from Git) don't see your local edits. If
the plan adds a package or extra, run `pixi install` first.

## The gates

| # | Gate | Command | Clean means |
|---|------|---------|-------------|
| 1 | Format | `pixi run -e dev ruff format <changed .py files>`, then `pixi run -e dev format-check` | `format-check` reports nothing |
| 2 | Lint | `pixi run -e dev lint` | `All checks passed!` |
| 3 | Types, core | `pixi run -e dev type-check` | `Success: no issues found` |
| 3b | Types, packages | `pixi run -e dev mypy packages/<name>/datavia/<name> --ignore-missing-imports` for **each** package the plan touches | no errors |
| 4 | Security | `pixi run -e dev security-scan`, then read `results` in `bandit-report.json` | no findings |
| 5 | Tests | `pixi run -e dev test-light` | all pass; skips only for gated e2e / optional-dependency tests |
| 6 | Coverage | see below | ≥ 90 % per new or changed module, uncovered lines justified |
| 7 | Docs | `pixi run -e dev docs` | build succeeds (it runs with `-W`, so any warning fails) |
| 7b | Doctests | `pixi run -e dev test-docs-verbose` | all doc examples pass |
| 8 | E2E (network, optional) | `DATAVIA_E2E=1 pixi run -e dev pytest <e2e test files> --no-cov` | passes; if the network or credentials are unavailable, report it as not run |

Format only the files you changed (gate 1). `pixi run format` would rewrite
every file in `datavia/ tests/ packages/`, including files outside the plan.

`pixi run -e dev ci-check` runs gates 1 (check only), 2, 3 and 5 together:
use it as a final confirmation, not instead of the individual gates.

## Baseline: find errors before you change anything

Before the first edit (develop step 3), run gates 1 (check only), 2, 3, 3b,
4, 5, 7 and 7b once on the untouched tree and save each output:

```bash
BASE=$(python3 skills/develop/scripts/checkpoint.py path)/baseline
mkdir -p "$BASE"
pixi run -e dev format-check > "$BASE/format.txt" 2>&1
pixi run -e dev lint         > "$BASE/lint.txt" 2>&1
# ... one file per gate
```

Every failure in the baseline is a **pre-existing error**. Summarise them
(gate, file, count, first lines) and ask the user **once**, before coding
(develop step 3). If you are Claude Code, use AskUserQuestion:

- **Continue without resolving them**: they are recorded as accepted and
  excluded from "clean" for this plan;
- **Fix them**: add the affected files and symbols to the plan (this is a
  plan change, so the plan inventory is updated and re-approved);
- **Fix some**: the user names which.

Record the answer in the plan's *Decisions* section. Don't ask again about
errors the user has already decided on.

## Coverage (gate 6)

`pytest` in this repo always adds `--cov=datavia` (see `addopts`). Measure the
changed modules explicitly with the network-free tests:

```bash
pixi run -e dev pytest <relevant test files> \
    --cov=<dotted.module.one> --cov=<dotted.module.two> --cov-report=term-missing
```

- Every **new or changed module** reaches **≥ 90 %** line coverage without
  `DATAVIA_E2E`.
- Every line still listed under `Missing` needs a reason in the final report
  (e.g. "network-only branch, covered by the gated e2e test"). Prefer adding
  a test with a fake payload over writing a reason.
- Don't game the number: no `# pragma: no cover` without a reason, no tests
  that only execute lines without asserting behaviour.
- A module that was already below 90 % before the change: at least the
  changed lines are covered; ask the user whether to raise the rest (treat it
  like a pre-existing error).

## The loop

```text
for gate in 1..8:
    run gate
    for each failure:
        introduced by this change?  → fix the cause (not the symptom), rerun
        in the baseline and accepted? → ignore, keep it in the report
        pre-existing but not in the baseline (e.g. a gate the baseline skipped,
        or hidden behind an earlier failure)?
                                      → stop and ask the user, as for the baseline
if any file changed after gate 1: run the whole sequence again
done when one full pass from gate 1 to 7b has no failures except accepted ones
```

To tell whether a failure is pre-existing, compare with the baseline output.
If that's unclear, check the original file version (`checkpoint.py diff`, or
`git stash` + rerun + `git stash pop` when the working tree held no
pre-existing user changes).

Hard rules:

- Never silence a finding without a written reason in the code (`noqa`,
  `type: ignore[code]`, `nosec`, `pytest.mark.skip`, `pragma: no cover`).
- Never lower thresholds or edit tool configuration in `pyproject.toml` to
  make a gate pass, unless that change is in the approved plan.
- Never weaken or delete an existing test to make it pass. If a test fails
  because behaviour intentionally changed, the test change must be in the
  plan; otherwise it's a scope change (develop step 4).
- Fixing a gate failure in a file or symbol that isn't in the plan is a
  scope change: follow the scope guard (develop step 4).

## Report

From the last clean pass keep the command and result line of each gate, the
per-module coverage with `Missing` lines and their reasons, the accepted
pre-existing errors, and whether the e2e tests ran.
