from src.core.components import CombatStats, Inventory, Shop
from src.core.items import add_item, item_count
from src.core.shop import buy_item, equip_item, sell_item
from tests.support.tiny_world import add_actor, build_tiny_map


def _shop_fixture() -> tuple:
    world = build_tiny_map()
    player = add_actor(world, 2, 2, dexterity=14, armor_class=12, weapon="club")
    shopkeeper = world.create_entity()
    world.inventories.add(player, Inventory(gold=100))
    world.inventories.add(shopkeeper, Inventory(gold=100))
    world.shops.add(shopkeeper, Shop("Quartermaster"))
    return world, player, shopkeeper


def test_buy_transfers_item_and_gold_between_shop_and_player() -> None:
    world, player, shopkeeper = _shop_fixture()
    add_item(world.inventories.require(shopkeeper), "weapon.longsword")

    result = buy_item(world, player, shopkeeper, "weapon.longsword")

    assert result.success is True
    assert item_count(world.inventories.require(player), "weapon.longsword") == 1
    assert item_count(world.inventories.require(shopkeeper), "weapon.longsword") == 0
    assert world.inventories.require(player).gold == 85
    assert world.inventories.require(shopkeeper).gold == 115


def test_sell_transfers_item_and_gold_between_player_and_shop() -> None:
    world, player, shopkeeper = _shop_fixture()
    add_item(world.inventories.require(player), "armor.leather")

    result = sell_item(world, player, shopkeeper, "armor.leather")

    assert result.success is True
    assert item_count(world.inventories.require(player), "armor.leather") == 0
    assert item_count(world.inventories.require(shopkeeper), "armor.leather") == 1
    assert world.inventories.require(player).gold == 105
    assert world.inventories.require(shopkeeper).gold == 95


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

    result = equip_item(world, player, "consumable.healing_potion")

    assert result.success is False
    assert world.combat_stats.require(player).armor_class == 12
    assert world.weapons.require(player).name == "club"
    assert world.equipment.get(player) is None
