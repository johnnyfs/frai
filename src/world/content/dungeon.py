"""Per-level dungeon population for the Sunken Halls (M15).

The world skeleton (:mod:`src.world.content.skeleton`) paints the three
dungeon levels and links them with stairs but leaves them empty content-
wise. This module owns the *flavour* of each level: signature kobold
variants, encounter groupings, traps, locked containers, and treasure.

Design notes
------------
- Content is fully data-driven. Each level is described by a typed
  ``DungeonLevelSpec`` (creature spawns, traps, containers) that the
  populate functions translate into world entities. No scripting.
- Placement is deterministic. Coordinates are computed from each
  level's ``Rect`` so the layout survives bounds tweaks. Every spawn
  goes through a "is this tile open?" guard so the path connecting the
  stairs stays clear and no two entities collide.
- Save-friendly. Every component used here (``CombatStats``, ``Trap``,
  ``Lock``, ``Container``, ``Inventory``, ``LootDrop`` …) already round-
  trips via the M16 save layer; the dungeon module only composes them.
- The M14 boss (``boss_kobold_warlord``) is still spawned by
  :func:`src.world.content.skeleton._populate_dungeon_boss`. This
  module adds the surrounding *escort* + level signature monsters but
  leaves the boss-marker plumbing in place.

Tuning
------
- L1 (Approach): 3 kobold scouts in two encounter groups. One trap on
  the approach corridor, one locked footlocker with a healing potion.
  Low DCs (8/10). Drops are mostly copper; one potion in the chest.
- L2 (Barracks): 3 kobold soldiers in two encounter groups. One armed
  pressure plate trap, one locked chest containing a weapon and gold.
  Mid DCs (12).
- L3 (Throne): 1 kobold elite as an escort plus the M14 warlord boss.
  One trap right before the throne, one locked strongbox with healing
  potions for the burn-resources moment. High DCs (15).

The numbers are tuned so a four-PC level-1 party can clear the dungeon
without long-resting between levels, but should choose to burn a slot
or potion or two on the boss fight. See
:func:`tests.test_dungeon_content` for the boss-fight balance
simulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.components import (
    AI,
    AIBehaviorType,
    BlocksMovement,
    Container,
    Faction,
    Inventory,
    Lock,
    LootDrop,
    Name,
    Position,
    Presentation,
    Trap,
)
from src.core.creatures import (
    combat_stats_for_creature,
    creature_component,
    creature_for_key,
    weapon_for_creature,
)
from src.core.entity import EntityId
from src.core.factions import FactionId
from src.core.items import add_item
from src.core.world import World


# ---------------------------------------------------------------------------
# Typed level descriptors
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreatureSpawnSpec:
    """A single creature spawn on a dungeon level.

    ``key`` is the catalog key in :data:`src.core.creatures.CREATURES`.
    ``offset`` is a ``(dx, dy)`` offset relative to the level's anchor
    point. ``ai_override`` lets specific spawns deviate from the
    catalog AI (e.g. one scout that holds-position instead of
    wandering).
    """

    key: str
    offset: tuple[int, int]
    ai_override: AI | None = None


@dataclass(frozen=True, slots=True)
class TrapSpec:
    """A single trap on a dungeon level."""

    offset: tuple[int, int]
    disarm_dc: int = 10
    damage: int = 2


@dataclass(frozen=True, slots=True)
class ContainerSpec:
    """A locked or unlocked container with seeded loot."""

    offset: tuple[int, int]
    name: str = "footlocker"
    gold: int = 0
    items: tuple[str, ...] = ()
    locked: bool = False
    pick_dc: int = 10


@dataclass(frozen=True, slots=True)
class DungeonLevelSpec:
    """Typed configuration for a single dungeon level (M15)."""

    location_id: str
    creatures: tuple[CreatureSpawnSpec, ...] = field(default_factory=tuple)
    traps: tuple[TrapSpec, ...] = field(default_factory=tuple)
    containers: tuple[ContainerSpec, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Per-level content (data only — no spawn logic here)
# ---------------------------------------------------------------------------


# L1 layout (anchor 70,37; rect 62..79 x 34..41).
# The connecting path drops in from column 70 row 22..37 (from the
# dungeon entrance) and exits east along row 37 toward L2. We spawn
# scouts and content off that spine so the player has to step off
# the road to engage them — and the trap sits on the spine so they
# can't avoid the encounter.
DUNGEON_LEVEL_1 = DungeonLevelSpec(
    location_id="dungeon_level_1",
    creatures=(
        # Encounter 1: pair of scouts in the west alcove. WANDER from
        # the catalog AI lets them shuffle around their starting tiles.
        CreatureSpawnSpec(key="kobold_scout", offset=(-6, 2)),
        CreatureSpawnSpec(key="kobold_scout", offset=(-5, 3)),
        # Encounter 2: lone scout in the east alcove.
        CreatureSpawnSpec(key="kobold_scout", offset=(6, 3)),
    ),
    traps=(
        # Pressure plate one tile south of the anchor — directly on
        # the spine, low damage so the party feels the warning rather
        # than getting hammered.
        TrapSpec(offset=(0, 2), disarm_dc=8, damage=2),
    ),
    containers=(
        # Low-DC footlocker in the south-west corner of the level rect.
        # Drops one healing potion plus a handful of copper.
        ContainerSpec(
            offset=(-7, 3),
            name="footlocker",
            gold=8,
            items=("consumable.healing_potion",),
            locked=True,
            pick_dc=10,
        ),
    ),
)


# L2 layout (anchor 98,37; rect 90..107 x 34..41).
# Through-path runs east-west on row 37 from L1, then south on column
# 98 to L3. Encounters fan north/south of the spine.
DUNGEON_LEVEL_2 = DungeonLevelSpec(
    location_id="dungeon_level_2",
    creatures=(
        # Encounter 1: pair of soldiers covering the north wall.
        CreatureSpawnSpec(key="kobold_soldier", offset=(-5, -2)),
        CreatureSpawnSpec(key="kobold_soldier", offset=(-3, -2)),
        # Encounter 2: lone soldier in the south alcove guarding the
        # stairs down to L3.
        CreatureSpawnSpec(key="kobold_soldier", offset=(5, 3)),
    ),
    traps=(
        # Mid-DC trap covering the corridor east of the anchor — the
        # path the player has to take to reach the stairs down to L3.
        TrapSpec(offset=(3, 0), disarm_dc=12, damage=4),
    ),
    containers=(
        # Mid-DC strongbox in the south-east corner. Drops a real
        # weapon (longsword) plus a stash of gold.
        ContainerSpec(
            offset=(7, 3),
            name="strongbox",
            gold=25,
            items=("weapon.longsword",),
            locked=True,
            pick_dc=12,
        ),
    ),
)


# L3 layout (anchor 98,50; rect 90..107 x 47..53).
# Boss sits one tile east of the anchor (see skeleton._populate_dungeon_boss).
# The elite is positioned to flank the party as they approach the boss
# room. Trap is between the stairs and the throne.
DUNGEON_LEVEL_3 = DungeonLevelSpec(
    location_id="dungeon_level_3",
    creatures=(
        # Elite escort — flanks from the south so the party can't
        # focus-fire the boss without dealing with the escort.
        CreatureSpawnSpec(key="kobold_elite", offset=(2, 2)),
    ),
    traps=(
        # High-DC trap on the throne approach. Higher damage so the
        # party has a real reason to spend a turn disarming.
        TrapSpec(offset=(-3, 0), disarm_dc=15, damage=6),
    ),
    containers=(
        # The warlord's treasury — a high-DC strongbox holding the
        # "burn resources beforehand" healing potions. Placed off the
        # boss tile so it remains lootable after the fight.
        ContainerSpec(
            offset=(-5, 2),
            name="strongbox",
            gold=40,
            items=(
                "consumable.healing_potion",
                "consumable.healing_potion",
            ),
            locked=True,
            pick_dc=15,
        ),
    ),
)


DUNGEON_LEVELS: tuple[DungeonLevelSpec, ...] = (
    DUNGEON_LEVEL_1,
    DUNGEON_LEVEL_2,
    DUNGEON_LEVEL_3,
)


# ---------------------------------------------------------------------------
# Populate helpers
# ---------------------------------------------------------------------------


def populate_dungeon_level(
    world: World,
    spec: DungeonLevelSpec,
    anchor_x: int,
    anchor_y: int,
    *,
    bounds_left: int,
    bounds_top: int,
    bounds_right: int,
    bounds_bottom: int,
) -> tuple[list[EntityId], list[EntityId], list[EntityId]]:
    """Populate one dungeon level from its typed spec.

    Returns ``(creatures, traps, containers)`` — the entity ids in the
    order they were spawned. Callers (the skeleton builder, tests) can
    use them to assert per-level content without re-querying the
    world. Spawns that would land outside the level rect or on an
    already-occupied tile are silently skipped: this keeps the build
    robust if a future bounds tweak shrinks a level.
    """

    creatures = [
        _spawn_creature_from_spec(world, creature, anchor_x, anchor_y, bounds_left, bounds_top, bounds_right, bounds_bottom)
        for creature in spec.creatures
    ]
    traps = [
        _spawn_trap_from_spec(world, trap, anchor_x, anchor_y, bounds_left, bounds_top, bounds_right, bounds_bottom)
        for trap in spec.traps
    ]
    containers = [
        _spawn_container_from_spec(world, container, anchor_x, anchor_y, bounds_left, bounds_top, bounds_right, bounds_bottom)
        for container in spec.containers
    ]
    return (
        [entity for entity in creatures if entity is not None],
        [entity for entity in traps if entity is not None],
        [entity for entity in containers if entity is not None],
    )


def _spawn_creature_from_spec(
    world: World,
    spec: CreatureSpawnSpec,
    anchor_x: int,
    anchor_y: int,
    bounds_left: int,
    bounds_top: int,
    bounds_right: int,
    bounds_bottom: int,
) -> EntityId | None:
    x = anchor_x + spec.offset[0]
    y = anchor_y + spec.offset[1]
    if not _is_free_spawn_tile(world, x, y, bounds_left, bounds_top, bounds_right, bounds_bottom):
        return None
    creature = creature_for_key(spec.key)
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation(creature.glyph))
    world.names.add(entity, Name(creature.name))
    world.factions.add(entity, Faction(FactionId.DUNGEON.value))
    world.blockers.add(entity, BlocksMovement("occupied"))
    world.creatures.add(entity, creature_component(creature))
    world.combat_stats.add(entity, combat_stats_for_creature(creature))
    world.weapons.add(entity, weapon_for_creature(creature))
    ai = spec.ai_override or creature.ai or AI(behavior=AIBehaviorType.CHASE)
    world.ai.add(entity, ai)
    if creature.loot.entries:
        world.loot_drops.add(entity, LootDrop(table=creature.loot))
    return entity


def _spawn_trap_from_spec(
    world: World,
    spec: TrapSpec,
    anchor_x: int,
    anchor_y: int,
    bounds_left: int,
    bounds_top: int,
    bounds_right: int,
    bounds_bottom: int,
) -> EntityId | None:
    x = anchor_x + spec.offset[0]
    y = anchor_y + spec.offset[1]
    if not _is_free_spawn_tile(world, x, y, bounds_left, bounds_top, bounds_right, bounds_bottom):
        return None
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation("^"))
    world.names.add(entity, Name("trap"))
    world.traps.add(entity, Trap(is_armed=True, disarm_dc=spec.disarm_dc, damage=spec.damage))
    return entity


def _spawn_container_from_spec(
    world: World,
    spec: ContainerSpec,
    anchor_x: int,
    anchor_y: int,
    bounds_left: int,
    bounds_top: int,
    bounds_right: int,
    bounds_bottom: int,
) -> EntityId | None:
    x = anchor_x + spec.offset[0]
    y = anchor_y + spec.offset[1]
    if not _is_free_spawn_tile(world, x, y, bounds_left, bounds_top, bounds_right, bounds_bottom):
        return None
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation("="))
    world.names.add(entity, Name(spec.name))
    world.containers.add(entity, Container(is_open=False))
    world.blockers.add(entity, BlocksMovement("container"))
    inventory = Inventory(gold=spec.gold)
    for item_id in spec.items:
        add_item(inventory, item_id)
    world.inventories.add(entity, inventory)
    if spec.locked:
        world.locks.add(entity, Lock(is_locked=True, pick_dc=spec.pick_dc))
    return entity


def _is_free_spawn_tile(
    world: World,
    x: int,
    y: int,
    bounds_left: int,
    bounds_top: int,
    bounds_right: int,
    bounds_bottom: int,
) -> bool:
    """True iff ``(x, y)`` is inside the rect, walkable, and unoccupied."""
    if not (bounds_left <= x <= bounds_right and bounds_top <= y <= bounds_bottom):
        return False
    if world.tile_at(x, y).blocks_movement:
        return False
    if world.entities_at(x, y):
        return False
    return True
