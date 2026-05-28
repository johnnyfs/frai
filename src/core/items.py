from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from src.core.components import Inventory, InventoryStack


class ItemKind(Enum):
    WEAPON = "weapon"
    ARMOR = "armor"
    CONSUMABLE = "consumable"
    MISC = "misc"


class EquipmentSlot(Enum):
    WEAPON = "weapon"
    ARMOR = "armor"


@dataclass(frozen=True, slots=True)
class ItemDefinition:
    item_id: str
    name: str
    kind: ItemKind
    value: int
    slot: EquipmentSlot | None = None
    weapon_name: str | None = None
    armor_name: str | None = None
    heal_amount: int = 0
    max_stack: int = 1


ITEMS: Mapping[str, ItemDefinition] = MappingProxyType(
    {
        "weapon.club": ItemDefinition(
            item_id="weapon.club",
            name="club",
            kind=ItemKind.WEAPON,
            value=1,
            slot=EquipmentSlot.WEAPON,
            weapon_name="club",
        ),
        "weapon.dagger": ItemDefinition(
            item_id="weapon.dagger",
            name="dagger",
            kind=ItemKind.WEAPON,
            value=2,
            slot=EquipmentSlot.WEAPON,
            weapon_name="dagger",
        ),
        "weapon.longsword": ItemDefinition(
            item_id="weapon.longsword",
            name="longsword",
            kind=ItemKind.WEAPON,
            value=15,
            slot=EquipmentSlot.WEAPON,
            weapon_name="longsword",
        ),
        "weapon.mace": ItemDefinition(
            item_id="weapon.mace",
            name="mace",
            kind=ItemKind.WEAPON,
            value=5,
            slot=EquipmentSlot.WEAPON,
            weapon_name="mace",
        ),
        "weapon.quarterstaff": ItemDefinition(
            item_id="weapon.quarterstaff",
            name="quarterstaff",
            kind=ItemKind.WEAPON,
            value=2,
            slot=EquipmentSlot.WEAPON,
            weapon_name="quarterstaff",
        ),
        "weapon.greataxe": ItemDefinition(
            item_id="weapon.greataxe",
            name="greataxe",
            kind=ItemKind.WEAPON,
            value=30,
            slot=EquipmentSlot.WEAPON,
            weapon_name="greataxe",
        ),
        "weapon.rapier": ItemDefinition(
            item_id="weapon.rapier",
            name="rapier",
            kind=ItemKind.WEAPON,
            value=25,
            slot=EquipmentSlot.WEAPON,
            weapon_name="rapier",
        ),
        "weapon.scimitar": ItemDefinition(
            item_id="weapon.scimitar",
            name="scimitar",
            kind=ItemKind.WEAPON,
            value=25,
            slot=EquipmentSlot.WEAPON,
            weapon_name="scimitar",
        ),
        "weapon.shortsword": ItemDefinition(
            item_id="weapon.shortsword",
            name="shortsword",
            kind=ItemKind.WEAPON,
            value=10,
            slot=EquipmentSlot.WEAPON,
            weapon_name="shortsword",
        ),
        "armor.leather": ItemDefinition(
            item_id="armor.leather",
            name="leather armor",
            kind=ItemKind.ARMOR,
            value=10,
            slot=EquipmentSlot.ARMOR,
            armor_name="leather armor",
        ),
        "armor.scale_mail": ItemDefinition(
            item_id="armor.scale_mail",
            name="scale mail",
            kind=ItemKind.ARMOR,
            value=50,
            slot=EquipmentSlot.ARMOR,
            armor_name="scale mail",
        ),
        "armor.chain_mail": ItemDefinition(
            item_id="armor.chain_mail",
            name="chain mail",
            kind=ItemKind.ARMOR,
            value=75,
            slot=EquipmentSlot.ARMOR,
            armor_name="chain mail",
        ),
        "consumable.healing_potion": ItemDefinition(
            item_id="consumable.healing_potion",
            name="healing potion",
            kind=ItemKind.CONSUMABLE,
            value=50,
            heal_amount=7,
            max_stack=10,
        ),
    }
)

WEAPON_ITEM_BY_NAME: Mapping[str, str] = MappingProxyType(
    {
        item.weapon_name: item.item_id
        for item in ITEMS.values()
        if item.weapon_name is not None
    }
)

ARMOR_ITEM_BY_NAME: Mapping[str, str | None] = MappingProxyType(
    {
        "none": None,
        **{
            item.armor_name: item.item_id
            for item in ITEMS.values()
            if item.armor_name is not None
        },
    }
)


def require_item(item_id: str) -> ItemDefinition:
    try:
        return ITEMS[item_id]
    except KeyError as exc:
        raise KeyError(f"Unknown item id: {item_id}") from exc


def weapon_item_id_for_name(name: str) -> str:
    try:
        return WEAPON_ITEM_BY_NAME[name]
    except KeyError as exc:
        raise KeyError(f"No item definition for weapon: {name}") from exc


def armor_item_id_for_name(name: str) -> str | None:
    try:
        return ARMOR_ITEM_BY_NAME[name]
    except KeyError as exc:
        raise KeyError(f"No item definition for armor: {name}") from exc


def item_count(inventory: Inventory, item_id: str) -> int:
    return sum(stack.quantity for stack in inventory.items if stack.item_id == item_id)


def has_item(inventory: Inventory, item_id: str, quantity: int = 1) -> bool:
    return quantity > 0 and item_count(inventory, item_id) >= quantity


def add_item(inventory: Inventory, item_id: str, quantity: int = 1) -> None:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    require_item(item_id)
    for stack in inventory.items:
        if stack.item_id == item_id:
            stack.quantity += quantity
            return
    inventory.items.append(InventoryStack(item_id=item_id, quantity=quantity))


def remove_item(inventory: Inventory, item_id: str, quantity: int = 1) -> bool:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if not has_item(inventory, item_id, quantity):
        return False
    remaining = quantity
    for stack in list(inventory.items):
        if stack.item_id != item_id:
            continue
        removed = min(stack.quantity, remaining)
        stack.quantity -= removed
        remaining -= removed
        if stack.quantity == 0:
            inventory.items.remove(stack)
        if remaining == 0:
            return True
    return False
