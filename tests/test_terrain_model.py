from dataclasses import replace

from src.core.actions import MoveAttempt
from src.core.effects import EmitMessage, MoveEntity
from src.core.world import TerrainBlocker
from src.map.tiles import (
    DUNGEON_FLOOR,
    FOREST,
    GRASS,
    HORIZONTAL_WALL,
    RUBBLE,
    TERRAIN_CATALOG,
    TOWN_FLOOR,
    VERTICAL_WALL,
    WATER,
    TileKind,
)
from src.systems.movement_system import (
    MovementContextResolver,
    MovementSystem,
    terrain_adjusted_movement_cost,
)
from src.systems.obstruction_system import ObstructionSystem
from tests.support.tiny_world import add_actor, build_tiny_map, resolve_action


def test_terrain_catalog_contains_required_map_categories() -> None:
    assert TERRAIN_CATALOG["overworld.grass"] is GRASS
    assert TERRAIN_CATALOG["forest"] is FOREST
    assert TERRAIN_CATALOG["town.floor"] is TOWN_FLOOR
    assert TERRAIN_CATALOG["dungeon.floor"] is DUNGEON_FLOOR
    assert TERRAIN_CATALOG["wall.horizontal"] is HORIZONTAL_WALL
    assert TERRAIN_CATALOG["wall.vertical"] is VERTICAL_WALL
    assert TERRAIN_CATALOG["blocked.water"] is WATER
    assert TERRAIN_CATALOG["difficult.rubble"] is RUBBLE


def test_blocked_terrain_reports_terrain_blocker_and_blocks_movement() -> None:
    world = build_tiny_map()
    player = add_actor(world, 2, 2)
    world.tiles[2][3] = WATER

    blockers = world.blockers_at(3, 2)
    result = MovementSystem(ObstructionSystem(), MovementContextResolver()).handle(
        MoveAttempt(player, 1, 0),
        world,
    )

    assert blockers == [TerrainBlocker(x=3, y=2, reason="water")]
    assert result.effects == [EmitMessage("Blocked.")]
    assert not any(isinstance(effect, MoveEntity) for effect in result.effects)


def test_difficult_terrain_keeps_cost_metadata_separate_from_blocking() -> None:
    world = build_tiny_map()
    player = add_actor(world, 2, 2)
    world.tiles[2][3] = RUBBLE

    context = MovementContextResolver().resolve(MoveAttempt(player, 1, 0), world)

    assert context.destination_tile is RUBBLE
    assert context.destination_tile.kind is TileKind.DIFFICULT
    assert context.blockers == []
    assert context.movement_cost == 6.0
    assert terrain_adjusted_movement_cost(1, 1, RUBBLE) == 8.5


def test_difficult_terrain_does_not_change_existing_move_effect_resolution() -> None:
    world = build_tiny_map()
    player = add_actor(world, 2, 2)
    world.tiles[2][3] = replace(RUBBLE)

    effects = resolve_action(MoveAttempt(player, 1, 0), world)

    assert effects == [MoveEntity(player, 3, 2)]
