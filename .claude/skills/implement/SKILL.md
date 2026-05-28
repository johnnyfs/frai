---
name: implement
description: Implementation workflow for milestone/subtask authors. Required steps, validation, and the help/observation/playtest update checklist enforced at review.
---

# /implement - Implementation Skill

Use this when assigned a milestone or subtask.

## Required Steps

1. Read the relevant code and the issue body in full.
2. Read current specs/docs (`docs/roadmap.md`, `docs/help/`, related skills).
3. State the smallest coherent implementation plan.
4. Identify dependencies and possible conflicts with other open PRs/work.
5. Create a worktree on a fresh branch off current `origin/main`:
   - `git fetch origin main`
   - `git worktree add /Users/jschmidt/lab/frai-m<NN>-<slug> -b agent/m<NN>-<slug> origin/main`
6. Implement in small commits.
7. Add or update tests.
8. **Update help** if you changed any player-facing command, key, mode, modal, or feature semantics (see Help/Observation/Playtest Checklist below).
9. **Update structured observation** output if you changed state that an agent playtester should see.
10. **Add or update a playtest fixture** if you added a new system/feature that benefits from end-to-end exercise.
11. Run validation (`uv run pytest`, launch app if UI-touching).
12. Push branch.
13. Open PR with:
    - summary
    - design notes
    - tests run (count and any new test names)
    - **help impact**: yes/no/n-a with file list
    - **observation impact**: yes/no/n-a with what changed
    - **playtest fixture impact**: yes/no/n-a
    - known limitations
    - follow-up work
14. Request lead review.

## Implementation Standards

- Keep world logic independent from terminal rendering.
- Keep input mapping separate from action resolution.
- Keep saveable state explicit (no raw object references; stable IDs; defaults for new fields).
- Do not bury core game rules inside UI code.
- Do not use arbitrary scripting for content where typed config is enough.
- Avoid large "manager" classes that own unrelated concerns.
- Prefer small pure functions for rules calculations.
- Prefer deterministic tests with seeded RNG.
- Use fixtures/builders for tiny worlds, tiny parties, tiny encounters.
- Add regression tests for every bug discovered (including playtest bugs you are fixing).

## Help/Observation/Playtest Checklist

Before opening the PR, answer each:

- [ ] If a player-facing **command/key** changed: `docs/help/` and the `?` topic body updated.
- [ ] If a **mode/modal** was added or renamed: `?` topics + observation `mode` field updated.
- [ ] If a new **action resource** (action/bonus action/reaction/spell slot) appeared: observation summary includes it.
- [ ] If a new **player-visible state** appeared (status effect, condition, faction, light level): observation reports it.
- [ ] If a new **subsystem** landed: at least one playtest fixture exercises it, or a follow-up issue is filed.
- [ ] If a **debug/agent** command was added: documented in help under a debug section.

PRs that don't pass this checklist either include the updates or file a follow-up `needs-help-update` issue. Reviewers enforce this.

## Validation Checklist

- Unit tests pass.
- Relevant integration tests pass.
- Existing gameplay path still works.
- `uv run python -m src.main` launches and reaches the start screen.
- No obvious circular imports.
- No terminal-only code in core rule tests.
- No untyped ad hoc dicts for important domain concepts unless deliberately transitional.
- If adding persisted fields, defaults/backward-compatibility are considered.

## Worktree etiquette

- One worktree per branch.
- Clean up worktrees when the PR merges: `git worktree remove <path>`.
- Never push to `main` directly. Always PR.
- Always rebase on current `main` before requesting review.
- Use `git push --force-with-lease`, not `--force`.
