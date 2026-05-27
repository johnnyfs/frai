from dataclasses import dataclass, field
from typing import Generic, TypeVar

from src.core.components import BlocksMovement, Character, Name, PlayerControlled, Position, Presentation
from src.core.entity import EntityId
from src.map.tiles import OUTSIDE, Tile

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class TerrainBlocker:
    x: int
    y: int
    reason: str


@dataclass(frozen=True, slots=True)
class EntityBlocker:
    entity: EntityId
    reason: str


BlockerRef = TerrainBlocker | EntityBlocker


@dataclass(slots=True)
class ComponentStore(Generic[T]):
    values: dict[EntityId, T]

    def add(self, entity: EntityId, component: T) -> None:
        self.values[entity] = component

    def get(self, entity: EntityId) -> T | None:
        return self.values.get(entity)

    def require(self, entity: EntityId) -> T:
        return self.values[entity]

    def has(self, entity: EntityId) -> bool:
        return entity in self.values


@dataclass(slots=True)
class World:
    width: int
    height: int
    tiles: list[list[Tile]]
    next_entity_id: int = 1
    positions: ComponentStore[Position] = field(default_factory=lambda: ComponentStore({}))
    presentations: ComponentStore[Presentation] = field(default_factory=lambda: ComponentStore({}))
    blockers: ComponentStore[BlocksMovement] = field(default_factory=lambda: ComponentStore({}))
    player_controlled: ComponentStore[PlayerControlled] = field(
        default_factory=lambda: ComponentStore({})
    )
    names: ComponentStore[Name] = field(default_factory=lambda: ComponentStore({}))
    characters: ComponentStore[Character] = field(default_factory=lambda: ComponentStore({}))

    def create_entity(self) -> EntityId:
        entity = EntityId(self.next_entity_id)
        self.next_entity_id += 1
        return entity

    def tile_at(self, x: int, y: int) -> Tile:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return OUTSIDE
        return self.tiles[y][x]

    def entities_at(self, x: int, y: int) -> list[EntityId]:
        return [
            entity
            for entity, position in self.positions.values.items()
            if position.x == x and position.y == y
        ]

    def blockers_at(self, x: int, y: int) -> list[BlockerRef]:
        blockers: list[BlockerRef] = []
        tile = self.tile_at(x, y)
        if tile.blocks_movement:
            blockers.append(TerrainBlocker(x=x, y=y, reason=tile.block_reason))
        for entity in self.entities_at(x, y):
            block = self.blockers.get(entity)
            if block is not None:
                blockers.append(EntityBlocker(entity=entity, reason=block.reason))
        return blockers

    def player_entity(self) -> EntityId:
        for entity in self.player_controlled.values:
            return entity
        raise LookupError("World has no player-controlled entity.")
