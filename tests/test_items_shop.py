from src.core.components import CombatStats, Equipment, Inventory, Shop
from src.core.entity import EntityId
from src.core.items import add_item, item_count
from src.core.shop import buy_item, equip_item, sell_item
from src.core.world import World
from tests.support.tiny_world import add_actor, build_tiny_map


def _shop_fixture() -> tuple[World, EntityId, EntityId]:
    world = build_tiny_map()
    player = add_actor(world, 2, 2, dexterity=14, armor_class=12, weapon="club")
    shopkeeper = world.create_entity()
    world.inventories.add(player, Inventory(gold=100))
    world.inventories.add(shopkeeper, Inventory(gold=100))
    world.shops.add(shopkeeper, Shop("Quartermaster"))
    return world, player, shopkeeper


def _inventory_snapshot(
    entity_inventory: Inventory,
) -> tuple[int, tuple[tuple[str, int], ...]]:
    return (
        entity_inventory.gold,
        tuple((stack.item_id, stack.quantity) for stack in entity_inventory.items),
    )


def _equipment_snapshot(
    world: World,
    entity: EntityId,
) -> tuple[str | None, str | None] | None:
    equipment = world.equipment.get(entity)
    if equipment is None:
        return None
    return equipment.weapon_item_id, equipment.armor_item_id


def _actor_snapshot(world: World, entity: EntityId) -> tuple:
    stats = world.combat_stats.get(entity)
    weapon = world.weapons.get(entity)
    armor = world.armor.get(entity)
    return (
        _inventory_snapshot(world.inventories.require(entity)),
        _equipment_snapshot(world, entity),
        None if weapon is None else weapon.name,
        None if armor is None else armor.name,
        None
        if stats is None
        else (
            stats.armor_class,
            stats.hit_points,
            stats.max_hit_points,
            stats.strength,
            stats.dexterity,
            stats.constitution,
            stats.proficiency_bonus,
        ),
    )


def _shop_state(world: World, player: EntityId, shopkeeper: EntityId) -> tuple:
    return (
        _inventory_snapshot(world.inventories.require(player)),
        _inventory_snapshot(world.inventories.require(shopkeeper)),
    )


def test_buy_transfers_item_and_gold_between_shop_and_player() -> None:
    world, player, shopkeeper = _shop_fixture()
    add_item(world.inventories.require(shopkeeper), "weapon.longsword")

    result = buy_item(world, player, shopkeeper, "weapon.longsword")

    assert result.success is True
    assert item_count(world.inventories.require(player), "weapon.longsword") == 1
    assert item_count(world.inventories.require(shopkeeper), "weapon.longsword") == 0
    assert world.inventories.require(player).gold == 85
    assert world.inventories.require(shopkeeper).gold == 115


def test_buy_with_insufficient_player_gold_does_not_mutate_state() -> None:
    world, player, shopkeeper = _shop_fixture()
    world.inventories.require(player).gold = 14
    add_item(world.inventories.require(shopkeeper), "weapon.longsword")
    before = _shop_state(world, player, shopkeeper)

    result = buy_item(world, player, shopkeeper, "weapon.longsword")

    assert result.success is False
    assert _shop_state(world, player, shopkeeper) == before


def test_buy_with_missing_shop_item_does_not_mutate_state() -> None:
    world, player, shopkeeper = _shop_fixture()
    before = _shop_state(world, player, shopkeeper)

    result = buy_item(world, player, shopkeeper, "weapon.longsword")

    assert result.success is False
    assert _shop_state(world, player, shopkeeper) == before


def test_buy_with_insufficient_shop_stock_does_not_mutate_state() -> None:
    world, player, shopkeeper = _shop_fixture()
    add_item(world.inventories.require(shopkeeper), "consumable.healing_potion")
    before = _shop_state(world, player, shopkeeper)

    result = buy_item(world, player, shopkeeper, "consumable.healing_potion", quantity=2)

    assert result.success is False
    assert _shop_state(world, player, shopkeeper) == before


def test_buy_with_invalid_quantity_does_not_mutate_state() -> None:
    world, player, shopkeeper = _shop_fixture()
    add_item(world.inventories.require(shopkeeper), "consumable.healing_potion")
    before = _shop_state(world, player, shopkeeper)

    result = buy_item(world, player, shopkeeper, "consumable.healing_potion", quantity=0)

    assert result.success is False
    assert _shop_state(world, player, shopkeeper) == before


def test_sell_transfers_item_and_gold_between_player_and_shop() -> None:
    world, player, shopkeeper = _shop_fixture()
    add_item(world.inventories.require(player), "armor.leather")

    result = sell_item(world, player, shopkeeper, "armor.leather")

    assert result.success is True
    assert item_count(world.inventories.require(player), "armor.leather") == 0
    assert item_count(world.inventories.require(shopkeeper), "armor.leather") == 1
    assert world.inventories.require(player).gold == 105
    assert world.inventories.require(shopkeeper).gold == 95


def test_sell_with_invalid_quantity_does_not_mutate_state() -> None:
    world, player, shopkeeper = _shop_fixture()
    add_item(world.inventories.require(player), "armor.leather")
    before = _shop_state(world, player, shopkeeper)

    result = sell_item(world, player, shopkeeper, "armor.leather", quantity=0)

    assert result.success is False
    assert _shop_state(world, player, shopkeeper) == before


def test_sell_with_insufficient_shop_gold_does_not_mutate_state() -> None:
    world, player, shopkeeper = _shop_fixture()
    world.inventories.require(shopkeeper).gold = 10
    add_item(world.inventories.require(player), "armor.chain_mail")
    before = _shop_state(world, player, shopkeeper)

    result = sell_item(world, player, shopkeeper, "armor.chain_mail")

    assert result.success is False
    assert _shop_state(world, player, shopkeeper) == before


def test_sell_equipped_item_does_not_mutate_state() -> None:
    world, player, shopkeeper = _shop_fixture()
    add_item(world.inventories.require(player), "armor.leather")
    world.equipment.add(player, Equipment(armor_item_id="armor.leather"))
    before = (
        _shop_state(world, player, shopkeeper),
        _equipment_snapshot(world, player),
    )

    result = sell_item(world, player, shopkeeper, "armor.leather")

    assert result.success is False
    assert (
        _shop_state(world, player, shopkeeper),
        _equipment_snapshot(world, player),
    ) == before


def test_sell_missing_owned_item_does_not_mutate_state() -> None:
    world, player, shopkeeper = _shop_fixture()
    before = _shop_state(world, player, shopkeeper)

    result = sell_item(world, player, shopkeeper, "armor.leather")

    assert result.success is False
    assert _shop_state(world, player, shopkeeper) == before


def test_equip_armor_changes_existing_combat_stats() -> None:
    world, player, _ = _shop_fixture()
    stats = world.combat_stats.require(player)
    stats.armor_class = 12
    add_item(world.inventories.require(player), "armor.scale_mail")

    result = equip_item(world, player, "armor.scale_mail")

    assert result.success is True
    assert world.armor.require(player).name == "scale mail"
    assert world.equipment.require(player).armor_item_id == "armor.scale_mail"
    assert world.combat_stats.require(player).armor_class == 16


def test_equip_weapon_changes_existing_weapon_component() -> None:
    world, player, _ = _shop_fixture()
    add_item(world.inventories.require(player), "weapon.greataxe")

    result = equip_item(world, player, "weapon.greataxe")

    assert result.success is True
    assert world.weapons.require(player).name == "greataxe"
    assert world.equipment.require(player).weapon_item_id == "weapon.greataxe"


def test_equip_unknown_item_does_not_mutate_state() -> None:
    world, player, _ = _shop_fixture()
    before = _actor_snapshot(world, player)

    result = equip_item(world, player, "weapon.missing")

    assert result.success is False
    assert _actor_snapshot(world, player) == before


def test_equip_non_owned_item_does_not_mutate_state() -> None:
    world, player, _ = _shop_fixture()
    before = _actor_snapshot(world, player)

    result = equip_item(world, player, "weapon.greataxe")

    assert result.success is False
    assert _actor_snapshot(world, player) == before


def test_invalid_equip_is_blocked_without_stat_change() -> None:
    world, player, _ = _shop_fixture()
    world.combat_stats.add(
        player,
        CombatStats(
            armor_class=12,
            hit_points=8,
            max_hit_points=8,
            strength=10,
            dexterity=14,
            constitution=10,
        ),
    )
    add_item(world.inventories.require(player), "consumable.healing_potion")
    before = _actor_snapshot(world, player)

    result = equip_item(world, player, "consumable.healing_potion")

    assert result.success is False
    assert _actor_snapshot(world, player) == before
