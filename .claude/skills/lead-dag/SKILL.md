---
name: lead-dag
description: Lead-agent milestone DAG and coordination workflow. Owns assignment loop, review pipeline, main-always-playable invariant, and standing playtester role. Continues until all unblocked work is assigned and completed.
---

# /lead-dag - Lead Agent Coordination Skill

Use this continuously. The lead is responsible for keeping work moving until every unblocked task on the DAG is assigned, reviewed, merged, and verified — not just until the next playable slice exists.

## Standing invariants

1. **`main` is always playable.** `uv run pytest` is green and `uv run python -m src.main` reaches the start screen without crashing. If either breaks after a merge, the next assignment is the fix.
2. **`main` checkout is current.** Before reviewing, merging, or assigning, the lead has run `git fetch --all --prune` and `git pull` on `main`.
3. **One `/playtest` agent runs at all times** once the agentic-playtest prerequisites exist (see `/playtest`). The playtester rotates targets but is never left empty.
4. **Help and observation are kept current.** Any merged PR that changed player-facing commands, modes, or state-visible-to-agents must have updated `?` help and the structured observation output. Reviewers enforce this.

## Inputs to refresh every loop

- `git log origin/main -10`
- `gh pr list --state open --json number,title,headRefName,mergeable,reviewDecision`
- `gh issue list --state open --limit 100`
- `docs/roadmap.md`
- Outstanding playtest reports (filter open issues by `playtest` label)

## Lead loop

1. Refresh repo, issues, PRs, roadmap.
2. Verify `main` invariants (tests green, app launches).
3. Run `/assign` — generate the next batch of dispatches.
4. For each assigned task, spawn a subagent with `/implement` (or `/spec` if design is fuzzy). Each subagent works in its own worktree on its own branch.
5. Ensure one `/playtest` agent is active if prerequisites are met. Do not duplicate.
6. For each open PR:
   - Spawn a reviewer subagent with `/review` if not yet reviewed.
   - Return reviewer findings to the author.
   - Author decides what to apply, force-pushes, re-requests review if needed.
7. Merge a PR only when:
   - Reviewer approved or all concerns explicitly resolved.
   - PR rebased on current `main`.
   - `uv run pytest` green on the merged result.
   - Help/observation/playtest updates included as required by the change.
8. After every merge:
   - `git pull` on `main`.
   - Update issue status (close, link).
   - Update `docs/roadmap.md`.
   - Trigger a `/playtest` smoke pass against the merged change.
   - Re-run `/assign` for newly unblocked work.
9. Triage every playtest bug:
   - Duplicate? Close with link.
   - Real bug? Convert into a DAG task with priority.
   - Improvement? Label `enhancement, playtest`, queue.
   - Dependencies? Note in body.
10. Continue until either:
    - Every issue and roadmap item is `merged/done`, **or**
    - All remaining items are explicitly blocked and the lead has filed unblock-tasks for the missing pieces.

## "Don't stop" rule

The lead does **not** stop after one PR, one milestone, or one playable slice. The lead keeps scheduling work as capacity opens. If subagents finish faster than the lead reviews, scale the reviewer pool, not the assignment cadence.

If literally nothing is assignable, the lead documents exactly which dependency chain is the bottleneck and files the unblock task — then either runs `/playtest` themselves or returns control to the human with a clear status.

## Conflict & coordination rules (delegated to `/assign`, summarized here)

- Never assign two tasks to the same core file (`src/app.py`, `src/core/components.py`, `src/core/world.py`) at once.
- Never assign two tasks changing save schema, input routing, or turn semantics in parallel.
- Content/UI/AI population work parallelizes safely once schemas stabilize.
- The standing `/playtest` agent does not count against parallelism budgets.

## PR merge protocol

For each PR being landed:

1. `git fetch && git checkout main && git pull`.
2. Confirm PR is rebased on current `main` (`gh pr view <N> --json mergeable`).
3. Spawn reviewer (`/review`) if not already done.
4. After reviewer + author exchange, validate locally:
   - Check out the PR branch into a test branch.
   - `uv run pytest`.
   - `uv run python -m src.main` reaches the start screen.
5. `gh pr merge <N> --squash --delete-branch`.
6. `git pull` to bring the merge into local `main`.
7. Update `docs/roadmap.md` status for the milestone.
8. Re-run `/assign`.

## Help & observation enforcement

Every implementation PR is checked for:

- Did this change a player-facing command, key, modal, or mode? → `?` help updated?
- Did this change the structured observation output? → tests updated?
- Did this add a new feature? → playtest fixture added or updated?
- Did this add a debug/agentic-playtest command? → documented in help (under a debug section if dev-only)?

Reviewers flag missing updates. The lead does not merge PRs that fail this check unless the author files a follow-up issue and labels it `needs-help-update`.

## Standing roles to staff

- **Lead**: orchestration only; reviews PRs, merges, keeps `main` playable.
- **Implementation agents**: dispatched on `/implement` against a specific issue, one issue at a time.
- **Reviewer agents**: dispatched per PR with `/review`; exit after one review pass.
- **Playtest agent**: exactly one at a time, dispatched with `/playtest`; rotates targets after each report.

## Milestone DAG

See `docs/roadmap.md` for the canonical DAG. Update it after every merge. The DAG is data, this skill is the loop.

## Prompt correction (preserved from prior version)

Do not assign "all classes/races" and "full SRD fidelity" before abstractions are cleaned. The DAG must force minimal valid representation for all classes/races first, then deepen mechanics class by class only after action economy, spells, and effects are stable.
