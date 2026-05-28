# Factions and hostility (M28)

This document describes the faction / relation model introduced in M28.
The agentic playtester and the future `?` help integration read this
file directly.

## Catalog

The engine knows the following canonical faction ids
(`FactionId` in `src/core/factions.py`):

| Faction id      | Used by                                                        |
| ---             | ---                                                            |
| `player_party`  | The player character, recruited companions, summons, pets.     |
| `town`          | Townsfolk, shopkeepers, guards, friendly NPCs.                 |
| `dungeon`       | Hostile monsters in dungeons / overworld encounters.           |
| `wildlife`      | Wild creatures that won't attack unprovoked (deer, rabbits).   |
| `unknown`       | Fallback for ad-hoc faction strings (legacy/save-compat).      |

Pre-M28 saves used raw strings `"player"` and `"enemy"`. Those are
aliased to `player_party` and `dungeon` at lookup time so old saves
keep loading without a migration step.

## Default relation matrix

The `RelationTable` is symmetric. The default table:

|                  | player_party | town    | dungeon | wildlife |
| ---              | ---          | ---     | ---     | ---      |
| **player_party** | friendly     | neutral | hostile | neutral  |
| **town**         | neutral      | friendly| hostile | neutral  |
| **dungeon**      | hostile      | hostile | friendly| neutral  |
| **wildlife**     | neutral      | neutral | neutral | neutral  |

A relation is one of `hostile`, `neutral`, or `friendly`. Only
`hostile` triggers turn-based mode or NPC attacks; `neutral` and
`friendly` both render as "not a target".

## Aggro overrides

Per-entity overrides flip the relation an individual actor sees toward
a target faction *without* moving the actor's own faction. The
canonical example is a shopkeeper who turns hostile after a theft:

```python
world.aggro_overrides.add(
    shopkeeper,
    AggroOverrideList(overrides=[
        AggroOverride(target=FactionId.PLAYER_PARTY, relation=Relation.HOSTILE),
    ]),
)
```

After applying that override:

- The shopkeeper treats the party as `hostile`, attacks on its turn,
  and triggers turn-based mode.
- Other town NPCs (baker, guard) keep the default `town ↔ player_party
  = neutral` relation. The town doesn't lynch the party as a group.
- The party's relation back to the shopkeeper is still resolved
  through faction (`town`), so the party only attacks the shopkeeper
  if they bump into it. The shopkeeper attacking the party is enough
  to flip the world into turn-based mode either way.

Overrides are persisted with the world; saving and loading round-trips
them.

## Companions, pets, and summons

A summoned creature, pet, or familiar carries an optional
`Faction.summoner` field pointing to its owner:

```python
world.factions.add(
    elemental,
    Faction(value=FactionId.WILDLIFE.value, summoner=player),
)
```

The awareness system walks the `summoner` chain when resolving
relations, so the elemental:

- Treats the player and the rest of the party as `friendly`.
- Treats the player's enemies as the player would (including any
  aggro overrides on the player).
- Doesn't itself need to be a member of `PartyState` — recruitment
  through `PartyState` is reserved for full companions; summons are
  transient and don't get a turn in initiative unless their content
  module wires it.

## Querying hostility

The awareness predicate is the single seam every other system goes
through:

```python
from src.systems.awareness_system import is_hostile_to, hostiles_requiring_battle

is_hostile_to(world, observer, target)            # bool
hostiles_requiring_battle(world, party_members)   # list[EntityId]
```

The predicate gates on combat stats, so non-combatant entities (doors,
signs, dropped items) never trigger combat even if they somehow
acquire a hostile faction.

## Observation impact

The `faction` field on actors in the M35 observation snapshot now
carries the canonical id (e.g. `town`, `dungeon`) for content
spawned via the M28 path. Legacy fixtures keep their original strings
(`"player"`, `"enemy"`); the engine resolves both the same way.

## Seams for M23 (stealth and perception)

M23 will gate `is_aware_of` on visibility and a perception check before
the relation table is consulted. Until awareness fires the relation
table is irrelevant — a hostile dungeon monster the party hasn't seen
yet won't trigger combat. The relation table only decides "if we did
see this entity, would we treat it as hostile?".

Future hooks:

- Per-faction stealth/perception modifiers can layer on `FactionId`
  (e.g. `wildlife` actors easier to surprise).
- An `AggroOverride(target=PLAYER_PARTY, relation=HOSTILE)` applied
  after a failed stealth check is a clean way to model "the guards
  spotted you" without rewriting the faction graph.
