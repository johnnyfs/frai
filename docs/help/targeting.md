# Targeting (M20)

Targeting mode lets the player pick a tile (and any entity on it)
before committing an action that needs a target outside the "step
into them" melee model. Ranged attacks (M11), spells, the examine
command (M21), throw-item, and any future interact-at-distance
action all funnel through this seam.

## Entering the modal

Targeting is opened by an action that needs a target. The opening
caller builds a `TargetingState` describing the origin tile, the
maximum cursor range, an optional line-of-sight / faction
`predicate`, and an `on_confirm` callback that produces the action
to dispatch. Today the only built-in opener is the test/spell
seam — player keys for "fire" / "throw" / "examine" land in M11
and M21.

While targeting is active the play screen continues to render, with
the cursor cell highlighted. The world is paused — no turn
advances, no clock ticks, no AI moves until the player confirms or
cancels.

## Key bindings

| Key                 | Effect                                       |
| ---                 | ---                                          |
| `h` `j` `k` `l`     | Move cursor west / south / north / east      |
| `y` `u` `b` `n`     | Move cursor diagonal (NW / NE / SW / SE)     |
| `Enter` (or `Space`)| Confirm — dispatch the action at the cursor  |
| `Esc` (or `q`)      | Cancel — close the modal, consume nothing    |

Cursor motion is bounded by the `range` configured by the caller
(Chebyshev distance from the origin tile). Out-of-range steps are
silently ignored — the cursor will not leave the legal area.

The cursor is allowed to pass over walls and unknown tiles. The
predicate is consulted only at confirm time, so a "behind the wall"
confirm is rejected with `Invalid target.` rather than blocking
cursor motion.

## Confirm semantics

On confirm the modal:

1. Checks the cursor is still in range. If not, emits
   `Target out of range.` and leaves the modal open.
2. Runs the caller's `predicate(world, x, y, origin)`. If it
   returns `False`, emits `Invalid target.` and leaves the modal
   open.
3. Calls `on_confirm(cursor)` to build the action. A `None`
   return is a silent cancel.
4. Closes the modal and routes the action through
   `app.resolve_action(action)` — the M46 phased resolver. The
   action itself decides whether to consume an action / movement /
   spell slot, exactly as if the player had pressed a normal play
   key.

## Cancel semantics

`Esc` (or `q`) closes the modal without dispatching anything. No
resource is consumed; the turn does not advance; a short
`Targeting cancelled.` message lands in the log.

## Predicates

`src.core.targeting` exposes a small library of pure predicates
that callers compose:

- `any_tile` — every in-bounds cell is legal.
- `any_visible_tile` — Bresenham-LOS from the origin tile.
- `any_visible_entity` — visible cell that has at least one entity.
- `hostile_entity` — visible enemy with live HP.
- `make_visible_predicate(observer, radius)` — consults
  `compute_visible_tiles(observer)` so closed doors / walls limit
  valid targets exactly like player vision.

Callers may also pass a custom predicate (e.g. "spell target must
have a condition store"); the seam is intentionally tiny.

## Save / load

Targeting is **transient runtime state**. The in-flight
`TargetingState` is held on the App only — `GameState.to_dict`
(the M16 save target) carries nothing about it. A save written
mid-modal drops the selection; loading lands the player back in
the play screen with no cursor pending.

## Architectural notes

- `src.core.targeting` owns the data types, the cursor math, and
  the predicate library. It does not import the App or the
  renderer.
- The App handles cursor input directly in `_handle_targeting_key`
  rather than routing through `map_key`. This is so a stray
  inventory or quit key in targeting mode cannot leak through and
  open another modal.
- Render highlight is a projection — the renderer reads
  `app.targeting.cursor` (and `origin`, `range`) and overlays an
  `X` on the cursor cell. The world is never mutated by the modal.
- The on-confirm action is dispatched via `app.resolve_action`,
  not `app.dispatcher.dispatch` directly, so the M46 pre/post
  hooks and reaction system see the action like any other.

## Forward seams

- **M21 examine** — opens targeting with `any_tile` and an
  `on_confirm` that reads the cell's entities / tile and emits
  descriptive messages instead of an action.
- **M11 ranged / spells** — opens targeting with a vision-aware
  predicate, configurable range, and an `on_confirm` that builds
  a `SpellAttempt` (or new equivalent action).
- **Throw item** — opens targeting with `any_visible_tile` and
  an `on_confirm` that builds a `ThrowItemAttempt`.
