# /implement - Implementation Skill

Use this when assigned a milestone or subtask.

## Required Steps

1. Read the relevant code.
2. Read current specs/docs if present.
3. State the smallest coherent implementation plan.
4. Identify dependencies and possible conflicts with other work.
5. Implement in small commits.
6. Add or update tests.
7. Run validation.
8. Push branch.
9. Open PR with:
   - summary
   - design notes
   - tests run
   - known limitations
   - follow-up work
10. Request lead review.

## Implementation Standards

- Keep world logic independent from terminal rendering.
- Keep input mapping separate from action resolution.
- Keep saveable state explicit.
- Do not bury core game rules inside UI code.
- Do not use arbitrary scripting for content where typed config is enough.
- Avoid large "manager" classes that own unrelated concerns.
- Prefer small pure functions for rules calculations.
- Prefer deterministic tests.
- Use fixtures/builders for tiny worlds, tiny parties, tiny encounters.
- Add regression tests for every bug discovered.

## Validation Checklist

- Unit tests pass.
- Relevant integration tests pass.
- Existing gameplay path still works.
- No obvious circular imports.
- No terminal-only code in core rule tests.
- No untyped ad hoc dicts for important domain concepts unless deliberately transitional.
- If adding persisted fields, defaults/backward-compatibility are considered.
