# Debug Commands (M33)

These commands are available only when the `FRAI_DEV` environment
variable is set to `1` (or `true`/`yes`/`on`). Outside dev mode they are
rejected with a single message and do not mutate state.

Run from a curses session: not yet bound to a key. The interactive prompt
is deferred until the mode-split refactor (M47) lands. The intended
entry points are:

- The playtest harness (M37) calls `App.run_debug_command("<cmd>")`
  directly.
- Tests use the same `App.run_debug_command` entry point.

All commands route through the standard `EffectApplier`, so they respect
the same invariants as gameplay actions (no direct world mutation).

| Command                      | Effect                                                              | Notes |
| ---                          | ---                                                                 | --- |
| `tp <x> <y>`                 | Teleports the active player entity to `(x, y)`.                     | Emits a `MoveEntity` effect. Does not move other party members. |
| `reveal`                     | Reveals the entire map memory.                                      | Stub until M19 (vision/memory). Emits a placeholder message. |
| `spawn <kind> [<x> <y>]`     | Spawns a catalog entity at `(x, y)` or adjacent to the player.      | Kinds: `kobold`, `goblin`, `chest`, `gold_pile`. |
| `grant gold <n>`             | Adds `n` gold to the player's inventory.                            | |
| `grant item <id> [<qty>]`    | Adds `qty` of the named item to the player's inventory.             | `id` must exist in `src.core.items.ITEMS`. |
| `grant xp <n>`               | Grants `n` XP to every party member.                                | Emits a `GrantXP` per PC. Crossing a level threshold attaches `LevelUpAvailable`; the player consumes it through the standard level-up modal. Unlike the kill/quest split, the debug grant is not divided across members. |
| `god on` / `god off`         | Adds/removes a `GodMode` component on the player.                   | While enabled, the player ignores all `DamageEntity` effects (combat, traps, future sources). |
| `quest <milestone>`          | Stub until M14 (quest content).                                     | Emits a placeholder message. |
| `dump [<path>]`              | Writes a JSON snapshot of the world to `path` (default `world_state.json`). | Best-effort snapshot, not a save format. |

## Architectural notes

- All commands emit effects; nothing bypasses the action/effect pipeline.
- The `god on/off` state is held by a `GodMode` component, deliberately
  separate from `CombatStats`. Save/load (M16) drops this component so
  dev-mode flags never leak into a real player's save.
- `dump` writes to the filesystem directly — it is the one debug command
  that has a side effect outside the world. The output format is not a
  stable save format; the format may change freely.
- The `?` help integration (M31) will surface this file only when
  `FRAI_DEV=1`; until M31 lands, this file is reference-only.
