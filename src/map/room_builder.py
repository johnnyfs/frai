from dataclasses import dataclass

from src.core.combat import goblin_stats, weapon_for_name
from src.core.components import BlocksMovement, Faction, Name, PlayerControlled, Position, Presentation
from src.core.entity import EntityId
from src.core.world import World
from src.map.tiles import FLOOR, HORIZONTAL_WALL, OUTSIDE, VERTICAL_WALL, Tile


@dataclass(frozen=True, slots=True)
class BuiltRoom:
    world: World
    player: EntityId


def build_room_world(width: int, height: int) -> BuiltRoom:
    tiles = [[OUTSIDE for _ in range(width)] for _ in range(height)]
    world = World(width=width, height=height, tiles=tiles)

    room_width = min(32, max(7, width // 2))
    room_height = min(20, max(7, height // 2))
    left = max(0, (width - room_width) // 2)
    top = max(0, (height - room_height) // 2)
    right = left + room_width - 1
    bottom = top + room_height - 1

    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            if y in (top, bottom):
                tile: Tile = HORIZONTAL_WALL
            elif x in (left, right):
                tile = VERTICAL_WALL
            else:
                tile = FLOOR
            world.tiles[y][x] = tile

    player = world.create_entity()
    world.positions.add(player, Position(x=(left + right) // 2, y=(top + bottom) // 2))
    world.presentations.add(player, Presentation("@"))
    world.player_controlled.add(player, PlayerControlled())
    world.names.add(player, Name("you"))
    world.factions.add(player, Faction("player"))

    goblin = world.create_entity()
    center_x = (left + right) // 2
    center_y = (top + bottom) // 2
    world.positions.add(goblin, Position(x=min(center_x + 5, right - 1), y=center_y))
    world.presentations.add(goblin, Presentation("o"))
    world.blockers.add(goblin, BlocksMovement("occupied"))
    world.names.add(goblin, Name("goblin"))
    world.factions.add(goblin, Faction("enemy"))
    world.combat_stats.add(goblin, goblin_stats())
    world.weapons.add(goblin, weapon_for_name("dagger"))
    return BuiltRoom(world=world, player=player)
