# Working on datavia

Every change to code, tests, configuration or documentation in this
repository follows `skills/develop/SKILL.md`. In short:

- **No code without an approved plan.** If there is none, enter plan mode and
  create one with the user. The plan lists every class, function and method
  added, changed or deleted, and every non-code file (with its section).
- **Read `docs/development/coding-standards.md` and
  `skills/develop/references/standards-checklist.md` before planning or
  writing code.** If the code you read violates them, ask whether to add a
  fix to the plan.
- **Errors that existed before your change** (lint, types, tests, docs):
  ask once whether to continue without resolving them or to fix them, and
  record the answer.
- **Scope guard.** Before editing anything the plan doesn't list, stop,
  explain why, and offer to extend the plan or to roll back
  (`python3 skills/develop/scripts/checkpoint.py restore`) and return to plan
  mode.
- **Done** means: every standards item reviewed, all quality gates clean
  (≥ 90 % coverage on new or changed modules), docs updated.

Task skills: `skills/add-data-source/SKILL.md` (new data sources),
`skills/research-data-source/SKILL.md` (find a source, probe its API).

Run tools from this directory with this repo's pixi environment:
`pixi run -e dev <task>`.
