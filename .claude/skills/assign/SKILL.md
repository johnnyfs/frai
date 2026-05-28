---
name: assign
description: Lead-agent skill for picking and dispatching subagent work. Maximizes safe parallelism, avoids dependency/conflict chaos, and keeps the standing playtester role staffed.
---

# /assign - Lead Work-Assignment Skill

Use this every time the lead refreshes the DAG, after every merge, and any time a slot opens. Keep running it until no unblocked unassigned work remains.

## Purpose

Help the lead agent choose which work to assign next so that:

- safe parallelism is maximized
- two agents do not collide on the same core files or abstractions
- foundations land before features that depend on them
- the standing playtester role is always staffed (once prerequisites exist)
- the human can always pull `main`, run the game, and play it

## Inputs to refresh every loop

1. `git fetch --all --prune` then `git log origin/main -10` for recent merges.
2. `gh pr list --state open --json number,title,headRefName,mergeable,reviewDecision` for open PRs.
3. `gh issue list --state open --limit 100 --json number,title,labels,body` for the work backlog.
4. `docs/roadmap.md` for the milestone DAG and statuses.
5. Outstanding `/playtest` bug reports filed against the current code.
6. The previous assignment list (which subagents are still running).

## Task status taxonomy

Tag every candidate task with one of:

- **blocked**: a hard dependency is still open
- **unblocked**: ready to be assigned
- **in-progress**: a subagent owns it on a branch
- **in-review**: a PR is open, reviewer dispatched or not
- **merged/done**
- **stale/needs-rebase**: PR is open but conflicts with current `main`

## "Unblocked" requires ALL of:

1. All declared dependencies are `merged/done` or explicitly waived in the issue.
2. The required abstractions/components exist (or this task is itself the abstraction).
3. The required tests/fixtures exist or are part of this task.
4. It does not collide on the same core files with another in-progress/in-review task.
5. It does not collide on save schema, input routing, or turn/action semantics with another in-progress task.

If any of those fail, the task is **blocked**. Record what blocks it.

## Priority rules

Within the unblocked pool, pick in this order:

1. **Critical playtest bug** filed against the current `main` (anything that prevents playing).
2. **Architectural foundations** that unblock many dependents (observation mode, skill checks, condition system, time/clocks, faction model).
3. **Test harness or fixture work** that unblocks playtest coverage.
4. **Help/observation drift** introduced by a recent merge.
5. **Systems that unblock many dependents** (vision/LOS before targeting before examine).
6. **Small vertical slices** over broad unfinished rewrites.
7. **High-risk timing/action/persistence work** before content fan-out depends on it.
8. **Content population** (NPCs, items, encounters, dungeon rooms) after schemas stabilize.

## Never assign in parallel

- Two tasks rewriting the same core action resolver.
- Two tasks changing save schema/migration in incompatible ways.
- Two tasks changing input routing or modal stack.
- Two tasks changing party/combat turn semantics.
- Two tasks touching the same large file (`src/app.py`, `src/core/components.py`, `src/core/world.py`) at the same time unless the second is explicitly trivial and reviewed for conflict risk.

## Safe to assign in parallel

- Content definitions (items, NPCs, monsters) after the schema landed.
- Isolated UI modals after the modal framework stabilizes.
- Distinct AI behaviors after the AI interface stabilizes.
- Tests, fixtures, docs, help text.
- Terrain/content population after the terrain model stabilizes.
- The standing `/playtest` agent — it does not block feature work unless it finds a serious regression.

## Assignment record (write one of these per dispatch)

```
Issue:        #<N> <title>
Subagent:     <handle/role>
Branch:       agent/m<NN>-<slug>
Worktree:     /Users/jschmidt/lab/frai-m<NN>-<slug>
Skill:        /spec | /implement | /review | /playtest
Touches:      <files / areas / abstractions>
Conflicts:    <which open work this collides with — none if safe>
Validation:   uv run pytest [-k pattern]
Reviewer:     <queued / focus area>
Help update?  yes/no/n-a
Observation update? yes/no/n-a
Playtest fixture impact? yes/no/n-a
```

## Loop rules

1. Refresh the inputs.
2. Mark every candidate.
3. If `main` is broken, no new feature work is assigned — only the fix and a `/playtest` smoke pass.
4. If a `/playtest` agent is not currently running and the prerequisites exist (observation mode + command scripting + at least one fixture), assign one.
5. Assign every safe unblocked task up to the parallelism budget. Default budget: 3 implementation agents + 1 playtester + reviewers on demand.
6. If nothing is assignable, explain *exactly* why and create unblock issues for the missing pieces. Never stop because "enough is in progress."
7. After every PR merge, re-run this skill before declaring idle.

## Standing roles

- **Lead**: orchestration only; reviews PRs, merges, keeps `main` playable.
- **Implementation agents**: dispatch on `/implement` against a specific issue.
- **Reviewer agents**: dispatched per PR with `/review`; exit after one review pass.
- **Playtest agent**: one at a time, dispatched with `/playtest`; rotates targets after each report.

## "Always playable" rule

The lead must hold `main` to the bar:

- `uv run pytest` is green.
- `uv run python -m src.main` launches and reaches the start screen without crashing.
- The most recent merged feature is reachable from a normal game start (or via a documented debug command).

If a merge breaks any of those, the lead's next assignment is the revert/fix — not new feature work.
