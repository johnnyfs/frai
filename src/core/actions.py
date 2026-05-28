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
class SpellMenuRequest:
    """Open the spell-selection modal for ``actor`` (M11).

    Routes through ``app.handle_key`` rather than the dispatcher
    because opening a modal is App-state, not world state — same
    pattern as ``InventoryRequest``.
    """

    actor: EntityId


@dataclass(frozen=True, slots=True)
class CloseSpellMenu:
    """Dismiss the spell-selection modal without casting (M11)."""

    pass


@dataclass(frozen=True, slots=True)
class SpellMenuChoice:
    """Player picked ``spell_id`` from the spell menu (M11).

    The App resolves what happens next: spells that need a target
    open the M20 targeting modal; spells with no target (self-buff
    placeholders) build the :class:`CastSpellAttempt` immediately.
    """

    actor: EntityId
    spell_id: str


@dataclass(frozen=True, slots=True)
class CastSpellAttempt:
    """Actor attempts to cast ``spell_id`` (M11).

    ``target_entity`` is the resolved single entity for
    ``SINGLE_ENTITY``-kind spells, ``target_tile`` is the cursor cell
    for ``AREA_RADIUS``-kind spells, and ``target_entities`` is the
    tuple of friendly entities for ``FRIENDLY_GROUP``-kind spells.
    Only the field(s) appropriate for the spell's ``target_kind``
    need to be populated — the spell system reads the catalog entry to
    decide which to consult.

    The action is the same shape whether the spell came from the
    player's menu, an AI's tactical choice, or a future scripted
    encounter. The caster's slot is consumed in the resolver's
    ``PRE_CHECK`` phase so a failed resolve doesn't burn the resource.
    """

    actor: EntityId
    spell_id: str
    target_entity: EntityId | None = None
    target_tile: tuple[int, int] | None = None
    target_entities: tuple[EntityId, ...] = ()


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


@dataclass(frozen=True, slots=True)
class SneakAttempt:
    """Actor tries to enter the ``hidden`` condition (M23 stealth)."""

    actor: EntityId
    dc: int = 10


@dataclass(frozen=True, slots=True)
class PerceptionAttempt:
    """Actor tries to spot every hidden creature within sight (M23)."""

    actor: EntityId
    dc: int = 10


@dataclass(frozen=True, slots=True)
class RestMenuRequest:
    """Open the rest-selection modal for ``actor`` (M34)."""

    actor: EntityId


@dataclass(frozen=True, slots=True)
class CloseRestMenu:
    """Dismiss the rest-selection modal without resting (M34)."""

    pass


@dataclass(frozen=True, slots=True)
class RestMenuChoice:
    """Player picked a rest kind from the rest menu (M34).

    ``kind`` is one of ``"short"`` or ``"long"``.
    """

    actor: EntityId
    kind: str


@dataclass(frozen=True, slots=True)
class LevelUpConfirm:
    """Player confirmed the pending level-up on ``actor`` (M25).

    The level-up modal turns this action into a :class:`LevelUp`
    effect; the modal itself does not mutate state directly. ``actor``
    is the first party member with a pending level-up — the modal
    only ever surfaces one at a time so the player sees what changed.
    """

    actor: EntityId


@dataclass(frozen=True, slots=True)
class LevelUpDismiss:
    """Close the level-up modal without applying (M25).

    The :class:`LevelUpAvailable` marker stays attached so the modal
    can reopen on the next level-up cue (or via the future `?` /
    debug entry). Used to escape the modal if the player wants to
    consult the help screen first.
    """

    actor: EntityId


@dataclass(frozen=True, slots=True)
class HelpRequest:
    """Open the help modal from any non-modal context.

    Pressing ``?`` in :class:`UIMode.play` emits this request. The App
    builds a fresh :class:`HelpState` and flips to :class:`UIMode.help`.
    Help is a pure UI modal — no world mutation, no clock advance.
    """

    pass


@dataclass(frozen=True, slots=True)
class RosterRequest:
    """Open the party roster modal.

    Pressing capital ``P`` (lowercase ``p`` is the perception sweep)
    emits this request. The App projects the party into a
    :class:`RosterState` and flips to :class:`UIMode.roster`.
    """

    actor: EntityId


@dataclass(frozen=True, slots=True)
class ExamineRequest:
    """Open the M21 examine/look cursor over the world.

    Pressing ``x`` or ``;`` in :class:`UIMode.play` emits this request.
    The App handles it directly by opening a targeting modal with the
    ``any_tile`` predicate and an on-confirm callback that emits
    description text rather than dispatching a world-changing action.

    Examine is **not** an action — it consumes no resource and does
    not advance any turn or clock. The request type exists so the
    input-system stays a pure key-to-action mapper and the App owns
    the targeting plumbing.
    """

    actor: EntityId


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
    | ExamineRequest
    | CastSpellAttempt
    | SpellMenuRequest
    | CloseSpellMenu
    | SpellMenuChoice
    | SneakAttempt
    | PerceptionAttempt
    | RestMenuRequest
    | CloseRestMenu
    | RestMenuChoice
    | LevelUpConfirm
    | LevelUpDismiss
    | HelpRequest
    | RosterRequest
)
