from dataclasses import dataclass
from typing import TypeAlias

from .character_creation import CharacterCreationState, CreatorCommand
from .entity import EntityId


@dataclass(frozen=True, slots=True)
class MoveAttempt:
    actor: EntityId
    dx: int
    dy: int


@dataclass(frozen=True, slots=True)
class QuitRequest:
    pass


@dataclass(frozen=True, slots=True)
class QuitConfirm:
    answer: bool


@dataclass(frozen=True, slots=True)
class CharacterCreationCommand:
    command: CreatorCommand
    state: CharacterCreationState
    key: str | None = None


@dataclass(frozen=True, slots=True)
class AttackAttempt:
    actor: EntityId
    target: EntityId


@dataclass(frozen=True, slots=True)
class StartChoice:
    create: bool


@dataclass(frozen=True, slots=True)
class GameOverChoice:
    restart: bool


@dataclass(frozen=True, slots=True)
class InventoryRequest:
    pass


@dataclass(frozen=True, slots=True)
class CloseInventory:
    pass


@dataclass(frozen=True, slots=True)
class EndTurn:
    pass


@dataclass(frozen=True, slots=True)
class ToggleTurnMode:
    pass


@dataclass(frozen=True, slots=True)
class InteractAttempt:
    actor: EntityId
    dx: int
    dy: int
    check_result: int | None = None


Action: TypeAlias = (
    MoveAttempt
    | QuitRequest
    | QuitConfirm
    | CharacterCreationCommand
    | AttackAttempt
    | StartChoice
    | GameOverChoice
    | InventoryRequest
    | CloseInventory
    | EndTurn
    | ToggleTurnMode
    | InteractAttempt
)
