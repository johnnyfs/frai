# Conditions, statuses, and durations (M24)

This document describes the condition/status system introduced in M24.
The agentic playtester (M37) and the future `?` help integration (M39)
read this file directly.

## Catalog

The engine knows the following condition kinds (`ConditionKind` in
`src/core/conditions.py`):

| Kind            | What it means                                                          |
| ---             | ---                                                                    |
| `poisoned`      | Disadvantage on attack rolls and ability checks (M11 will deepen).     |
| `prone`         | Movement is halved; attacks against you within 5ft have advantage.     |
| `restrained`    | Speed becomes 0; attacks against you have advantage; you have disadv.  |
| `frightened`    | Disadvantage on ability checks and attacks while source is in sight.   |
| `hidden`        | Attacks against unaware targets have advantage; cleared on attack.     |
| `blessed`       | +d4 to attack rolls and saves (placeholder for M11 spell wiring).      |
| `burning`       | Takes fixed damage (default 1; configurable via payload) every round.  |
| `unconscious`   | Incapacitated and prone. M29 builds death-saves on top of this tag.    |
| `blinded`       | Attacks against you have advantage; you have disadvantage on attacks.  |
| `deafened`      | Cannot hear and auto-fail hearing-based perception checks.             |
| `concentrating` | You are concentrating on a spell. Special-cased — see below.           |

Today these are tags. Combat-mechanical effects beyond the **burning**
round-tick are deferred to M11 (basic spells) and M29 (downed) so this
milestone stays focused on the data + duration plumbing.

## Duration policies

Conditions choose how long they last via `DurationPolicy`:

| Policy            | Decrements / clears on...                                             |
| ---               | ---                                                                   |
| `rounds(n)`       | Each combat round boundary (after the round wraps).                   |
| `turns(n)`        | Each explore-mode "turn" (one minute per player action).              |
| `minutes(n)`      | When the world clock reaches `now + n minutes`.                       |
| `until_rest()`    | Cleared by the rest system (M34) on a long rest.                      |
| `until_removed()` | Persists until something explicitly emits `EndCondition`.             |

`Rounds` and `Turns` policies seed a countdown at apply time. `Minutes`
policies resolve to an absolute `expires_at` against `world.clock`.

## Concentration

Only one `concentrating` condition can be active on an actor at a time.
Applying a new `concentrating` condition automatically ends the prior
one (the player chose to redirect their focus). The engine performs
this handoff inside `apply_condition` so no spell code needs to know
about it.

Concentration breakage from damage is wired in M11: the App registers a
reaction hook on the M46 resolver that watches every resolved attempt
for `DamageEntity` effects on a concentrating actor and appends an
`EndCondition(CONCENTRATING)`. The simpler "any damage breaks" rule is
used today; the SRD `DC 10 or half damage` save is a M24 follow-up.

## Effects

Two typed effects drive the system from the rest of the engine:

- `ApplyCondition(entity, condition)` — attach a `Condition` to a target.
  The applier resolves the duration policy against `world.clock` and
  handles concentration handoff.
- `EndCondition(entity, kind)` — remove every condition of `kind` from
  the target. No-op if none are present.

Both route through the standard `EffectApplier` so save/load and the
observation snapshot stay consistent.

## Tick wiring

The condition tick driver lives in `src/core/conditions.py` as
`tick_conditions(world, actors, boundary=...)`. The `App` calls it from
three places:

| Boundary     | Wiring point                                  | Drives                       |
| ---          | ---                                           | ---                          |
| `round`      | `App._tick_round_boundary` (called by         | ROUNDS countdowns, burning   |
|              | `TurnController.end_turn_with_enemy_phase`)   | round-tick damage            |
| `turn`       | `App._tick_world_clock` when the advance is   | TURNS countdowns             |
|              | a full explore-mode minute                    |                              |
| `clock`      | `App._tick_world_clock` (always)              | MINUTES expirations          |
| `long_rest`  | M34 rest system (entry point exposed today)   | UNTIL_REST clearing          |

The schedule (`world.schedule`) is consulted on every clock advance via
the existing `advance_world_clock` integration. Conditions do not push
their own entries onto the schedule yet — they tick procedurally — but
the seam is here so M34/M11 can attach pre-computed expiry events if
that turns out to be more efficient.

## Save-friendliness

`ConditionStore` is just another `ComponentStore` on `World`. It
round-trips through `to_dict` / `from_dict` like any other component:
the dump tool (M33) walks it automatically; M16 will pick it up the
same way. Payloads are plain dicts of JSON-safe values.

## Observation impact

Every party-member entry in the M35 observation snapshot now carries a
`conditions: [{kind, duration, expires_at, rounds_remaining,
turns_remaining}]` field. An empty list means "no conditions". The kind
and duration are string-valued (the enum `value`), which keeps the
snapshot stable across save/load round trips.

## M29 (downed): unconscious lifecycle

`unconscious` is a normal `ConditionKind`. M29 owns the lifecycle:

1. `EffectApplier._apply_damage_entity` emits
   `ApplyCondition(unconscious, UntilRemoved)` when a player-controlled
   actor's HP reaches 0 (instead of removing them).
2. The death-save state machine (`src/core/death_saves.py`) decides
   when to clear it (via `EndCondition` on healing / crit-success) or
   convert to a kill (three failures).
3. The condition tag is dropped when the actor recovers — either to
   1 HP via crit-success / natural healing, or to 1 HP via a post-rest
   stable restore.

See `docs/help/death.md` for the full M29 contract.
