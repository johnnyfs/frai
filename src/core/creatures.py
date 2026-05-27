from dataclasses import dataclass

from src.core.components import CombatStats, Creature, Weapon


@dataclass(frozen=True, slots=True)
class CreatureSpec:
    key: str
    name: str
    glyph: str
    attack_verb: str
    stats: CombatStats
    weapon: Weapon


CREATURES: dict[str, CreatureSpec] = {
    "frog": CreatureSpec(
        key="frog",
        name="frog",
        glyph=":",
        attack_verb="bites",
        stats=CombatStats(
            armor_class=11,
            hit_points=3,
            max_hit_points=3,
            strength=3,
            dexterity=13,
            constitution=8,
            proficiency_bonus=2,
        ),
        weapon=Weapon("bite", 2, "piercing", ability="DEX"),
    ),
    "rat": CreatureSpec(
        key="rat",
        name="rat",
        glyph="r",
        attack_verb="bites",
        stats=CombatStats(
            armor_class=10,
            hit_points=2,
            max_hit_points=2,
            strength=2,
            dexterity=11,
            constitution=9,
            proficiency_bonus=2,
        ),
        weapon=Weapon("bite", 2, "piercing", ability="DEX"),
    ),
    "bat": CreatureSpec(
        key="bat",
        name="bat",
        glyph="B",
        attack_verb="bites",
        stats=CombatStats(
            armor_class=12,
            hit_points=1,
            max_hit_points=1,
            strength=2,
            dexterity=15,
            constitution=8,
            proficiency_bonus=2,
        ),
        weapon=Weapon("bite", 2, "piercing", ability="DEX"),
    ),
    "goblin": CreatureSpec(
        key="goblin",
        name="goblin",
        glyph="o",
        attack_verb="stabs",
        stats=CombatStats(
            armor_class=15,
            hit_points=10,
            max_hit_points=10,
            strength=8,
            dexterity=15,
            constitution=10,
            proficiency_bonus=2,
        ),
        weapon=Weapon("dagger", 4, "piercing", ability="DEX", finesse=True),
    ),
}


def creature_for_key(key: str) -> CreatureSpec:
    return CREATURES[key]


def creature_component(spec: CreatureSpec) -> Creature:
    return Creature(kind=spec.key, attack_verb=spec.attack_verb)


def combat_stats_for_creature(spec: CreatureSpec) -> CombatStats:
    stats = spec.stats
    return CombatStats(
        armor_class=stats.armor_class,
        hit_points=stats.hit_points,
        max_hit_points=stats.max_hit_points,
        strength=stats.strength,
        dexterity=stats.dexterity,
        constitution=stats.constitution,
        proficiency_bonus=stats.proficiency_bonus,
    )


def weapon_for_creature(spec: CreatureSpec) -> Weapon:
    weapon = spec.weapon
    return Weapon(
        name=weapon.name,
        damage_die=weapon.damage_die,
        damage_type=weapon.damage_type,
        ability=weapon.ability,
        finesse=weapon.finesse,
    )
