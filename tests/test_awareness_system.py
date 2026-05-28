from src.systems.awareness_system import (
    hostiles_requiring_battle,
    is_aware_of,
    is_hostile_to,
)
from tests.support.tiny_world import (
    add_actor,
    add_enemy,
    add_party_member,
    build_tiny_map,
    build_tiny_party_world,
)


def test_hostiles_requiring_battle_empty_world_returns_empty_list() -> None:
    world = build_tiny_map()

    assert hostiles_requiring_battle(world, party=[]) == []


def test_hostiles_requiring_battle_party_only_world_returns_empty_list() -> None:
    fixture = build_tiny_party_world()

    assert hostiles_requiring_battle(fixture.world, fixture.party) == []


def test_hostiles_requiring_battle_returns_hostile_entity() -> None:
    fixture = build_tiny_party_world()
    enemy = add_enemy(fixture.world, 4, 2)

    assert hostiles_requiring_battle(fixture.world, fixture.party) == [enemy]


def test_hostiles_requiring_battle_excludes_party_members() -> None:
    fixture = build_tiny_party_world()
    enemy = add_enemy(fixture.world, 4, 2)

    hostiles = hostiles_requiring_battle(fixture.world, fixture.party)

    assert fixture.player not in hostiles
    assert fixture.companion not in hostiles
    assert hostiles == [enemy]


def test_hostiles_requiring_battle_ignores_dead_hostiles() -> None:
    fixture = build_tiny_party_world()
    enemy = add_enemy(fixture.world, 4, 2, hit_points=1)
    fixture.world.combat_stats.require(enemy).hit_points = 0

    assert hostiles_requiring_battle(fixture.world, fixture.party) == []


def test_hostiles_requiring_battle_treats_shared_faction_as_non_hostile() -> None:
    """A town NPC sharing a faction with the party is not hostile."""
    fixture = build_tiny_party_world()
    # Add a "townsfolk" NPC and a party member that also belongs to "townsfolk".
    townsfolk = add_party_member(fixture.world, 3, 3, name="guard", glyph="t")
    fixture.world.factions.require(townsfolk).value = "townsfolk"
    npc = add_enemy(fixture.world, 4, 2, name="baker", glyph="b")
    fixture.world.factions.require(npc).value = "townsfolk"

    party = fixture.party + [townsfolk]

    assert hostiles_requiring_battle(fixture.world, party) == []


def test_is_hostile_to_respects_faction_difference() -> None:
    fixture = build_tiny_party_world()
    enemy = add_enemy(fixture.world, 4, 2)

    assert is_hostile_to(fixture.world, fixture.player, enemy) is True
    assert is_hostile_to(fixture.world, fixture.player, fixture.companion) is False
    assert is_hostile_to(fixture.world, fixture.player, fixture.player) is False


def test_is_hostile_to_requires_combat_stats() -> None:
    world = build_tiny_map()
    player = add_actor(world, 2, 2)
    # A factioned entity without combat stats (e.g. a door) is not hostile.
    bystander = world.create_entity()
    from src.core.components import Faction, Position

    world.positions.add(bystander, Position(x=4, y=2))
    world.factions.add(bystander, Faction("enemy"))

    assert is_hostile_to(world, player, bystander) is False


def test_is_aware_of_reports_live_positioned_target() -> None:
    fixture = build_tiny_party_world()
    enemy = add_enemy(fixture.world, 4, 2)

    assert is_aware_of(fixture.world, fixture.player, enemy) is True


def test_is_aware_of_skips_dead_target() -> None:
    fixture = build_tiny_party_world()
    enemy = add_enemy(fixture.world, 4, 2, hit_points=1)
    fixture.world.combat_stats.require(enemy).hit_points = 0

    assert is_aware_of(fixture.world, fixture.player, enemy) is False


def test_is_aware_of_returns_false_for_self() -> None:
    fixture = build_tiny_party_world()

    assert is_aware_of(fixture.world, fixture.player, fixture.player) is False
