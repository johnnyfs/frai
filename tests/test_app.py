from src.app import create_app
from src.core.modes import CharacterCreationMode, ConfirmQuitMode, NormalMode


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


def test_app_starts_in_character_creation() -> None:
    app = create_app()

    assert isinstance(app.mode, CharacterCreationMode)


def test_character_creation_flow_assigns_sheet_and_starts_game() -> None:
    app = create_app()

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
