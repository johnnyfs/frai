from dataclasses import dataclass
from enum import Enum


class TileKind(Enum):
    FLOOR = "floor"
    PASSAGE = "passage"
    WALL = "wall"
    OUTSIDE = "outside"


@dataclass(frozen=True, slots=True)
class Tile:
    kind: TileKind
    glyph: str
    blocks_movement: bool = False
    block_reason: str = "blocked"


FLOOR = Tile(TileKind.FLOOR, ".", blocks_movement=False)
PASSAGE = Tile(TileKind.PASSAGE, "#", blocks_movement=False)
HORIZONTAL_WALL = Tile(TileKind.WALL, "-", blocks_movement=True)
VERTICAL_WALL = Tile(TileKind.WALL, "|", blocks_movement=True)
WALL = HORIZONTAL_WALL
OUTSIDE = Tile(TileKind.OUTSIDE, " ", blocks_movement=True)
