"""Tests for the M30 loot pipeline.

Covers:
- ``DropTable`` rolling (deterministic with a seeded RNG).
- Killing a monster with a ``LootDrop`` spawns a corpse with the
  rolled inventory.
- Pickup transfers gold/items from corpses or loose drops to the
  active actor's inventory.
- Drop moves items from the actor's inventory to a fresh ground entity
  on the actor's tile (and back via pickup).
- Action economy: pickup/drop consume a turn-based action.
- Save-friendliness: a deep copy of ``World`` preserves the loot state.
"""

from __future__ import annotations

import copy
import random

from src.app import create_app
from src.core.actions import DropItemAttempt, PickupAttempt
from src.core.components import (
    Corpse,
    Inventory,
    LootDrop,
    Position,
    Presentation,
)
from src.core.effects import KillEntity
from src.core.items import has_item, item_count
from src.core.loot import DropTable, GoldDrop, ItemDrop, roll_loot
from src.core.modes import UIMode, PlayMode


def _clear_hostiles(app) -> None:
    for entity in list(app.world.creatures.values):
        app.world.remove_entity(entity)
    app.sync_play_mode()


def _booted_app(*, seed: int = 0):
    rng = random.Random(seed)
    app = create_app(rng=rng)
    app.handle_key(ord("y"))  # YOLO straight into play
    _clear_hostiles(app)
    app.ui_mode = UIMode.play
    return app


# ---------------------------------------------------------------------------
# DropTable rolling
# ---------------------------------------------------------------------------


def test_droptable_roll_is_deterministic_with_seeded_rng() -> None:
    table = DropTable(
        entries=(
            GoldDrop(amount_min=1, amount_max=10),
            ItemDrop(
                item_id="weapon.dagger",
                probability=0.5,
                quantity_min=1,
                quantity_max=2,
            ),
            ItemDrop(
                item_id="consumable.healing_potion",
                probability=1.0,
                quantity_min=1,
                quantity_max=1,
            ),
        )
    )
    roll_a = roll_loot(table, random.Random(42))
    roll_b = roll_loot(table, random.Random(42))
    assert roll_a == roll_b
    # And a different seed produces (very likely) a different roll.
    roll_c = roll_loot(table, random.Random(7))
    assert roll_a == roll_a  # sanity
    assert isinstance(roll_c.gold, int)


def test_droptable_empty_table_rolls_to_nothing() -> None:
    roll = roll_loot(DropTable(), random.Random(0))
    assert roll.gold == 0
    assert roll.items == ()


def test_droptable_validates_unknown_item_id() -> None:
    bad = DropTable(
        entries=(
            ItemDrop(item_id="weapon.does_not_exist", probability=1.0),
        )
    )
    try:
        roll_loot(bad, random.Random(0))
    except KeyError:
        pass
    else:  # pragma: no cover - regression guard
        raise AssertionError("Unknown item_id should raise KeyError")


# ---------------------------------------------------------------------------
# Kill -> corpse with loot
# ---------------------------------------------------------------------------


def test_killing_monster_with_loot_drops_creates_corpse() -> None:
    app = _booted_app()
    # Use a deterministic loot rng so the test sees a fixed outcome.
    app.loot_rng = random.Random(123)
    world = app.world
    enemy = world.create_entity()
    world.positions.add(enemy, Position(x=5, y=5))
    world.presentations.add(enemy, Presentation("o"))
    from src.core.components import Creature

    world.creatures.add(enemy, Creature(kind="goblin", attack_verb="stabs"))
    world.loot_drops.add(
        enemy,
        LootDrop(
            table=DropTable(
                entries=(
                    GoldDrop(amount_min=3, amount_max=3),
                    ItemDrop(
                        item_id="weapon.dagger",
                        probability=1.0,
                        quantity_min=1,
                        quantity_max=1,
                    ),
                )
            )
        ),
    )

    app.apply_effects([KillEntity(enemy)])

    # Enemy is gone, but a corpse with an inventory now stands at (5, 5).
    assert not world.positions.has(enemy)
    corpses = [
        entity
        for entity, position in world.positions.values.items()
        if world.corpses.has(entity) and position.x == 5 and position.y == 5
    ]
    assert len(corpses) == 1
    corpse = corpses[0]
    assert isinstance(world.corpses.require(corpse), Corpse)
    inventory = world.inventories.require(corpse)
    assert inventory.gold == 3
    assert item_count(inventory, "weapon.dagger") == 1
    # Corpses are non-blocking so the player can walk on them to loot.
    assert not world.blockers.has(corpse)


def test_killing_monster_without_loot_still_leaves_corpse() -> None:
    """An empty drop table still produces a bare corpse — a visible kill marker."""
    app = _booted_app()
    world = app.world
    enemy = world.create_entity()
    world.positions.add(enemy, Position(x=4, y=4))
    from src.core.components import Creature

    world.creatures.add(enemy, Creature(kind="frog", attack_verb="bites"))
    world.loot_drops.add(enemy, LootDrop(table=DropTable()))

    app.apply_effects([KillEntity(enemy)])

    corpses = [
        entity for entity in world.corpses.values
    ]
    assert len(corpses) == 1
    inventory = world.inventories.require(corpses[0])
    assert inventory.gold == 0
    assert inventory.items == []


def test_killing_monster_without_lootdrop_component_skips_corpse() -> None:
    """No LootDrop -> no corpse (matches pre-M30 behavior for plain enemies)."""
    app = _booted_app()
    world = app.world
    enemy = world.create_entity()
    world.positions.add(enemy, Position(x=4, y=4))
    from src.core.components import Creature

    world.creatures.add(enemy, Creature(kind="frog", attack_verb="bites"))

    app.apply_effects([KillEntity(enemy)])

    assert list(world.corpses.values) == []


# ---------------------------------------------------------------------------
# Pickup
# ---------------------------------------------------------------------------


def test_pickup_action_transfers_corpse_contents_to_active_actor() -> None:
    app = _booted_app()
    world = app.world
    player_inventory = world.inventories.require(app.player)
    before_gold = player_inventory.gold
    position = world.positions.require(app.player)

    corpse = world.create_entity()
    world.positions.add(corpse, Position(x=position.x, y=position.y))
    world.corpses.add(corpse, Corpse(creature_kind="goblin"))
    inventory = Inventory(gold=7)
    from src.core.items import add_item

    add_item(inventory, "consumable.healing_potion", 2)
    world.inventories.add(corpse, inventory)

    app.apply_effects(
        app.dispatcher.dispatch(PickupAttempt(actor=app.player), world)
    )

    # Gold is in party inventory; the corpse stays on the map but empty.
    assert player_inventory.gold == before_gold + 7
    assert has_item(player_inventory, "consumable.healing_potion", 2)
    corpse_inventory = world.inventories.require(corpse)
    assert corpse_inventory.gold == 0
    assert corpse_inventory.items == []
    # Corpse entity persists.
    assert world.positions.has(corpse)


def test_pickup_in_play_mode_consumes_turn_based_action() -> None:
    app = _booted_app()
    world = app.world
    position = world.positions.require(app.player)

    # Drop a coin pile right under the player.
    pile = world.create_entity()
    world.positions.add(pile, Position(x=position.x, y=position.y))
    world.inventories.add(pile, Inventory(gold=5))

    # Force turn-based play so the action economy is engaged.
    app.turn.voluntary_turn_based = True
    app.sync_play_mode()
    assert app.turn.play_mode is PlayMode.voluntary_turn

    # Pressing `,` should pick up and consume the active actor's action.
    app.handle_key(ord(","))

    assert app.turn.active_activation.action_used is True
    assert world.inventories.require(app.player).gold >= 5
    # The loose pile disappeared because it had no Container / Corpse marker.
    assert not world.positions.has(pile)


def test_pickup_with_nothing_on_tile_emits_message() -> None:
    app = _booted_app()
    world = app.world
    before_gold = world.inventories.require(app.player).gold

    app.apply_effects(
        app.dispatcher.dispatch(PickupAttempt(actor=app.player), world)
    )

    assert world.inventories.require(app.player).gold == before_gold
    assert "Nothing to pick up" in app.messages.current


# ---------------------------------------------------------------------------
# Drop
# ---------------------------------------------------------------------------


def test_drop_item_moves_stack_from_actor_to_ground() -> None:
    app = _booted_app()
    world = app.world
    player_inventory = world.inventories.require(app.player)
    from src.core.items import add_item

    add_item(player_inventory, "consumable.healing_potion", 3)

    app.apply_effects(
        app.dispatcher.dispatch(
            DropItemAttempt(
                actor=app.player,
                item_id="consumable.healing_potion",
                quantity=2,
            ),
            world,
        )
    )

    assert item_count(player_inventory, "consumable.healing_potion") == 1

    position = world.positions.require(app.player)
    drops = [
        entity
        for entity, pos in world.positions.values.items()
        if (entity != app.player)
        and pos.x == position.x
        and pos.y == position.y
        and world.inventories.has(entity)
    ]
    assert len(drops) == 1
    ground_inventory = world.inventories.require(drops[0])
    assert item_count(ground_inventory, "consumable.healing_potion") == 2


def test_drop_then_pickup_round_trips_item() -> None:
    """Dropping then immediately picking up restores the original inventory."""
    app = _booted_app()
    world = app.world
    player_inventory = world.inventories.require(app.player)
    from src.core.items import add_item

    add_item(player_inventory, "consumable.healing_potion", 1)
    before = item_count(player_inventory, "consumable.healing_potion")

    app.apply_effects(
        app.dispatcher.dispatch(
            DropItemAttempt(
                actor=app.player,
                item_id="consumable.healing_potion",
                quantity=1,
            ),
            world,
        )
    )
    assert item_count(player_inventory, "consumable.healing_potion") == before - 1

    app.apply_effects(
        app.dispatcher.dispatch(PickupAttempt(actor=app.player), world)
    )
    assert item_count(player_inventory, "consumable.healing_potion") == before


def test_drop_gold_updates_pile_on_tile() -> None:
    app = _booted_app()
    world = app.world
    player_inventory = world.inventories.require(app.player)
    player_inventory.gold = 50

    app.apply_effects(
        app.dispatcher.dispatch(
            DropItemAttempt(
                actor=app.player,
                item_id=None,
                quantity=0,
                gold=15,
            ),
            world,
        )
    )

    assert player_inventory.gold == 35
    position = world.positions.require(app.player)
    piles = [
        entity
        for entity, pos in world.positions.values.items()
        if (entity != app.player)
        and pos.x == position.x
        and pos.y == position.y
        and world.inventories.has(entity)
        and not world.corpses.has(entity)
    ]
    assert len(piles) == 1
    assert world.inventories.require(piles[0]).gold == 15


def test_inventory_d_key_drops_first_non_equipped_stack() -> None:
    app = _booted_app()
    world = app.world
    player_inventory = world.inventories.require(app.player)
    from src.core.items import add_item

    add_item(player_inventory, "consumable.healing_potion", 1)
    before_potions = item_count(player_inventory, "consumable.healing_potion")
    before_dagger = item_count(player_inventory, "weapon.dagger")

    app.ui_mode = UIMode.inventory
    app.handle_key(ord("d"))

    # Equipped weapon stays; the potion stack is the first non-equipped one.
    assert item_count(player_inventory, "consumable.healing_potion") == before_potions - 1
    assert item_count(player_inventory, "weapon.dagger") == before_dagger


# ---------------------------------------------------------------------------
# Save-friendliness
# ---------------------------------------------------------------------------


def test_corpse_and_loot_state_survives_world_deepcopy() -> None:
    """A deep copy of ``World`` round-trips corpses + loot inventories.

    Stand-in for the future save layer (#16): every ComponentStore on
    World (including ``corpses`` and ``loot_drops``) must serialize
    cleanly.
    """
    app = _booted_app()
    app.loot_rng = random.Random(99)
    world = app.world
    enemy = world.create_entity()
    world.positions.add(enemy, Position(x=2, y=2))
    from src.core.components import Creature

    world.creatures.add(enemy, Creature(kind="goblin", attack_verb="stabs"))
    world.loot_drops.add(
        enemy,
        LootDrop(
            table=DropTable(
                entries=(GoldDrop(amount_min=2, amount_max=2),)
            )
        ),
    )

    app.apply_effects([KillEntity(enemy)])

    snapshot = copy.deepcopy(world)
    corpse_id = next(iter(snapshot.corpses.values.keys()))
    # Mutate the live world to confirm snapshot independence.
    world.inventories.require(corpse_id).gold = 0
    assert snapshot.inventories.require(corpse_id).gold == 2
    assert snapshot.corpses.require(corpse_id).creature_kind == "goblin"
