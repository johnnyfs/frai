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


@dataclass(slots=True)
class CombatStats:
    armor_class: int
    hit_points: int
    max_hit_points: int
    strength: int
    dexterity: int
    constitution: int
    proficiency_bonus: int = 2


@dataclass(slots=True)
class Weapon:
    name: str
    damage_die: int
    damage_type: str
    ability: str = "STR"
    finesse: bool = False


@dataclass(slots=True)
class Armor:
    name: str
    base_armor_class: int
    dexterity_cap: int | None = None


@dataclass(slots=True)
class Faction:
    value: str
