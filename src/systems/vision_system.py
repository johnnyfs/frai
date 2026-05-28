"""Vision tick: refresh party visible set and per-tile memory.

The vision system has no actions of its own and produces no effects. It is
a "projection" step: the caller invokes :meth:`VisionSystem.tick` after a
party action that could change what the party can see (movement, door
opening, party rotation). The system mutates the passed
:class:`PartyMemory` in place:

- For every party member that is alive and positioned, compute the
  visible set with :func:`compute_visible_tiles`.
- Union those sets to get the *party* visible set.
- For each visible tile, snapshot the current terrain glyph and static
  features (doors, containers, revealed traps) into memory.

Live actors and creatures are intentionally *not* written to memory.
Memory is for the layout the party has seen; live enemy / NPC positions
are reported only by the visible set.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from src.core.entity import EntityId
from src.core.vision import (
    DEFAULT_VISION_RADIUS,
    PartyMemory,
    RememberedTile,
    compute_visible_tiles,
    static_features_at,
)
from src.core.world import World
from src.systems.awareness_system import is_alive


@dataclass(slots=True)
class VisionSystem:
    radius: int = DEFAULT_VISION_RADIUS

    def tick(
        self,
        world: World,
        observers: Iterable[EntityId],
        memory: PartyMemory,
    ) -> frozenset[tuple[int, int]]:
        visible: set[tuple[int, int]] = set()
        for observer in observers:
            if not is_alive(world, observer):
                continue
            visible.update(compute_visible_tiles(world, observer, radius=self.radius))
        for x, y in visible:
            tile = world.tile_at(x, y)
            memory.remember(x, y, RememberedTile(glyph=tile.glyph, features=static_features_at(world, x, y)))
        memory.set_visible(visible)
        return memory.visible
