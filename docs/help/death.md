# Downed, death saves, and recovery (M29)

This document describes the downed / unconscious / death-save loop
introduced in M29. The agentic playtester (M37) and the future `?` help
integration (M39) read this file directly.

## High-level flow

```
   damage
     ↓
   HP > 0 ────────────────► HP == 0  (combatant has CombatStats)
                                ↓
                              PC?
                       no  ────┴────  yes
                       ↓               ↓
              instant death       unconscious + DeathSaves(0, 0)
                                       ↓
                              end-of-round: roll d20 + CON mod vs DC 10
                                       ↓
        natural 20      crit/success      success      failure      natural 1
            ↓                ↓               ↓             ↓             ↓
        wake at 1 HP    +1 success       +1 success    +1 failure    +2 failures
                                              ↓             ↓
                                       3 successes:    3 failures:
                                       stable           dead
```

## State diagram

- **Normal** — HP > 0. Standard play.
- **Dying** — HP == 0 with a `DeathSaves` row whose `stable` flag is
  `False`. Carries the SRD `unconscious` condition. Rolls one death save
  at every round boundary.
- **Stable** — HP == 0 with a `DeathSaves` row whose `stable` flag is
  `True`. Still unconscious; no longer rolls death saves. A short or
  long rest restores them to 1 HP.
- **Dead** — entity is removed from the world. The kill is finalised
  through the standard `KillEntity` effect pipeline.

## When does a PC get downed?

A `DamageEntity` effect that drives a player-controlled combatant's
HP to 0 (without overflowing the negative-max threshold) routes through
`begin_downed`. The effect handler:

1. Clamps `hit_points` at 0.
2. Emits `ApplyCondition(unconscious, UntilRemoved)` so the SRD
   condition propagates through the M24 plumbing.
3. Inserts a `DeathSaves(successes=0, failures=0, stable=False)`
   component on the actor.
4. Emits a `… falls unconscious.` message.

If the same damage roll would push the actor below `-max_hit_points`
(SRD massive damage), the M29 path is bypassed and `KillEntity` is
emitted directly — the PC dies outright.

NPC combatants (no `PlayerControlled` marker) follow the legacy path: HP
clamps at 0 and the CombatSystem / SpellSystem follow-up `KillEntity`
removes them from the world. Only PCs earn death saves.

## Damage while downed

Each `DamageEntity` on a downed PC counts as one death-save failure.
A blow whose `amount >= max_hit_points` counts as two failures (the
critical-hit branch). Reaching three failures emits `KillEntity` for
real.

## Healing while downed

Any positive `ApplyHealing` to an unconscious actor revives them:

1. HP rises to the heal amount (clamped at max).
2. The `unconscious` condition ends via `EndCondition`.
3. The `DeathSaves` row is dropped.
4. A `… wakes up.` message is emitted.

This means a low-level cure spell or potion can pull a PC out of the
dying state regardless of how many failures they had accumulated.

## Death-save resolution

At every round boundary (`App._tick_round_boundary`), every dying PC
rolls one DC-10 CON save:

| Roll                                        | Effect                                                                |
| ---                                         | ---                                                                   |
| Natural 20                                  | Restore 1 HP, clear unconscious, drop `DeathSaves`.                   |
| Natural 1                                   | +2 failures.                                                          |
| `total >= 10`                               | +1 success.                                                           |
| `total < 10`                                | +1 failure.                                                           |
| Successes reach 3                           | `stable = True`. Still unconscious; no more rolls.                    |
| Failures reach 3                            | `KillEntity` for real.                                                |

Death saves draw from `App.loot_rng`, so seeded fixtures stay
deterministic across runs.

Stable PCs do **not** roll saves. They wait for a rest.

## Stable recovery on rest

The M34 rest system (`attempt_short_rest` / `attempt_long_rest`)
restores every stable PC to 1 HP before its own recovery logic runs:

- `hit_points` becomes 1.
- The `unconscious` condition is ended via `EndCondition`.
- The `DeathSaves` row is dropped.
- A `… recovers from unconsciousness.` message is emitted.

The restore fires even when the rest itself is interrupted by an
encounter check — the SRD lets a stable actor regain 1 HP after enough
time has passed regardless of whether the rest completes.

## Party wipe → game-over

When the post-effect tick detects that every party member is either
unconscious or dead, `UIMode` flips to `game_over`. This happens both
on the damage path (`_apply_damage_entity` checks after a PC enters
the downed state) and on the kill path (`_apply_kill_entity` checks
just before removing a dying party member).

A single conscious PC keeps the game running.

## Save-friendliness

The `DeathSaves` component is a regular dataclass on `World.death_saves`
and round-trips through the standard component store pipeline. The
`unconscious` condition uses the existing M24 plumbing (`UntilRemoved`
duration). A save written mid-downed reloads with the same successes,
failures, and stable flag.

## Observation snapshot

Every actor summary in the M35 observation now carries an optional
`death_saves` field. The field is `null` when the actor is not in the
downed state machine; otherwise it surfaces:

```json
"death_saves": {
  "successes": 1,
  "failures": 2,
  "stable": false
}
```

The `unconscious` condition appears in the `conditions` list as usual,
so an agentic playtester can detect "downed" by either checking that
field or the condition list.

## Architectural notes

- **No new dispatcher system.** Downed transitions live entirely in
  the effect-applier handlers (`_apply_damage_entity`,
  `_apply_kill_entity`, `_apply_apply_healing`). The death-save
  resolver is a pure function in `src/core/death_saves.py` that the
  App calls from its round-boundary tick.
- **No new effect type.** Begin-downed, revive-on-heal, and stable
  rest restore all compose the existing `ApplyCondition`,
  `EndCondition`, `EmitMessage`, and `KillEntity` primitives.
- **Behaviour preservation.** NPC enemies still die outright on HP=0;
  the M28 distinction is encoded by gating the M29 transitions on
  `PlayerControlled`. Existing combat tests that assert "non-player
  KillEntity removes the entity" still pass.

## Seams for future milestones

- **M25 (leveling)** can grant proficiency or CON-based bonuses to
  death saves once the SRD-real save-modifier path lands.
- **M11 (spells)** already wires healing through `ApplyHealing`, so
  spare-the-dying / revivify equivalents land naturally — they just
  need to be added to the catalog.
- **M15 (encounters)** can hook the party-wipe check to flip into a
  bespoke "you have fallen" cutscene rather than the bare `game_over`
  modal.
