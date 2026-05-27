from src.core.character_creation import CharacterSheet
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
    "Barbarian": "greataxe",
    "Bard": "rapier",
    "Cleric": "mace",
    "Druid": "scimitar",
    "Fighter": "longsword",
    "Monk": "shortsword",
    "Paladin": "longsword",
    "Ranger": "shortsword",
    "Rogue": "rapier",
    "Sorcerer": "dagger",
    "Warlock": "quarterstaff",
    "Wizard": "quarterstaff",
}

HIT_DIE_BY_CLASS: dict[str, int] = {
    "Barbarian": 12,
    "Bard": 8,
    "Cleric": 8,
    "Druid": 8,
    "Fighter": 10,
    "Monk": 8,
    "Paladin": 10,
    "Ranger": 10,
    "Rogue": 8,
    "Sorcerer": 6,
    "Warlock": 8,
    "Wizard": 6,
}

STARTER_ARMOR_BY_CLASS: dict[str, str] = {
    "Barbarian": "none",
    "Bard": "leather armor",
    "Cleric": "scale mail",
    "Druid": "leather armor",
    "Fighter": "chain mail",
    "Monk": "none",
    "Paladin": "chain mail",
    "Ranger": "scale mail",
    "Rogue": "leather armor",
    "Sorcerer": "none",
    "Warlock": "leather armor",
    "Wizard": "none",
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
    return weapon_for_name(STARTER_MELEE_WEAPON_BY_CLASS[character_class])


def armor_for_name(name: str) -> Armor:
    armor = ARMOR[name]
    return Armor(
        name=armor.name,
        base_armor_class=armor.base_armor_class,
        dexterity_cap=armor.dexterity_cap,
    )


def starter_armor_for_class(character_class: str) -> Armor:
    return armor_for_name(STARTER_ARMOR_BY_CLASS[character_class])


def armor_class_for(dexterity: int, armor: Armor | None) -> int:
    dexterity_modifier = ability_modifier(dexterity)
    if armor is None:
        return 10 + dexterity_modifier
    if armor.name == "none":
        return 10 + dexterity_modifier
    if armor.dexterity_cap is None:
        return armor.base_armor_class + dexterity_modifier
    return armor.base_armor_class + min(dexterity_modifier, armor.dexterity_cap)


def combat_stats_for_sheet(sheet: CharacterSheet, armor: Armor | None = None) -> CombatStats:
    dexterity = sheet.attributes["DEX"]
    constitution = sheet.attributes["CON"]
    hit_die = HIT_DIE_BY_CLASS[sheet.character_class]
    max_hit_points = max(1, hit_die + ability_modifier(constitution))
    return CombatStats(
        armor_class=armor_class_for(dexterity, armor),
        hit_points=max_hit_points,
        max_hit_points=max_hit_points,
        strength=sheet.attributes["STR"],
        dexterity=dexterity,
        constitution=constitution,
        proficiency_bonus=2,
    )


def goblin_stats() -> CombatStats:
    return CombatStats(
        armor_class=15,
        hit_points=10,
        max_hit_points=10,
        strength=8,
        dexterity=15,
        constitution=10,
        proficiency_bonus=2,
    )
