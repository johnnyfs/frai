"""Vision, line-of-sight, and party memory primitives.

This module supplies the data types and the pure LOS function used by
``VisionSystem``. It deliberately stays out of rendering and turn flow:

- :class:`VisibilityState` tags a tile cell as ``unknown`` / ``remembered`` /
  ``visible``.
- :class:`LightLevel` is a two-state ``lit`` / ``dark`` value attached to a
  tile (today derived from terrain; later milestones can add light sources).
- :class:`RememberedTile` is what we keep in :class:`PartyMemory` for a tile
  the party has previously seen: the last-known tile glyph plus the static
  features that were on the tile when last observed. Live entities are
  *not* remembered; they are reported only when actually in the visible
  set.
- :func:`compute_visible_tiles` is a pure function over a ``World`` and an
  observer position. It walks every cell within radius and uses a
  Bresenham line to check whether the cell is reachable from the observer
  without crossing a sight-blocker.

LOS algorithm choice
--------------------

We use a *Bresenham ray per target tile within radius* algorithm. Each tile
in the bounding square of ``radius`` around the observer is tested by
walking the integer Bresenham line from observer to tile and rejecting the
tile if any intermediate cell blocks sight. The observer's own tile is
always visible. We accept slight asymmetry at the edges (a sight-blocking
tile is included if the ray reaches it) because the acceptance tests for
M19 ("creature behind wall is not visible", "LOS through a doorway works;
through closed door does not") are unambiguous under this model. Shadow-
casting is more efficient at large radii but the engine's working radius
is small and the doors/walls case is what we actually need to be correct.
This module exposes :func:`blocks_sight` so future milestones (Stealth,
Targeting) can extend the predicate (e.g. smoke, foliage) without
rewriting the LOS walker.

Light levels are consumed by :func:`compute_visible_tiles` only to the
extent that a ``dark`` tile is *only* visible if the observer is adjacent
to it. This keeps the seam in place for M11 / M23 to extend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from src.core.entity import EntityId
from src.core.world import World


class VisibilityState(str, Enum):
    UNKNOWN = "unknown"
    REMEMBERED = "remembered"
    VISIBLE = "visible"


class LightLevel(str, Enum):
    LIT = "lit"
    DARK = "dark"


DEFAULT_VISION_RADIUS = 10


@dataclass(frozen=True, slots=True)
class RememberedFeature:
    """A static, memorable feature on a tile.

    ``kind`` is a stable string token (``"door"``, ``"container"``,
    ``"trap"``) and ``glyph`` is the glyph that should render when the tile
    is in :class:`VisibilityState.REMEMBERED` state. Live actors / mobile
    creatures are intentionally excluded; memory is for the world layout.
    """

    kind: str
    glyph: str
    is_open: bool = False


@dataclass(slots=True)
class RememberedTile:
    """Snapshot of a tile last time the party saw it."""

    glyph: str
    features: tuple[RememberedFeature, ...] = ()


@dataclass(slots=True)
class PartyMemory:
    """Per-party tile memory keyed by ``(x, y)``.

    The renderer asks :meth:`state_at` for each on-screen cell, then
    chooses what to draw based on whether the cell is in the current
    visible set, has a remembered snapshot, or is unknown. Memory is
    intentionally a flat map keyed by integer coordinates; when the engine
    grows multiple maps (M16 save/restore) this becomes a per-map-id dict
    keyed by the active map id.
    """

    tiles: dict[tuple[int, int], RememberedTile] = field(default_factory=dict)
    visible: frozenset[tuple[int, int]] = field(default_factory=frozenset)

    def state_at(self, x: int, y: int) -> VisibilityState:
        if (x, y) in self.visible:
            return VisibilityState.VISIBLE
        if (x, y) in self.tiles:
            return VisibilityState.REMEMBERED
        return VisibilityState.UNKNOWN

    def remember(self, x: int, y: int, tile: RememberedTile) -> None:
        self.tiles[(x, y)] = tile

    def set_visible(self, cells: Iterable[tuple[int, int]]) -> None:
        self.visible = frozenset(cells)


def blocks_sight(world: World, x: int, y: int) -> bool:
    """True if the cell at ``(x, y)`` blocks the path of a sight ray.

    Today: out-of-bounds tiles, terrain with ``blocks_movement`` (walls,
    water), and *closed* doors block sight. Open doors do not. This is the
    one extension point the LOS walker uses; future milestones that
    introduce smoke, foliage, magical darkness, etc. should extend here.
    """
    if not (0 <= x < world.width and 0 <= y < world.height):
        return True
    tile = world.tile_at(x, y)
    if tile.blocks_movement:
        return True
    for entity in world.entities_at(x, y):
        if world.doors.has(entity):
            door = world.doors.require(entity)
            if not door.is_open:
                return True
    return False


def light_level_at(world: World, x: int, y: int) -> LightLevel:
    """Two-state light level for a tile.

    M19 keeps this conservative: anywhere inside the bounds of the world
    is :attr:`LightLevel.LIT`. The seam exists so M11 (spells) and later
    light-source content can mark dim rooms as ``DARK`` without touching
    the LOS walker.
    """
    if not (0 <= x < world.width and 0 <= y < world.height):
        return LightLevel.DARK
    return LightLevel.LIT


def compute_visible_tiles(
    world: World,
    observer: EntityId,
    *,
    radius: int = DEFAULT_VISION_RADIUS,
) -> set[tuple[int, int]]:
    """Pure function: tiles visible to ``observer`` right now.

    Walks every cell in a square of side ``2*radius+1`` centred on the
    observer's position and returns those whose Bresenham line from the
    observer is unobstructed (excluding the destination tile from the
    blocker test, so wall tiles at the edge of LOS are themselves seen).
    Dark tiles are only visible if the observer is adjacent to them.
    """
    if not world.positions.has(observer):
        return set()
    origin = world.positions.require(observer)
    ox, oy = origin.x, origin.y
    visible: set[tuple[int, int]] = {(ox, oy)}
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx == 0 and dy == 0:
                continue
            if dx * dx + dy * dy > radius * radius:
                continue
            tx, ty = ox + dx, oy + dy
            if not (0 <= tx < world.width and 0 <= ty < world.height):
                continue
            if not _ray_clear(world, ox, oy, tx, ty):
                continue
            if light_level_at(world, tx, ty) is LightLevel.DARK:
                if max(abs(dx), abs(dy)) > 1:
                    continue
            visible.add((tx, ty))
    return visible


def _ray_clear(world: World, x0: int, y0: int, x1: int, y1: int) -> bool:
    """True if the integer Bresenham line from start to end is unblocked.

    The start and end cells are *not* tested for blocking, so a wall tile
    at the end of a ray is reported as visible (you can see the wall) and
    a player standing inside a non-floor cell would still see their own
    cell. Intermediate cells are tested via :func:`blocks_sight`.
    """
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x1 > x0 else -1
    sy = 1 if y1 > y0 else -1
    err = dx - dy
    x, y = x0, y0
    while True:
        if (x, y) != (x0, y0) and (x, y) != (x1, y1):
            if blocks_sight(world, x, y):
                return False
        if x == x1 and y == y1:
            return True
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x += sx
        if e2 < dx:
            err += dx
            y += sy


def static_features_at(world: World, x: int, y: int) -> tuple[RememberedFeature, ...]:
    """Static, memorable features on a tile (doors, containers, traps).

    Mobile creatures and party members are excluded — memory is for layout,
    not live actor positions. Traps that are not yet revealed (still armed
    and not yet triggered) are *not* memorised; that's a future seam for
    M23 perception. We currently memorise armed traps as plain glyphs only
    once they have been disarmed or triggered.
    """
    features: list[RememberedFeature] = []
    for entity in world.entities_at(x, y):
        if world.doors.has(entity):
            door = world.doors.require(entity)
            glyph = _presentation_glyph(world, entity) or ("'" if door.is_open else "+")
            features.append(RememberedFeature(kind="door", glyph=glyph, is_open=door.is_open))
            continue
        if world.containers.has(entity):
            container = world.containers.require(entity)
            glyph = _presentation_glyph(world, entity) or "="
            features.append(
                RememberedFeature(kind="container", glyph=glyph, is_open=container.is_open)
            )
            continue
        if world.traps.has(entity):
            trap = world.traps.require(entity)
            if trap.is_armed:
                continue
            glyph = _presentation_glyph(world, entity) or "^"
            features.append(RememberedFeature(kind="trap", glyph=glyph))
    return tuple(features)


def _presentation_glyph(world: World, entity: EntityId) -> str | None:
    presentation = world.presentations.get(entity)
    if presentation is None:
        return None
    return presentation.glyph
