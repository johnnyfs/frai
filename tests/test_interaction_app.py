from src.app import create_app
from src.core.actions import InteractAttempt, MoveAttempt
from src.core.components import BlocksMovement, Container, Door, Lock, Position, Trap
from src.core.modes import NormalMode


def _clear_hostiles(app) -> None:
    for entity in list(app.world.creatures.values):
        app.world.remove_entity(entity)
    app.sync_major_mode()


def _add_feature(app, x: int, y: int):
    entity = app.world.create_entity()
    app.world.positions.add(entity, Position(x, y))
    return entity


def test_app_applies_locked_door_interaction_and_allows_movement_afterward() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    _clear_hostiles(app)
    app.mode = NormalMode()
    player_position = app.world.positions.require(app.player)
    start_x = player_position.x
    door = _add_feature(app, start_x + 1, player_position.y)
    app.world.doors.add(door, Door())
    app.world.locks.add(door, Lock(is_locked=True, pick_dc=12))
    app.world.blockers.add(door, BlocksMovement("locked door"))

    app.apply_effects(app._handle_interaction(InteractAttempt(app.player, 1, 0, check_result=12)))

    assert app.world.locks.require(door).is_locked is False
    assert app.world.doors.require(door).is_open is True
    assert not app.world.blockers.has(door)

    app.apply_effects(app._handle_explore_move(MoveAttempt(app.player, 1, 0)))

    assert app.world.positions.require(app.player).x == start_x + 1


def test_app_failed_lock_pick_leaves_blocker() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    _clear_hostiles(app)
    player_position = app.world.positions.require(app.player)
    door = _add_feature(app, player_position.x + 1, player_position.y)
    app.world.doors.add(door, Door())
    app.world.locks.add(door, Lock(is_locked=True, pick_dc=12))
    app.world.blockers.add(door, BlocksMovement("locked door"))

    app.apply_effects(app._handle_interaction(InteractAttempt(app.player, 1, 0, check_result=8)))

    assert app.world.locks.require(door).is_locked is True
    assert app.world.blockers.has(door)


def test_app_trap_trigger_damages_and_disarms_non_reusable_trap() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    _clear_hostiles(app)
    player_position = app.world.positions.require(app.player)
    trap = _add_feature(app, player_position.x + 1, player_position.y)
    app.world.traps.add(trap, Trap(disarm_dc=12, damage=3))
    before_hp = app.world.combat_stats.require(app.player).hit_points

    app.apply_effects(app._handle_interaction(InteractAttempt(app.player, 1, 0, check_result=5)))

    assert app.world.combat_stats.require(app.player).hit_points == before_hp - 3
    assert app.world.traps.require(trap).is_armed is False


def test_app_container_interaction_marks_container_open() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    _clear_hostiles(app)
    player_position = app.world.positions.require(app.player)
    container = _add_feature(app, player_position.x + 1, player_position.y)
    app.world.containers.add(container, Container())

    app.apply_effects(app._handle_interaction(InteractAttempt(app.player, 1, 0)))

    assert app.world.containers.require(container).is_open is True


def test_turn_based_interaction_spends_action_and_blocks_second_interaction() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    frog = next(iter(app.world.creatures.values))
    for entity in list(app.world.creatures.values):
        if entity != frog:
            app.world.remove_entity(entity)
    player_position = app.world.positions.require(app.player)
    container = _add_feature(app, player_position.x + 1, player_position.y)
    app.world.containers.add(container, Container())
    app.sync_major_mode()

    app.apply_effects(app._handle_interaction(InteractAttempt(app.player, 1, 0)))

    assert app.activation.action_used is True
    assert app.world.containers.require(container).is_open is True

    app.world.containers.require(container).is_open = False
    app.apply_effects(app._handle_interaction(InteractAttempt(app.player, 1, 0)))

    assert app.world.containers.require(container).is_open is False
    assert app.messages.current == "Action already used."
