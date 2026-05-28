from dataclasses import dataclass
from typing import TypeAlias

from .character_creation import CharacterCreationState, CharacterSheet
from .conditions import Condition, ConditionKind
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


# ---------------------------------------------------------------------------
# Debug effects (M33)
#
# Only the dev-mode debug pipeline emits these. They route through the normal
# EffectApplier so save/load and observation stay consistent, but no
# gameplay system produces them. None of them persist any dev-only state
# into save data — see `core.dump` and the GodMode component for the
# explicit non-persistence comments.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SetGodMode:
    """Toggle the GodMode component on `entity`. enabled=False removes it."""

    entity: EntityId
    enabled: bool


@dataclass(frozen=True, slots=True)
class SpawnEntity:
    """Spawn a catalog entity at (x, y).

    `kind` is a key in `src.systems.debug_system.DEBUG_SPAWN_CATALOG`. The
    applier is the only place that knows how to materialize each kind, so a
    new dev spawn is a one-line catalog addition.
    """

    kind: str
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class GrantGold:
    """Add `amount` gold to `entity`'s inventory (which must exist)."""

    entity: EntityId
    amount: int


@dataclass(frozen=True, slots=True)
class GrantItem:
    """Add `quantity` of `item_id` to `entity`'s inventory."""

    entity: EntityId
    item_id: str
    quantity: int = 1


@dataclass(frozen=True, slots=True)
class TransferInventory:
    """Move every item (and all gold) from ``source`` into ``destination``.

    Used by the M30 pickup flow. ``source`` is left with an empty
    inventory; the entity itself is not removed (corpses persist, and
    containers are emptied in place).
    """

    source: EntityId
    destination: EntityId


@dataclass(frozen=True, slots=True)
class SpawnCorpse:
    """Drop a corpse at (x, y) with the rolled loot inventory.

    ``creature_kind`` is purely cosmetic / informational; the corpse's
    ``Inventory`` is the authoritative contents store.
    """

    x: int
    y: int
    creature_kind: str
    gold: int
    items: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class ApplyCondition:
    """Attach a :class:`Condition` to ``entity``.

    The applier resolves duration policies against the current world
    clock at apply time (so ``Minutes(5)`` becomes a concrete
    ``expires_at``), and special-cases ``concentrating`` so applying a
    new one ends any prior concentration on the same actor.
    """

    entity: EntityId
    condition: Condition


@dataclass(frozen=True, slots=True)
class EndCondition:
    """Remove every condition of ``kind`` from ``entity``.

    No-op if the actor has no condition store yet, or no conditions of
    that kind.
    """

    entity: EntityId
    kind: ConditionKind


@dataclass(frozen=True, slots=True)
class DropToGround:
    """Drop ``quantity`` of ``item_id`` (or ``gold`` if item_id is None)
    from ``source``'s inventory to a fresh ground entity at (x, y).

    The pickup flow can later transfer it back into a party member's
    inventory like any other ground stuff. Stackable items merge with
    an existing dropped-stuff entity on the same tile.
    """

    source: EntityId
    x: int
    y: int
    item_id: str | None
    quantity: int = 1
    gold: int = 0


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
    | SetGodMode
    | SpawnEntity
    | GrantGold
    | GrantItem
    | TransferInventory
    | SpawnCorpse
    | DropToGround
    | ApplyCondition
    | EndCondition
)
