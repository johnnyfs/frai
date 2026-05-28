from dataclasses import dataclass, field
from enum import Enum

from .character_creation import CharacterSheet
from .dialogue import DialogueTree
from .entity import EntityId
from .factions import AggroOverrideList  # noqa: F401  re-exported for save/load
from .loot import DropTable


class AIBehaviorType(str, Enum):
    CHASE = "chase"
    FLEE = "flee"
    WANDER = "wander"
    RANGED = "ranged"


class NPCKind(str, Enum):
    """What flavour of NPC this is (M13).

    The kind is purely informational — the dialogue tree on the NPC
    fully describes its behaviour. ``kind`` exists so observation /
    debug tooling and content authors can tell "this NPC is a shop
    front" from "this NPC tells me a clue" at a glance, and so the
    M14 quest pipeline has a stable tag to filter on.
    """

    INFO = "info"
    RECRUIT = "recruit"
    SHOPKEEPER = "shopkeeper"


@dataclass(slots=True)
class NPC:
    """Marker component identifying an entity as an NPC.

    The ``DialogueTree`` lives in a sibling ``NPCDialogue`` component
    so the dialogue payload can be optional / replaced over the
    course of a quest without touching the NPC marker itself.
    """

    kind: NPCKind


@dataclass(slots=True)
class NPCDialogue:
    """Conversation payload for an NPC (M13)."""

    tree: DialogueTree


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
    """Which faction an entity belongs to.

    ``value`` is the canonical faction id (see ``FactionId`` in
    ``src/core/factions.py``). Pre-M28 saves stored raw strings like
    ``"player"`` and ``"enemy"``; those are aliased to ``player_party``
    and ``dungeon`` at lookup time so legacy saves keep loading.

    ``summoner`` lets a companion / pet / familiar / summon inherit its
    effective relations from another entity. When set, the awareness
    system resolves the faction by walking the chain to the summoner so
    a fireball-elemental summoned by the player is always treated as
    PLAYER_PARTY-aligned even if its own ``value`` is something else.
    """

    value: str
    summoner: EntityId | None = None


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
class BossMarker:
    """Tag an entity as a quest boss (M14).

    The ``token`` field is a stable string id matched against the
    ``boss_marker`` field on :class:`~src.core.quest.QuestObjective`.
    When a tagged entity dies, the quest progress hook in the effect
    applier flips the corresponding quest forward if the kill is the
    last criterion outstanding.
    """

    token: str


@dataclass(slots=True)
class GodMode:
    """Debug-only marker: holder ignores incoming DamageEntity effects.

    Set by the M33 debug `god on/off` command. Not persisted — save/load
    will deliberately drop this so toggling god in a dev session never
    leaks into a normal player's save.
    """

    enabled: bool = True
