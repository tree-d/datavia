# Agent skills

Step-by-step workflows for coding agents working on datavia. Each skill is a
folder with a `SKILL.md` (frontmatter + instructions) and optional
`references/` and `scripts/` files that the skill tells the agent to use at
the right step.

| Skill | Use it to |
|-------|-----------|
| [`develop`](develop/SKILL.md) | **Every change** to code, tests, config or docs. No code without an approved plan that lists every class, function and file touched. The agent reads the coding standards before planning, asks about standards violations and pre-existing errors it finds, stops at unplanned edits (offering to extend the plan, or to roll back and re-plan), reviews every standards item, and runs lint/type/security/coverage/test/docs gates until clean. |
| [`add-data-source`](add-data-source/SKILL.md) | Integrate a new data source, provider, dataset or variable end to end: triage → API lookup → plan → code → standards review → docs → quality gates. Builds on `develop`. |
| [`research-data-source`](research-data-source/SKILL.md) | Find and evaluate a source when it isn't clear which one to use, and probe the provider's API with a throwaway Python script. Writes a research brief; never edits the repo. |

`develop/scripts/checkpoint.py` saves the original of every file before the
agent first edits it (in `.git/dev-checkpoint/`), so an unplanned change can
be rolled back exactly, without losing your own uncommitted work:

```bash
python3 skills/develop/scripts/checkpoint.py list      # what the agent touched
python3 skills/develop/scripts/checkpoint.py diff      # its changes so far
python3 skills/develop/scripts/checkpoint.py restore   # undo them all
```

## Using them

- **Claude Code** discovers the skills through the symlink
  `.claude/skills -> ../skills`, and the repository's `CLAUDE.md` makes
  `develop` apply to every change. Invoke them as `/add-data-source <what to
  add>`, `/research-data-source <data need>`, or just describe the task.
- **Other agents**: point the agent at `CLAUDE.md` or the `SKILL.md` file,
  e.g. "Follow `skills/add-data-source/SKILL.md` to add …".

The skills build on the developer docs and don't replace them:
[`docs/development/adding-a-pipeline.md`](../docs/development/adding-a-pipeline.md)
and [`docs/development/coding-standards.md`](../docs/development/coding-standards.md).
When those change, check that the skills' references still match.
