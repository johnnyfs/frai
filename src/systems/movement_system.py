from dataclasses import dataclass

from src.core.actions import Action, AttackAttempt, MoveAttempt
from src.core.dispatcher import DispatchResult
from src.core.effects import EmitMessage, MoveEntity
from src.core.entity import EntityId
from src.core.turns import movement_cost
from src.core.world import BlockerRef, World
from src.map.tiles import Tile
from src.systems.obstruction_system import ObstructionSystem


@dataclass(frozen=True, slots=True)
class MovementContext:
    actor: EntityId
    destination_x: int
    destination_y: int
    destination_entities: list[EntityId]
    blockers: list[BlockerRef]
    destination_tile: Tile
    movement_cost: float


class MovementContextResolver:
    def resolve(self, action: MoveAttempt, world: World) -> MovementContext:
        position = world.positions.require(action.actor)
        destination_x = position.x + action.dx
        destination_y = position.y + action.dy
        destination_tile = world.tile_at(destination_x, destination_y)
        return MovementContext(
            actor=action.actor,
            destination_x=destination_x,
            destination_y=destination_y,
            destination_entities=world.entities_at(destination_x, destination_y),
            blockers=world.blockers_at(destination_x, destination_y),
            destination_tile=destination_tile,
            movement_cost=movement_cost_for_attempt(world, action),
        )


@dataclass(slots=True)
class MovementSystem:
    obstruction: ObstructionSystem
    context_resolver: MovementContextResolver

    def handle(self, action: Action, world: World) -> DispatchResult:
        if not isinstance(action, MoveAttempt):
            return DispatchResult()

        context = self.context_resolver.resolve(action, world)
        target = _hostile_target(action.actor, context.destination_entities, world)
        if target is not None:
            return DispatchResult(replacement=AttackAttempt(action.actor, target))

        obstruction = self.obstruction.movement_allowed(
            world, context.destination_x, context.destination_y
        )
        if not obstruction.allowed:
            return DispatchResult(effects=[EmitMessage("Blocked.")], cancel=True)

        return DispatchResult(
            effects=[
                MoveEntity(
                    entity=action.actor,
                    x=context.destination_x,
                    y=context.destination_y,
                )
            ],
            cancel=True,
        )


def _hostile_target(actor: EntityId, entities: list[EntityId], world: World) -> EntityId | None:
    actor_faction = world.factions.get(actor)
    if actor_faction is None:
        return None
    for entity in entities:
        target_faction = world.factions.get(entity)
        if target_faction is not None and target_faction.value != actor_faction.value:
            if world.combat_stats.has(entity):
                return entity
    return None


def terrain_adjusted_movement_cost(dx: int, dy: int, tile: Tile) -> float:
    return movement_cost(dx, dy) * tile.movement_cost_multiplier


def movement_cost_for_attempt(world: World, action: MoveAttempt) -> float:
    position = world.positions.require(action.actor)
    destination_tile = world.tile_at(position.x + action.dx, position.y + action.dy)
    return terrain_adjusted_movement_cost(action.dx, action.dy, destination_tile)
