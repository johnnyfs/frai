import curses

from src.core.actions import (
    Action,
    CharacterCreationCommand,
    CloseInventory,
    CloseRestMenu,
    CloseSpellMenu,
    DropItemAttempt,
    EndTurn,
    ExamineRequest,
    GameOverChoice,
    InteractAttempt,
    InventoryRequest,
    MoveAttempt,
    PerceptionAttempt,
    PickupAttempt,
    QuitConfirm,
    QuitRequest,
    RestMenuChoice,
    RestMenuRequest,
    SneakAttempt,
    SpellMenuChoice,
    SpellMenuRequest,
    StartChoice,
    ToggleTurnMode,
)
from src.core.character_creation import CharacterCreationState
from src.core.entity import EntityId
from src.core.modes import UIMode

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


def map_key(
    key: int,
    ui_mode: UIMode,
    player: EntityId,
    character_creation_state: CharacterCreationState | None = None,
) -> Action | None:
    """Translate a key press to an Action, dispatched by UIMode.

    `character_creation_state` must be provided when `ui_mode` is
    `UIMode.character_creation`; otherwise it is ignored.
    """

    key_name = _key_name(key)
    if key_name == "resize":
        return None

    if ui_mode is UIMode.start:
        if key_name == "c":
            return StartChoice(create=True)
        if key_name == "y":
            return StartChoice(create=False)
        if key_name == "q":
            return QuitRequest()
        return None

    if ui_mode is UIMode.game_over:
        if key_name == "r":
            return GameOverChoice(restart=True)
        if key_name == "q":
            return GameOverChoice(restart=False)
        return None

    if ui_mode is UIMode.inventory:
        if key_name in ("i", "q", "b"):
            return CloseInventory()
        # `d` is handled directly in App.handle_key because picking
        # which item to drop needs to read the world inventory. We
        # return None here so the App layer can resolve it.
        return None

    if ui_mode is UIMode.character_creation:
        if character_creation_state is None:
            return None
        state = character_creation_state
        if key in (curses.KEY_BACKSPACE, 8, 127) or key_name == "b":
            return CharacterCreationCommand("back", state)
        if key_name == "r":
            return CharacterCreationCommand("reroll", state)
        if key_name == "y":
            return CharacterCreationCommand("confirm", state)
        if key_name is not None and len(key_name) == 1 and key_name.isalpha():
            return CharacterCreationCommand("choose", state, key_name)
        return None

    if key_name is None:
        return None

    if ui_mode is UIMode.play:
        if key_name == " ":
            return EndTurn()
        if key_name == "t":
            return ToggleTurnMode()
        if key_name == "i":
            return InventoryRequest()
        if key_name == "e":
            return InteractAttempt(actor=player, dx=0, dy=0)
        if key_name == ",":
            return PickupAttempt(actor=player)
        if key_name in ("x", ";"):
            return ExamineRequest(actor=player)
        if key_name == "s":
            return SpellMenuRequest(actor=player)
        if key_name == "z":
            return SneakAttempt(actor=player)
        if key_name == "p":
            return PerceptionAttempt(actor=player)
        if key_name == "r":
            return RestMenuRequest(actor=player)
        if key_name in MOVE_KEYS:
            dx, dy = MOVE_KEYS[key_name]
            return MoveAttempt(actor=player, dx=dx, dy=dy)
        if key_name == "q":
            return QuitRequest()
        return None

    if ui_mode is UIMode.spell_menu:
        if key_name in (None, "q") or key == 27:
            return CloseSpellMenu()
        # Letters a..z choose the corresponding slot index. The App
        # resolves the index against the active actor's spell list,
        # so the input layer remains data-free.
        if key_name is not None and len(key_name) == 1 and key_name.isalpha():
            return SpellMenuChoice(actor=player, spell_id=key_name)
        return None

    if ui_mode is UIMode.rest_menu:
        # The rest modal accepts ``s`` (short), ``l`` (long), and a
        # cancel via ``q`` / ``Esc``. Keep it tight: any other key is
        # ignored so a stray inventory press during the modal cannot
        # leak through. The App resolves the kind through the rest
        # system; the input layer just normalises the choice.
        if key_name in (None, "q") or key == 27:
            return CloseRestMenu()
        if key_name == "s":
            return RestMenuChoice(actor=player, kind="short")
        if key_name == "l":
            return RestMenuChoice(actor=player, kind="long")
        return None

    if ui_mode is UIMode.quit_confirm:
        if key_name == "y":
            return QuitConfirm(True)
        if key_name == "n":
            return QuitConfirm(False)
        return None

    return None
