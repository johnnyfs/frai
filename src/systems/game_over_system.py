from src.core.actions import Action, GameOverChoice
from src.core.dispatcher import DispatchResult
from src.core.effects import QuitGame, RestartGame
from src.core.world import World


class GameOverSystem:
    def handle(self, action: Action, world: World) -> DispatchResult:
        if not isinstance(action, GameOverChoice):
            return DispatchResult()
        if action.restart:
            return DispatchResult(effects=[RestartGame()], cancel=True)
        return DispatchResult(effects=[QuitGame()], cancel=True)
