from dataclasses import dataclass

from .character_creation import CharacterSheet


@dataclass(slots=True)
class Position:
    x: int
    y: int


@dataclass(slots=True)
class Presentation:
    glyph: str


@dataclass(slots=True)
class BlocksMovement:
    reason: str = "blocked"


@dataclass(slots=True)
class PlayerControlled:
    pass


@dataclass(slots=True)
class Name:
    value: str


@dataclass(slots=True)
class Character:
    sheet: CharacterSheet
