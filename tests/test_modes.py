"""Tests for the split UIMode / PlayMode enums and App accessors."""

import pytest

from src.app import create_app
from src.core.components import CombatStats, Faction, Name, Position
from src.core.effects import KillEntity
from src.core.modes import (
    PlayMode,
    UIMode,
    is_turn_based_play,
    play_mode_for_state,
)


def _clear_creatures(app) -> None:
    for entity in list(app.world.creatures.values):
        app.world.remove_entity(entity)


def test_ui_mode_and_play_mode_are_independent_enums() -> None:
    # UIMode covers every screen the player may see.
    expected_ui = {
        "start",
        "character_creation",
        "play",
        "inventory",
        "dialogue",
        "shop",
        "targeting",
        "examine",
        "help",
        "roster",
        "character_sheet",
        "message_pager",
        "spell_menu",
        "rest_menu",
        "level_up",
        "quit_confirm",
        "game_over",
    }
    assert {member.value for member in UIMode} == expected_ui

    # PlayMode is exclusively the gameplay state machine.
    assert {member.value for member in PlayMode} == {
        "explore",
        "turn_based",
        "voluntary_turn",
    }


def test_play_mode_for_state_matches_hostile_and_voluntary_inputs() -> None:
    assert play_mode_for_state(hostiles_present=False) is PlayMode.explore
    assert play_mode_for_state(hostiles_present=True) is PlayMode.turn_based
    assert (
        play_mode_for_state(hostiles_present=False, voluntary_turn_based=True)
        is PlayMode.voluntary_turn
    )
    # Hostiles dominate the voluntary flag.
    assert (
        play_mode_for_state(hostiles_present=True, voluntary_turn_based=True)
        is PlayMode.turn_based
    )


def test_is_turn_based_play_covers_voluntary_and_forced_turn_modes() -> None:
    assert is_turn_based_play(PlayMode.explore) is False
    assert is_turn_based_play(PlayMode.turn_based) is True
    assert is_turn_based_play(PlayMode.voluntary_turn) is True


def test_app_starts_with_start_ui_mode_and_no_creation_state() -> None:
    app = create_app()

    assert app.ui_mode is UIMode.start
    assert app.character_creation_state is None


def test_yolo_choice_transitions_to_play_ui_mode() -> None:
    app = create_app()

    app.handle_key(ord("y"))

    assert app.ui_mode is UIMode.play
    # Hostiles spawned by the world builder force turn_based.
    assert app.play_mode is PlayMode.turn_based


def test_killing_player_transitions_ui_mode_to_game_over() -> None:
    app = create_app()
    app.handle_key(ord("y"))

    app.apply_effects([KillEntity(app.player)])

    assert app.ui_mode is UIMode.game_over


def test_inventory_open_changes_only_ui_mode_not_play_mode() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    _clear_creatures(app)
    app.sync_play_mode()

    assert app.ui_mode is UIMode.play
    assert app.play_mode is PlayMode.explore

    app.handle_key(ord("i"))

    # Modal screen overlays play; PlayMode is preserved underneath.
    assert app.ui_mode is UIMode.inventory
    assert app.play_mode is PlayMode.explore

    app.handle_key(ord("q"))

    assert app.ui_mode is UIMode.play
    assert app.play_mode is PlayMode.explore


def test_voluntary_turn_play_mode_transitions_reproduce_current_semantics() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    _clear_creatures(app)
    app.sync_play_mode()

    assert app.play_mode is PlayMode.explore

    app.handle_key(ord("t"))

    assert app.play_mode is PlayMode.voluntary_turn
    assert app.voluntary_turn_based is True

    app.handle_key(ord("t"))

    assert app.play_mode is PlayMode.explore
    assert app.voluntary_turn_based is False


def test_hostile_in_sight_forces_turn_based_play_mode_even_in_voluntary_turn() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    _clear_creatures(app)
    app.sync_play_mode()
    app.handle_key(ord("t"))
    assert app.play_mode is PlayMode.voluntary_turn

    hostile = app.world.create_entity()
    player_position = app.world.positions.require(app.player)
    app.world.positions.add(hostile, Position(player_position.x + 2, player_position.y))
    app.world.names.add(hostile, Name("ambusher"))
    app.world.factions.add(hostile, Faction("enemy"))
    app.world.combat_stats.add(
        hostile,
        CombatStats(
            armor_class=10,
            hit_points=1,
            max_hit_points=1,
            strength=10,
            dexterity=10,
            constitution=10,
        ),
    )
    app.sync_play_mode()

    assert app.play_mode is PlayMode.turn_based
    assert app.voluntary_turn_based is False


def test_current_play_mode_raises_when_ui_mode_is_not_play() -> None:
    app = create_app()
    assert app.ui_mode is UIMode.start

    with pytest.raises(RuntimeError, match="PlayMode is undefined"):
        _ = app.current_play_mode


def test_current_play_mode_returns_play_mode_when_ui_mode_is_play() -> None:
    app = create_app()
    app.handle_key(ord("y"))

    # current_play_mode must agree with the underlying play_mode field.
    assert app.current_play_mode is app.play_mode


def test_set_mode_clears_character_creation_state_when_leaving_creation() -> None:
    app = create_app()
    app.handle_key(ord("c"))

    assert app.ui_mode is UIMode.character_creation
    assert app.character_creation_state is not None

    # Press 'b' enough times to back out — but easier: drive YOLO completion
    # by stepping through. Instead, run the full character creation flow.
    app.handle_key(ord("d"))  # Dragonborn
    app.handle_key(ord("a"))  # Barbarian
    app.handle_key(ord("e"))  # Berserker
    app.handle_key(ord("a"))
    app.handle_key(ord("t"))
    app.handle_key(ord("y"))  # continue skills
    app.handle_key(ord("y"))  # keep attributes
    app.handle_key(ord("y"))  # confirm

    assert app.ui_mode is UIMode.play
    assert app.character_creation_state is None
