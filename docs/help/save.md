# Save / restore (M16)

The engine writes the full game state to a single JSON file on disk and
reads it back on demand. Saves are deliberately plain JSON — no
`pickle`, no eval, no native binary blobs — so they can be inspected,
diffed, and (in the worst case) hand-edited.

## File location

By default the save file lives at

```
~/.local/share/frai/save.json
```

(`$XDG_DATA_HOME/frai/save.json` is honoured when `$XDG_DATA_HOME` is
set). The path is overridable per-process by setting

```
FRAI_SAVE_PATH=/path/to/save.json
```

before launch. The playtest harness and tests pass an explicit path, so
the override only matters for power users keeping multiple save slots.

`src.core.save.default_save_path()` is the single source of truth — UI
and harness code should both call it rather than hard-coding paths.

## Save format

The file is pretty-printed JSON with sorted keys. The top-level keys
are:

| Key                            | Purpose                                       |
| ---                            | ---                                           |
| `schema_version`               | Integer; gates `migrate()` (current: `1`).    |
| `format`                       | Always `"frai.save"`; rough sanity check.     |
| `world`                        | Tiles + entity components + clock + schedule. |
| `party`                        | Members, active index, focus, follow order.   |
| `turn`                         | Action economy + voluntary flag + activations.|
| `ui_mode`, `play_mode`         | Modal screen + play sub-mode.                 |
| `facing`                       | Last movement direction (2-element list).     |
| `messages`                     | Current message + pending pages.              |
| `memory`                       | M19 party vision (visible + remembered tiles).|
| `clock`, `schedule`            | World time + scheduled events.                |
| `character_creation_state`     | In-flight new-character wizard, or `null`.    |
| `player_entity_id`             | Convenience cache (`World.player_entity()` is the source of truth). |
| `loot_rng_state`               | Loot RNG state for deterministic resume.      |

Tiles inside `world.tiles` are stored as catalog tokens (e.g.
`"room.floor"`, `"wall.horizontal"`). Unknown tokens load as `OUTSIDE`
to keep older saves loadable. See `src/map/tiles.py` for the catalog
and the `_adhoc.*` fallback shape used for ad-hoc test tiles.

## What is persisted vs skipped

**Persisted:**

- ECS world: entities + every component store on `World` except
  `god_modes`. Tile grid is stored by catalog token. Clock and schedule
  are preserved.
- Party state: members, active/focused index, follow order.
- Turn controller bookkeeping: round number, voluntary turn flag,
  per-actor `ActivationState`.
- UI mode, play mode, facing, messages, party memory, character
  creation state.
- Player entity id and the loot RNG state.

**Skipped (intentional):**

- Dispatcher and the systems it owns (deterministic from a fresh
  `create_app`).
- Vision system instance (rebuilt and immediately ticked on load).
- The in-progress `AutowalkRequest` (M22 — auto-walk drops on load
  by design).
- The curses screen / running flag.
- `GodMode` debug markers (M33 — never written to a normal save).

Effect appliers, dispatchers, and the message-pager wiring are part of
the scaffolding that `create_app` rebuilds; the loaded `GameState`
slots into the freshly-scaffolded `App` so behaviour wiring (callable
closures, system references) is always pristine.

## Schema versioning

`schema_version` is bumped whenever the on-disk shape changes. The
`migrate(payload, ...)` helper in `src/core/save.py` runs a ladder of
single-step migrations: `0 -> 1`, `1 -> 2`, etc. Each step returns a
new dict with the bumped version so older saves can be inspected
side-by-side with their post-migration form.

Today only version `1` is defined and `migrate` is effectively a
no-op. The `_migrate_0_to_1` placeholder is in place so the first real
shape change has a slot to land in without touching the load path.

## Probe rebinding on load

`TurnController` uses two callable seams that read live world state:
`hostiles_probe()` (forces turn-based mode when hostiles are in
awareness range) and `can_take_turn(entity)` (skips downed companions
in the turn rotation). These callables aren't serializable. The loader
reconstructs them as closures over the *loaded* `GameState`, so a
later `App.restart()` (which replaces `game_state.world`) keeps
behaving correctly.

After rebinding, `load_game` reconciles `play_mode` by re-running the
hostile probe: a save written during combat lands in `turn_based`, a
save written in a quiet hallway lands back in `explore`, and a
voluntary turn-based save survives if no hostiles are around.

## Limitations / TODO

- `ScheduledEvent` subclasses lose their per-subclass payload on load
  because we don't yet have a registry of `kind -> subclass`. The
  schedule round-trips `(due_at, kind)` pairs as plain
  `ScheduledEvent`s. Once richer scheduled effects land (M24 status
  expirations are the main pressure), the registry plugs in here.
- `loot_rng_state` survives, but no other system-level RNG does. If a
  future system grows its own RNG, add it to the payload alongside
  `loot_rng_state`.
- The party-memory schema records remembered glyphs + static features
  but does not yet preserve the original tile *kind* (just the
  glyph). The renderer doesn't need it today.
