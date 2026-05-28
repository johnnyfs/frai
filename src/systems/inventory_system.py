from src.core.actions import Action, CloseInventory, InventoryRequest
from src.core.dispatcher import DispatchResult
from src.core.effects import SetMode
from src.core.modes import UIMode
from src.core.world import World


class InventorySystem:
    def handle(self, action: Action, world: World) -> DispatchResult:
        if isinstance(action, InventoryRequest):
            return DispatchResult(effects=[SetMode(UIMode.inventory)], cancel=True)
        if isinstance(action, CloseInventory):
            return DispatchResult(effects=[SetMode(UIMode.play)], cancel=True)
        return DispatchResult()
