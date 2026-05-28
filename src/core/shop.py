from dataclasses import dataclass

from src.core.combat import armor_class_for, armor_for_name, weapon_for_name
from src.core.components import Equipment, Inventory, Shop
from src.core.entity import EntityId
from src.core.items import (
    EquipmentSlot,
    add_item,
    has_item,
    remove_item,
    require_item,
)
from src.core.world import World


@dataclass(frozen=True, slots=True)
class OperationResult:
    success: bool
    message: str


def buy_item(
    world: World,
    buyer: EntityId,
    shopkeeper: EntityId,
    item_id: str,
    quantity: int = 1,
) -> OperationResult:
    if quantity <= 0:
        return OperationResult(False, "Quantity must be positive.")
    try:
        item = require_item(item_id)
    except KeyError:
        return OperationResult(False, "Unknown item.")

    buyer_inventory = world.inventories.require(buyer)
    shop_inventory = world.inventories.require(shopkeeper)
    shop = world.shops.require(shopkeeper)
    if not has_item(shop_inventory, item_id, quantity):
        return OperationResult(False, f"{shop.name} does not have enough {item.name}.")

    price = buy_price(item.value, shop) * quantity
    if buyer_inventory.gold < price:
        return OperationResult(False, "Not enough gold.")

    remove_item(shop_inventory, item_id, quantity)
    add_item(buyer_inventory, item_id, quantity)
    buyer_inventory.gold -= price
    shop_inventory.gold += price
    return OperationResult(True, f"Bought {quantity} {item.name} for {price} gold.")


def sell_item(
    world: World,
    seller: EntityId,
    shopkeeper: EntityId,
    item_id: str,
    quantity: int = 1,
) -> OperationResult:
    if quantity <= 0:
        return OperationResult(False, "Quantity must be positive.")
    try:
        item = require_item(item_id)
    except KeyError:
        return OperationResult(False, "Unknown item.")

    seller_inventory = world.inventories.require(seller)
    shop_inventory = world.inventories.require(shopkeeper)
    shop = world.shops.require(shopkeeper)
    if _is_equipped(world, seller, item_id):
        return OperationResult(False, f"Unequip {item.name} before selling it.")
    if not has_item(seller_inventory, item_id, quantity):
        return OperationResult(False, f"You do not have enough {item.name}.")

    price = sell_price(item.value, shop) * quantity
    if shop_inventory.gold < price:
        return OperationResult(False, f"{shop.name} cannot afford that.")

    remove_item(seller_inventory, item_id, quantity)
    add_item(shop_inventory, item_id, quantity)
    seller_inventory.gold += price
    shop_inventory.gold -= price
    return OperationResult(True, f"Sold {quantity} {item.name} for {price} gold.")


def equip_item(world: World, actor: EntityId, item_id: str) -> OperationResult:
    try:
        item = require_item(item_id)
    except KeyError:
        return OperationResult(False, "Unknown item.")

    inventory = world.inventories.require(actor)
    if not has_item(inventory, item_id):
        return OperationResult(False, f"You do not have {item.name}.")
    if item.slot is None:
        return OperationResult(False, f"{item.name} cannot be equipped.")

    equipment = world.equipment.get(actor)
    if equipment is None:
        equipment = Equipment()
        world.equipment.add(actor, equipment)

    if item.slot == EquipmentSlot.WEAPON:
        if item.weapon_name is None:
            return OperationResult(False, f"{item.name} is missing weapon data.")
        world.weapons.add(actor, weapon_for_name(item.weapon_name))
        equipment.weapon_item_id = item.item_id
        return OperationResult(True, f"Equipped {item.name}.")

    if item.slot == EquipmentSlot.ARMOR:
        if item.armor_name is None:
            return OperationResult(False, f"{item.name} is missing armor data.")
        armor = armor_for_name(item.armor_name)
        world.armor.add(actor, armor)
        equipment.armor_item_id = item.item_id
        stats = world.combat_stats.get(actor)
        if stats is not None:
            stats.armor_class = armor_class_for(stats.dexterity, armor)
        return OperationResult(True, f"Equipped {item.name}.")

    return OperationResult(False, f"{item.name} cannot be equipped.")


def buy_price(base_value: int, shop: Shop) -> int:
    return max(1, round(base_value * shop.buy_markup))


def sell_price(base_value: int, shop: Shop) -> int:
    return max(1, round(base_value * shop.sell_markdown))


def stock_inventory(gold: int, item_ids: list[str]) -> Inventory:
    inventory = Inventory(gold=gold)
    for item_id in item_ids:
        add_item(inventory, item_id)
    return inventory


def _is_equipped(world: World, actor: EntityId, item_id: str) -> bool:
    equipment = world.equipment.get(actor)
    if equipment is None:
        return False
    return item_id in (equipment.weapon_item_id, equipment.armor_item_id)
