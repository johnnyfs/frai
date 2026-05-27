from src.core.actions import CharacterCreationCommand, CloseInventory, InventoryRequest, MoveAttempt, QuitConfirm, QuitRequest, StartChoice
from src.core.character_creation import initial_character_creation_state
from src.core.entity import EntityId
from src.core.modes import CharacterCreationMode, ConfirmQuitMode, InventoryMode, NormalMode, StartChoiceMode
from src.systems.input_system import map_key


def test_normal_mode_maps_hjkl_yubn_to_move_attempts() -> None:
    player = EntityId(1)

    action = map_key(ord("h"), NormalMode(), player)

    assert action == MoveAttempt(actor=player, dx=-1, dy=0)


def test_normal_mode_maps_q_to_quit_request() -> None:
    assert map_key(ord("q"), NormalMode(), EntityId(1)) == QuitRequest()


def test_normal_mode_maps_i_to_inventory_request() -> None:
    assert map_key(ord("i"), NormalMode(), EntityId(1)) == InventoryRequest()


def test_inventory_mode_maps_close_keys() -> None:
    assert map_key(ord("i"), InventoryMode(), EntityId(1)) == CloseInventory()
    assert map_key(ord("q"), InventoryMode(), EntityId(1)) == CloseInventory()


def test_confirm_quit_mode_maps_answers() -> None:
    player = EntityId(1)

    assert map_key(ord("y"), ConfirmQuitMode(), player) == QuitConfirm(True)
    assert map_key(ord("n"), ConfirmQuitMode(), player) == QuitConfirm(False)


def test_start_choice_mode_maps_create_and_yolo() -> None:
    player = EntityId(1)

    assert map_key(ord("c"), StartChoiceMode(), player) == StartChoice(create=True)
    assert map_key(ord("y"), StartChoiceMode(), player) == StartChoice(create=False)
    assert map_key(ord("q"), StartChoiceMode(), player) == QuitRequest()


def test_character_creation_mode_maps_navigation_to_commands() -> None:
    state = initial_character_creation_state()
    mode = CharacterCreationMode(state)

    assert map_key(ord("d"), mode, EntityId(1)) == CharacterCreationCommand("choose", state, "d")
    assert map_key(ord("b"), mode, EntityId(1)) == CharacterCreationCommand("back", state)
    assert map_key(ord("y"), mode, EntityId(1)) == CharacterCreationCommand("confirm", state)
    assert map_key(10, mode, EntityId(1)) is None
