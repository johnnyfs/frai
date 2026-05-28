from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from src.core.character_creation import CharacterSheet
from src.core.combat import combat_stats_for_sheet, starter_armor_for_class, starter_weapon_for_class
from src.core.components import (
    BlocksMovement,
    Character,
    Equipment,
    Faction,
    Inventory,
    Name,
    NPC,
    NPCDialogue,
    NPCKind,
    PlayerControlled,
    Position,
    Presentation,
    Shop,
)
from src.core.dialogue import info_tree, recruit_tree, shopkeeper_tree
from src.core.entity import EntityId
from src.core.factions import FactionId
from src.core.items import add_item, armor_item_id_for_name, weapon_item_id_for_name
from src.core.world import World
from src.map.tiles import DUNGEON_FLOOR, FOREST, GRASS, ROAD, TOWN_FLOOR, WATER, Tile


MIN_WORLD_SKELETON_WIDTH = 118
MIN_WORLD_SKELETON_HEIGHT = 56


class LocationKind(Enum):
    OVERWORLD = "overworld"
    TOWN = "town"
    FOREST = "forest"
    DUNGEON_ENTRANCE = "dungeon_entrance"
    DUNGEON_LEVEL = "dungeon_level"


class LinkKind(Enum):
    ROAD = "road"
    STAIRS_DOWN = "stairs_down"


@dataclass(frozen=True, slots=True)
class Point:
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class Rect:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width - 1

    @property
    def bottom(self) -> int:
        return self.top + self.height - 1

    def contains(self, point: Point) -> bool:
        return self.left <= point.x <= self.right and self.top <= point.y <= self.bottom

    def points(self) -> Iterable[Point]:
        for y in range(self.top, self.bottom + 1):
            for x in range(self.left, self.right + 1):
                yield Point(x, y)


@dataclass(frozen=True, slots=True)
class LocationSpec:
    id: str
    name: str
    kind: LocationKind
    bounds: Rect
    anchor: Point
    terrain: Tile


@dataclass(frozen=True, slots=True)
class LocationLink:
    source_id: str
    target_id: str
    kind: LinkKind
    path: tuple[Point, ...]


@dataclass(frozen=True, slots=True)
class BuiltWorldSkeleton:
    world: World
    player: EntityId
    locations: Mapping[str, LocationSpec]
    links: tuple[LocationLink, ...]


REQUIRED_LOCATION_IDS = (
    "overworld",
    "town",
    "forest",
    "dungeon_entrance",
    "dungeon_level_1",
    "dungeon_level_2",
    "dungeon_level_3",
)


def build_world_skeleton(
    width: int = MIN_WORLD_SKELETON_WIDTH,
    height: int = MIN_WORLD_SKELETON_HEIGHT,
) -> BuiltWorldSkeleton:
    if width < MIN_WORLD_SKELETON_WIDTH or height < MIN_WORLD_SKELETON_HEIGHT:
        raise ValueError(
            "World skeleton needs at least "
            f"{MIN_WORLD_SKELETON_WIDTH}x{MIN_WORLD_SKELETON_HEIGHT} tiles."
        )

    tiles = [[GRASS for _ in range(width)] for _ in range(height)]
    world = World(width=width, height=height, tiles=tiles)
    _add_water_border(world)

    locations = _location_specs()
    for location in locations:
        _paint_rect(world, location.bounds, location.terrain)

    links = _location_links(locations)
    for link in links:
        tile = ROAD if link.kind is LinkKind.ROAD else DUNGEON_FLOOR
        _paint_path(world, link.path, tile)

    player = world.create_entity()
    start = _location_by_id(locations, "overworld").anchor
    world.positions.add(player, Position(start.x, start.y))
    world.presentations.add(player, Presentation("@"))
    world.player_controlled.add(player, PlayerControlled())
    world.names.add(player, Name("you"))
    world.factions.add(player, Faction(FactionId.PLAYER_PARTY.value))

    _populate_town(world, _location_by_id(locations, "town"))

    return BuiltWorldSkeleton(
        world=world,
        player=player,
        locations=MappingProxyType({location.id: location for location in locations}),
        links=links,
    )


def connected_location_ids(links: Iterable[LocationLink], start_id: str) -> set[str]:
    graph: dict[str, set[str]] = {}
    for link in links:
        graph.setdefault(link.source_id, set()).add(link.target_id)
        graph.setdefault(link.target_id, set()).add(link.source_id)

    seen = {start_id}
    frontier = deque([start_id])
    while frontier:
        location_id = frontier.popleft()
        for neighbor_id in graph.get(location_id, set()):
            if neighbor_id in seen:
                continue
            seen.add(neighbor_id)
            frontier.append(neighbor_id)
    return seen


def reachable_points(world: World, start: Point) -> set[Point]:
    if world.tile_at(start.x, start.y).blocks_movement:
        return set()

    seen = {start}
    frontier = deque([start])
    while frontier:
        point = frontier.popleft()
        for neighbor in _neighbors(point):
            if neighbor in seen:
                continue
            if world.tile_at(neighbor.x, neighbor.y).blocks_movement:
                continue
            seen.add(neighbor)
            frontier.append(neighbor)
    return seen


def shortest_walkable_path(world: World, start: Point, goal: Point) -> tuple[Point, ...]:
    if world.tile_at(start.x, start.y).blocks_movement:
        return ()
    if world.tile_at(goal.x, goal.y).blocks_movement:
        return ()

    came_from: dict[Point, Point | None] = {start: None}
    frontier = deque([start])
    while frontier:
        point = frontier.popleft()
        if point == goal:
            return _reconstruct_path(came_from, goal)
        for neighbor in _neighbors(point):
            if neighbor in came_from:
                continue
            if world.tile_at(neighbor.x, neighbor.y).blocks_movement:
                continue
            # Entity blockers (NPCs, doors before they're opened, etc.)
            # rule out a tile unless it's the goal itself -- the caller
            # may want to know "can I path to that NPC's tile" without
            # the BFS refusing to even consider it.
            if neighbor != goal and any(
                world.blockers.has(entity)
                for entity in world.entities_at(neighbor.x, neighbor.y)
            ):
                continue
            came_from[neighbor] = point
            frontier.append(neighbor)
    return ()


def _location_specs() -> tuple[LocationSpec, ...]:
    return (
        LocationSpec(
            id="overworld",
            name="Old Road Crossroads",
            kind=LocationKind.OVERWORLD,
            bounds=Rect(left=10, top=20, width=12, height=8),
            anchor=Point(15, 24),
            terrain=GRASS,
        ),
        LocationSpec(
            id="town",
            name="Hearthgate",
            kind=LocationKind.TOWN,
            bounds=Rect(left=6, top=6, width=18, height=10),
            anchor=Point(15, 11),
            terrain=TOWN_FLOOR,
        ),
        LocationSpec(
            id="forest",
            name="Briarwood",
            kind=LocationKind.FOREST,
            bounds=Rect(left=34, top=5, width=22, height=14),
            anchor=Point(44, 12),
            terrain=FOREST,
        ),
        LocationSpec(
            id="dungeon_entrance",
            name="Sunken Gate",
            kind=LocationKind.DUNGEON_ENTRANCE,
            bounds=Rect(left=70, top=18, width=14, height=8),
            anchor=Point(76, 22),
            terrain=DUNGEON_FLOOR,
        ),
        LocationSpec(
            id="dungeon_level_1",
            name="Sunken Halls L1",
            kind=LocationKind.DUNGEON_LEVEL,
            bounds=Rect(left=62, top=34, width=18, height=8),
            anchor=Point(70, 37),
            terrain=DUNGEON_FLOOR,
        ),
        LocationSpec(
            id="dungeon_level_2",
            name="Sunken Halls L2",
            kind=LocationKind.DUNGEON_LEVEL,
            bounds=Rect(left=90, top=34, width=18, height=8),
            anchor=Point(98, 37),
            terrain=DUNGEON_FLOOR,
        ),
        LocationSpec(
            id="dungeon_level_3",
            name="Sunken Halls L3",
            kind=LocationKind.DUNGEON_LEVEL,
            bounds=Rect(left=90, top=47, width=18, height=7),
            anchor=Point(98, 50),
            terrain=DUNGEON_FLOOR,
        ),
    )


def _location_links(locations: tuple[LocationSpec, ...]) -> tuple[LocationLink, ...]:
    by_id = {location.id: location for location in locations}
    return (
        _link(by_id, "overworld", "town", LinkKind.ROAD),
        _link(by_id, "overworld", "forest", LinkKind.ROAD),
        _link(by_id, "overworld", "dungeon_entrance", LinkKind.ROAD),
        _link(by_id, "dungeon_entrance", "dungeon_level_1", LinkKind.STAIRS_DOWN),
        _link(by_id, "dungeon_level_1", "dungeon_level_2", LinkKind.STAIRS_DOWN),
        _link(by_id, "dungeon_level_2", "dungeon_level_3", LinkKind.STAIRS_DOWN),
    )


def _link(
    locations: Mapping[str, LocationSpec],
    source_id: str,
    target_id: str,
    kind: LinkKind,
) -> LocationLink:
    return LocationLink(
        source_id=source_id,
        target_id=target_id,
        kind=kind,
        path=_manhattan_path(locations[source_id].anchor, locations[target_id].anchor),
    )


def _location_by_id(locations: tuple[LocationSpec, ...], location_id: str) -> LocationSpec:
    for location in locations:
        if location.id == location_id:
            return location
    raise KeyError(location_id)


def _populate_town(world: World, town: LocationSpec) -> None:
    """Spawn the three M13 town NPCs around the town anchor.

    Placement is deterministic: the info NPC sits one tile west of the
    anchor, the shopkeeper one tile east, and the recruitable adventurer
    one tile south. The town bounds are large enough that all three
    cells are inside the town rect for the minimum-skeleton size.
    """

    # Positions are picked inside the town rect but away from the
    # column that the overworld road paints (column 15) so the BFS
    # path test in tests/test_world_content_skeleton.py still finds
    # a clean walk to the town anchor without bumping an NPC tile.
    bounds = town.bounds
    info_x = bounds.left + 2
    info_y = bounds.top + 2
    shop_x = bounds.right - 2
    shop_y = bounds.top + 2
    recruit_x = bounds.left + 2
    recruit_y = bounds.bottom - 2
    _spawn_info_npc(
        world,
        info_x,
        info_y,
        name="Old Gerda",
        line=(
            "The dungeon lies east of town -- the Sunken Gate. "
            "Mind the dark."
        ),
    )
    _spawn_shopkeeper_npc(
        world,
        shop_x,
        shop_y,
        name="Quartermaster Hadrin",
    )
    _spawn_recruit_npc(
        world,
        recruit_x,
        recruit_y,
        name="Karn the Wanderer",
        ask_text=(
            "Looking for sword work. "
            "I can swing for coin if you'll have me. Join up?"
        ),
        accept_text="Then you have my blade. Lead on.",
    )


def _spawn_info_npc(world: World, x: int, y: int, *, name: str, line: str) -> EntityId:
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation("@"))
    world.names.add(entity, Name(name))
    world.factions.add(entity, Faction("town"))
    world.blockers.add(entity, BlocksMovement("occupied"))
    world.npcs.add(entity, NPC(kind=NPCKind.INFO))
    world.npc_dialogues.add(
        entity,
        NPCDialogue(tree=info_tree(speaker_id=name, text=line)),
    )
    return entity


def _spawn_shopkeeper_npc(
    world: World,
    x: int,
    y: int,
    *,
    name: str,
    gold: int = 200,
) -> EntityId:
    """Spawn an NPC that doubles as the M12 shopkeeper.

    The entity carries both an :class:`NPC` marker (so ``e`` opens
    dialogue rather than the door/lock/container path) and a
    :class:`Shop` component with an :class:`Inventory` (so the open-shop
    dialogue effect lands on a real shop). The starting stock is
    deliberately small -- M14/M17 will tune it.
    """

    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation("@"))
    world.names.add(entity, Name(name))
    world.factions.add(entity, Faction("town"))
    world.blockers.add(entity, BlocksMovement("occupied"))
    world.npcs.add(entity, NPC(kind=NPCKind.SHOPKEEPER))
    world.npc_dialogues.add(
        entity,
        NPCDialogue(
            tree=shopkeeper_tree(
                speaker_id=name,
                greeting=(
                    f"Welcome to {name}'s shop. "
                    "Looking to trade, or just passing through?"
                ),
            )
        ),
    )
    world.shops.add(entity, Shop(name=name))
    inventory = Inventory(gold=gold)
    for item_id in (
        "weapon.shortsword",
        "armor.leather",
        "consumable.healing_potion",
    ):
        try:
            add_item(inventory, item_id)
        except KeyError:
            # The item catalog may not contain a tuned id yet -- skip
            # quietly so the skeleton boots even when stock is changed.
            continue
    world.inventories.add(entity, inventory)
    return entity


def _spawn_recruit_npc(
    world: World,
    x: int,
    y: int,
    *,
    name: str,
    ask_text: str,
    accept_text: str,
) -> EntityId:
    """Spawn a recruitable adventurer NPC.

    Carries a full character sheet, combat stats, weapon and armor so
    that joining the party gives a working member without a follow-up
    initialisation pass. The sheet is a deliberately small fighter
    profile -- the goal is "another body in the line", not "tuned
    companion".
    """

    sheet = CharacterSheet(
        race="Human",
        character_class="Fighter",
        specialization="Champion",
        base_attributes={
            "STR": 14, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10,
        },
        attributes={
            "STR": 14, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10,
        },
        skills=("Athletics",),
    )

    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation("@"))
    world.names.add(entity, Name(name))
    world.factions.add(entity, Faction("town"))
    world.blockers.add(entity, BlocksMovement("occupied"))
    world.npcs.add(entity, NPC(kind=NPCKind.RECRUIT))
    world.npc_dialogues.add(
        entity,
        NPCDialogue(
            tree=recruit_tree(
                speaker_id=name,
                ask_text=ask_text,
                accept_text=accept_text,
            )
        ),
    )

    world.characters.add(entity, Character(sheet))
    armor = starter_armor_for_class(sheet.character_class)
    world.armor.add(entity, armor)
    world.combat_stats.add(entity, combat_stats_for_sheet(sheet, armor))
    weapon = starter_weapon_for_class(sheet.character_class)
    world.weapons.add(entity, weapon)

    inventory = Inventory(gold=10)
    weapon_item_id = weapon_item_id_for_name(weapon.name)
    armor_item_id = armor_item_id_for_name(armor.name)
    add_item(inventory, weapon_item_id)
    if armor_item_id is not None:
        add_item(inventory, armor_item_id)
    world.inventories.add(entity, inventory)
    world.equipment.add(
        entity,
        Equipment(weapon_item_id=weapon_item_id, armor_item_id=armor_item_id),
    )
    return entity


def _add_water_border(world: World) -> None:
    for x in range(world.width):
        world.tiles[0][x] = WATER
        world.tiles[world.height - 1][x] = WATER
    for y in range(world.height):
        world.tiles[y][0] = WATER
        world.tiles[y][world.width - 1] = WATER


def _paint_rect(world: World, rect: Rect, tile: Tile) -> None:
    for point in rect.points():
        world.tiles[point.y][point.x] = tile


def _paint_path(world: World, path: Iterable[Point], tile: Tile) -> None:
    for point in path:
        world.tiles[point.y][point.x] = tile


def _manhattan_path(start: Point, end: Point) -> tuple[Point, ...]:
    points: list[Point] = []
    step_x = 1 if end.x >= start.x else -1
    for x in range(start.x, end.x + step_x, step_x):
        points.append(Point(x, start.y))

    step_y = 1 if end.y >= start.y else -1
    for y in range(start.y + step_y, end.y + step_y, step_y):
        points.append(Point(end.x, y))
    return tuple(points)


def _neighbors(point: Point) -> Iterable[Point]:
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            yield Point(point.x + dx, point.y + dy)


def _reconstruct_path(came_from: Mapping[Point, Point | None], goal: Point) -> tuple[Point, ...]:
    path = [goal]
    point = goal
    while (previous := came_from[point]) is not None:
        path.append(previous)
        point = previous
    path.reverse()
    return tuple(path)
