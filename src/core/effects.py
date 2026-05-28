from dataclasses import dataclass
from typing import TypeAlias

from .character_creation import CharacterCreationState, CharacterSheet
from .entity import EntityId
from .modes import UIMode


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
    """Switch the active UI screen.

    `character_creation_state` is only populated when switching to
    `UIMode.character_creation`; other UI modes carry no per-screen
    payload today.
    """

    mode: UIMode
    character_creation_state: CharacterCreationState | None = None


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


@dataclass(frozen=True, slots=True)
class OpenEntity:
    entity: EntityId


@dataclass(frozen=True, slots=True)
class UnlockEntity:
    entity: EntityId


@dataclass(frozen=True, slots=True)
class DisarmTrap:
    entity: EntityId


@dataclass(frozen=True, slots=True)
class TriggerTrap:
    entity: EntityId


@dataclass(frozen=True, slots=True)
class RemoveBlocker:
    entity: EntityId


Effect: TypeAlias = (
    MoveEntity
    | EmitMessage
    | SetMode
    | QuitGame
    | SetCharacterSheet
    | DamageEntity
    | KillEntity
    | RestartGame
    | OpenEntity
    | UnlockEntity
    | DisarmTrap
    | TriggerTrap
    | RemoveBlocker
)
