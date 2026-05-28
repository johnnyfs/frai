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


_TILE_TO_TOKEN: Mapping[Tile, str] = MappingProxyType(
    {tile: token for token, tile in TERRAIN_CATALOG.items()}
)


def tile_token(tile: Tile) -> str:
    """Return the stable catalog token for ``tile``.

    Used by save/load (M16) to round-trip tile references without
    persisting the full Tile dataclass. Unknown tiles (e.g. ad-hoc test
    fixtures) fall back to a generated token built from the tile kind
    and glyph so load can reconstruct an equivalent ``Tile`` instance.
    """
    token = _TILE_TO_TOKEN.get(tile)
    if token is not None:
        return token
    return f"_adhoc.{tile.kind.value}.{tile.glyph}"


def tile_from_token(token: str) -> Tile:
    """Reverse of :func:`tile_token`.

    Resolves a catalog token to its canonical ``Tile`` singleton. Tokens
    prefixed with ``_adhoc.`` (emitted for tiles not in the catalog) are
    reconstructed as a generic ``Tile`` with the recorded kind/glyph and
    sensible defaults for everything else. This is intentionally lossy
    for fields we never persist (e.g. ``movement_cost_multiplier``) —
    if a future map authoring system needs them, it should put the tile
    in the catalog so the singleton round-trips by reference.
    """
    tile = TERRAIN_CATALOG.get(token)
    if tile is not None:
        return tile
    if token.startswith("_adhoc."):
        _, kind_value, glyph = token.split(".", 2)
        return Tile(kind=TileKind(kind_value), glyph=glyph)
    # Unknown token: fall back to OUTSIDE so load doesn't crash on a
    # save written by a future build with new tiles. The renderer will
    # paint void; tests can fail on the missing-token check.
    return OUTSIDE
