"""Unit tests for the EffectApplier domain handlers.

These tests exercise each Effect type in isolation against a small App
fixture. They are complementary to the broader app tests; the goal is to
pin down the per-effect contract so future re-organization (split per
domain into separate modules) is safe.
"""

from src.app import create_app
from src.core.components import Container, Door, Lock, Trap
from src.core.effects import (
    DamageEntity,
    DisarmTrap,
    EmitMessage,
    KillEntity,
    MoveEntity,
    OpenEntity,
    QuitGame,
    RemoveBlocker,
    SetMode,
    TriggerTrap,
    UnlockEntity,
)
from src.core.effects_applier import EffectApplier
from src.core.modes import UIMode


def test_effect_applier_is_constructed_on_app() -> None:
    app = create_app()
    assert isinstance(app.effect_applier, EffectApplier)


def test_move_entity_updates_position() -> None:
    app = create_app()
    start = app.world.positions.require(app.player)
    target_x, target_y = start.x + 2, start.y + 1
    app.apply_effects([MoveEntity(app.player, target_x, target_y)])
    position = app.world.positions.require(app.player)
    assert (position.x, position.y) == (target_x, target_y)


def test_emit_message_appends_to_message_state() -> None:
    app = create_app()
    app.apply_effects([EmitMessage("hello there")])
    assert "hello there" in app.messages.current


def test_emit_message_joins_multiple_messages_in_order() -> None:
    app = create_app()
    app.apply_effects([EmitMessage("first"), EmitMessage("second")])
    assert "first" in app.messages.current
    assert "second" in app.messages.current
    assert app.messages.current.index("first") < app.messages.current.index("second")


def test_damage_entity_clamps_at_zero() -> None:
    app = create_app()
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = 5
    app.apply_effects([DamageEntity(app.player, 99)])
    assert app.world.combat_stats.require(app.player).hit_points == 0


def test_damage_entity_missing_stats_is_noop() -> None:
    app = create_app()
    entity = app.world.create_entity()
    # No combat_stats added on purpose.
    app.apply_effects([DamageEntity(entity, 3)])  # should not raise


def test_kill_entity_player_triggers_game_over_mode() -> None:
    app = create_app()
    app.apply_effects([KillEntity(app.player)])
    assert app.ui_mode is UIMode.game_over
    # Player entity is preserved so the game-over screen still has context.
    assert app.world.positions.has(app.player)


def test_kill_entity_non_player_removes_from_world() -> None:
    app = create_app()
    entity = app.world.create_entity()
    app.apply_effects([KillEntity(entity)])
    assert not app.world.positions.has(entity)


def test_open_entity_opens_door_and_container() -> None:
    app = create_app()
    door_entity = app.world.create_entity()
    app.world.doors.add(door_entity, Door(is_open=False))
    container_entity = app.world.create_entity()
    app.world.containers.add(container_entity, Container(is_open=False))

    app.apply_effects([OpenEntity(door_entity), OpenEntity(container_entity)])

    assert app.world.doors.require(door_entity).is_open is True
    assert app.world.containers.require(container_entity).is_open is True


def test_unlock_entity_unlocks_lock() -> None:
    app = create_app()
    entity = app.world.create_entity()
    app.world.locks.add(entity, Lock(is_locked=True))
    app.apply_effects([UnlockEntity(entity)])
    assert app.world.locks.require(entity).is_locked is False


def test_unlock_entity_missing_lock_is_noop() -> None:
    app = create_app()
    entity = app.world.create_entity()
    app.apply_effects([UnlockEntity(entity)])  # should not raise


def test_disarm_trap_disarms_trap() -> None:
    app = create_app()
    entity = app.world.create_entity()
    app.world.traps.add(entity, Trap(is_armed=True))
    app.apply_effects([DisarmTrap(entity)])
    assert app.world.traps.require(entity).is_armed is False


def test_trigger_trap_disarms_non_reusable_trap() -> None:
    app = create_app()
    entity = app.world.create_entity()
    app.world.traps.add(entity, Trap(is_armed=True, reusable=False))
    app.apply_effects([TriggerTrap(entity)])
    assert app.world.traps.require(entity).is_armed is False


def test_trigger_trap_leaves_reusable_trap_armed() -> None:
    app = create_app()
    entity = app.world.create_entity()
    app.world.traps.add(entity, Trap(is_armed=True, reusable=True))
    app.apply_effects([TriggerTrap(entity)])
    assert app.world.traps.require(entity).is_armed is True


def test_remove_blocker_removes_blocker_entry() -> None:
    app = create_app()
    # The player has a blocker by default.
    assert app.player in app.world.blockers.values
    app.apply_effects([RemoveBlocker(app.player)])
    assert app.player not in app.world.blockers.values


def test_remove_blocker_missing_blocker_is_noop() -> None:
    app = create_app()
    entity = app.world.create_entity()
    app.apply_effects([RemoveBlocker(entity)])  # should not raise


def test_set_mode_changes_ui_mode() -> None:
    app = create_app()
    app.apply_effects([SetMode(UIMode.play)])
    assert app.ui_mode is UIMode.play
    assert app.character_creation_state is None


def test_quit_game_flips_running_flag() -> None:
    app = create_app()
    assert app.running is True
    app.apply_effects([QuitGame()])
    assert app.running is False


def test_mixed_batch_applies_in_order() -> None:
    app = create_app()
    start = app.world.positions.require(app.player)
    target_x, target_y = start.x + 1, start.y
    app.apply_effects(
        [
            MoveEntity(app.player, target_x, target_y),
            EmitMessage("moved"),
        ]
    )
    position = app.world.positions.require(app.player)
    assert (position.x, position.y) == (target_x, target_y)
    assert "moved" in app.messages.current
