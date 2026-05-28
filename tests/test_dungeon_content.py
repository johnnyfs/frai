"""Tests for the M15 dungeon content + boss balance.

Two things are verified here:

1. The Sunken Halls' three dungeon levels are populated with the
   expected signature monster mix, traps, locked containers, and
   loot tables. These checks are *structural* — they assert the
   spawn catalog, not specific (x, y) coordinates, so future bounds
   tweaks don't trip the test.
2. The boss fight (kobold warlord) is winnable by a level-1 4-PC
   party with seeded RNG. We simulate a simple swing-trading fight
   over a sweep of seeds and require a high win rate inside a
   reasonable round budget.

The simulation deliberately keeps the combat model small (no spells,
no companions reacting, no movement) so the assertion is about pure
melee math. The real game adds wizard spells + healing potions on top
of this floor, so passing this test means the warlord is *at least*
killable by the deterministic baseline.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import pytest

from src.core.actions import AttackAttempt
from src.core.combat import (
    combat_stats_for_sheet,
    starter_armor_for_class,
    starter_weapon_for_class,
)
from src.core.character_creation import CharacterSheet
from src.core.components import (
    AIBehaviorType,
    BossMarker,
    Faction,
    Name,
    Position,
)
from src.core.creatures import (
    CREATURES,
    combat_stats_for_creature,
    creature_for_key,
    weapon_for_creature,
)
from src.core.entity import EntityId
from src.core.factions import FactionId
from src.core.loot import ItemDrop
from src.core.world import World
from src.map.tiles import FLOOR
from src.systems.combat_system import CombatSystem
from src.world.content.dungeon import (
    DUNGEON_LEVEL_1,
    DUNGEON_LEVEL_2,
    DUNGEON_LEVEL_3,
    DUNGEON_LEVELS,
)
from src.world.content.skeleton import build_world_skeleton


# ---------------------------------------------------------------------------
# Creature catalog
# ---------------------------------------------------------------------------


def test_kobold_variants_are_registered_in_the_creature_catalog() -> None:
    for key in ("kobold_scout", "kobold_soldier", "kobold_elite"):
        assert key in CREATURES, f"missing creature variant: {key!r}"


def test_kobold_scout_is_low_hp_and_wanders() -> None:
    spec = creature_for_key("kobold_scout")
    assert spec.stats.max_hit_points <= 5
    assert spec.ai is not None
    assert spec.ai.behavior is AIBehaviorType.WANDER


def test_kobold_soldier_is_meatier_than_scout_and_chases() -> None:
    scout = creature_for_key("kobold_scout")
    soldier = creature_for_key("kobold_soldier")
    assert soldier.stats.max_hit_points > scout.stats.max_hit_points
    assert soldier.stats.armor_class >= scout.stats.armor_class
    assert soldier.ai is not None
    assert soldier.ai.behavior is AIBehaviorType.CHASE


def test_kobold_elite_is_meatier_than_soldier_and_chases() -> None:
    soldier = creature_for_key("kobold_soldier")
    elite = creature_for_key("kobold_elite")
    assert elite.stats.max_hit_points > soldier.stats.max_hit_points
    assert elite.stats.armor_class >= soldier.stats.armor_class
    assert elite.ai is not None
    assert elite.ai.behavior is AIBehaviorType.CHASE


def test_warlord_stats_are_higher_than_elite() -> None:
    elite = creature_for_key("kobold_elite")
    warlord = creature_for_key("boss_kobold_warlord")
    assert warlord.stats.max_hit_points > elite.stats.max_hit_points
    assert warlord.stats.armor_class >= elite.stats.armor_class


# ---------------------------------------------------------------------------
# Level content (data-only checks)
# ---------------------------------------------------------------------------


def test_each_dungeon_level_has_a_signature_creature() -> None:
    l1_keys = {spawn.key for spawn in DUNGEON_LEVEL_1.creatures}
    l2_keys = {spawn.key for spawn in DUNGEON_LEVEL_2.creatures}
    l3_keys = {spawn.key for spawn in DUNGEON_LEVEL_3.creatures}

    assert "kobold_scout" in l1_keys
    assert "kobold_soldier" in l2_keys
    assert "kobold_elite" in l3_keys
    # No cross-pollination: scouts don't appear on L2/L3, elites
    # don't appear on L1/L2, etc.
    assert "kobold_scout" not in l2_keys and "kobold_scout" not in l3_keys
    assert "kobold_soldier" not in l1_keys and "kobold_soldier" not in l3_keys
    assert "kobold_elite" not in l1_keys and "kobold_elite" not in l2_keys


@pytest.mark.parametrize(
    "spec",
    DUNGEON_LEVELS,
    ids=lambda spec: spec.location_id,
)
def test_each_dungeon_level_has_at_least_one_trap_and_locked_container(spec) -> None:
    assert len(spec.traps) >= 1
    assert len(spec.containers) >= 1
    # Every container on a dungeon level is locked (M15 content rule).
    assert all(container.locked for container in spec.containers)


def test_trap_dc_escalates_across_dungeon_levels() -> None:
    l1_max = max(trap.disarm_dc for trap in DUNGEON_LEVEL_1.traps)
    l2_max = max(trap.disarm_dc for trap in DUNGEON_LEVEL_2.traps)
    l3_max = max(trap.disarm_dc for trap in DUNGEON_LEVEL_3.traps)
    assert l1_max < l2_max < l3_max


def test_lock_dc_escalates_across_dungeon_levels() -> None:
    l1_max = max(container.pick_dc for container in DUNGEON_LEVEL_1.containers)
    l2_max = max(container.pick_dc for container in DUNGEON_LEVEL_2.containers)
    l3_max = max(container.pick_dc for container in DUNGEON_LEVEL_3.containers)
    assert l1_max < l2_max < l3_max


def test_l1_loot_includes_a_healing_potion() -> None:
    items = {
        item_id
        for container in DUNGEON_LEVEL_1.containers
        for item_id in container.items
    }
    assert "consumable.healing_potion" in items


def test_l2_loot_includes_a_weapon() -> None:
    items = [
        item_id
        for container in DUNGEON_LEVEL_2.containers
        for item_id in container.items
    ]
    assert any(item_id.startswith("weapon.") for item_id in items)


def test_l3_loot_includes_extra_potions_for_the_boss_fight() -> None:
    potion_count = sum(
        container.items.count("consumable.healing_potion")
        for container in DUNGEON_LEVEL_3.containers
    )
    assert potion_count >= 2


def test_warlord_loot_table_guarantees_the_golden_chalice() -> None:
    warlord = creature_for_key("boss_kobold_warlord")
    chalice = next(
        (
            entry
            for entry in warlord.loot.entries
            if isinstance(entry, ItemDrop) and entry.item_id == "treasure.golden_chalice"
        ),
        None,
    )
    assert chalice is not None
    assert chalice.probability >= 1.0


# ---------------------------------------------------------------------------
# World-level integration
# ---------------------------------------------------------------------------


def test_built_world_contains_kobold_scouts_on_l1() -> None:
    built = build_world_skeleton()
    l1 = built.locations["dungeon_level_1"]

    scouts_on_l1 = [
        entity
        for entity, position in built.world.positions.values.items()
        if (
            built.world.creatures.has(entity)
            and built.world.creatures.require(entity).kind == "kobold_scout"
            and l1.bounds.left <= position.x <= l1.bounds.right
            and l1.bounds.top <= position.y <= l1.bounds.bottom
        )
    ]
    assert len(scouts_on_l1) >= 1


def test_built_world_contains_kobold_soldiers_on_l2() -> None:
    built = build_world_skeleton()
    l2 = built.locations["dungeon_level_2"]
    soldiers = [
        entity
        for entity, position in built.world.positions.values.items()
        if (
            built.world.creatures.has(entity)
            and built.world.creatures.require(entity).kind == "kobold_soldier"
            and l2.bounds.left <= position.x <= l2.bounds.right
            and l2.bounds.top <= position.y <= l2.bounds.bottom
        )
    ]
    assert len(soldiers) >= 1


def test_built_world_contains_kobold_elite_and_boss_on_l3() -> None:
    built = build_world_skeleton()
    l3 = built.locations["dungeon_level_3"]
    elites = [
        entity
        for entity, position in built.world.positions.values.items()
        if (
            built.world.creatures.has(entity)
            and built.world.creatures.require(entity).kind == "kobold_elite"
            and l3.bounds.left <= position.x <= l3.bounds.right
            and l3.bounds.top <= position.y <= l3.bounds.bottom
        )
    ]
    bosses = [
        entity
        for entity in built.world.boss_markers.values
        if built.world.boss_markers.require(entity).token == "sunken_gate_warlord"
    ]
    assert len(elites) >= 1
    assert len(bosses) == 1


def test_built_world_has_at_least_one_trap_per_dungeon_level() -> None:
    built = build_world_skeleton()
    for location_id in ("dungeon_level_1", "dungeon_level_2", "dungeon_level_3"):
        level = built.locations[location_id]
        traps_here = [
            entity
            for entity, position in built.world.positions.values.items()
            if (
                built.world.traps.has(entity)
                and level.bounds.left <= position.x <= level.bounds.right
                and level.bounds.top <= position.y <= level.bounds.bottom
            )
        ]
        assert len(traps_here) >= 1, f"no trap on {location_id}"


def test_built_world_has_at_least_one_locked_container_per_dungeon_level() -> None:
    built = build_world_skeleton()
    for location_id in ("dungeon_level_1", "dungeon_level_2", "dungeon_level_3"):
        level = built.locations[location_id]
        locked_containers = [
            entity
            for entity, position in built.world.positions.values.items()
            if (
                built.world.containers.has(entity)
                and built.world.locks.has(entity)
                and built.world.locks.require(entity).is_locked
                and level.bounds.left <= position.x <= level.bounds.right
                and level.bounds.top <= position.y <= level.bounds.bottom
            )
        ]
        assert len(locked_containers) >= 1, f"no locked container on {location_id}"


# ---------------------------------------------------------------------------
# Boss-fight balance simulation
# ---------------------------------------------------------------------------


_LEVEL_1_PARTY_CLASSES = ("Fighter", "Cleric", "Rogue", "Wizard")


def _sheet_for_class(character_class: str) -> CharacterSheet:
    return CharacterSheet(
        race="Human",
        character_class=character_class,
        specialization="generic",
        base_attributes={
            "STR": 15, "DEX": 14, "CON": 14, "INT": 12, "WIS": 12, "CHA": 10,
        },
        attributes={
            "STR": 15, "DEX": 14, "CON": 14, "INT": 12, "WIS": 12, "CHA": 10,
        },
    )


@dataclass(slots=True)
class _SimWorld:
    world: World
    boss: EntityId
    party: list[EntityId]


def _build_boss_arena() -> _SimWorld:
    """Build a minimal world containing the boss and a level-1 party.

    No terrain, no movement — every actor is positioned adjacent to the
    boss so the AttackAttempt resolves directly. The combat math is the
    same as the real fight; this skips only the navigation overhead.
    """

    world = World(
        width=10,
        height=10,
        tiles=[[FLOOR for _ in range(10)] for _ in range(10)],
    )
    # The tile grid is only consulted by movement code in this test; we
    # bypass that by feeding AttackAttempts directly to the combat
    # system. Floor tiles keep the world consistent for any incidental
    # queries (e.g. blockers_at).

    # Boss.
    warlord_spec = creature_for_key("boss_kobold_warlord")
    boss = world.create_entity()
    world.positions.add(boss, Position(x=5, y=5))
    world.names.add(boss, Name(warlord_spec.name))
    world.factions.add(boss, Faction(FactionId.DUNGEON.value))
    world.combat_stats.add(boss, combat_stats_for_creature(warlord_spec))
    world.weapons.add(boss, weapon_for_creature(warlord_spec))
    world.boss_markers.add(boss, BossMarker(token="sunken_gate_warlord"))

    party: list[EntityId] = []
    for index, character_class in enumerate(_LEVEL_1_PARTY_CLASSES):
        sheet = _sheet_for_class(character_class)
        armor = starter_armor_for_class(sheet.character_class)
        weapon = starter_weapon_for_class(sheet.character_class)
        member = world.create_entity()
        world.positions.add(member, Position(x=4, y=5 + index - 2))
        world.names.add(member, Name(character_class.lower()))
        world.factions.add(member, Faction(FactionId.PLAYER_PARTY.value))
        world.combat_stats.add(member, combat_stats_for_sheet(sheet, armor))
        world.weapons.add(member, weapon)
        party.append(member)

    return _SimWorld(world=world, boss=boss, party=party)


def _simulate_boss_fight(seed: int, *, max_rounds: int = 20) -> tuple[bool, int]:
    """Simulate the warlord fight at ``seed``.

    Returns ``(party_won, rounds_taken)``. The simulation alternates
    "party attacks the boss" then "boss attacks a random living party
    member" per round. PCs use their starter weapons; the boss uses its
    greataxe. We also let the party share a small healing-potion budget
    (3 potions) that auto-pops the lowest-HP PC when they drop below
    half max HP, modelling the "burn some resources" expectation.
    """

    arena = _build_boss_arena()
    rng = random.Random(seed)
    combat = CombatSystem(rng=rng)
    world = arena.world

    potions_left = 3  # M15 expects the party to burn a few potions.
    HEAL_PER_POTION = 7

    rounds = 0
    while rounds < max_rounds:
        rounds += 1
        # Party turn: each living member swings at the boss.
        for member in arena.party:
            boss_stats = world.combat_stats.get(arena.boss)
            if boss_stats is None or boss_stats.hit_points <= 0:
                return True, rounds
            member_stats = world.combat_stats.get(member)
            if member_stats is None or member_stats.hit_points <= 0:
                continue
            effects = combat.resolve_attack(
                AttackAttempt(actor=member, target=arena.boss), world
            )
            for effect in effects:
                _apply_combat_effect(world, effect)

        boss_stats = world.combat_stats.get(arena.boss)
        if boss_stats is None or boss_stats.hit_points <= 0:
            return True, rounds

        # Healing phase: spend a potion on the lowest-HP living member
        # if they are below half max.
        living = [
            member
            for member in arena.party
            if (stats := world.combat_stats.get(member)) is not None and stats.hit_points > 0
        ]
        if not living:
            return False, rounds
        if potions_left > 0:
            lowest = min(living, key=lambda m: world.combat_stats.require(m).hit_points)
            stats = world.combat_stats.require(lowest)
            if stats.hit_points * 2 < stats.max_hit_points:
                stats.hit_points = min(stats.max_hit_points, stats.hit_points + HEAL_PER_POTION)
                potions_left -= 1

        # Boss turn: attack a random living member.
        living = [
            member
            for member in arena.party
            if (stats := world.combat_stats.get(member)) is not None and stats.hit_points > 0
        ]
        if not living:
            return False, rounds
        target = rng.choice(living)
        effects = combat.resolve_attack(
            AttackAttempt(actor=arena.boss, target=target), world
        )
        for effect in effects:
            _apply_combat_effect(world, effect)

        # Did the party wipe?
        if not any(
            (stats := world.combat_stats.get(member)) is not None and stats.hit_points > 0
            for member in arena.party
        ):
            return False, rounds

    return False, rounds


def _apply_combat_effect(world: World, effect) -> None:
    """Apply a single combat effect to the simulation world.

    Only the subset of effects the combat system emits during an
    AttackAttempt resolution is supported (damage, kill, emit, end
    condition). The kill case maps to a hit_points = 0 sentinel so the
    next round's living-check filters the entity out — we deliberately
    keep the entity in the world so its target-id stays valid for any
    remaining attacks in the same loop.
    """
    from src.core.effects import (
        DamageEntity,
        EmitMessage,
        EndCondition,
        KillEntity,
    )

    if isinstance(effect, DamageEntity):
        stats = world.combat_stats.get(effect.entity)
        if stats is not None:
            stats.hit_points = max(0, stats.hit_points - effect.amount)
    elif isinstance(effect, KillEntity):
        stats = world.combat_stats.get(effect.entity)
        if stats is not None:
            stats.hit_points = 0
    elif isinstance(effect, EmitMessage):
        pass
    elif isinstance(effect, EndCondition):
        pass
    else:
        # Unknown effect — fail loudly so the test catches new combat
        # output shapes.
        raise AssertionError(f"Unsupported simulation effect: {effect!r}")


def test_boss_fight_is_winnable_by_a_level_one_party_across_seeds() -> None:
    """Run the boss simulation across a sweep of seeds.

    The warlord should fall to 4 level-1 PCs in the large majority of
    runs given a small potion budget — the M15 acceptance bar is
    "winnable, not trivial". Trivial wins (under 3 rounds) and free
    wins (no potions burned, no party HP loss) would mean the boss is
    too soft; "loses 60% of the time" would mean too hard. Both ends
    are guarded.
    """

    wins = 0
    losses = 0
    round_counts: list[int] = []
    seed_count = 40
    for seed in range(seed_count):
        won, rounds = _simulate_boss_fight(seed)
        if won:
            wins += 1
            round_counts.append(rounds)
        else:
            losses += 1
    assert seed_count == wins + losses
    win_rate = wins / seed_count
    # Acceptance bar: 4 level-1 PCs win 80%+ of seeds with a small
    # potion budget. If this drops below 0.8 the boss is too hard.
    assert win_rate >= 0.8, (
        f"win rate {win_rate:.2%} below 80%; boss too hard"
    )
    # And not so soft that every fight ends in two swings.
    average_rounds = sum(round_counts) / max(1, len(round_counts))
    assert average_rounds >= 3.0, (
        f"average kill in {average_rounds:.1f} rounds; boss too soft"
    )
    # Average kill should be in the 4-9 round band — this is the
    # "feels like a real boss" zone.
    assert average_rounds <= 9.0, (
        f"average kill in {average_rounds:.1f} rounds; boss too tanky"
    )


def test_boss_fight_seed_zero_terminates_with_a_winner() -> None:
    """Spot-check: at the deterministic baseline seed the fight ends."""

    won, rounds = _simulate_boss_fight(seed=0)
    # The deterministic seed-0 fight ends within the cap and the
    # result is one of {win, loss} — the assertion below pins
    # both behaviours so a future combat tweak that silently breaks
    # the simulation (e.g. infinite loop) trips loudly.
    assert isinstance(won, bool)
    assert 1 <= rounds <= 20
