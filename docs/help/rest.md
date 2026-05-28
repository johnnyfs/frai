# Rest and shelter zones (M34)

This document describes the rest verb and the shelter-zone data the
M34 milestone introduced. The agentic playtester (M37) and the future
`?` help integration (M39) read this file directly.

## Key bindings

| Key                 | Effect                                                |
| ---                 | ---                                                   |
| `r` (in play)       | Open the rest menu for the active actor               |
| `s` (rest menu)     | Take a short rest                                     |
| `l` (rest menu)     | Take a long rest                                      |
| `q` / `Esc` (menu)  | Close the menu; no time passes, no resources spent    |

Pressing `r` while in combat (`play_mode == turn_based`) is refused
with a clear banner; the modal does not open. Pressing `r` while not
standing inside a shelter zone opens the modal but every rest pick
returns "There is no shelter here to rest in." so the player learns
the constraint by trying.

## Shelter zones

A `ShelterZone` is a rectangular area on the map that explicitly
permits rest. The engine never infers "safe to rest" from terrain;
only zones flagged here unlock the rest verb. This keeps rest a
content-driven decision — a tile being forest or town floor says
nothing about whether sleeping there is wise.

Each zone carries:

| Field             | Meaning                                                          |
| ---               | ---                                                              |
| `zone_id`         | Unique slug used by save data and the occupancy tracker.         |
| `rest_permission` | `none`, `short_only`, `long_only`, or `both`.                    |
| `rest_risk`       | `none`, `encounter_check`, or `forbidden`.                       |
| `entry_message`   | Text emitted once when the party leader enters the zone.         |
| `exit_message`    | Text emitted once when the party leader exits the zone.          |
| `cost`            | Gold deducted from the active actor on a successful rest.        |
| `requirements`    | Item ids the active actor must hold (tents, keys, etc.).         |
| `uses_remaining`  | `None` for unlimited; integer counter otherwise.                 |

Zones live on `World.shelter_zones` (a `ShelterZoneRegistry`) and the
party's current zone is tracked on `World.zone_occupancy` so save /
load preserves the "we already greeted the party" state — without it,
loading a save inside a zone would re-emit the entry text on the next
tick.

## Default-world shelters

The M8 world skeleton (`src/world/content/skeleton.py`) registers two
zones at construction time:

| Zone id          | Location          | Permission   | Risk | Cost  | Notes                              |
| ---              | ---               | ---          | ---  | ---   | ---                                |
| `tavern_room`    | Town (Hearthgate) | `both`       | safe | 5gp   | 3x3 patch in the SE corner.        |
| `forest_glade`   | Forest (Briarwood)| `short_only` | safe | 0gp   | 3x3 patch around the forest anchor.|

The tavern is the only spot in the default world that permits a long
rest, so a player who burned spell slots in the dungeon has to walk
back to town to refill. The glade is a free short-rest waypoint for
HP top-up en route.

## Rest semantics

Short rests advance the clock by 10 minutes (`SECONDS_PER_SHORT_REST`).
Each party member regains `max(1, missing_hp // 2)` HP — the SRD
Hit-Dice spend simplified to "half of what you're missing". Spell
slots are not refilled on a short rest; the M11 SRD-lite reading keeps
slot refill as a long-rest reward.

Long rests advance the clock by 8 hours (`SECONDS_PER_LONG_REST`).
Every party member is fully healed and every spell slot is refilled
via `SpellSlots.reset_to_max()`. Every `UNTIL_REST` condition on every
actor is cleared via `tick_conditions(..., boundary="long_rest")` —
the rest system does not reimplement the sweep; it routes through the
M24 driver so future "exhaustion goes down by 1" handlers land in one
place.

## Risky shelters

A zone with `rest_risk == encounter_check` rolls a single d20 when the
rest commits. On a roll below `ENCOUNTER_CHECK_DC` (today, 11), the
rest is interrupted: time still advances (the party tried to bed down)
but no recovery happens and no gold is spent. The interruption banner
reads "Your short rest is interrupted by signs of danger." (or "long",
matching the kind attempted).

This is intentionally simpler than a real encounter deck. A future
M14 / M15 follow-up will replace the single roll with a proper
random-encounter draw that spawns the interrupting creature.

A zone with `rest_risk == forbidden` refuses every rest outright,
even if `rest_permission` would otherwise allow it. Use this for
narrative chokepoints that still want to emit entry text.

## Cost & uses

The `cost` field is the gold deducted from the **active actor's**
inventory on a successful rest. The check fires before any other
mutation so a refusal ("You cannot afford the 5gp cost.") leaves the
world untouched. A zone with `uses_remaining` decrements its counter
by one on each successful rest; once exhausted, further attempts
refuse with "This shelter has been used up." `uses_remaining` is
preserved across save/load so a consumable inn key honors its
single use.

## Observation impact

The rest menu surfaces as `ui_mode == "rest_menu"` in the M35
snapshot. The modal's `options` list is `["short", "long", "cancel"]`
regardless of the current zone — the rest system itself produces the
refusal banner on an unsupported pick, so an agent learns the
constraint by trying.

In explore mode, the `available_actions` list now includes `"rest"`
so an agentic playtester knows the verb is available without
inspecting the input system.

## Zone entry / exit messages

`ZoneSystem.tick_zone_transitions(app)` runs after every effect batch
(via `App.apply_effects`). It reads the party leader's tile, compares
against `World.zone_occupancy.current_zone_id`, and emits at most one
exit message (when leaving) followed by at most one entry message
(when arriving). A move within the same zone returns no effects.

The exit message is intentionally surfaced through the zone system
rather than the rest system: a player who walks into a glade, then
walks back out without resting, still sees both messages. The rest
system never emits entry / exit text — only the per-rest banners.

## Save-friendliness

`ShelterZoneRegistry` and `ZoneOccupancyState` are explicit fields on
`World` and round-trip through `World.to_dict` / `from_dict`. The
registry preserves zone order, permission, risk, cost, requirements,
and `uses_remaining`; the occupancy state preserves the current zone
id so a save written inside the tavern does not re-greet the party on
the next tick after load.

## Architectural notes

- **Separation of concerns.** `RestSystem` enforces permission /
  cost / risk and produces the recovery effects. `ZoneSystem` emits
  entry / exit messages. The two systems share zone data (the
  registry on `World`) but neither calls the other.
- **No App reference in the rest system.** `attempt_short_rest` and
  `attempt_long_rest` take an `App` only because they read multiple
  cross-cutting fields (`active_actor`, `world`, `play_mode`,
  `loot_rng`). They do not mutate the App directly — they return
  typed effects the caller applies via the standard `EffectApplier`.
- **No new dispatcher system.** Rest is a player-initiated, multi-
  store verb that ends in a clock advance + condition sweep. The
  Dispatcher's per-system / per-action contract is a poor fit, and
  splitting orchestration between the system and the App handler
  obscures the flow. Two free functions in `src/systems/rest_system.py`
  capture the verb cleanly.

## Seams for future milestones

- **M14 quest path** can attach a `requirements=("quest.tavern_key",)`
  to the tavern shelter so the player has to find the key first.
- **M15 boss / villain** can drop a `rest_risk=encounter_check`
  shelter inside the dungeon entrance so resting near the boss is
  meaningfully tense.
- **M25 leveling** will revisit the short-rest recovery to use real
  Hit Dice spends rather than the "half missing HP" simplification.
- **M29 downed** hooks the start of every rest to restore stable PCs
  (3-success downed actors) to 1 HP. See `docs/help/death.md`.
