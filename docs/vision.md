# Vision, LOS, and Party Memory (M19)

This document captures the M19 design decisions so later milestones
(Targeting, Examine, Stealth, Auto-walk) plug into the same seam without
re-reading the implementation.

## Data model

- `VisibilityState` (`src/core/vision.py`): `unknown` | `remembered` | `visible`.
- `LightLevel` (`src/core/vision.py`): `lit` | `dark`. Two states for now.
- `RememberedTile`: per-coordinate snapshot of the last-seen tile glyph
  and the static features (doors, containers, revealed traps) that were
  on it when the party last looked.
- `PartyMemory`: a `dict[(x, y) -> RememberedTile]` plus a
  `frozenset[(x, y)]` for the current visible set. Lives on `App`
  (`app.memory`).

Live actors and creatures are *not* memorised. They appear only when
their tile is in the visible set. Memory is for layout, not live
positions.

## LOS algorithm

We chose **Bresenham-line LOS per candidate tile** (see
`compute_visible_tiles` in `src/core/vision.py`). For each cell within
`radius` (default 10) of the observer:

1. Skip cells outside the world bounds.
2. Skip cells outside the circular radius.
3. Walk the integer Bresenham line from the observer to the target.
4. Reject the cell if any intermediate cell satisfies `blocks_sight`.
5. The target cell itself is *not* tested for blocking, so wall tiles at
   the edge of LOS render as visible (you can see the wall).

`blocks_sight` flags:

- Out-of-bounds cells.
- Terrain that blocks movement (walls, water).
- Entities with a closed `Door` component.

Future extensions (smoke, foliage, magical darkness) extend
`blocks_sight`. The LOS walker does not need to change.

Why Bresenham per tile and not symmetric shadowcasting? Working radii
are small (≤ 10), the acceptance tests are about walls and doorways
(both correctly handled), and the per-tile walker is straightforward to
test and reason about. Symmetric shadowcasting is a worthwhile upgrade
when content demands either tighter symmetry guarantees or larger fields
of view; it can drop into `compute_visible_tiles` without touching the
memory store or render path.

## Light levels

`light_level_at` currently returns `LIT` for every in-bounds tile. The
visible-tile walker treats `DARK` cells as visible only when adjacent to
the observer. This is the seam M11 (spells / light sources) and M23
(stealth) will use to make individual tiles dim.

## Vision system

`VisionSystem.tick(world, observers, memory)`:

1. For each alive, positioned observer, compute its visible set.
2. Union into the party visible set.
3. For every visible cell, snapshot terrain + static features into
   `memory.tiles`.
4. Replace `memory.visible` with the new union.

No effects are emitted. The system is invoked by `App` after every
`apply_effects` call and after `advance_party_turn` rotates the active
actor.

## Renderer integration

`render(...)` accepts an optional `memory: PartyMemory | None`. With
`memory=None`, the renderer falls back to the prior omniscient path
(used by tests and library callers that haven't wired vision in). With
memory provided:

- Visible cells render the live world (current entity glyph or terrain).
- Remembered cells render the last-seen tile glyph; if a static feature
  was remembered, its glyph supplants the bare terrain. No live actor
  glyphs.
- Unknown cells render as a blank space.

The opt-in flag keeps the M19 PR small and lets tests for other
subsystems continue using the omniscient projection.

## Forward seams

- **M20 Targeting** uses `compute_visible_tiles` (or its sibling
  predicate `is_aware_of` from `awareness_system.py`) to constrain
  legal targets.
- **M21 Examine** reads `memory.state_at(x, y)` plus
  `memory.tiles[(x, y)]` to describe remembered vs current cells.
- **M23 Stealth/Perception** extends `blocks_sight` (concealment) and
  `compute_visible_tiles` (perception DC) and layers a per-target
  detection predicate on top of LOS.
- **M22 Auto-walk** can stop when `memory.state_at(x, y)` flips to
  `VISIBLE` for a hostile.
- **M16 Save/restore** persists `PartyMemory.tiles`. The keys are
  already JSON-serialisable tuples; the per-map-id dimension will be
  added when the engine grows multiple maps.
