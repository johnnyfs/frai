from src.core.character_creation import CLASSES, CharacterSheet, require_class
from src.core.components import Armor, CombatStats, Weapon


def ability_modifier(score: int) -> int:
    return (score - 10) // 2


WEAPONS: dict[str, Weapon] = {
    "club": Weapon("club", 4, "bludgeoning"),
    "dagger": Weapon("dagger", 4, "piercing", ability="DEX", finesse=True),
    "greataxe": Weapon("greataxe", 12, "slashing"),
    "longsword": Weapon("longsword", 8, "slashing"),
    "mace": Weapon("mace", 6, "bludgeoning"),
    "quarterstaff": Weapon("quarterstaff", 6, "bludgeoning"),
    "rapier": Weapon("rapier", 8, "piercing", ability="DEX", finesse=True),
    "scimitar": Weapon("scimitar", 6, "slashing", ability="DEX", finesse=True),
    "shortsword": Weapon("shortsword", 6, "piercing", ability="DEX", finesse=True),
}

ARMOR: dict[str, Armor] = {
    "none": Armor("none", 10),
    "leather armor": Armor("leather armor", 11),
    "scale mail": Armor("scale mail", 14, dexterity_cap=2),
    "chain mail": Armor("chain mail", 16, dexterity_cap=0),
}

STARTER_MELEE_WEAPON_BY_CLASS: dict[str, str] = {
    character_class.name: character_class.starting_equipment.weapon
    for character_class in CLASSES
}

HIT_DIE_BY_CLASS: dict[str, int] = {
    character_class.name: character_class.hit_die for character_class in CLASSES
}

STARTER_ARMOR_BY_CLASS: dict[str, str] = {
    character_class.name: character_class.starting_equipment.armor
    for character_class in CLASSES
}


def weapon_for_name(name: str) -> Weapon:
    weapon = WEAPONS[name]
    return Weapon(
        name=weapon.name,
        damage_die=weapon.damage_die,
        damage_type=weapon.damage_type,
        ability=weapon.ability,
        finesse=weapon.finesse,
    )


def starter_weapon_for_class(character_class: str) -> Weapon:
    return weapon_for_name(require_class(character_class).starting_equipment.weapon)


def armor_for_name(name: str) -> Armor:
    armor = ARMOR[name]
    return Armor(
        name=armor.name,
        base_armor_class=armor.base_armor_class,
        dexterity_cap=armor.dexterity_cap,
    )


def starter_armor_for_class(character_class: str) -> Armor:
    return armor_for_name(require_class(character_class).starting_equipment.armor)


def armor_class_for(dexterity: int, armor: Armor | None) -> int:
    dexterity_modifier = ability_modifier(dexterity)
    if armor is None:
        return 10 + dexterity_modifier
    if armor.name == "none":
        return 10 + dexterity_modifier
    if armor.dexterity_cap is None:
        return armor.base_armor_class + dexterity_modifier
    if armor.dexterity_cap == 0:
        return armor.base_armor_class
    return armor.base_armor_class + min(dexterity_modifier, armor.dexterity_cap)


def proficiency_bonus_for_level(level: int) -> int:
    return 2 + max(0, level - 1) // 4


def combat_stats_for_sheet(sheet: CharacterSheet, armor: Armor | None = None) -> CombatStats:
    dexterity = sheet.attributes["DEX"]
    constitution = sheet.attributes["CON"]
    hit_die = require_class(sheet.character_class).hit_die
    max_hit_points = max(1, hit_die + ability_modifier(constitution))
    return CombatStats(
        armor_class=armor_class_for(dexterity, armor),
        hit_points=max_hit_points,
        max_hit_points=max_hit_points,
        strength=sheet.attributes["STR"],
        dexterity=dexterity,
        constitution=constitution,
        proficiency_bonus=proficiency_bonus_for_level(sheet.level),
    )
