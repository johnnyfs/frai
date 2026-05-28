"""Tests for the M21 description composer.

The composer is a pure module over ``World`` (+ optional
``PartyMemory``). Tests are split into three layers:

* ``describe_terrain`` covers the per-tile prose catalog and the
  ``render_token`` overrides.
* ``describe_entity`` covers component-driven entity summaries — name,
  HP, faction, conditions, doors, traps, containers, corpses, and
  ground-item inventories.
* ``examine_tile`` covers the memory-aware path: visible vs.
  remembered vs. unknown tiles.
"""

from __future__ import annotations

from src.core.components import (
    Container,
    Corpse,
    Door,
    Inventory,
    InventoryStack,
    Lock,
    LootDrop,
    Position,
    Trap,
)
from src.core.conditions import (
    Condition,
    ConditionKind,
    ConditionStore,
    DurationPolicy,
)
from src.core.descriptions import (
    describe_entity,
    describe_terrain,
    describe_tile,
    examine_tile,
)
from src.core.loot import DropTable
from src.core.vision import (
    PartyMemory,
    RememberedFeature,
    RememberedTile,
)
from src.map.tiles import (
    DUNGEON_FLOOR,
    FLOOR,
    FOREST,
    PASSAGE,
    RUBBLE,
    WATER,
    HORIZONTAL_WALL,
)
from tests.support.tiny_world import (
    add_actor,
    add_enemy,
    build_tiny_map,
)


# ---------------------------------------------------------------------
# Terrain prose
# ---------------------------------------------------------------------


def test_describe_terrain_floor() -> None:
    assert describe_terrain(FLOOR) == "stone floor"


def test_describe_terrain_water_override() -> None:
    # ``terrain.blocked.water`` override beats the generic BLOCKED kind.
    assert describe_terrain(WATER) == "deep water"


def test_describe_terrain_forest() -> None:
    assert describe_terrain(FOREST) == "dense forest"


def test_describe_terrain_rubble() -> None:
    assert describe_terrain(RUBBLE) == "rubble"


def test_describe_terrain_passage_override() -> None:
    assert describe_terrain(PASSAGE) == "narrow passage"


def test_describe_terrain_dungeon_floor_kind_fallback() -> None:
    # No token override for dungeon floor today — falls back to the
    # per-kind prose.
    assert describe_terrain(DUNGEON_FLOOR) == "dungeon floor"


# ---------------------------------------------------------------------
# Entity descriptions
# ---------------------------------------------------------------------


def test_describe_entity_creature_includes_name_and_hp() -> None:
    world = build_tiny_map()
    enemy = add_enemy(world, 3, 2, name="frog", kind="frog", hit_points=4)
    text = describe_entity(world, enemy)
    assert "frog" in text
    assert "HP 4/4" in text


def test_describe_entity_includes_faction_label() -> None:
    world = build_tiny_map()
    enemy = add_enemy(world, 3, 2)
    text = describe_entity(world, enemy)
    assert "faction: enemy" in text


def test_describe_entity_includes_dead_marker_when_hp_zero() -> None:
    world = build_tiny_map()
    enemy = add_enemy(world, 3, 2)
    world.combat_stats.require(enemy).hit_points = 0
    text = describe_entity(world, enemy)
    assert "[dead]" in text
    assert "HP " not in text


def test_describe_entity_includes_conditions() -> None:
    world = build_tiny_map()
    actor = add_actor(world, 2, 2)
    store = ConditionStore()
    store.add(
        Condition(
            kind=ConditionKind.POISONED,
            duration=DurationPolicy.until_removed(),
        )
    )
    world.conditions.add(actor, store)
    text = describe_entity(world, actor)
    assert "conditions:" in text
    assert "poisoned" in text


def test_describe_entity_door_closed_affordance() -> None:
    world = build_tiny_map()
    entity = world.create_entity()
    world.positions.add(entity, Position(2, 2))
    world.doors.add(entity, Door(is_open=False))
    text = describe_entity(world, entity)
    assert "(closed door)" in text


def test_describe_entity_door_open_affordance() -> None:
    world = build_tiny_map()
    entity = world.create_entity()
    world.positions.add(entity, Position(2, 2))
    world.doors.add(entity, Door(is_open=True))
    text = describe_entity(world, entity)
    assert "(open door)" in text


def test_describe_entity_locked_door_is_locked() -> None:
    world = build_tiny_map()
    entity = world.create_entity()
    world.positions.add(entity, Position(2, 2))
    world.doors.add(entity, Door(is_open=False))
    world.locks.add(entity, Lock(is_locked=True))
    text = describe_entity(world, entity)
    assert "(locked door)" in text


def test_describe_entity_trap_armed() -> None:
    world = build_tiny_map()
    entity = world.create_entity()
    world.positions.add(entity, Position(2, 2))
    world.traps.add(entity, Trap(is_armed=True))
    text = describe_entity(world, entity)
    assert "(armed trap)" in text


def test_describe_entity_trap_disarmed() -> None:
    world = build_tiny_map()
    entity = world.create_entity()
    world.positions.add(entity, Position(2, 2))
    world.traps.add(entity, Trap(is_armed=False))
    text = describe_entity(world, entity)
    assert "(disarmed trap)" in text


def test_describe_entity_container_affordance() -> None:
    world = build_tiny_map()
    entity = world.create_entity()
    world.positions.add(entity, Position(2, 2))
    world.containers.add(entity, Container(is_open=False))
    text = describe_entity(world, entity)
    assert "(closed container)" in text


def test_describe_entity_corpse_includes_creature_kind() -> None:
    world = build_tiny_map()
    entity = world.create_entity()
    world.positions.add(entity, Position(2, 2))
    world.corpses.add(entity, Corpse(creature_kind="frog"))
    text = describe_entity(world, entity)
    assert "(corpse of frog)" in text


def test_describe_entity_ground_items() -> None:
    world = build_tiny_map()
    entity = world.create_entity()
    world.positions.add(entity, Position(2, 2))
    inventory = Inventory(gold=5)
    inventory.items.append(InventoryStack(item_id="weapon.club", quantity=1))
    world.inventories.add(entity, inventory)
    text = describe_entity(world, entity)
    assert "[contains:" in text
    assert "5 gold" in text
    assert "club" in text


# ---------------------------------------------------------------------
# Live tile description
# ---------------------------------------------------------------------


def test_describe_tile_returns_terrain_only_for_empty_tile() -> None:
    world = build_tiny_map()
    lines = describe_tile(world, 2, 2)
    assert lines == ["You see stone floor."]


def test_describe_tile_includes_entities() -> None:
    world = build_tiny_map()
    add_enemy(world, 3, 2, name="frog", kind="frog")
    lines = describe_tile(world, 3, 2)
    assert lines[0] == "You see stone floor."
    assert any("frog" in line for line in lines[1:])


def test_describe_tile_includes_wall_terrain() -> None:
    world = build_tiny_map()
    # The wall at (0, 0) renders "stone wall" via the token override.
    lines = describe_tile(world, 0, 0)
    assert "stone wall" in lines[0]


# ---------------------------------------------------------------------
# Memory-aware examine
# ---------------------------------------------------------------------


def test_examine_tile_unknown_returns_refusal_line() -> None:
    world = build_tiny_map()
    memory = PartyMemory()
    lines = examine_tile(world, memory, 2, 2)
    assert lines == ["You don't know what's there."]


def test_examine_tile_visible_returns_full_description() -> None:
    world = build_tiny_map()
    add_enemy(world, 3, 2, name="frog", kind="frog")
    memory = PartyMemory()
    memory.set_visible({(3, 2)})
    lines = examine_tile(world, memory, 3, 2)
    assert lines[0] == "You see stone floor."
    assert any("frog" in line for line in lines[1:])


def test_examine_tile_remembered_uses_last_seen_marker() -> None:
    world = build_tiny_map()
    memory = PartyMemory()
    memory.remember(2, 2, RememberedTile(glyph="."))
    # Not in visible set: state_at -> REMEMBERED.
    lines = examine_tile(world, memory, 2, 2)
    assert lines[0].startswith("(last seen)")
    assert "stone floor" in lines[0]


def test_examine_tile_remembered_excludes_live_creature() -> None:
    """A creature standing on a remembered tile is NOT surfaced.

    Memory tracks layout, not actor positions. The remembered tile
    description must come strictly from the cached snapshot.
    """

    world = build_tiny_map()
    add_enemy(world, 3, 2, name="frog", kind="frog")
    memory = PartyMemory()
    memory.remember(3, 2, RememberedTile(glyph="."))
    lines = examine_tile(world, memory, 3, 2)
    # No "frog" anywhere — memory description ignores live actors.
    assert all("frog" not in line for line in lines)


def test_examine_tile_remembered_shows_remembered_door() -> None:
    world = build_tiny_map()
    memory = PartyMemory()
    memory.remember(
        2,
        2,
        RememberedTile(
            glyph=".",
            features=(RememberedFeature(kind="door", glyph="+", is_open=False),),
        ),
    )
    lines = examine_tile(world, memory, 2, 2)
    assert any("door" in line for line in lines[1:])
    assert any("closed" in line for line in lines[1:])


def test_examine_tile_remembered_shows_disarmed_trap() -> None:
    world = build_tiny_map()
    memory = PartyMemory()
    memory.remember(
        2,
        2,
        RememberedTile(
            glyph=".",
            features=(RememberedFeature(kind="trap", glyph="^"),),
        ),
    )
    lines = examine_tile(world, memory, 2, 2)
    assert any("disarmed trap" in line for line in lines[1:])
