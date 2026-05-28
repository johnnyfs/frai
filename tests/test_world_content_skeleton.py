from src.core.actions import MoveAttempt
from src.core.effects import MoveEntity
from src.map.tiles import DUNGEON_FLOOR, FOREST, GRASS, ROAD, TOWN_FLOOR
from src.systems.movement_system import MovementContextResolver, MovementSystem
from src.systems.obstruction_system import ObstructionSystem
from src.world.content.skeleton import (
    REQUIRED_LOCATION_IDS,
    LinkKind,
    LocationKind,
    Point,
    build_world_skeleton,
    connected_location_ids,
    reachable_points,
    shortest_walkable_path,
)


def test_world_skeleton_contains_required_locations_and_terrain() -> None:
    built = build_world_skeleton()

    assert set(built.locations) == set(REQUIRED_LOCATION_IDS)
    assert built.locations["overworld"].kind is LocationKind.OVERWORLD
    assert built.locations["town"].kind is LocationKind.TOWN
    assert built.locations["forest"].kind is LocationKind.FOREST
    assert built.locations["dungeon_entrance"].kind is LocationKind.DUNGEON_ENTRANCE
    assert built.locations["dungeon_level_1"].kind is LocationKind.DUNGEON_LEVEL
    assert built.locations["dungeon_level_2"].kind is LocationKind.DUNGEON_LEVEL
    assert built.locations["dungeon_level_3"].kind is LocationKind.DUNGEON_LEVEL

    assert any(tile is GRASS for row in built.world.tiles for tile in row)
    assert any(tile is ROAD for row in built.world.tiles for tile in row)
    assert any(tile is TOWN_FLOOR for row in built.world.tiles for tile in row)
    assert any(tile is FOREST for row in built.world.tiles for tile in row)
    assert any(tile is DUNGEON_FLOOR for row in built.world.tiles for tile in row)


def test_world_skeleton_location_graph_is_connected() -> None:
    built = build_world_skeleton()

    assert connected_location_ids(built.links, "overworld") == set(REQUIRED_LOCATION_IDS)


def test_world_skeleton_dungeon_levels_are_connected_in_order() -> None:
    built = build_world_skeleton()

    dungeon_links = [
        (link.source_id, link.target_id)
        for link in built.links
        if link.kind is LinkKind.STAIRS_DOWN
    ]

    assert dungeon_links == [
        ("dungeon_entrance", "dungeon_level_1"),
        ("dungeon_level_1", "dungeon_level_2"),
        ("dungeon_level_2", "dungeon_level_3"),
    ]


def test_world_skeleton_required_locations_are_tile_reachable() -> None:
    built = build_world_skeleton()
    start = built.locations["overworld"].anchor

    reachable = reachable_points(built.world, start)

    for location_id in REQUIRED_LOCATION_IDS:
        assert built.locations[location_id].anchor in reachable


def test_world_skeleton_player_can_walk_to_each_required_location() -> None:
    built = build_world_skeleton()
    movement = MovementSystem(ObstructionSystem(), MovementContextResolver())

    for location_id in REQUIRED_LOCATION_IDS:
        goal = built.locations[location_id].anchor
        _walk_player_to(built, movement, goal)
        position = built.world.positions.require(built.player)
        assert Point(position.x, position.y) == goal


def _walk_player_to(
    built,
    movement: MovementSystem,
    goal: Point,
) -> None:
    position = built.world.positions.require(built.player)
    path = shortest_walkable_path(built.world, Point(position.x, position.y), goal)
    assert path

    for step in path[1:]:
        position = built.world.positions.require(built.player)
        action = MoveAttempt(
            built.player,
            dx=step.x - position.x,
            dy=step.y - position.y,
        )
        result = movement.handle(action, built.world)
        moves = [effect for effect in result.effects if isinstance(effect, MoveEntity)]
        assert moves == [MoveEntity(built.player, step.x, step.y)]
        position.x = step.x
        position.y = step.y
