# Plan: <title>

## Context

<Why the change is made, what prompted it, the intended outcome.>

## Approach

<The chosen approach in a few paragraphs; alternatives only if the user asked.
Mention the existing functions / classes that will be reused, with paths.>

## Change inventory

Every class, function and method that will be **added**, **changed** or
**deleted**, and every non-code file (docs, config, CI, data) with the section
that changes. During execution, anything not listed here is out of scope
(develop step 4: scope guard).

| File | Symbol / section | Action | What and why |
|------|------------------|--------|--------------|
| `packages/<pkg>/datavia/<pkg>/<module>.py` | module (new) | add | ... |
| `packages/<pkg>/datavia/<pkg>/<module>.py` | `class <Name>Downloader` | add | ... |
| `packages/<pkg>/datavia/<pkg>/<module>.py` | `<Name>Downloader.download` | add | ... |
| `datavia/core/<module>.py` | `<function>` | change | ... |
| `datavia/core/<module>.py` | `<OldClass>` | delete | ... |
| `tests/test_<x>.py` | tests for `<Name>Downloader.download`: success, HTTP error → `"failed"`, empty payload | add | ... |
| `pyproject.toml` | `[project.optional-dependencies]` `<name>`, `pipelines` | change | ... |
| `docs/user_guide/variables.rst` | *Pipelines* table, *Variables → <Section>* | change | ... |
| `docs/changelog.rst` | *Unreleased → Added* | change | ... |

Rules:

- Name methods as `Class.method`; list module-level constants that change.
- Tests: list each test file with the behaviours (or test names) it covers.
  Additional test cases for those listed behaviours are within scope.
- Generated files (`bandit-report.json`, coverage output, Sphinx build) are
  not listed and must not be committed.

## Decisions

Answers the user gave that must not be asked again:

- Pre-existing errors: <continue without resolving / fix: ... / none found>
- Standards violations found in existing code: <item → fix in this plan / leave / separate task>
- Scope changes approved during execution: <file / symbol → reason>

## Verification

<Gates from `skills/develop/references/quality-gates.md` that apply, the
coverage targets per module, and any manual or e2e checks.>
