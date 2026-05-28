---
name: review
description: Focused PR review skill. Architectural fit, turn/action semantics, save-friendliness, plus help/observation/playtest-impact enforcement.
---

# /review - Review Skill

Use this to review a subagent PR. One review pass per dispatch, then exit.

## Focus

- Does the change fit the object model?
- Does it preserve turn/action semantics?
- Does it confuse UI commands with world intents?
- Does it mutate world state from rendering/input code?
- Does it introduce hidden callbacks or order-dependent side effects?
- Does it make future save/load harder?
- Does it make generated content harder?
- Does it duplicate existing abstractions?
- Are tests meaningful and small?
- Are edge cases covered?

## Specific Gotchas for This Project

- Explore mode vs turn-based mode must stay coherent.
- Voluntary turn-based mode must not break enemy-triggered combat.
- Party following outside combat must not corrupt individual positions in combat.
- Action, bonus action, movement, reaction, and extra action must be tracked distinctly.
- Spells should not become one-off special cases outside the action system.
- Terrain restrictions should be data-driven enough to scale.
- Traps/locks/doors/items/containers should exercise generic interaction mechanisms.
- NPC dialogue should stay simple but not be hardwired into rendering.
- Shop inventory and player inventory must remain separable.
- Saveable state must avoid raw object references that cannot serialize cleanly.
- Color/rendering should be projection, not game state.
- Vision/memory must be projection too; don't mutate world from render or LOS code.
- Conditions/durations must run through a generic timing/tick path, not ad-hoc per-effect timers.
- Targeting state must live in transient UI/runtime state, not in persistent world.

## Help / Observation / Playtest enforcement

Verify the PR body answers each:

- [ ] Player-facing command/key change? → `?` help and `docs/help/` updated.
- [ ] New mode/modal/screen? → help topic + observation `mode` field reflect it.
- [ ] New action resource or status visible to agents? → structured observation updated, tests cover.
- [ ] New subsystem? → playtest fixture added or follow-up `needs-playtest-fixture` issue filed.
- [ ] Debug/agent command added? → documented in help, ideally under a dev-only section.

If any of those are missing without a filed follow-up issue, flag it as a blocking concern.

## Agent-playtest specific checks

- Repeated-movement commands (e.g. `5j`, `10h`) interrupt safely on: combat start, modal open, blocked tile, newly visible hostile, low HP (when modeled).
- Structured observation does not require ANSI parsing.
- Deterministic seed support is not broken by the change.
- A playtest agent can produce a reproduction script for any behavior introduced.

## Review Output

- Approve / request changes.
- Top 3 concerns only unless severe (then list all blockers).
- Concrete suggested fixes (file:line where possible).
- Tests missing or weak.
- Architectural risk rating: low / medium / high.
- Help/observation/playtest status: clean / needs-followup / blocking.

Exit after one pass. The lead routes feedback to the author.
