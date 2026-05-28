from src.core.actions import (
    CharacterCreationCommand,
    CloseInventory,
    EndTurn,
    ExamineRequest,
    InteractAttempt,
    InventoryRequest,
    MoveAttempt,
    QuitConfirm,
    QuitRequest,
    StartChoice,
    ToggleTurnMode,
)
from src.core.character_creation import initial_character_creation_state
from src.core.entity import EntityId
from src.core.modes import UIMode
from src.systems.input_system import map_key


def test_play_mode_maps_hjkl_yubn_to_move_attempts() -> None:
    player = EntityId(1)

    action = map_key(ord("h"), UIMode.play, player)

    assert action == MoveAttempt(actor=player, dx=-1, dy=0)


def test_play_mode_maps_q_to_quit_request() -> None:
    assert map_key(ord("q"), UIMode.play, EntityId(1)) == QuitRequest()


def test_play_mode_maps_i_to_inventory_request() -> None:
    assert map_key(ord("i"), UIMode.play, EntityId(1)) == InventoryRequest()


def test_play_mode_maps_e_to_interact_attempt() -> None:
    assert map_key(ord("e"), UIMode.play, EntityId(1)) == InteractAttempt(EntityId(1), 0, 0)


def test_play_mode_maps_space_to_end_turn() -> None:
    assert map_key(ord(" "), UIMode.play, EntityId(1)) == EndTurn()


def test_play_mode_maps_t_to_turn_mode_toggle() -> None:
    assert map_key(ord("t"), UIMode.play, EntityId(1)) == ToggleTurnMode()


def test_play_mode_maps_x_to_examine_request() -> None:
    assert map_key(ord("x"), UIMode.play, EntityId(1)) == ExamineRequest(EntityId(1))


def test_play_mode_maps_semicolon_to_examine_request() -> None:
    # NetHack-style ``;`` alias.
    assert map_key(ord(";"), UIMode.play, EntityId(1)) == ExamineRequest(EntityId(1))


def test_inventory_mode_maps_close_keys() -> None:
    assert map_key(ord("i"), UIMode.inventory, EntityId(1)) == CloseInventory()
    assert map_key(ord("q"), UIMode.inventory, EntityId(1)) == CloseInventory()


def test_quit_confirm_mode_maps_answers() -> None:
    player = EntityId(1)

    assert map_key(ord("y"), UIMode.quit_confirm, player) == QuitConfirm(True)
    assert map_key(ord("n"), UIMode.quit_confirm, player) == QuitConfirm(False)


def test_start_mode_maps_create_and_yolo() -> None:
    player = EntityId(1)

    assert map_key(ord("c"), UIMode.start, player) == StartChoice(create=True)
    assert map_key(ord("y"), UIMode.start, player) == StartChoice(create=False)
    assert map_key(ord("q"), UIMode.start, player) == QuitRequest()


def test_character_creation_mode_maps_navigation_to_commands() -> None:
    state = initial_character_creation_state()

    assert map_key(
        ord("d"), UIMode.character_creation, EntityId(1), character_creation_state=state
    ) == CharacterCreationCommand("choose", state, "d")
    assert map_key(
        ord("b"), UIMode.character_creation, EntityId(1), character_creation_state=state
    ) == CharacterCreationCommand("back", state)
    assert map_key(
        ord("y"), UIMode.character_creation, EntityId(1), character_creation_state=state
    ) == CharacterCreationCommand("confirm", state)
    assert (
        map_key(10, UIMode.character_creation, EntityId(1), character_creation_state=state)
        is None
    )


def test_character_creation_mode_returns_none_when_state_missing() -> None:
    assert (
        map_key(ord("d"), UIMode.character_creation, EntityId(1)) is None
    )
