from dataclasses import dataclass, field
from typing import Protocol

from .actions import Action
from .effects import Effect
from .world import World


@dataclass(slots=True)
class DispatchResult:
    effects: list[Effect] = field(default_factory=list)
    replacement: Action | None = None
    cancel: bool = False


class System(Protocol):
    def handle(self, action: Action, world: World) -> DispatchResult:
        ...


@dataclass(slots=True)
class Dispatcher:
    systems: list[System]

    def dispatch(self, action: Action | None, world: World) -> list[Effect]:
        if action is None:
            return []

        current = action
        effects: list[Effect] = []
        index = 0
        while index < len(self.systems):
            result = self.systems[index].handle(current, world)
            effects.extend(result.effects)
            if result.cancel:
                break
            if result.replacement is not None:
                current = result.replacement
                index = 0
                continue
            index += 1
        return effects
