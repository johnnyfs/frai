from dataclasses import dataclass
from random import Random

from src.core.creatures import (
    combat_stats_for_creature,
    creature_component,
    creature_for_key,
    weapon_for_creature,
)
from src.core.components import (
    BlocksMovement,
    Faction,
    LootDrop,
    Name,
    PlayerControlled,
    Position,
    Presentation,
)
from src.core.config import PLAYFIELD_WIDTH
from src.core.entity import EntityId
from src.core.factions import FactionId
from src.core.world import World
from src.map.tiles import FLOOR, HORIZONTAL_WALL, OUTSIDE, PASSAGE, VERTICAL_WALL, Tile


@dataclass(frozen=True, slots=True)
class BuiltRoom:
    world: World
    player: EntityId


def build_room_world(width: int, height: int, rng: Random | None = None) -> BuiltRoom:
    rng = rng if rng is not None else Random()
    tiles = [[OUTSIDE for _ in range(width)] for _ in range(height)]
    world = World(width=width, height=height, tiles=tiles)

    room_width = min(32, max(7, width // 2))
    room_height = min(20, max(7, height // 2))
    left = max(0, (width - room_width) // 2)
    top = max(0, (height - room_height) // 2)
    right = left + room_width - 1
    bottom = top + room_height - 1

    _carve_room(world, left, top, room_width, room_height)

    player = world.create_entity()
    world.positions.add(player, Position(x=(left + right) // 2, y=(top + bottom) // 2))
    world.presentations.add(player, Presentation("@"))
    world.player_controlled.add(player, PlayerControlled())
    world.names.add(player, Name("you"))
    world.factions.add(player, Faction(FactionId.PLAYER_PARTY.value))

    center_x = (left + right) // 2
    center_y = (top + bottom) // 2
    for creature_key, x, y in (
        ("frog", center_x + 5, center_y),
        ("frog", center_x - 5, center_y + 2),
    ):
        _add_creature(
            world,
            creature_key,
            min(max(x, left + 1), right - 1),
            min(max(y, top + 1), bottom - 1),
        )
    _add_side_passages(world, rng, left, top, right, bottom, room_width, room_height, center_x, center_y)
    return BuiltRoom(world=world, player=player)


def _carve_room(world: World, left: int, top: int, width: int, height: int) -> None:
    right = left + width - 1
    bottom = top + height - 1
    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            if y in (top, bottom):
                tile: Tile = HORIZONTAL_WALL
            elif x in (left, right):
                tile = VERTICAL_WALL
            else:
                tile = FLOOR
            world.tiles[y][x] = tile


def _add_side_passages(
    world: World,
    rng: Random,
    left: int,
    top: int,
    right: int,
    bottom: int,
    room_width: int,
    room_height: int,
    center_x: int,
    center_y: int,
) -> None:
    visible_room_columns = 6
    side_room_width = room_width
    side_room_height = room_height
    used_doorway_ys: set[int] = set()

    left_room_right = center_x - (PLAYFIELD_WIDTH // 2) + visible_room_columns
    left_room_left = left_room_right - side_room_width + 1
    if 0 <= left_room_left and left_room_right < left - 1:
        doorway_y = _random_doorway_y(rng, top, bottom, center_y, used_doorway_ys)
        used_doorway_ys.add(doorway_y)
        side_top = _side_room_top(world.height, side_room_height, doorway_y)
        _carve_room(world, left_room_left, side_top, side_room_width, side_room_height)
        world.tiles[doorway_y][left] = FLOOR
        world.tiles[doorway_y][left_room_right] = FLOOR
        for x in range(left_room_right + 1, left):
            world.tiles[doorway_y][x] = PASSAGE

    right_room_left = center_x + (PLAYFIELD_WIDTH // 2) - visible_room_columns
    right_room_right = right_room_left + side_room_width - 1
    if right + 1 < right_room_left and right_room_right < world.width:
        doorway_y = _random_doorway_y(rng, top, bottom, center_y, used_doorway_ys)
        side_top = _side_room_top(world.height, side_room_height, doorway_y)
        _carve_room(world, right_room_left, side_top, side_room_width, side_room_height)
        world.tiles[doorway_y][right] = FLOOR
        world.tiles[doorway_y][right_room_left] = FLOOR
        for x in range(right + 1, right_room_left):
            world.tiles[doorway_y][x] = PASSAGE


def _random_doorway_y(
    rng: Random,
    top: int,
    bottom: int,
    center_y: int,
    used: set[int],
) -> int:
    choices = [y for y in range(top + 1, bottom) if y != center_y and y not in used]
    if not choices:
        choices = [y for y in range(top + 1, bottom) if y != center_y]
    if not choices:
        return center_y
    return rng.choice(choices)


def _side_room_top(world_height: int, room_height: int, doorway_y: int) -> int:
    top = doorway_y - room_height // 2
    return min(max(0, top), max(0, world_height - room_height))


def _add_creature(world: World, creature_key: str, x: int, y: int) -> EntityId:
    spec = creature_for_key(creature_key)
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation(spec.glyph))
    world.blockers.add(entity, BlocksMovement("occupied"))
    world.names.add(entity, Name(spec.name))
    world.creatures.add(entity, creature_component(spec))
    world.factions.add(entity, Faction(FactionId.DUNGEON.value))
    world.combat_stats.add(entity, combat_stats_for_creature(spec))
    world.weapons.add(entity, weapon_for_creature(spec))
    if spec.loot.entries:
        world.loot_drops.add(entity, LootDrop(table=spec.loot))
    return entity
