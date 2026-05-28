from dataclasses import dataclass, field

from src.core.components import AI, AIBehaviorType, CombatStats, Creature, Weapon
from src.core.loot import DropTable, GoldDrop, ItemDrop


@dataclass(frozen=True, slots=True)
class CreatureSpec:
    key: str
    name: str
    glyph: str
    attack_verb: str
    stats: CombatStats
    weapon: Weapon
    loot: DropTable = field(default_factory=DropTable)
    ai: AI | None = None


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
    # M15 dungeon level 1 signature monster. Low HP and modest damage:
    # a level-1 party should clear a small group without spending
    # resources. The WANDER behaviour gives them a "rats-in-a-room"
    # feel — they shuffle around tiles rather than beelining the
    # player, so noise + line-of-sight matters.
    "kobold_scout": CreatureSpec(
        key="kobold_scout",
        name="kobold scout",
        glyph="k",
        attack_verb="stabs",
        stats=CombatStats(
            armor_class=12,
            hit_points=4,
            max_hit_points=4,
            strength=8,
            dexterity=15,
            constitution=9,
            proficiency_bonus=2,
        ),
        weapon=Weapon("dagger", 4, "piercing", ability="DEX", finesse=True),
        loot=DropTable(
            entries=(
                GoldDrop(amount_min=1, amount_max=3),
            )
        ),
        ai=AI(behavior=AIBehaviorType.WANDER, attack_range=1, preferred_range=1),
    ),
    # M15 dungeon level 2 signature monster. Tougher than the scout
    # and uses a real weapon (shortsword) so PCs feel the step up.
    # Standard CHASE behaviour: they hunt the party.
    "kobold_soldier": CreatureSpec(
        key="kobold_soldier",
        name="kobold soldier",
        glyph="k",
        attack_verb="slashes",
        stats=CombatStats(
            armor_class=13,
            hit_points=9,
            max_hit_points=9,
            strength=11,
            dexterity=14,
            constitution=11,
            proficiency_bonus=2,
        ),
        weapon=Weapon("shortsword", 6, "piercing", ability="DEX", finesse=True),
        loot=DropTable(
            entries=(
                GoldDrop(amount_min=2, amount_max=6),
                ItemDrop(
                    item_id="weapon.shortsword",
                    probability=0.25,
                    quantity_min=1,
                    quantity_max=1,
                ),
            )
        ),
        ai=AI(behavior=AIBehaviorType.CHASE, attack_range=1, preferred_range=1),
    ),
    # M15 dungeon level 3 signature monster (boss escort). Heavier
    # mace damage and a meatier HP pool so the warlord's room isn't
    # a free trip. Still survivable by a level-1 party — these are
    # tuned so one elite + the boss together don't auto-down a PC.
    "kobold_elite": CreatureSpec(
        key="kobold_elite",
        name="kobold elite",
        glyph="k",
        attack_verb="bashes",
        stats=CombatStats(
            armor_class=14,
            hit_points=14,
            max_hit_points=14,
            strength=13,
            dexterity=13,
            constitution=12,
            proficiency_bonus=2,
        ),
        weapon=Weapon("mace", 6, "bludgeoning", ability="STR"),
        loot=DropTable(
            entries=(
                GoldDrop(amount_min=4, amount_max=10),
                ItemDrop(
                    item_id="weapon.mace",
                    probability=0.5,
                    quantity_min=1,
                    quantity_max=1,
                ),
            )
        ),
        ai=AI(behavior=AIBehaviorType.CHASE, attack_range=1, preferred_range=1),
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
        loot=DropTable(
            entries=(
                GoldDrop(amount_min=1, amount_max=5),
                ItemDrop(
                    item_id="weapon.dagger",
                    probability=0.5,
                    quantity_min=1,
                    quantity_max=1,
                ),
            )
        ),
    ),
    # M14 quest boss. Sized for a level-1 party of four with starter
    # gear: HP and AC high enough to be a real fight, weapon strong
    # enough to threaten downs, drop table guaranteed to give the
    # golden chalice (the quest treasure) plus a stash of gold for
    # flavor. Spawned in the entry room of dungeon level 3 by the
    # world skeleton.
    "boss_kobold_warlord": CreatureSpec(
        key="boss_kobold_warlord",
        name="kobold warlord",
        glyph="K",
        attack_verb="cleaves",
        stats=CombatStats(
            armor_class=16,
            hit_points=40,
            max_hit_points=40,
            strength=15,
            dexterity=13,
            constitution=14,
            proficiency_bonus=3,
        ),
        weapon=Weapon("greataxe", 12, "slashing", ability="STR"),
        loot=DropTable(
            entries=(
                GoldDrop(amount_min=40, amount_max=80),
                ItemDrop(
                    item_id="treasure.golden_chalice",
                    probability=1.0,
                    quantity_min=1,
                    quantity_max=1,
                ),
            )
        ),
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
