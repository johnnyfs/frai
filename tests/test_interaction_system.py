from src.core.actions import InteractAttempt, MoveAttempt
from src.core.components import BlocksMovement, Container, Door, Lock, Position, Trap
from src.core.effects import (
    DamageEntity,
    DisarmTrap,
    EmitMessage,
    OpenEntity,
    RemoveBlocker,
    TriggerTrap,
    UnlockEntity,
)
from src.systems.interaction_system import InteractionSystem
from src.systems.movement_system import MovementContextResolver, MovementSystem
from src.systems.obstruction_system import ObstructionSystem
from tests.support.tiny_world import add_actor, build_tiny_map


def _add_feature(world, x: int, y: int):
    entity = world.create_entity()
    world.positions.add(entity, Position(x, y))
    return entity


def test_locked_door_blocks_until_picked_and_opened() -> None:
    world = build_tiny_map()
    actor = add_actor(world, 2, 2)
    door = _add_feature(world, 3, 2)
    world.doors.add(door, Door())
    world.locks.add(door, Lock(is_locked=True, pick_dc=12))
    world.blockers.add(door, BlocksMovement("locked door"))
    movement = MovementSystem(ObstructionSystem(), MovementContextResolver())
    interaction = InteractionSystem()

    assert movement.handle(MoveAttempt(actor, 1, 0), world).effects == [EmitMessage("Blocked.")]

    result = interaction.handle(InteractAttempt(actor, 1, 0, check_result=12), world)

    assert result.effects == [
        UnlockEntity(door),
        OpenEntity(door),
        RemoveBlocker(door),
        EmitMessage("Unlocked and opened."),
    ]


def test_failed_lock_pick_leaves_door_locked() -> None:
    world = build_tiny_map()
    actor = add_actor(world, 2, 2)
    door = _add_feature(world, 3, 2)
    world.doors.add(door, Door())
    world.locks.add(door, Lock(is_locked=True, pick_dc=12))
    interaction = InteractionSystem()

    result = interaction.handle(InteractAttempt(actor, 1, 0, check_result=8), world)

    assert result.effects == [EmitMessage("Lock pick failed.")]
    assert world.locks.require(door).is_locked is True


def test_unlocked_door_opens_and_removes_blocker() -> None:
    world = build_tiny_map()
    actor = add_actor(world, 2, 2)
    door = _add_feature(world, 3, 2)
    world.doors.add(door, Door())
    world.blockers.add(door, BlocksMovement("door"))
    interaction = InteractionSystem()

    result = interaction.handle(InteractAttempt(actor, 1, 0), world)

    assert result.effects == [OpenEntity(door), RemoveBlocker(door), EmitMessage("Opened.")]


def test_trap_triggers_damage_on_failed_disarm() -> None:
    world = build_tiny_map()
    actor = add_actor(world, 2, 2)
    trap = _add_feature(world, 3, 2)
    world.traps.add(trap, Trap(disarm_dc=12, damage=3))
    interaction = InteractionSystem()

    result = interaction.handle(InteractAttempt(actor, 1, 0, check_result=7), world)

    assert result.effects == [DamageEntity(actor, 3), EmitMessage("Trap triggered."), TriggerTrap(trap)]


def test_trap_can_be_disarmed() -> None:
    world = build_tiny_map()
    actor = add_actor(world, 2, 2)
    trap = _add_feature(world, 3, 2)
    world.traps.add(trap, Trap(disarm_dc=12, damage=3))
    interaction = InteractionSystem()

    result = interaction.handle(InteractAttempt(actor, 1, 0, check_result=12), world)

    assert result.effects == [DisarmTrap(trap), EmitMessage("Trap disarmed.")]


def test_reusable_trap_still_emits_trigger_event() -> None:
    world = build_tiny_map()
    actor = add_actor(world, 2, 2)
    trap = _add_feature(world, 3, 2)
    world.traps.add(trap, Trap(disarm_dc=12, damage=3, reusable=True))
    interaction = InteractionSystem()

    result = interaction.handle(InteractAttempt(actor, 1, 0, check_result=7), world)

    assert result.effects == [DamageEntity(actor, 3), EmitMessage("Trap triggered."), TriggerTrap(trap)]


def test_locked_door_without_check_emits_refusal_message() -> None:
    world = build_tiny_map()
    actor = add_actor(world, 2, 2)
    door = _add_feature(world, 3, 2)
    world.doors.add(door, Door())
    world.locks.add(door, Lock(is_locked=True, pick_dc=12))
    world.blockers.add(door, BlocksMovement("locked door"))
    interaction = InteractionSystem()

    result = interaction.handle(InteractAttempt(actor, 1, 0), world)

    assert result.effects == [EmitMessage("It's locked. You need a way to pick it.")]
    assert world.locks.require(door).is_locked is True
    assert world.blockers.has(door)


def test_armed_trap_without_check_refuses_without_damage() -> None:
    world = build_tiny_map()
    actor = add_actor(world, 2, 2)
    trap = _add_feature(world, 3, 2)
    world.traps.add(trap, Trap(disarm_dc=12, damage=3))
    interaction = InteractionSystem()

    result = interaction.handle(InteractAttempt(actor, 1, 0), world)

    assert result.effects == [EmitMessage("You sense danger - you need a way to disarm it.")]
    assert world.traps.require(trap).is_armed is True


def test_container_opens() -> None:
    world = build_tiny_map()
    actor = add_actor(world, 2, 2)
    container = _add_feature(world, 3, 2)
    world.containers.add(container, Container())
    world.blockers.add(container, BlocksMovement("container"))
    interaction = InteractionSystem()

    result = interaction.handle(InteractAttempt(actor, 1, 0), world)

    # Opening a container mirrors the door branch: the blocker is removed
    # so the actor can step onto the tile and use the M30 pickup path.
    assert result.effects == [
        OpenEntity(container),
        RemoveBlocker(container),
        EmitMessage("Opened."),
    ]
