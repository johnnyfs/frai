from dataclasses import dataclass

from src.core.actions import Action, InteractAttempt
from src.core.dispatcher import DispatchResult
from src.core.effects import (
    DamageEntity,
    DisarmTrap,
    Effect,
    EmitMessage,
    OpenEntity,
    RemoveBlocker,
    TriggerTrap,
    UnlockEntity,
)
from src.core.entity import EntityId
from src.core.world import World


@dataclass(slots=True)
class InteractionSystem:
    def handle(self, action: Action, world: World) -> DispatchResult:
        if not isinstance(action, InteractAttempt):
            return DispatchResult()

        position = world.positions.require(action.actor)
        target_x = position.x + action.dx
        target_y = position.y + action.dy
        target = _interaction_target(world, target_x, target_y)
        if target is None:
            return DispatchResult(effects=[EmitMessage("Nothing to interact with.")], cancel=True)

        effects = _resolve_interaction(world, action, target)
        return DispatchResult(effects=effects, cancel=True)


def _interaction_target(world: World, x: int, y: int) -> EntityId | None:
    for entity in world.entities_at(x, y):
        if (
            world.doors.has(entity)
            or world.traps.has(entity)
            or world.containers.has(entity)
        ):
            return entity
    return None


def _resolve_interaction(
    world: World,
    action: InteractAttempt,
    target: EntityId,
) -> list[Effect]:
    trap = world.traps.get(target)
    if trap is not None and trap.is_armed:
        if _passes_check(action.check_result, trap.disarm_dc):
            return [DisarmTrap(target), EmitMessage("Trap disarmed.")]
        effects: list[Effect] = [
            DamageEntity(action.actor, trap.damage),
            EmitMessage("Trap triggered."),
        ]
        if not trap.reusable:
            effects.append(TriggerTrap(target))
        return effects

    lock = world.locks.get(target)
    if lock is not None and lock.is_locked:
        if not _passes_check(action.check_result, lock.pick_dc):
            return [EmitMessage("Lock pick failed.")]
        return [
            UnlockEntity(target),
            OpenEntity(target),
            RemoveBlocker(target),
            EmitMessage("Unlocked and opened."),
        ]

    if world.doors.has(target):
        door = world.doors.require(target)
        if door.is_open:
            return [EmitMessage("Already open.")]
        return [OpenEntity(target), RemoveBlocker(target), EmitMessage("Opened.")]

    if world.containers.has(target):
        container = world.containers.require(target)
        if container.is_open:
            return [EmitMessage("Already open.")]
        return [OpenEntity(target), EmitMessage("Opened.")]

    return [EmitMessage("Nothing happens.")]


def _passes_check(check_result: int | None, dc: int) -> bool:
    return check_result is not None and check_result >= dc
