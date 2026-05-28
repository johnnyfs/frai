"""Apply typed Effect dataclasses against an App/World.

Centralizes the dispatch that used to live in ``App.apply_effects`` so the
matching logic is no longer a long ``isinstance`` chain inline in the App.

Handlers are grouped by domain (movement, combat, interaction, lifecycle, UI,
messages). Today they all live in this single module; once the seams are
stable they can be split into per-domain modules.

A few handlers are not pure ``World`` mutations — they reach into ``App`` state
(``mode``, ``running``, ``party``, ``restart``). Those handlers take the App as
a ``host`` argument. They are documented below and are the seams for the next
refactor pass.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from src.core.components import Corpse, GodMode, Inventory, Name, Position, Presentation
from src.core.effects import (
    DamageEntity,
    DisarmTrap,
    DropToGround,
    Effect,
    EmitMessage,
    GrantGold,
    GrantItem,
    KillEntity,
    MoveEntity,
    OpenEntity,
    QuitGame,
    RemoveBlocker,
    RestartGame,
    SetCharacterSheet,
    SetGodMode,
    SetMode,
    SpawnCorpse,
    SpawnEntity,
    TransferInventory,
    TriggerTrap,
    UnlockEntity,
)
from src.core.modes import UIMode

if TYPE_CHECKING:
    from src.app import App


class _AppHost(Protocol):
    """Subset of ``App`` that effect handlers depend on.

    Documents the coupling between effect handlers and App state so that the
    seams are visible. Anything outside this protocol must go through ``world``.
    """

    # Direct attributes the handlers mutate.
    ui_mode: UIMode
    character_creation_state: object
    running: bool
    party: object  # PartyState; typed loosely to avoid a circular import.
    active_party_index: int
    player: object
    loot_rng: object  # random.Random; used by the kill-loot pipeline.

    # Methods the handlers invoke.
    def restart(self) -> None: ...


class EffectApplier:
    """Dispatch a batch of Effects against the world and the host App.

    Construct with the owning App; ``apply_all`` consumes a list of effects in
    order, mutating the world and (where necessary) the host App state.
    """

    __slots__ = ("_host",)

    def __init__(self, host: "App") -> None:
        self._host = host

    def apply_all(self, effects: list[Effect]) -> None:
        messages: list[str] = []
        for effect in effects:
            self._dispatch(effect, messages)
        if messages:
            self._host.messages.emit(" ".join(message for message in messages if message))

    def _dispatch(self, effect: Effect, messages: list[str]) -> None:
        # Movement
        if isinstance(effect, MoveEntity):
            _apply_move_entity(self._host, effect)
            return

        # Combat
        if isinstance(effect, DamageEntity):
            _apply_damage_entity(self._host, effect)
            return
        if isinstance(effect, KillEntity):
            _apply_kill_entity(self._host, effect)
            return

        # Interaction
        if isinstance(effect, OpenEntity):
            _apply_open_entity(self._host, effect)
            return
        if isinstance(effect, UnlockEntity):
            _apply_unlock_entity(self._host, effect)
            return
        if isinstance(effect, DisarmTrap):
            _apply_disarm_trap(self._host, effect)
            return
        if isinstance(effect, TriggerTrap):
            _apply_trigger_trap(self._host, effect)
            return
        if isinstance(effect, RemoveBlocker):
            _apply_remove_blocker(self._host, effect)
            return

        # Lifecycle
        if isinstance(effect, SetCharacterSheet):
            _apply_set_character_sheet(self._host, effect)
            return
        if isinstance(effect, RestartGame):
            _apply_restart_game(self._host, effect)
            return
        if isinstance(effect, QuitGame):
            _apply_quit_game(self._host, effect)
            return

        # UI
        if isinstance(effect, SetMode):
            _apply_set_mode(self._host, effect)
            return

        # Debug (M33)
        if isinstance(effect, SetGodMode):
            _apply_set_god_mode(self._host, effect)
            return
        if isinstance(effect, SpawnEntity):
            _apply_spawn_entity(self._host, effect)
            return
        if isinstance(effect, GrantGold):
            _apply_grant_gold(self._host, effect)
            return
        if isinstance(effect, GrantItem):
            _apply_grant_item(self._host, effect)
            return

        # Loot / pickup / drop (M30)
        if isinstance(effect, TransferInventory):
            _apply_transfer_inventory(self._host, effect, messages)
            return
        if isinstance(effect, SpawnCorpse):
            _apply_spawn_corpse(self._host, effect)
            return
        if isinstance(effect, DropToGround):
            _apply_drop_to_ground(self._host, effect, messages)
            return

        # Messages
        if isinstance(effect, EmitMessage):
            messages.append(effect.text)
            return


# ---------------------------------------------------------------------------
# MovementEffects
# ---------------------------------------------------------------------------


def _apply_move_entity(host: "App", effect: MoveEntity) -> None:
    """Pure world mutation."""
    position = host.world.positions.require(effect.entity)
    position.x = effect.x
    position.y = effect.y


# ---------------------------------------------------------------------------
# CombatEffects
# ---------------------------------------------------------------------------


def _apply_damage_entity(host: "App", effect: DamageEntity) -> None:
    """Pure world mutation. Missing combat_stats is a no-op (preserved behavior).

    Entities with an enabled `GodMode` component (M33 debug `god on`) ignore
    all damage. This is intentionally checked here, not in the combat system,
    so anything that emits `DamageEntity` (combat, traps, future effects) is
    automatically respected.
    """
    god = host.world.god_modes.get(effect.entity)
    if god is not None and god.enabled:
        return
    stats = host.world.combat_stats.get(effect.entity)
    if stats is not None:
        stats.hit_points = max(0, stats.hit_points - effect.amount)


def _apply_kill_entity(host: "App", effect: KillEntity) -> None:
    """Reaches into App state: if the player dies we flip to UIMode.game_over.

    For non-player kills, if the dying entity has a ``LootDrop`` we roll
    its drop table (via the host's ``loot_rng`` so seeded fixtures stay
    deterministic) and spawn a corpse at the same tile carrying the
    rolled inventory. The dying entity is then removed.
    """
    if effect.entity == host.player:
        host.ui_mode = UIMode.game_over
        host.character_creation_state = None
        return
    world = host.world
    loot_drop = world.loot_drops.get(effect.entity)
    position = world.positions.get(effect.entity)
    creature = world.creatures.get(effect.entity)
    if loot_drop is not None and position is not None:
        from src.core.loot import roll_loot

        roll = roll_loot(loot_drop.table, host.loot_rng)
        if roll.gold or roll.items:
            _spawn_corpse_entity(
                world,
                x=position.x,
                y=position.y,
                creature_kind=creature.kind if creature is not None else "",
                gold=roll.gold,
                items=roll.items,
            )
        else:
            # Empty roll: still leave a bare corpse so the kill is visible.
            _spawn_corpse_entity(
                world,
                x=position.x,
                y=position.y,
                creature_kind=creature.kind if creature is not None else "",
                gold=0,
                items=(),
            )
    world.remove_entity(effect.entity)


def _spawn_corpse_entity(
    world,
    *,
    x: int,
    y: int,
    creature_kind: str,
    gold: int,
    items: tuple[tuple[str, int], ...],
):
    """Create a corpse entity at (x, y) with the rolled inventory.

    Corpses are non-blocking ground entities (you can walk onto a
    corpse to loot it with ``,``). They carry an ``Inventory`` so the
    same pickup/transfer code works for corpses, dropped items, and
    open containers alike.
    """
    from src.core.items import add_item

    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation("%"))
    name = f"{creature_kind} corpse" if creature_kind else "corpse"
    world.names.add(entity, Name(name))
    world.corpses.add(entity, Corpse(creature_kind=creature_kind))
    inventory = Inventory(gold=gold)
    for item_id, quantity in items:
        add_item(inventory, item_id, quantity=quantity)
    world.inventories.add(entity, inventory)
    return entity


# ---------------------------------------------------------------------------
# InteractionEffects
# ---------------------------------------------------------------------------


def _apply_open_entity(host: "App", effect: OpenEntity) -> None:
    """Pure world mutation. Opens doors and containers for the entity.

    Containers always have an ``Inventory`` component as their authoritative
    contents store. Opening a container ensures the inventory exists (empty by
    default) so callers can rely on ``world.inventories.get(container)`` once
    the open effect has been applied.
    """
    world = host.world
    if world.doors.has(effect.entity):
        world.doors.require(effect.entity).is_open = True
    if world.containers.has(effect.entity):
        world.containers.require(effect.entity).is_open = True
        if not world.inventories.has(effect.entity):
            world.inventories.add(effect.entity, Inventory())


def _apply_unlock_entity(host: "App", effect: UnlockEntity) -> None:
    """Pure world mutation."""
    lock = host.world.locks.get(effect.entity)
    if lock is not None:
        lock.is_locked = False


def _apply_disarm_trap(host: "App", effect: DisarmTrap) -> None:
    """Pure world mutation."""
    trap = host.world.traps.get(effect.entity)
    if trap is not None:
        trap.is_armed = False


def _apply_trigger_trap(host: "App", effect: TriggerTrap) -> None:
    """Pure world mutation. Non-reusable traps disarm themselves on trigger."""
    trap = host.world.traps.get(effect.entity)
    if trap is not None and not trap.reusable:
        trap.is_armed = False


def _apply_remove_blocker(host: "App", effect: RemoveBlocker) -> None:
    """Pure world mutation."""
    host.world.blockers.values.pop(effect.entity, None)


# ---------------------------------------------------------------------------
# LifecycleEffects
# ---------------------------------------------------------------------------


def _apply_set_character_sheet(host: "App", effect: SetCharacterSheet) -> None:
    """Reaches into App state when the player sheet changes (rebuilds party).

    Imports here are local to avoid a hard import cycle with ``src.app``.
    """
    from src.app import _assign_character_sheet, _replace_companions_for_player_sheet

    _assign_character_sheet(host.world, effect.entity, effect.sheet)
    if effect.entity == host.player:
        new_members = _replace_companions_for_player_sheet(
            host.world,
            host.player,
            host.party.members,
            effect.sheet,
        )
        # Mutate PartyState in place so the TurnController and any
        # other holders see the updated roster without rewiring.
        host.party.members = new_members
        host.party.follow_order = list(new_members)
        host.party.focused_index = None
        host.party.active_index = 0


def _apply_restart_game(host: "App", effect: RestartGame) -> None:
    """Reaches into App state: delegates to ``App.restart``."""
    host.restart()


def _apply_quit_game(host: "App", effect: QuitGame) -> None:
    """Reaches into App state: flips the running flag."""
    host.running = False


# ---------------------------------------------------------------------------
# UIEffects
# ---------------------------------------------------------------------------


def _apply_set_mode(host: "App", effect: SetMode) -> None:
    """Reaches into App state: swaps the active UI mode.

    When switching to `UIMode.character_creation` the effect carries the
    creation state to install; for every other UIMode the field is
    cleared so stale state cannot leak across screen transitions.
    """
    host.ui_mode = effect.mode
    if effect.mode is UIMode.character_creation:
        host.character_creation_state = effect.character_creation_state
    else:
        host.character_creation_state = None


# ---------------------------------------------------------------------------
# DebugEffects (M33)
# ---------------------------------------------------------------------------


def _apply_set_god_mode(host: "App", effect: SetGodMode) -> None:
    """Toggle the GodMode component on the target.

    `enabled=True` adds (or refreshes) a GodMode; `enabled=False` clears it.
    """
    store = host.world.god_modes
    if effect.enabled:
        store.add(effect.entity, GodMode(enabled=True))
    else:
        store.values.pop(effect.entity, None)


def _apply_spawn_entity(host: "App", effect: SpawnEntity) -> None:
    """Materialize a debug-catalog entity at (x, y).

    The catalog lives in `src.systems.debug_system`; we import lazily so the
    core effects module stays free of system-layer imports.
    """
    from src.systems.debug_system import DEBUG_SPAWN_CATALOG

    spawner = DEBUG_SPAWN_CATALOG.get(effect.kind)
    if spawner is None:
        return
    spawner(host.world, effect.x, effect.y)


def _apply_grant_gold(host: "App", effect: GrantGold) -> None:
    """Add gold to `entity`'s inventory; no-op if none."""
    inventory = host.world.inventories.get(effect.entity)
    if inventory is None:
        return
    inventory.gold += effect.amount


def _apply_grant_item(host: "App", effect: GrantItem) -> None:
    """Add `quantity` of `item_id` to `entity`'s inventory; no-op if no inventory."""
    from src.core.items import add_item

    inventory = host.world.inventories.get(effect.entity)
    if inventory is None:
        return
    add_item(inventory, effect.item_id, quantity=effect.quantity)


# ---------------------------------------------------------------------------
# Loot effects (M30)
# ---------------------------------------------------------------------------


def _apply_transfer_inventory(
    host: "App", effect: TransferInventory, messages: list[str]
) -> None:
    """Move every item and all gold from ``source`` into ``destination``.

    Empty loose drops (anything that owns Inventory + Position but is
    not a Container and not a Corpse) are removed from the world after
    the transfer. Corpses and chests stay on the ground so the player
    has a visible record.
    """
    from src.core.items import add_item

    world = host.world
    source_inventory = world.inventories.get(effect.source)
    destination_inventory = world.inventories.get(effect.destination)
    if source_inventory is None or destination_inventory is None:
        return

    transferred_names: list[str] = []
    if source_inventory.gold:
        destination_inventory.gold += source_inventory.gold
        transferred_names.append(f"{source_inventory.gold} gold")
        source_inventory.gold = 0
    for stack in list(source_inventory.items):
        add_item(destination_inventory, stack.item_id, quantity=stack.quantity)
        transferred_names.append(
            f"{stack.quantity}x {stack.item_id}"
            if stack.quantity != 1
            else stack.item_id
        )
        source_inventory.items.remove(stack)

    if transferred_names:
        source_name = world.name_for(effect.source)
        messages.append(
            f"Picked up {', '.join(transferred_names)} from {source_name}."
        )

    # Loose ground drops (no container, no corpse) are removed when empty.
    is_container = world.containers.has(effect.source)
    is_corpse = world.corpses.has(effect.source)
    if not is_container and not is_corpse:
        world.remove_entity(effect.source)


def _apply_spawn_corpse(host: "App", effect: SpawnCorpse) -> None:
    """Create a corpse entity at (x, y) with the rolled loot inventory."""
    _spawn_corpse_entity(
        host.world,
        x=effect.x,
        y=effect.y,
        creature_kind=effect.creature_kind,
        gold=effect.gold,
        items=effect.items,
    )


def _apply_drop_to_ground(
    host: "App", effect: DropToGround, messages: list[str]
) -> None:
    """Move ``quantity`` of ``item_id`` (or gold) from source to a ground entity.

    Merges into any existing loose ground-drop entity on the tile (one
    that has Inventory + Position and is not a container/corpse). A
    corpse on the tile is not merged into — dropping onto a corpse
    creates a new pile next to it (semantically still at the same tile
    but rendered as a fresh entity).
    """
    from src.core.items import add_item, has_item, remove_item

    world = host.world
    source_inventory = world.inventories.get(effect.source)
    if source_inventory is None:
        return

    if effect.item_id is None:
        # Gold drop.
        if effect.gold <= 0 or source_inventory.gold < effect.gold:
            return
        source_inventory.gold -= effect.gold
        target = _ground_drop_at(world, effect.x, effect.y)
        if target is None:
            target = _spawn_ground_drop(world, effect.x, effect.y)
        target_inventory = world.inventories.require(target)
        target_inventory.gold += effect.gold
        messages.append(f"Dropped {effect.gold} gold.")
        return

    if effect.quantity <= 0:
        return
    if not has_item(source_inventory, effect.item_id, effect.quantity):
        return
    remove_item(source_inventory, effect.item_id, effect.quantity)
    target = _ground_drop_at(world, effect.x, effect.y)
    if target is None:
        target = _spawn_ground_drop(world, effect.x, effect.y)
    target_inventory = world.inventories.require(target)
    add_item(target_inventory, effect.item_id, quantity=effect.quantity)
    qty = f"{effect.quantity}x " if effect.quantity != 1 else ""
    messages.append(f"Dropped {qty}{effect.item_id}.")


def _ground_drop_at(world, x: int, y: int):
    """Return an existing loose-drop entity at (x, y), if any.

    Containers and corpses are intentionally NOT merged into — dropping
    onto a corpse leaves a separate pile so the player can still tell
    "stuff I dropped" from "stuff the monster carried".
    """
    for entity in world.entities_at(x, y):
        if not world.inventories.has(entity):
            continue
        if world.containers.has(entity):
            continue
        if world.corpses.has(entity):
            continue
        if world.creatures.has(entity):
            continue
        if world.player_controlled.has(entity):
            continue
        return entity
    return None


def _spawn_ground_drop(world, x: int, y: int):
    """Create a fresh loose-drop ground entity (Inventory + Position)."""
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation("*"))
    world.names.add(entity, Name("items"))
    world.inventories.add(entity, Inventory())
    return entity
