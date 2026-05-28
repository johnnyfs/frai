# Examine / look (M21)

The examine command opens a cursor over the world so the player can
inspect any tile: terrain, creatures, items, doors, traps, containers,
corpses, and active conditions. It is the NetHack-style "look"
command — read-only, no resource consumed, no turn advanced.

## Key bindings

| Key | Effect                                                       |
| --- | ---                                                          |
| `x` | Open examine cursor over the active actor's tile             |
| `;` | Alias for `x` (NetHack-style "look" key)                     |

While the examine cursor is up, the same keys as M20 targeting drive
the cursor:

| Key                 | Effect                                       |
| ---                 | ---                                          |
| `h` `j` `k` `l`     | Move cursor west / south / north / east      |
| `y` `u` `b` `n`     | Move cursor diagonal (NW / NE / SW / SE)     |
| `Enter` (or `Space`)| Confirm — emit description for the cursor    |
| `Esc` (or `q`)      | Cancel — close the modal silently            |

The cursor opens on the active actor's tile and can range out to the
party's vision radius (Chebyshev distance). Cursor motion is free
within that range; the predicate is permissive (`any_tile`) so the
cursor can walk over walls, remembered tiles, and even unknown tiles.

## What examine shows

Confirm assembles a short, structured description from the tile's
terrain and the typed components of any entity on the cell. The
composer lives in `src.core.descriptions` so the same text feeds the
M37 agent playtester via the structured observation surface.

For each cell the description includes (in order):

1. The terrain ("stone floor", "deep water", "stone wall", ...).
2. One line per entity on the tile. For each entity:
   - Name (always).
   - Creature kind in parentheses (when present).
   - HP / max HP (when the entity has combat stats).
   - Faction tag (when present, e.g. `faction: player_party`).
   - Active conditions (e.g. `conditions: poisoned, blessed`).
   - Affordance hint for doors / locks / traps / containers
     (e.g. `(locked door)`, `(armed trap)`, `(open container)`).
   - Inventory summary for ground-item entities
     (e.g. `[contains: 5 gold, club]`).

A dead entity is marked `[dead]` instead of an HP line.

## Memory-aware semantics

Examine respects the M19 vision/memory model:

| Tile state                     | What examine shows                                                                                  |
| ---                            | ---                                                                                                 |
| **Visible** (in current LOS)   | Full live description: terrain + every entity currently on the tile.                                |
| **Remembered** (last seen)     | Terrain prefixed with `(last seen)` and any **static** features cached in memory (doors, disarmed traps, containers). Live creatures and ground items are NOT surfaced. |
| **Unknown** (never seen)       | A single line: `You don't know what's there.`                                                       |

The static-only rule for remembered tiles is deliberate: memory tracks
layout, not actor positions. A creature standing on a remembered-but-
not-visible tile will not appear in the examine text until the party
sees the tile again.

## Action economy

Examine does **not** consume any resource:

- No action / bonus action / movement spent.
- No clock tick (explore mode) or round advance (turn-based).
- The active actor is unchanged.
- The world is not mutated — examine is read-only.

This makes it safe to spam from any state: open, read, cancel, and
the gameplay state is identical to before.

## Save / load

Examine reuses M20 targeting infrastructure, which is transient
runtime state. A save written mid-examine drops the cursor; loading
lands the player back in the play screen with no cursor pending.

## Agent observation seam

The M35 structured observation surfaces an `examine=...` token in the
targeting modal's `options` list whenever the cursor is up. The token
mirrors the prose the player would see on confirm, joined with ` | `
between lines. An agentic playtester (M37) can poll this token to
read "what's at the cursor" without separately confirming.

This is the canonical "what changed" channel for the structured-
observation system: agents can call the description composer
(`src.core.descriptions.examine_tile`) directly for arbitrary cells,
not just the cursor.

## Architectural notes

- `src.core.descriptions` owns the prose composer. Pure module over
  `(World, PartyMemory)` — no App / dispatcher / RNG dependency.
- The App opens the examine modal via `App.begin_examine`, which is
  just a thin wrapper that constructs a `TargetingState` with the
  `any_tile` predicate, an empty `cancel_message` (so Esc doesn't
  emit a banner over the description), and an `on_confirm` callback
  that emits description text and returns `None`.
- Returning `None` from `on_confirm` routes the targeting layer
  through `cancel_targeting`, which is now per-state customisable via
  `TargetingState.cancel_message`. The default ("Targeting
  cancelled.") is preserved for every existing caller.

## Forward seams

- **M23 stealth/perception** — examine on a hidden creature should
  surface "you can't make out what's there" rather than the live
  creature name. The predicate composer will gain a perception gate.
- **M25 leveling / identification** — examined items with unknown
  enchantments will show "unidentified" until M12-flavor identify
  lands.
- **M37 harness** — agents can already read examine text via the
  observation `examine=...` token; future work surfaces the same
  composer as a `look(x, y)` script command without opening the modal.
