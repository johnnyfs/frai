from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class TileKind(Enum):
    FLOOR = "floor"
    PASSAGE = "passage"
    WALL = "wall"
    OUTSIDE = "outside"
    OVERWORLD = "overworld"
    FOREST = "forest"
    TOWN = "town"
    DUNGEON = "dungeon"
    BLOCKED = "blocked"
    DIFFICULT = "difficult"


@dataclass(frozen=True, slots=True)
class Tile:
    kind: TileKind
    glyph: str
    blocks_movement: bool = False
    block_reason: str = "blocked"
    movement_cost_multiplier: float = 1.0
    render_token: str = "terrain"
    color_token: str = "terrain.default"


FLOOR = Tile(
    TileKind.FLOOR,
    ".",
    blocks_movement=False,
    render_token="terrain.room.floor",
    color_token="terrain.stone.floor",
)
PASSAGE = Tile(
    TileKind.PASSAGE,
    "#",
    blocks_movement=False,
    render_token="terrain.passage",
    color_token="terrain.stone.passage",
)
HORIZONTAL_WALL = Tile(
    TileKind.WALL,
    "-",
    blocks_movement=True,
    block_reason="wall",
    render_token="terrain.wall.horizontal",
    color_token="terrain.stone.wall",
)
VERTICAL_WALL = Tile(
    TileKind.WALL,
    "|",
    blocks_movement=True,
    block_reason="wall",
    render_token="terrain.wall.vertical",
    color_token="terrain.stone.wall",
)
WALL = HORIZONTAL_WALL
OUTSIDE = Tile(
    TileKind.OUTSIDE,
    " ",
    blocks_movement=True,
    block_reason="outside map",
    render_token="terrain.outside",
    color_token="terrain.void",
)

GRASS = Tile(
    TileKind.OVERWORLD,
    ",",
    blocks_movement=False,
    render_token="terrain.overworld.grass",
    color_token="terrain.grass",
)
ROAD = Tile(
    TileKind.OVERWORLD,
    "=",
    blocks_movement=False,
    render_token="terrain.overworld.road",
    color_token="terrain.road",
)
FOREST = Tile(
    TileKind.FOREST,
    "T",
    blocks_movement=False,
    movement_cost_multiplier=2.0,
    render_token="terrain.forest",
    color_token="terrain.forest",
)
TOWN_FLOOR = Tile(
    TileKind.TOWN,
    ".",
    blocks_movement=False,
    render_token="terrain.town.floor",
    color_token="terrain.town.floor",
)
DUNGEON_FLOOR = Tile(
    TileKind.DUNGEON,
    ".",
    blocks_movement=False,
    render_token="terrain.dungeon.floor",
    color_token="terrain.dungeon.floor",
)
WATER = Tile(
    TileKind.BLOCKED,
    "~",
    blocks_movement=True,
    block_reason="water",
    render_token="terrain.blocked.water",
    color_token="terrain.water",
)
RUBBLE = Tile(
    TileKind.DIFFICULT,
    ";",
    blocks_movement=False,
    movement_cost_multiplier=2.0,
    render_token="terrain.difficult.rubble",
    color_token="terrain.rubble",
)

TERRAIN_CATALOG: Mapping[str, Tile] = MappingProxyType(
    {
        "room.floor": FLOOR,
        "passage": PASSAGE,
        "wall.horizontal": HORIZONTAL_WALL,
        "wall.vertical": VERTICAL_WALL,
        "outside": OUTSIDE,
        "overworld.grass": GRASS,
        "overworld.road": ROAD,
        "forest": FOREST,
        "town.floor": TOWN_FLOOR,
        "dungeon.floor": DUNGEON_FLOOR,
        "blocked.water": WATER,
        "difficult.rubble": RUBBLE,
    }
)
