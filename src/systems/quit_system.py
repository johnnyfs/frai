from src.core.actions import Action, QuitConfirm, QuitRequest
from src.core.dispatcher import DispatchResult
from src.core.effects import EmitMessage, QuitGame, SetMode
from src.core.modes import ConfirmQuitMode, NormalMode
from src.core.world import World


class QuitSystem:
    def handle(self, action: Action, world: World) -> DispatchResult:
        if isinstance(action, QuitRequest):
            return DispatchResult(
                effects=[EmitMessage("Quit? y/n"), SetMode(ConfirmQuitMode())],
                cancel=True,
            )
        if isinstance(action, QuitConfirm):
            if action.answer:
                return DispatchResult(effects=[QuitGame()], cancel=True)
            return DispatchResult(
                effects=[SetMode(NormalMode()), EmitMessage("")],
                cancel=True,
            )
        return DispatchResult()
