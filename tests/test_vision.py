"""Tests for vision, line-of-sight, and party memory rendering."""

from src.core.components import BlocksMovement, Door, Position, Presentation
from src.core.entity import EntityId
from src.core.vision import (
    PartyMemory,
    RememberedFeature,
    RememberedTile,
    VisibilityState,
    blocks_sight,
    compute_visible_tiles,
)
from src.core.world import World
from src.map.tiles import FLOOR, HORIZONTAL_WALL, OUTSIDE, VERTICAL_WALL
from src.systems.render_system import _projected_presentation
from src.systems.vision_system import VisionSystem
from tests.support.tiny_world import add_actor, add_enemy, build_tiny_map


def _empty_world(width: int, height: int) -> World:
    tiles = [[FLOOR for _ in range(width)] for _ in range(height)]
    return World(width=width, height=height, tiles=tiles)


def _place_door(world: World, x: int, y: int, *, is_open: bool) -> EntityId:
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.doors.add(entity, Door(is_open=is_open))
    world.presentations.add(entity, Presentation("'" if is_open else "+"))
    if not is_open:
        world.blockers.add(entity, BlocksMovement("door"))
    return entity


def test_visibility_state_is_unknown_until_seen() -> None:
    memory = PartyMemory()
    assert memory.state_at(0, 0) is VisibilityState.UNKNOWN


def test_visibility_state_is_visible_when_in_current_set() -> None:
    memory = PartyMemory()
    memory.set_visible({(2, 3)})
    assert memory.state_at(2, 3) is VisibilityState.VISIBLE


def test_visibility_state_is_remembered_when_seen_but_not_visible() -> None:
    memory = PartyMemory()
    memory.remember(4, 4, RememberedTile(glyph="."))
    memory.set_visible(set())
    assert memory.state_at(4, 4) is VisibilityState.REMEMBERED


def test_blocks_sight_includes_walls_and_closed_doors_only() -> None:
    world = build_tiny_map(width=7, height=5)
    door = _place_door(world, 3, 2, is_open=False)
    assert blocks_sight(world, 0, 0) is True  # vertical wall
    assert blocks_sight(world, 2, 2) is False  # floor
    assert blocks_sight(world, 3, 2) is True  # closed door
    # Open the door.
    world.doors.require(door).is_open = True
    assert blocks_sight(world, 3, 2) is False


def test_blocks_sight_treats_out_of_bounds_as_blocked() -> None:
    world = build_tiny_map(width=5, height=5)
    assert blocks_sight(world, -1, 2) is True
    assert blocks_sight(world, 10, 10) is True


def test_compute_visible_tiles_excludes_tiles_behind_walls() -> None:
    width, height = 9, 5
    tiles = [[FLOOR for _ in range(width)] for _ in range(height)]
    for x in range(width):
        tiles[0][x] = HORIZONTAL_WALL
        tiles[height - 1][x] = HORIZONTAL_WALL
    for y in range(height):
        tiles[y][0] = VERTICAL_WALL
        tiles[y][width - 1] = VERTICAL_WALL
    # Vertical wall between the observer and the far side.
    for y in range(1, height - 1):
        tiles[y][4] = VERTICAL_WALL
    world = World(width=width, height=height, tiles=tiles)
    observer = add_actor(world, 2, 2)

    visible = compute_visible_tiles(world, observer)

    # Wall itself is visible.
    assert (4, 2) in visible
    # Anything behind the wall is not.
    assert (5, 2) not in visible
    assert (6, 2) not in visible


def test_compute_visible_tiles_sees_through_open_doorway() -> None:
    width, height = 9, 5
    tiles = [[FLOOR for _ in range(width)] for _ in range(height)]
    for x in range(width):
        tiles[0][x] = HORIZONTAL_WALL
        tiles[height - 1][x] = HORIZONTAL_WALL
    for y in range(height):
        tiles[y][0] = VERTICAL_WALL
        tiles[y][width - 1] = VERTICAL_WALL
    for y in range(1, height - 1):
        tiles[y][4] = VERTICAL_WALL
    # Carve doorway at (4, 2) so the wall has a hole.
    tiles[2][4] = FLOOR
    world = World(width=width, height=height, tiles=tiles)
    door = _place_door(world, 4, 2, is_open=True)
    observer = add_actor(world, 2, 2)

    visible = compute_visible_tiles(world, observer)

    assert (4, 2) in visible
    assert (5, 2) in visible
    assert (6, 2) in visible

    # Close the door — far side now hidden.
    world.doors.require(door).is_open = True  # sanity
    world.doors.require(door).is_open = False
    visible_closed = compute_visible_tiles(world, observer)
    assert (5, 2) not in visible_closed
    assert (6, 2) not in visible_closed


def test_vision_system_marks_visible_and_memorises_static_features() -> None:
    world = build_tiny_map(width=9, height=5)
    observer = add_actor(world, 2, 2)
    door = _place_door(world, 3, 2, is_open=False)
    memory = PartyMemory()

    VisionSystem(radius=4).tick(world, [observer], memory)

    assert (2, 2) in memory.visible
    assert (3, 2) in memory.visible
    remembered = memory.tiles[(3, 2)]
    assert any(feature.kind == "door" for feature in remembered.features)
    # The door was closed; the remembered feature reflects that.
    door_feature = next(f for f in remembered.features if f.kind == "door")
    assert door_feature.is_open is False
    _ = door  # keep reference


def test_vision_system_clears_visible_when_observer_dies() -> None:
    world = build_tiny_map(width=7, height=5)
    observer = add_actor(world, 2, 2)
    memory = PartyMemory()
    VisionSystem(radius=4).tick(world, [observer], memory)
    assert memory.visible

    # Kill the observer; visible set should empty.
    world.combat_stats.require(observer).hit_points = 0
    VisionSystem(radius=4).tick(world, [observer], memory)
    assert memory.visible == frozenset()


def test_creature_behind_wall_is_not_visible_through_party_memory() -> None:
    width, height = 9, 5
    tiles = [[FLOOR for _ in range(width)] for _ in range(height)]
    for y in range(1, height - 1):
        tiles[y][4] = VERTICAL_WALL
    world = World(width=width, height=height, tiles=tiles)
    observer = add_actor(world, 2, 2)
    hidden_enemy = add_enemy(world, 6, 2)
    memory = PartyMemory()

    VisionSystem(radius=8).tick(world, [observer], memory)

    assert memory.state_at(6, 2) is VisibilityState.UNKNOWN
    # Sanity: the wall *is* visible.
    assert (4, 2) in memory.visible
    _ = hidden_enemy


def test_moving_observer_updates_both_visible_and_remembered_sets() -> None:
    width, height = 11, 7
    tiles = [[FLOOR for _ in range(width)] for _ in range(height)]
    # Solid vertical wall blocking row y=3 (where observer sits) from the
    # far side.
    for y in range(0, height):
        tiles[y][5] = VERTICAL_WALL
    world = World(width=width, height=height, tiles=tiles)
    observer = add_actor(world, 2, 3)
    memory = PartyMemory()

    vision = VisionSystem(radius=8)
    vision.tick(world, [observer], memory)
    assert (2, 3) in memory.visible
    initial_visible = set(memory.visible)
    assert (8, 3) not in initial_visible

    # Punch a hole in the wall (open doorway) and move the observer next
    # to it; far side becomes visible.
    tiles[3][5] = FLOOR
    world.positions.require(observer).x = 4
    vision.tick(world, [observer], memory)
    assert (8, 3) in memory.visible

    # Now move beyond view of the starting cell — that tile becomes
    # remembered (no longer visible).
    world.positions.require(observer).x = 9
    vision = VisionSystem(radius=2)
    vision.tick(world, [observer], memory)
    assert memory.state_at(2, 3) is VisibilityState.REMEMBERED
    assert (2, 3) not in memory.visible


def test_renderer_blanks_unknown_tiles_and_remembers_terrain_without_live_actors() -> None:
    width, height = 9, 5
    tiles = [[FLOOR for _ in range(width)] for _ in range(height)]
    for y in range(1, height - 1):
        tiles[y][4] = VERTICAL_WALL
    world = World(width=width, height=height, tiles=tiles)
    observer = add_actor(world, 2, 2)
    enemy = add_enemy(world, 1, 2)
    memory = PartyMemory()
    VisionSystem(radius=6).tick(world, [observer], memory)

    # Unknown tile beyond the wall: blank.
    far_glyph = _projected_presentation(observer, world, 8, 2, [observer], memory)
    assert far_glyph.char == " "

    # Visible tile with live enemy: shows enemy glyph.
    enemy_glyph = _projected_presentation(observer, world, 1, 2, [observer], memory)
    assert enemy_glyph.char != " "
    assert enemy_glyph.char != "."

    # Move observer away so the enemy's tile leaves the visible set.
    world.positions.require(observer).x = 3
    world.positions.require(observer).y = 2
    # Force the enemy out of view by moving it to where we already saw floor.
    world.positions.require(enemy).x = 1
    # Recompute — visibility radius 1 keeps only nearby tiles.
    VisionSystem(radius=1).tick(world, [observer], memory)
    # The tile at (1, 2) was previously visible (and snapshot taken when
    # enemy stood there). Now out of LOS, so it must render its
    # remembered glyph (no live enemy glyph).
    remembered_glyph = _projected_presentation(observer, world, 1, 2, [observer], memory)
    assert remembered_glyph.char == "."


def test_remembered_tiles_persist_static_feature_glyph_over_terrain() -> None:
    world = build_tiny_map(width=9, height=5)
    observer = add_actor(world, 2, 2)
    door = _place_door(world, 3, 2, is_open=False)
    memory = PartyMemory()

    VisionSystem(radius=4).tick(world, [observer], memory)
    # Drop the observer out so the door tile becomes remembered.
    world.positions.require(observer).x = 1
    VisionSystem(radius=0).tick(world, [observer], memory)

    glyph = _projected_presentation(observer, world, 3, 2, [observer], memory)
    # Remembered door tile shows the door's static glyph, not the floor.
    assert glyph.char == "+"
    _ = door


def test_render_falls_back_to_omniscient_when_memory_is_none() -> None:
    tiles = [[OUTSIDE for _ in range(3)] for _ in range(3)]
    tiles[1][1] = FLOOR
    world = World(width=3, height=3, tiles=tiles)
    observer = EntityId(1)
    world.positions.add(observer, Position(x=1, y=1))

    glyph = _projected_presentation(observer, world, 1, 1, [observer], None)
    assert glyph.char == "@"


def test_open_door_visibility_unblocks_the_doorway() -> None:
    width, height = 9, 5
    tiles = [[FLOOR for _ in range(width)] for _ in range(height)]
    for y in range(1, height - 1):
        tiles[y][4] = VERTICAL_WALL
    tiles[2][4] = FLOOR
    world = World(width=width, height=height, tiles=tiles)
    door = _place_door(world, 4, 2, is_open=False)
    observer = add_actor(world, 2, 2)

    memory_closed = PartyMemory()
    VisionSystem(radius=8).tick(world, [observer], memory_closed)
    assert (6, 2) not in memory_closed.visible

    world.doors.require(door).is_open = True
    memory_open = PartyMemory()
    VisionSystem(radius=8).tick(world, [observer], memory_open)
    assert (6, 2) in memory_open.visible
