"""UI screen modes and gameplay play modes.

UIMode names which screen or modal is active. PlayMode names the gameplay
state inside `UIMode.play`. Splitting them lets future modal screens
(targeting, examine, help, message pager) stack over play without
colliding with the explore/turn-based gameplay state machine.

PlayMode is only meaningful while `UIMode == play`. App keeps both
fields side by side; `App.play_mode` is only consulted from the play
screen, and helper accessors raise if read from a non-play UI mode.
"""

from enum import Enum
from typing import Literal


class UIMode(Enum):
    """Which screen or modal is currently being shown."""

    start = "start"
    character_creation = "character_creation"
    play = "play"
    inventory = "inventory"
    dialogue = "dialogue"
    shop = "shop"
    targeting = "targeting"
    examine = "examine"
    help = "help"
    message_pager = "message_pager"
    quit_confirm = "quit_confirm"
    game_over = "game_over"


class PlayMode(Enum):
    """Gameplay state while `UIMode == play`.

    - `explore`: free movement, no per-turn action budget.
    - `turn_based`: forced turn-based (hostiles in sight).
    - `voluntary_turn`: player-opted turn-based without hostiles.
    """

    explore = "explore"
    turn_based = "turn_based"
    voluntary_turn = "voluntary_turn"


# Compatibility alias for legacy string-typed status rendering.
PlayModeName = Literal["explore", "turn_based", "voluntary_turn"]


def is_turn_based_play(mode: PlayMode) -> bool:
    """True when the play mode applies per-turn action budgets."""

    return mode in (PlayMode.turn_based, PlayMode.voluntary_turn)


def play_mode_for_state(
    hostiles_present: bool,
    voluntary_turn_based: bool = False,
) -> PlayMode:
    """Derive the PlayMode from hostile presence and the voluntary flag.

    Hostiles always win: forced turn-based mode overrides any voluntary
    opt-in. With no hostiles, the voluntary flag picks between explore
    and the player-opted turn-based mode.
    """

    if hostiles_present:
        return PlayMode.turn_based
    if voluntary_turn_based:
        return PlayMode.voluntary_turn
    return PlayMode.explore
