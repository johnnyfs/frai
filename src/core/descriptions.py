"""Tile / entity description composer for the M21 examine command.

The examine modal (``UIMode.targeting`` opened with a `look` flavour)
needs short, structured prose describing whatever the cursor is on:
the terrain, any creature, item, door, trap, or container, plus the
basic affordance hint a player needs to decide whether to interact.

Why a separate module?
----------------------

* **Reuse.** The same text composer feeds two clients: the player-
  facing examine modal (M21) and the structured-observation system
  (M35) — agents will read these strings to learn "what's at the
  cursor" without re-implementing the rules.
* **Composability.** Tiles can hold any mix of terrain, entities,
  items, conditions. Rather than a giant per-entity ``__str__`` we
  build the description from typed components: each helper turns one
  component into one line, and ``describe_tile`` stitches them.
* **Memory-aware.** Examine respects M19 vision/memory. A
  ``RememberedTile`` description carries a ``last seen`` marker; an
  unknown tile is reported as such with no leak of live world state.

The module is purposefully pure: every entry point is a function over
``(World, ...)`` (plus ``PartyMemory`` for memory-aware paths) and
returns strings. There is no App / dispatcher / RNG dependency, which
keeps the composer testable in isolation and safe to call from
``observe()``.

Returned strings
----------------

Each ``describe_*`` returns a single-line summary. ``examine_tile``
returns ``list[str]`` so the caller can choose whether to emit each
line separately (the App message log paginates one-at-a-time) or join
them with a separator. The order is:

1. Memory marker (if examining a remembered/unknown tile).
2. Terrain description.
3. One line per entity on the tile (creatures, items, doors, traps,
   containers, corpses) in a stable order.

Terminology
-----------

* "live" tile — the cursor is on a tile currently in ``memory.visible``.
* "remembered" tile — the tile has been seen before but is not in the
  live visible set. Static features (terrain, doors, containers, traps
  that have already been disarmed/triggered) are reported with a
  ``last seen`` prefix. Live creatures and items are *not* surfaced
  because memory doesn't track them.
* "unknown" tile — never been seen. We emit a single line and stop.
"""

from __future__ import annotations

from src.core.components import (
    Container,
    Corpse,
    Door,
    Faction,
    Lock,
    Trap,
)
from src.core.conditions import ConditionStore
from src.core.entity import EntityId
from src.core.items import ITEMS
from src.core.vision import PartyMemory, RememberedFeature, VisibilityState
from src.core.world import World
from src.map.tiles import Tile, TileKind


# Single, stable per-tile prose for each :class:`TileKind`. The catalog
# is kept inside this module (rather than living on the ``Tile`` dataclass)
# so future tile flavour text doesn't bloat the world-state types. New
# tile kinds fall through to a generic "unknown terrain" entry.
_TERRAIN_PROSE: dict[TileKind, str] = {
    TileKind.FLOOR: "stone floor",
    TileKind.PASSAGE: "narrow passage",
    TileKind.WALL: "solid wall",
    TileKind.OUTSIDE: "open void",
    TileKind.OVERWORLD: "overworld terrain",
    TileKind.FOREST: "dense forest",
    TileKind.TOWN: "town floor",
    TileKind.DUNGEON: "dungeon floor",
    TileKind.BLOCKED: "impassable terrain",
    TileKind.DIFFICULT: "difficult terrain",
}


# Per-token terrain prose overrides. ``Tile.render_token`` carries the
# fine-grained variant id (e.g. ``terrain.blocked.water``) so we can
# surface "deep water" without exploding the :class:`TileKind` enum.
_TERRAIN_TOKEN_OVERRIDES: dict[str, str] = {
    "terrain.blocked.water": "deep water",
    "terrain.difficult.rubble": "rubble",
    "terrain.overworld.grass": "grassland",
    "terrain.overworld.road": "road",
    "terrain.passage": "narrow passage",
    "terrain.wall.horizontal": "stone wall",
    "terrain.wall.vertical": "stone wall",
    "terrain.outside": "open void",
}


def describe_terrain(tile: Tile) -> str:
    """One-line terrain description for ``tile``.

    Consults the per-token override map first so map authors can opt
    into more flavourful prose for a specific tile (e.g. "deep water"
    instead of "impassable terrain"); falls back to the per-kind prose
    when no override exists. The returned string is suitable for direct
    emit; callers add the memory marker / leading article themselves.
    """

    prose = _TERRAIN_TOKEN_OVERRIDES.get(tile.render_token)
    if prose is not None:
        return prose
    return _TERRAIN_PROSE.get(tile.kind, "unknown terrain")


def describe_entity(world: World, entity: EntityId) -> str:
    """Short single-line summary for ``entity``.

    The summary composes from the typed components on the entity:

    * Name (always; falls back to ``entity <id>`` via ``world.name_for``).
    * Creature kind (when the entity has a ``Creature`` component).
    * HP / max HP (when ``CombatStats`` are present).
    * Faction tag (when ``Faction`` is present and meaningful).
    * Conditions (when the ``ConditionStore`` is non-empty).
    * Door / lock / trap / container affordance hints.
    * Item description (when the entity is a ground item with an
      ``Inventory``).

    The composer is intentionally tolerant: missing components just
    skip the corresponding fragment. This means an entity with only a
    name still produces a sensible summary, which is what the test
    fixtures and ad-hoc world dumps need.
    """

    name = world.name_for(entity)
    fragments: list[str] = [name]

    creature = world.creatures.get(entity)
    if creature is not None and creature.kind and creature.kind != name:
        fragments.append(f"({creature.kind})")

    stats = world.combat_stats.get(entity)
    if stats is not None:
        if stats.hit_points <= 0:
            fragments.append("[dead]")
        else:
            fragments.append(f"HP {stats.hit_points}/{stats.max_hit_points}")

    faction_label = _faction_label(world.factions.get(entity))
    if faction_label is not None:
        fragments.append(f"faction: {faction_label}")

    condition_text = _conditions_summary(world.conditions.get(entity))
    if condition_text:
        fragments.append(f"conditions: {condition_text}")

    affordance = _affordance_summary(world, entity)
    if affordance is not None:
        fragments.append(affordance)

    item_text = _ground_item_summary(world, entity)
    if item_text is not None:
        fragments.append(item_text)

    return " ".join(fragments)


def describe_tile(world: World, x: int, y: int) -> list[str]:
    """Compose a live tile description.

    Always returns at least one line (the terrain). Each entity on the
    tile contributes one additional line. Used by ``examine_tile`` for
    in-view tiles; memory-aware callers should use ``examine_tile``
    instead.
    """

    tile = world.tile_at(x, y)
    lines: list[str] = [_format_terrain_line(tile)]
    for entity in world.entities_at(x, y):
        lines.append(describe_entity(world, entity))
    return lines


def examine_tile(
    world: World, memory: PartyMemory, x: int, y: int
) -> list[str]:
    """Memory-aware tile description.

    Routes through ``memory.state_at(x, y)``:

    * ``VISIBLE`` — full live description (terrain + every entity).
    * ``REMEMBERED`` — terrain + remembered static features
      (doors, containers, disarmed traps) with a ``last seen`` prefix.
      Live creatures and ground items are intentionally NOT surfaced
      because memory does not track them.
    * ``UNKNOWN`` — a single "you don't know what's there" line.

    The returned list is suitable for ``MessageState.emit`` one-at-a-time
    or joined and emitted as a single multi-line message.
    """

    state = memory.state_at(x, y)
    if state is VisibilityState.UNKNOWN:
        return ["You don't know what's there."]
    if state is VisibilityState.VISIBLE:
        return describe_tile(world, x, y)
    # Remembered: surface the cached snapshot. The memory's ``glyph``
    # field is unused here because examine speaks prose, not glyphs.
    remembered = memory.tiles.get((x, y))
    lines: list[str] = []
    tile = world.tile_at(x, y)
    lines.append(_format_terrain_line(tile, marker="(last seen) "))
    if remembered is not None:
        for feature in remembered.features:
            line = _format_remembered_feature(feature)
            if line is not None:
                lines.append(line)
    return lines


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _format_terrain_line(tile: Tile, *, marker: str = "") -> str:
    """Format the terrain prose with an optional memory marker."""

    return f"{marker}You see {describe_terrain(tile)}."


def _format_remembered_feature(feature: RememberedFeature) -> str | None:
    """Translate a :class:`RememberedFeature` into examine-text.

    Mirrors the affordance hints :func:`_affordance_summary` produces
    for live entities, but operates on the memory snapshot (which only
    stores ``kind`` / ``glyph`` / ``is_open``). Returns ``None`` for
    feature kinds that have nothing useful to surface.
    """

    if feature.kind == "door":
        status = "open" if feature.is_open else "closed"
        return f"There was a {status} door here."
    if feature.kind == "container":
        status = "open" if feature.is_open else "closed"
        return f"There was a {status} container here."
    if feature.kind == "trap":
        return "There was a disarmed trap here."
    return None


def _faction_label(faction: Faction | None) -> str | None:
    """Return a short faction tag for examine display.

    Empty / unknown values return ``None`` so the composer can skip the
    fragment entirely. Pre-M28 ``"player"`` / ``"enemy"`` raw strings
    still render — we deliberately don't normalize them so legacy
    fixtures stay legible.
    """

    if faction is None:
        return None
    value = faction.value
    if not value:
        return None
    return value


def _conditions_summary(store: ConditionStore | None) -> str:
    """Comma-joined condition kinds; empty string when the store is empty."""

    if store is None or not store.conditions:
        return ""
    return ", ".join(condition.kind.value for condition in store.conditions)


def _affordance_summary(world: World, entity: EntityId) -> str | None:
    """Affordance hint for doors / locks / traps / containers / corpses.

    Returns ``None`` when the entity carries none of those components,
    so the caller can skip a trailing space. The lock check is folded
    into the door branch (a locked door is still a door); a standalone
    lock without a door is rare and falls through to a generic hint.
    """

    door = world.doors.get(entity)
    if door is not None:
        lock = world.locks.get(entity)
        if lock is not None and lock.is_locked:
            return "(locked door)"
        return "(open door)" if door.is_open else "(closed door)"

    container = world.containers.get(entity)
    if container is not None:
        lock = world.locks.get(entity)
        if lock is not None and lock.is_locked:
            return "(locked container)"
        return "(open container)" if container.is_open else "(closed container)"

    trap = world.traps.get(entity)
    if trap is not None:
        if trap.is_armed:
            return "(armed trap)"
        return "(disarmed trap)"

    corpse = world.corpses.get(entity)
    if corpse is not None:
        kind = corpse.creature_kind or "creature"
        return f"(corpse of {kind})"

    return None


def _ground_item_summary(world: World, entity: EntityId) -> str | None:
    """Summarise a ground-item entity (loose Inventory on the map).

    Ground items are entities with an ``Inventory`` but no ``Creature``
    / ``Container`` / ``Corpse`` marker. The summary lists the contained
    items and gold; returns ``None`` when the entity isn't a ground
    stash so callers can skip the fragment.
    """

    if world.creatures.has(entity) or world.containers.has(entity):
        return None
    if world.corpses.has(entity):
        return None
    inventory = world.inventories.get(entity)
    if inventory is None:
        return None
    fragments: list[str] = []
    if inventory.gold > 0:
        fragments.append(f"{inventory.gold} gold")
    for stack in inventory.items:
        item = ITEMS.get(stack.item_id)
        item_name = item.name if item is not None else stack.item_id
        if stack.quantity > 1:
            fragments.append(f"{item_name} x{stack.quantity}")
        else:
            fragments.append(item_name)
    if not fragments:
        return None
    return f"[contains: {', '.join(fragments)}]"
