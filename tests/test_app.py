from src.app import create_app
from src.core.config import PLAYFIELD_WIDTH
from src.core.effects import KillEntity
from src.core.modes import CharacterCreationMode, ConfirmQuitMode, GameOverMode, InventoryMode, NormalMode, StartChoiceMode


def test_quit_prompt_and_cancel_flow_uses_effects() -> None:
    app = create_app()
    app.mode = NormalMode()

    app.handle_key(ord("q"))

    assert isinstance(app.mode, ConfirmQuitMode)
    assert app.messages.current == "Quit? y/n"
    assert app.running is True

    app.handle_key(ord("n"))

    assert isinstance(app.mode, NormalMode)
    assert app.messages.current == ""
    assert app.running is True


def test_quit_confirmation_stops_app() -> None:
    app = create_app()
    app.mode = NormalMode()

    app.handle_key(ord("q"))
    app.handle_key(ord("y"))

    assert app.running is False


def test_app_starts_in_start_choice() -> None:
    app = create_app()

    assert isinstance(app.mode, StartChoiceMode)


def test_create_choice_enters_character_creation() -> None:
    app = create_app()

    app.handle_key(ord("c"))

    assert isinstance(app.mode, CharacterCreationMode)


def test_start_choice_can_request_quit_confirmation() -> None:
    app = create_app()

    app.handle_key(ord("q"))

    assert isinstance(app.mode, ConfirmQuitMode)
    assert app.messages.current == "Quit? y/n"


def test_character_creation_flow_assigns_sheet_and_starts_game() -> None:
    app = create_app()

    app.handle_key(ord("c"))  # create a character
    app.handle_key(ord("d"))  # Dragonborn
    app.handle_key(ord("a"))  # Barbarian
    app.handle_key(ord("e"))  # Berserker
    app.handle_key(ord("a"))  # Animal Handling
    app.handle_key(ord("t"))  # Athletics
    app.handle_key(ord("y"))  # continue skills
    app.handle_key(ord("y"))  # keep attributes
    app.handle_key(ord("y"))  # confirm

    assert isinstance(app.mode, NormalMode)
    assert app.world.characters.has(app.player)
    assert app.messages.current.startswith("Welcome,")


def test_yolo_choice_assigns_sheet_and_starts_game() -> None:
    app = create_app()

    app.handle_key(ord("y"))

    assert isinstance(app.mode, NormalMode)
    assert app.world.characters.has(app.player)
    assert app.world.combat_stats.has(app.player)
    assert app.world.weapons.has(app.player)
    assert app.messages.current.startswith("YOLO:")


def test_created_character_gets_class_starter_weapon() -> None:
    app = create_app()

    app.handle_key(ord("c"))
    app.handle_key(ord("d"))  # Dragonborn
    app.handle_key(ord("a"))  # Barbarian
    app.handle_key(ord("e"))  # Berserker
    app.handle_key(ord("a"))
    app.handle_key(ord("t"))
    app.handle_key(ord("y"))
    app.handle_key(ord("y"))
    app.handle_key(ord("y"))

    assert app.world.weapons.require(app.player).name == "greataxe"


def test_created_character_gets_class_starter_armor() -> None:
    app = create_app()

    app.handle_key(ord("c"))
    app.handle_key(ord("u"))  # Human
    app.handle_key(ord("f"))  # Fighter
    app.handle_key(ord("c"))  # Champion
    app.handle_key(ord("a"))
    app.handle_key(ord("t"))
    app.handle_key(ord("y"))
    app.handle_key(ord("y"))
    app.handle_key(ord("y"))

    assert app.world.armor.require(app.player).name == "chain mail"
    assert app.world.combat_stats.require(app.player).armor_class == 16


def test_inventory_command_opens_and_closes_inventory() -> None:
    app = create_app()
    app.handle_key(ord("y"))

    app.handle_key(ord("i"))

    assert isinstance(app.mode, InventoryMode)

    app.handle_key(ord("q"))

    assert isinstance(app.mode, NormalMode)


def test_long_messages_require_key_to_continue_before_actions() -> None:
    app = create_app()
    app.messages.emit("x" * (PLAYFIELD_WIDTH + 20))

    app.handle_key(ord("y"))

    assert app.messages.current
    assert isinstance(app.mode, StartChoiceMode)


def test_player_death_can_restart_to_opening_choice() -> None:
    app = create_app()
    old_player = app.player

    app.apply_effects([KillEntity(app.player)])

    assert isinstance(app.mode, GameOverMode)
    assert app.running is True

    app.handle_key(ord("r"))

    assert isinstance(app.mode, StartChoiceMode)
    assert app.player != old_player or app.world.player_entity() == app.player


def test_game_over_restart_is_not_blocked_by_pending_messages() -> None:
    app = create_app()
    app.messages.emit("x" * 100)
    app.apply_effects([KillEntity(app.player)])

    app.handle_key(ord("r"))

    assert isinstance(app.mode, StartChoiceMode)
