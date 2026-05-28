import curses

from src.core.actions import (
    Action,
    CharacterCreationCommand,
    CloseInventory,
    EndTurn,
    GameOverChoice,
    InventoryRequest,
    MoveAttempt,
    QuitConfirm,
    QuitRequest,
    StartChoice,
)
from src.core.entity import EntityId
from src.core.modes import (
    CharacterCreationMode,
    ConfirmQuitMode,
    GameMode,
    GameOverMode,
    InventoryMode,
    NormalMode,
    StartChoiceMode,
)

MOVE_KEYS: dict[str, tuple[int, int]] = {
    "h": (-1, 0),
    "j": (0, 1),
    "k": (0, -1),
    "l": (1, 0),
    "y": (-1, -1),
    "u": (1, -1),
    "b": (-1, 1),
    "n": (1, 1),
}


def _key_name(key: int) -> str | None:
    if key == curses.KEY_RESIZE:
        return "resize"
    if 0 <= key <= 255:
        try:
            return chr(key).lower()
        except ValueError:
            return None
    return None


def map_key(key: int, mode: GameMode, player: EntityId) -> Action | None:
    key_name = _key_name(key)
    if key_name == "resize":
        return None

    if isinstance(mode, StartChoiceMode):
        if key_name == "c":
            return StartChoice(create=True)
        if key_name == "y":
            return StartChoice(create=False)
        if key_name == "q":
            return QuitRequest()
        return None

    if isinstance(mode, GameOverMode):
        if key_name == "r":
            return GameOverChoice(restart=True)
        if key_name == "q":
            return GameOverChoice(restart=False)
        return None

    if isinstance(mode, InventoryMode):
        if key_name in ("i", "q", "b"):
            return CloseInventory()
        return None

    if isinstance(mode, CharacterCreationMode):
        if key in (curses.KEY_BACKSPACE, 8, 127) or key_name == "b":
            return CharacterCreationCommand("back", mode.state)
        if key_name == "r":
            return CharacterCreationCommand("reroll", mode.state)
        if key_name == "y":
            return CharacterCreationCommand("confirm", mode.state)
        if key_name is not None and len(key_name) == 1 and key_name.isalpha():
            return CharacterCreationCommand("choose", mode.state, key_name)
        return None

    if key_name is None:
        return None

    if isinstance(mode, NormalMode):
        if key_name == " ":
            return EndTurn()
        if key_name == "i":
            return InventoryRequest()
        if key_name in MOVE_KEYS:
            dx, dy = MOVE_KEYS[key_name]
            return MoveAttempt(actor=player, dx=dx, dy=dy)
        if key_name == "q":
            return QuitRequest()

    if isinstance(mode, ConfirmQuitMode):
        if key_name == "y":
            return QuitConfirm(True)
        if key_name == "n":
            return QuitConfirm(False)

    return None
