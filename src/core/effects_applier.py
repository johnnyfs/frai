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

from src.core.components import GodMode, Inventory
from src.core.effects import (
    DamageEntity,
    DisarmTrap,
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
    SpawnEntity,
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
    party: list
    active_party_index: int
    player: object

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
    """Reaches into App state: if the player dies we flip to UIMode.game_over."""
    if effect.entity == host.player:
        host.ui_mode = UIMode.game_over
        host.character_creation_state = None
    else:
        host.world.remove_entity(effect.entity)


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
        host.party = _replace_companions_for_player_sheet(
            host.world,
            host.player,
            host.party,
            effect.sheet,
        )
        host.active_party_index = 0


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
