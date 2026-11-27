---
name: develop
description: Mandatory workflow for any change to datavia's code, tests, configuration or documentation — features, fixes, refactors, new data sources. Enforces plan-first (no code without an approved plan that lists every class, function and file touched), reading the coding standards before writing code, asking about standards violations and pre-existing errors found along the way, a scope guard with rollback for unplanned edits, a standards review, and lint/type/security/coverage/test/docs gates until clean.
---

# Develop in datavia

This skill applies to **every** change to files in this repository. Task
skills such as `add-data-source` build on it and add their own steps.

Two rules hold throughout:

- **Ask, don't assume.** Whenever this skill says "ask", ask the user and
  wait (Claude Code: AskUserQuestion, with up to four related items batched
  per question). Record every answer in the plan's *Decisions* section and
  never ask the same question twice.
- **Report, don't hide.** Anything skipped, accepted or not verified goes in
  the final report.

---

## Step 0: Plan gate (no code without a plan)

Before writing or editing **any** file in the repository, check that there is
a plan for this change that the user has approved in this session (approved
in plan mode, or a plan file the user pointed to and confirmed).

If there isn't one: **don't write code.**

- Claude Code: enter plan mode (EnterPlanMode) and tell the user you are
  doing so to create a plan with them.
- Other agents: tell the user that a plan is needed first and propose to
  draft one together; wait for their go-ahead.

Read-only work is allowed without a plan: reading files, searching, running
the existing test suite or gates, research and API probes in a temp
directory outside the repo.

The user may explicitly waive planning for a change ("just do it, no plan").
Record that, and still apply steps 1, 4 (with the inventory being exactly
what the user asked for), 5, 6 and 7.

## Step 1: Read the standards before planning and coding

Before drafting the plan, and in any case before the first line of code,
read:

1. `docs/development/coding-standards.md`;
2. `references/standards-checklist.md` (the concrete datavia version);
3. any task checklist the calling skill names (e.g.
   `skills/add-data-source/references/pipeline-contract-checklist.md`);
4. the existing code you will change or imitate, and its tests.

Design the plan so the new code meets every item from the start: names,
structure, docstrings, type hints, tests. The review in step 5 should find
nothing new.

**Violations in existing code.** While reading, note any code that doesn't
meet the standards (missing docstrings or type hints, broad `except`,
`print` in library code, duplicated logic, misleading names, ...). Collect
them with file, symbol and the checklist item, then ask the user, per item
or batched: *add a fix to this plan*, *leave it*, or *note it as a separate
task*. Ask during planning when possible. If you find more later (step 4),
ask then; a "yes" there is a plan change and goes through the scope guard.

## Step 2: Plan with the user

Write the plan using `references/plan-template.md`. It must contain:

- context and approach, including which existing helpers are reused;
- the **change inventory**: every class, function and method to be
  **added, changed or deleted**, and every non-code file (docs, config, CI)
  with the section that changes;
- the decisions taken so far (standards violations, scope);
- the verification: which gates apply, coverage targets, e2e checks.

Get the user's approval (Claude Code: ExitPlanMode). A plan without a
complete inventory isn't ready for approval.

## Step 3: Baseline and checkpoint (before the first edit)

1. **Checkpoint**, so the change can be rolled back exactly:

   ```bash
   python3 skills/develop/scripts/checkpoint.py start
   python3 skills/develop/scripts/checkpoint.py save <every file in the inventory>
   ```

   `save` copies existing files and records new ones. It keeps the user's
   own uncommitted changes intact on restore, unlike `git checkout`. Files
   saved later are recorded the first time they are saved; later saves of
   the same file are no-ops.

2. **Baseline gates**: run the gates on the untouched tree as described in
   `references/quality-gates.md` (*Baseline*) and save their output.

3. **Pre-existing errors**: if the baseline has any failure, list them
   compactly and ask the user, once: *continue without resolving them*, or
   *fix them* (all or some; the fixes are added to the inventory and the plan
   is re-approved). Record the decision.

## Step 4: Implement within the plan (scope guard)

Rules while editing:

- **Before the first edit of any file**, run
  `checkpoint.py save <file>`; also for new files and for files added to the
  plan later.
- Write code to the standards from step 1 as you go; write the tests with
  the code, not after.
- Only touch what the inventory lists. Formatting changes from `ruff format`
  to inventory files are fine; don't run formatters or fixers repo-wide.

**Scope guard.** If you find you need to add, change or delete a class,
function or method, or edit a file or doc section, that the inventory does
**not** list, **stop before editing it** and tell the user:

- what you want to change (file, symbol);
- why it's needed (what fails or is missing without it);
- what you have changed so far (`checkpoint.py list`).

Then ask, offering:

1. **Add it to the plan and continue.** Update the inventory and
   *Decisions*, save the file to the checkpoint, go on.
2. **Roll back and return to plan mode.** Run `checkpoint.py restore` (the
   working tree returns to the pre-plan state, the user's own changes
   included), enter plan mode (Claude Code: EnterPlanMode), and revise the
   plan with the user.
3. **Do it differently.** The user proposes an alternative within the
   current plan.

This applies equally to fixes needed to make a gate pass (step 6) and to
standards violations found while implementing.

## Step 5: Standards review, every point

Go through `references/standards-checklist.md` and any task checklist **item
by item** against every new and changed file (`checkpoint.py diff`, new
files included). Record **pass**, **fixed** (what changed) or **N/A** (why)
for each item. An item without a verdict is a failed review. Fixes must stay
within the inventory (scope guard otherwise).

## Step 6: Quality gates, iterate until clean

Run the gates in `references/quality-gates.md` and loop until one full pass
is clean apart from pre-existing errors the user accepted. Coverage: ≥ 90 %
for every new or changed module, with a reason for every uncovered line.

When a failure shows up that is **not** caused by this change and wasn't in
the baseline, stop and ask the user as in step 3 (continue without resolving,
or fix it via a plan change). Don't ask again about errors already decided.

## Step 7: Report and close

Tell the user:

- what changed, matched against the inventory (and any approved scope
  changes);
- the standards review: the fixed and N/A items, and the count of passes;
- the gate results of the last clean pass, per-module coverage with reasons
  for uncovered lines, accepted pre-existing errors, e2e run or not;
- open questions and follow-up tasks (e.g. standards violations noted as
  separate tasks).

Keep the checkpoint until the user accepts the result, so a rollback is
still possible; then run `checkpoint.py clear`. Don't commit unless asked.
