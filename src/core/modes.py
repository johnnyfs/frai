from dataclasses import dataclass
from typing import TypeAlias

from .character_creation import CharacterCreationState


@dataclass(frozen=True, slots=True)
class NormalMode:
    pass


@dataclass(frozen=True, slots=True)
class ConfirmQuitMode:
    pass


@dataclass(frozen=True, slots=True)
class CharacterCreationMode:
    state: CharacterCreationState


GameMode: TypeAlias = NormalMode | ConfirmQuitMode | CharacterCreationMode
