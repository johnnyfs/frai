# Stealth, noise, and perception (M23)

This document describes the stealth pipeline introduced in M23. The
agentic playtester (M37) and the future `?` help integration (M39)
read this file directly.

## Keys

| Key | Action               | What it does                                                        |
| --- | -------------------- | ------------------------------------------------------------------- |
| `z` | Sneak                | DC 10 Dexterity (Stealth) check. Pass: gain `hidden`. Fail: revealed. |
| `p` | Perception           | DC 10 Wisdom (Perception) check. Pass: reveal hidden creatures in LOS. |

Both keys live on the play screen. Either action emits a one-line
message into the message log describing the outcome.

## Two-axis model

The engine distinguishes two related notions:

- **Visibility** is geometric: is there an unobstructed line from
  observer to target? This is the M19 `compute_visible_tiles` result.
- **Awareness** is cognitive: does the observer *know* about the
  target? An observer can have a clear sightline to a hidden creature
  and still be unaware until perception fires; an observer that heard
  a noise can be *suspicious* without ever seeing the source.

The awareness predicate (`src.systems.awareness_system.is_aware_of`)
resolves in this order:

1. Self / dead / unpositioned targets are never aware.
2. If the observer's `AwarenessTracker` recorded state about the
   target is `aware`, the predicate returns `True`. (An alerted guard
   remembers the intruder even behind a corner.)
3. If the target carries the `hidden` condition and the observer is
   not already aware, the predicate returns `False`.
4. Otherwise, fall back to "alive and positioned" — the pre-M23
   permissive behaviour, which keeps untracked hostiles working.

## Awareness states

`AwarenessState` is a three-step ramp:

| State        | What it means                                                            |
| ---          | ---                                                                      |
| `unaware`    | Default. No idea this entity exists.                                     |
| `suspicious` | Heard something. Doesn't act on the contact yet, but the floor is rising. |
| `aware`      | Knows where the entity is. AI will engage on its next turn.              |

The ramp is one-way under M23: you can move up but not down. A
follow-up will model "lost track of the intruder after N rounds".

## Noise

Actions carry a `NoiseLevel`:

| Level     | Default propagation radius (Chebyshev) | Awareness ramp                |
| ---       | ---                                    | ---                           |
| `silent`  | 0                                      | none                          |
| `quiet`   | 2                                      | -> `suspicious`               |
| `loud`    | 8                                      | -> `aware`                    |

The defaults today:

- **Attacks** (any `AttackAttempt`) are loud. The attacker's `hidden`
  tag clears on attack.
- **Spell casts** are loud (every spell in the M11 catalog has a
  verbal component).
- **Movement / pickup / interaction** do not currently emit noise
  explicitly. Footstep noise is a follow-up.

The propagation helper (`src.core.stealth.propagate_noise`) walks
every entity with an `AwarenessTracker`, filters to hostiles, and
ramps any within range. Friends and neutrals never ramp on player
noise — a town full of NPCs doesn't all aggro on a sword swing.

## Stealth action (`z`)

DC 10 Dexterity (Stealth) check. Proficiency from the actor's
character sheet is honored.

- **Pass**: applies the `hidden` condition with `until_removed`
  duration. The actor stays hidden until something explicitly clears
  it (attacking, casting, being spotted by perception).
- **Fail**: emits an `EndCondition` for `hidden` — if the actor was
  already hidden, the failed attempt blows their cover.

## Perception action (`p`)

DC 10 Wisdom (Perception) check.

- **Pass**: every hidden creature in the actor's visible set (M19 LOS)
  has its `hidden` condition stripped, and the actor's
  `AwarenessTracker` is updated to `aware` for each spotted entity.
  Hidden creatures behind walls remain hidden — perception is
  geometry-aware.
- **Fail**: nothing changes.

## AI interaction

The enemy AI's target picker (`_nearest_living_party_member`) consults
`is_aware_of` and skips party members the enemy doesn't perceive. A
sneaking party member is therefore invisible to the AI; the AI will
either chase a visible companion or wait if every party member is
hidden.

## Observation impact

Two M35 observation fields land in this milestone:

- Each `VisibleEntity` now exposes an `awareness` field — the entity's
  recorded state about the active actor (`unaware` / `suspicious` /
  `aware`), or `None` when no tracker is installed.
- `available_actions` includes `sneak` and `perceive` in play mode.

The `hidden` condition is already projected by the M24 conditions
pipeline (each actor's `conditions` list carries it as a kind).

## Save-friendliness

`AwarenessTracker` is a typed `ComponentStore` on `World` and
round-trips through `to_dict` / `from_dict` like any other component.
The `hidden` condition is a normal `ConditionKind`, so the M16
save/load path picks it up automatically.

## Follow-ups (not in M23)

- Per-observer hidden bit. Today `hidden` is global: a hidden actor
  is hidden from everyone who isn't already aware. The per-observer
  split ships when the per-target stealth roll lands.
- Footstep noise on movement. The seam (`propagate_noise(..., QUIET)`)
  exists; movement code just doesn't call it yet.
- Decay (downgrading awareness after a few rounds without contact).
- Per-faction perception / stealth modifiers (deafened ear-based
  checks, wildlife easier to surprise).
- Distractions / illusion sounds via the noise propagation helper.
