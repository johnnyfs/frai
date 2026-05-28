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


@dataclass(frozen=True, slots=True)
class AttackAttempt:
    actor: EntityId
    target: EntityId


@dataclass(frozen=True, slots=True)
class StartChoice:
    create: bool


@dataclass(frozen=True, slots=True)
class GameOverChoice:
    restart: bool


@dataclass(frozen=True, slots=True)
class InventoryRequest:
    pass


@dataclass(frozen=True, slots=True)
class CloseInventory:
    pass


@dataclass(frozen=True, slots=True)
class EndTurn:
    pass


@dataclass(frozen=True, slots=True)
class ToggleTurnMode:
    pass


@dataclass(frozen=True, slots=True)
class InteractAttempt:
    actor: EntityId
    dx: int
    dy: int
    check_result: int | None = None


@dataclass(frozen=True, slots=True)
class PickupAttempt:
    """Actor tries to pick up everything on their tile (M30).

    Resolution scans for any non-actor entity at the actor's position
    that owns an ``Inventory`` (corpses, dropped items, chests left
    open) and transfers contents into the actor's inventory.
    """

    actor: EntityId


@dataclass(frozen=True, slots=True)
class DropItemAttempt:
    """Actor drops ``quantity`` of ``item_id`` to their tile (M30).

    ``item_id`` is None to drop ``gold`` instead — the gold field is
    used only when ``item_id is None``.
    """

    actor: EntityId
    item_id: str | None
    quantity: int = 1
    gold: int = 0


Action: TypeAlias = (
    MoveAttempt
    | QuitRequest
    | QuitConfirm
    | CharacterCreationCommand
    | AttackAttempt
    | StartChoice
    | GameOverChoice
    | InventoryRequest
    | CloseInventory
    | EndTurn
    | ToggleTurnMode
    | InteractAttempt
    | PickupAttempt
    | DropItemAttempt
)
