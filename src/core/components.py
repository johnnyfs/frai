from dataclasses import dataclass, field
from enum import Enum

from .character_creation import CharacterSheet
from .loot import DropTable


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
class InventoryStack:
    item_id: str
    quantity: int = 1


@dataclass(slots=True)
class Inventory:
    gold: int = 0
    items: list[InventoryStack] = field(default_factory=list)


@dataclass(slots=True)
class Equipment:
    weapon_item_id: str | None = None
    armor_item_id: str | None = None


@dataclass(slots=True)
class Shop:
    name: str
    buy_markup: float = 1.0
    sell_markdown: float = 0.5


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
    reusable: bool = False


@dataclass(slots=True)
class Container:
    """Marker for an entity that holds an inventory of items.

    The ``is_open`` flag controls whether the contents are accessible to the
    player. The authoritative contents store is the ``Inventory`` component on
    the same entity (just like shops). Opening a container via ``OpenEntity``
    ensures an empty ``Inventory`` exists if one wasn't seeded by content code.
    """

    is_open: bool = False


@dataclass(slots=True)
class LootDrop:
    """A drop table attached to an entity (typically a monster).

    On death, the kill-effect handler rolls ``table`` and spawns a
    corpse with the rolled contents at the dying entity's position.
    Attaching ``LootDrop`` to a non-creature entity is legal — any
    ``KillEntity`` against it will run the same pipeline — but corpses
    only make sense for creatures today.
    """

    table: DropTable


@dataclass(slots=True)
class Corpse:
    """Marker for an entity that was once a creature.

    Corpses are persistent ground entities with an ``Inventory`` holding
    the rolled loot. They are not removed when emptied so the player has
    a visible record that someone died here. The ``creature_kind`` field
    is informational and survives a save/load.
    """

    creature_kind: str = ""


@dataclass(slots=True)
class GodMode:
    """Debug-only marker: holder ignores incoming DamageEntity effects.

    Set by the M33 debug `god on/off` command. Not persisted — save/load
    will deliberately drop this so toggling god in a dev session never
    leaks into a normal player's save.
    """

    enabled: bool = True
