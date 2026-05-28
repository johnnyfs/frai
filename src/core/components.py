from dataclasses import dataclass
from enum import Enum

from .character_creation import CharacterSheet


class AIBehaviorType(str, Enum):
    CHASE = "chase"
    FLEE = "flee"
    WANDER = "wander"
    RANGED = "ranged"


@dataclass(frozen=True, slots=True)
class AI:
    behavior: AIBehaviorType = AIBehaviorType.CHASE
    attack_range: int = 1
    preferred_range: int = 3


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
class Creature:
    kind: str
    attack_verb: str


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


@dataclass(slots=True)
class Door:
    is_open: bool = False


@dataclass(slots=True)
class Lock:
    is_locked: bool = True
    pick_dc: int = 10


@dataclass(slots=True)
class Trap:
    is_armed: bool = True
    disarm_dc: int = 10
    damage: int = 1


@dataclass(slots=True)
class Container:
    is_open: bool = False
