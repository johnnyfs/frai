# /review - Review Skill

Use this to review a subagent PR.

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
- Traps/locks/doors/items should exercise generic interaction mechanisms.
- NPC dialogue should stay simple but not be hardwired into rendering.
- Shop inventory and player inventory must remain separable.
- Saveable state must avoid raw object references that cannot serialize cleanly.
- Color/rendering should be projection, not game state.

## Review Output

- Approve / request changes.
- Top 3 concerns only unless severe.
- Concrete suggested fixes.
- Tests missing.
- Architectural risk rating: low / medium / high.
