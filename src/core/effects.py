from dataclasses import dataclass
from typing import TypeAlias

from .character_creation import CharacterSheet
from .entity import EntityId
from .modes import GameMode


@dataclass(frozen=True, slots=True)
class MoveEntity:
    entity: EntityId
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class EmitMessage:
    text: str


@dataclass(frozen=True, slots=True)
class SetMode:
    mode: GameMode


@dataclass(frozen=True, slots=True)
class QuitGame:
    pass


@dataclass(frozen=True, slots=True)
class SetCharacterSheet:
    entity: EntityId
    sheet: CharacterSheet


@dataclass(frozen=True, slots=True)
class DamageEntity:
    entity: EntityId
    amount: int


@dataclass(frozen=True, slots=True)
class KillEntity:
    entity: EntityId


@dataclass(frozen=True, slots=True)
class RestartGame:
    pass


Effect: TypeAlias = (
    MoveEntity
    | EmitMessage
    | SetMode
    | QuitGame
    | SetCharacterSheet
    | DamageEntity
    | KillEntity
    | RestartGame
)
