from dataclasses import dataclass

from src.core.actions import Action, MoveAttempt
from src.core.dispatcher import DispatchResult
from src.core.effects import EmitMessage, MoveEntity
from src.core.entity import EntityId
from src.core.world import BlockerRef, World
from src.systems.obstruction_system import ObstructionSystem


@dataclass(frozen=True, slots=True)
class MovementContext:
    actor: EntityId
    destination_x: int
    destination_y: int
    destination_entities: list[EntityId]
    blockers: list[BlockerRef]


class MovementContextResolver:
    def resolve(self, action: MoveAttempt, world: World) -> MovementContext:
        position = world.positions.require(action.actor)
        destination_x = position.x + action.dx
        destination_y = position.y + action.dy
        return MovementContext(
            actor=action.actor,
            destination_x=destination_x,
            destination_y=destination_y,
            destination_entities=world.entities_at(destination_x, destination_y),
            blockers=world.blockers_at(destination_x, destination_y),
        )


@dataclass(slots=True)
class MovementSystem:
    obstruction: ObstructionSystem
    context_resolver: MovementContextResolver

    def handle(self, action: Action, world: World) -> DispatchResult:
        if not isinstance(action, MoveAttempt):
            return DispatchResult()

        context = self.context_resolver.resolve(action, world)
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
