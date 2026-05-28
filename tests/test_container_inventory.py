"""Tests for the unified Container/Inventory model (M42).

Containers in the world own an ``Inventory`` component as their authoritative
contents store. The ``Container`` component itself is a thin marker carrying
just an ``is_open`` flag; everything else (gold, item stacks) lives in
``Inventory`` on the same entity, mirroring how shops already work.
"""

from __future__ import annotations

import copy

from src.app import create_app
from src.core.actions import InteractAttempt
from src.core.components import Container, Inventory, Position
from src.core.effects import OpenEntity
from src.core.items import (
    add_item,
    has_item,
    item_count,
    transfer_item,
)
from src.core.modes import UIMode


def _clear_hostiles(app) -> None:
    for entity in list(app.world.creatures.values):
        app.world.remove_entity(entity)
    app.sync_play_mode()


def _add_container(app, x: int, y: int, *, contents=None, gold: int = 0):
    entity = app.world.create_entity()
    app.world.positions.add(entity, Position(x, y))
    app.world.containers.add(entity, Container())
    if contents or gold:
        inventory = Inventory(gold=gold)
        for item_id, quantity in (contents or {}).items():
            add_item(inventory, item_id, quantity)
        app.world.inventories.add(entity, inventory)
    return entity


def test_opening_container_exposes_inventory_component() -> None:
    """Opening an empty container creates an Inventory if none was seeded."""
    app = create_app()
    app.handle_key(ord("y"))
    _clear_hostiles(app)
    app.ui_mode = UIMode.play
    player_position = app.world.positions.require(app.player)
    container = _add_container(app, player_position.x + 1, player_position.y)

    assert app.world.inventories.get(container) is None

    app.apply_effects([OpenEntity(container)])

    assert app.world.containers.require(container).is_open is True
    inventory = app.world.inventories.require(container)
    assert isinstance(inventory, Inventory)
    assert inventory.gold == 0
    assert inventory.items == []


def test_opening_container_preserves_seeded_contents() -> None:
    """Pre-seeded container Inventory is not clobbered by OpenEntity."""
    app = create_app()
    app.handle_key(ord("y"))
    _clear_hostiles(app)
    app.ui_mode = UIMode.play
    player_position = app.world.positions.require(app.player)
    container = _add_container(
        app,
        player_position.x + 1,
        player_position.y,
        contents={"consumable.healing_potion": 2},
        gold=7,
    )

    app.apply_effects([OpenEntity(container)])

    inventory = app.world.inventories.require(container)
    assert inventory.gold == 7
    assert item_count(inventory, "consumable.healing_potion") == 2


def test_interaction_attempt_opens_container_and_reveals_inventory() -> None:
    """End-to-end: the e-key interaction makes container contents accessible."""
    app = create_app()
    app.handle_key(ord("y"))
    _clear_hostiles(app)
    app.ui_mode = UIMode.play
    player_position = app.world.positions.require(app.player)
    container = _add_container(
        app,
        player_position.x + 1,
        player_position.y,
        contents={"weapon.dagger": 1},
    )

    app.apply_effects(
        app._handle_interaction(InteractAttempt(app.player, 1, 0))
    )

    assert app.world.containers.require(container).is_open is True
    assert has_item(app.world.inventories.require(container), "weapon.dagger")


def test_items_transfer_between_party_inventory_and_container() -> None:
    """Items can move party -> container and back via transfer_item."""
    app = create_app()
    app.handle_key(ord("y"))
    _clear_hostiles(app)
    app.ui_mode = UIMode.play
    player_position = app.world.positions.require(app.player)
    container = _add_container(
        app,
        player_position.x + 1,
        player_position.y,
        contents={"consumable.healing_potion": 3},
    )
    app.apply_effects([OpenEntity(container)])

    party_inventory = app.world.inventories.require(app.player)
    container_inventory = app.world.inventories.require(container)
    before_party = item_count(party_inventory, "consumable.healing_potion")

    # Take 2 potions from the container.
    assert transfer_item(
        container_inventory, party_inventory, "consumable.healing_potion", 2
    )
    assert item_count(container_inventory, "consumable.healing_potion") == 1
    assert (
        item_count(party_inventory, "consumable.healing_potion") == before_party + 2
    )

    # Put one back.
    assert transfer_item(
        party_inventory, container_inventory, "consumable.healing_potion", 1
    )
    assert item_count(container_inventory, "consumable.healing_potion") == 2
    assert (
        item_count(party_inventory, "consumable.healing_potion") == before_party + 1
    )


def test_transfer_item_rejects_insufficient_quantity() -> None:
    """A transfer that can't be satisfied leaves both inventories untouched."""
    source = Inventory()
    destination = Inventory()
    add_item(source, "weapon.dagger", 1)

    assert transfer_item(source, destination, "weapon.dagger", 2) is False
    assert item_count(source, "weapon.dagger") == 1
    assert item_count(destination, "weapon.dagger") == 0


def test_container_contents_survive_world_deepcopy() -> None:
    """Container Inventory data round-trips through a deep copy.

    Acts as a stand-in for save/load: the Inventory component on a container
    entity is the authoritative contents store, so a snapshot-and-restore of
    the world must preserve it. When a real save layer lands it should cover
    every ComponentStore including ``inventories`` for container entities.
    """
    app = create_app()
    app.handle_key(ord("y"))
    _clear_hostiles(app)
    app.ui_mode = UIMode.play
    player_position = app.world.positions.require(app.player)
    container = _add_container(
        app,
        player_position.x + 1,
        player_position.y,
        contents={"weapon.dagger": 1, "consumable.healing_potion": 4},
        gold=12,
    )
    app.apply_effects([OpenEntity(container)])

    snapshot = copy.deepcopy(app.world)

    # Mutate the live world to make sure the snapshot is independent.
    live_inventory = app.world.inventories.require(container)
    live_inventory.gold = 0
    live_inventory.items.clear()

    restored_inventory = snapshot.inventories.require(container)
    assert restored_inventory.gold == 12
    assert item_count(restored_inventory, "weapon.dagger") == 1
    assert item_count(restored_inventory, "consumable.healing_potion") == 4
    assert snapshot.containers.require(container).is_open is True


def test_closing_a_container_is_out_of_scope() -> None:
    """M42 does not add a close-container flow.

    Documenting expectation: once opened, the Inventory remains on the entity
    and contents stay accessible until a future milestone introduces a close
    action or a separate "open container" UI. This test exists so that a
    future change which silently changes that behavior gets noticed.
    """
    app = create_app()
    app.handle_key(ord("y"))
    _clear_hostiles(app)
    app.ui_mode = UIMode.play
    player_position = app.world.positions.require(app.player)
    container = _add_container(
        app,
        player_position.x + 1,
        player_position.y,
        contents={"weapon.dagger": 1},
    )
    app.apply_effects([OpenEntity(container)])

    # Even after the container is flipped back to is_open=False, the
    # authoritative contents remain on the entity. There is no
    # CloseContainer action yet; this is a deliberate scope boundary.
    app.world.containers.require(container).is_open = False
    assert app.world.inventories.has(container)
    assert has_item(app.world.inventories.require(container), "weapon.dagger")
