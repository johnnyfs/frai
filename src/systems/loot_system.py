"""Pickup/drop action resolver (M30).

Picks up every loose item + gold on the actor's tile via a single
``TransferInventory`` effect per source. The same entity model (an
``Inventory`` on a positioned entity) covers corpses, loose dropped
piles, and open containers — see M42 for the unified container model.

Dropping is the inverse: an item or some gold flows from the actor's
inventory to a fresh ground-drop entity (a non-container, non-corpse
inventory at the actor's tile).
"""

from __future__ import annotations

from src.core.actions import Action, DropItemAttempt, PickupAttempt
from src.core.dispatcher import DispatchResult
from src.core.effects import (
    DropToGround,
    Effect,
    EmitMessage,
    TransferInventory,
)
from src.core.entity import EntityId
from src.core.world import World


class LootSystem:
    """Resolves ``PickupAttempt`` and ``DropItemAttempt`` actions."""

    def handle(self, action: Action, world: World) -> DispatchResult:
        if isinstance(action, PickupAttempt):
            return DispatchResult(
                effects=_resolve_pickup(world, action.actor),
                cancel=True,
            )
        if isinstance(action, DropItemAttempt):
            return DispatchResult(
                effects=_resolve_drop(world, action),
                cancel=True,
            )
        return DispatchResult()


def _resolve_pickup(world: World, actor: EntityId) -> list[Effect]:
    if not world.inventories.has(actor):
        return [EmitMessage("You have no way to carry anything.")]
    position = world.positions.get(actor)
    if position is None:
        return []
    sources = _pickup_sources(world, actor, position.x, position.y)
    if not sources:
        return [EmitMessage("Nothing to pick up.")]
    # Containers that are still closed (or locked, or armed-with-trap)
    # must be opened/picked/disarmed via the `e` interaction path first
    # — picking up by walking onto the tile and pressing `,` would
    # otherwise bypass locks and trap checks entirely (issue #65). We
    # detect the obstruction once and surface a single hint so the
    # player knows which verb to use.
    refusal = _container_refusal(world, sources)
    if refusal is not None:
        return [EmitMessage(refusal)]
    effects: list[Effect] = []
    picked_any = False
    for source in sources:
        inventory = world.inventories.require(source)
        if inventory.gold == 0 and not inventory.items:
            continue
        effects.append(TransferInventory(source=source, destination=actor))
        picked_any = True
    if not picked_any:
        return [EmitMessage("Nothing to pick up.")]
    return effects


def _container_refusal(world: World, sources: list[EntityId]) -> str | None:
    """Return a refusal message if any pickup source is a guarded container.

    "Guarded" means a container that is still closed, or one whose lock
    is locked, or one that carries an armed trap. The interaction
    system (``e``) is the right entry point for all three: it routes
    the actor through the disarm/pick/open sequence. Picking up through
    ``,`` would silently bypass that, which is the bug behind #65.
    """
    for source in sources:
        if not world.containers.has(source):
            continue
        container = world.containers.require(source)
        trap = world.traps.get(source)
        lock = world.locks.get(source)
        if trap is not None and trap.is_armed:
            return "Something is rigged here — disarm it first."
        if lock is not None and lock.is_locked:
            return "It is locked — open it first."
        if not container.is_open:
            return "It is closed — open it first."
    return None


def _pickup_sources(
    world: World, actor: EntityId, x: int, y: int
) -> list[EntityId]:
    """Return every non-actor, non-creature entity at (x, y) with an Inventory.

    Sorted by entity id for deterministic ordering, matching how
    ``world.entities_at`` would already iterate.
    """
    sources: list[EntityId] = []
    for entity in world.entities_at(x, y):
        if entity == actor:
            continue
        if not world.inventories.has(entity):
            continue
        if world.player_controlled.has(entity):
            continue
        if world.creatures.has(entity):
            continue
        sources.append(entity)
    return sources


def _resolve_drop(world: World, action: DropItemAttempt) -> list[Effect]:
    if not world.inventories.has(action.actor):
        return [EmitMessage("Nothing to drop.")]
    inventory = world.inventories.require(action.actor)
    position = world.positions.get(action.actor)
    if position is None:
        return []

    if action.item_id is None:
        if action.gold <= 0:
            return [EmitMessage("Nothing to drop.")]
        if inventory.gold < action.gold:
            return [EmitMessage("Not enough gold.")]
        return [
            DropToGround(
                source=action.actor,
                x=position.x,
                y=position.y,
                item_id=None,
                quantity=0,
                gold=action.gold,
            )
        ]

    quantity = max(1, action.quantity)
    from src.core.items import has_item

    if not has_item(inventory, action.item_id, quantity):
        return [EmitMessage("You don't have that.")]
    return [
        DropToGround(
            source=action.actor,
            x=position.x,
            y=position.y,
            item_id=action.item_id,
            quantity=quantity,
            gold=0,
        )
    ]
