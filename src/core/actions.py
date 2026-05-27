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


Action: TypeAlias = MoveAttempt | QuitRequest | QuitConfirm | CharacterCreationCommand
