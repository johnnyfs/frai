"""Tests for the M28 faction / relation model."""

from src.core.components import Faction, Position
from src.core.factions import (
    AggroOverride,
    AggroOverrideList,
    DEFAULT_RELATION_TABLE,
    FactionId,
    Relation,
    default_relation_table,
)
from src.systems.awareness_system import (
    hostiles_requiring_battle,
    is_hostile_to,
)
from tests.support.tiny_world import (
    add_enemy,
    add_party_member,
    build_tiny_party_world,
)


# ---------------------------------------------------------------------------
# RelationTable defaults
# ---------------------------------------------------------------------------


def test_default_relation_table_matches_spec() -> None:
    table = default_relation_table()
    expected = [
        (FactionId.PLAYER_PARTY, FactionId.PLAYER_PARTY, Relation.FRIENDLY),
        (FactionId.PLAYER_PARTY, FactionId.TOWN, Relation.NEUTRAL),
        (FactionId.PLAYER_PARTY, FactionId.DUNGEON, Relation.HOSTILE),
        (FactionId.PLAYER_PARTY, FactionId.WILDLIFE, Relation.NEUTRAL),
        (FactionId.TOWN, FactionId.DUNGEON, Relation.HOSTILE),
        (FactionId.TOWN, FactionId.WILDLIFE, Relation.NEUTRAL),
        (FactionId.DUNGEON, FactionId.WILDLIFE, Relation.NEUTRAL),
    ]
    for a, b, relation in expected:
        assert table.relation(a, b) is relation
        # Symmetry: the table is order-independent.
        assert table.relation(b, a) is relation


def test_relation_table_symmetric_by_default() -> None:
    table = default_relation_table()
    assert table.relation(
        FactionId.PLAYER_PARTY, FactionId.DUNGEON
    ) is table.relation(FactionId.DUNGEON, FactionId.PLAYER_PARTY)


def test_relation_table_self_is_friendly_by_default() -> None:
    table = default_relation_table()
    # Faction with no explicit entry against itself still resolves as
    # friendly so single-faction packs don't fight each other.
    assert table.relation(FactionId.UNKNOWN, FactionId.UNKNOWN) is Relation.FRIENDLY


def test_relation_table_default_for_unconfigured_pair_is_neutral() -> None:
    table = default_relation_table()
    # WILDLIFE ↔ WILDLIFE is explicitly neutral per the spec docstring;
    # this asserts the published default rather than the table internals.
    assert table.relation(FactionId.WILDLIFE, FactionId.WILDLIFE) is Relation.NEUTRAL


# ---------------------------------------------------------------------------
# Town NPCs are not hostile
# ---------------------------------------------------------------------------


def test_town_npc_does_not_trigger_turn_based_mode() -> None:
    fixture = build_tiny_party_world()
    baker = add_enemy(fixture.world, 4, 2, name="baker", glyph="b")
    fixture.world.factions.require(baker).value = FactionId.TOWN.value

    assert hostiles_requiring_battle(fixture.world, fixture.party) == []
    assert is_hostile_to(fixture.world, fixture.player, baker) is False


def test_dungeon_monster_triggers_turn_based_mode() -> None:
    fixture = build_tiny_party_world()
    goblin = add_enemy(fixture.world, 4, 2, name="goblin", glyph="g")
    fixture.world.factions.require(goblin).value = FactionId.DUNGEON.value

    assert hostiles_requiring_battle(fixture.world, fixture.party) == [goblin]
    assert is_hostile_to(fixture.world, fixture.player, goblin) is True


def test_wildlife_neutral_until_aggro() -> None:
    fixture = build_tiny_party_world()
    deer = add_enemy(fixture.world, 4, 2, name="deer", glyph="d")
    fixture.world.factions.require(deer).value = FactionId.WILDLIFE.value

    assert hostiles_requiring_battle(fixture.world, fixture.party) == []


# ---------------------------------------------------------------------------
# AggroOverride
# ---------------------------------------------------------------------------


def test_aggroed_shopkeeper_triggers_combat() -> None:
    """An aggro'd town NPC flips that NPC to hostile against the party."""
    fixture = build_tiny_party_world()
    shopkeeper = add_enemy(fixture.world, 4, 2, name="shopkeeper", glyph="s")
    fixture.world.factions.require(shopkeeper).value = FactionId.TOWN.value
    # Before override: neutral, no combat.
    assert hostiles_requiring_battle(fixture.world, fixture.party) == []
    # Apply the override (e.g. after a theft).
    overrides = AggroOverrideList(
        overrides=[AggroOverride(FactionId.PLAYER_PARTY, Relation.HOSTILE)]
    )
    fixture.world.aggro_overrides.add(shopkeeper, overrides)
    assert hostiles_requiring_battle(fixture.world, fixture.party) == [shopkeeper]
    # The override is directional: the shopkeeper sees the party as
    # hostile and will attack on its turn.
    assert is_hostile_to(fixture.world, shopkeeper, fixture.player) is True


def test_aggro_override_does_not_flip_whole_faction() -> None:
    """Other town NPCs remain neutral when one shopkeeper aggros."""
    fixture = build_tiny_party_world()
    shopkeeper = add_enemy(fixture.world, 4, 2, name="shopkeeper", glyph="s")
    baker = add_enemy(fixture.world, 4, 1, name="baker", glyph="b")
    fixture.world.factions.require(shopkeeper).value = FactionId.TOWN.value
    fixture.world.factions.require(baker).value = FactionId.TOWN.value
    fixture.world.aggro_overrides.add(
        shopkeeper,
        AggroOverrideList(
            overrides=[AggroOverride(FactionId.PLAYER_PARTY, Relation.HOSTILE)]
        ),
    )
    hostiles = hostiles_requiring_battle(fixture.world, fixture.party)
    assert hostiles == [shopkeeper]
    assert baker not in hostiles


def test_aggro_override_set_replaces_existing() -> None:
    overrides = AggroOverrideList()
    overrides.set(FactionId.PLAYER_PARTY, Relation.HOSTILE)
    overrides.set(FactionId.PLAYER_PARTY, Relation.FRIENDLY)
    assert overrides.relation_to(FactionId.PLAYER_PARTY) is Relation.FRIENDLY
    assert len(overrides.overrides) == 1


def test_aggro_override_clear() -> None:
    overrides = AggroOverrideList()
    overrides.set(FactionId.PLAYER_PARTY, Relation.HOSTILE)
    overrides.clear(FactionId.PLAYER_PARTY)
    assert overrides.relation_to(FactionId.PLAYER_PARTY) is None


# ---------------------------------------------------------------------------
# Companions and summons
# ---------------------------------------------------------------------------


def test_companion_attacking_hostile_does_not_flip_to_hostile_to_player() -> None:
    """A companion fighting a goblin stays friendly to the player."""
    fixture = build_tiny_party_world()
    goblin = add_enemy(fixture.world, 4, 2, name="goblin", glyph="g")
    fixture.world.factions.require(goblin).value = FactionId.DUNGEON.value

    # Companion attacks goblin (no state change required) — the relation
    # graph must remain: companion ↔ goblin hostile, companion ↔ player
    # friendly. This test just asserts the static graph; combat does not
    # mutate the faction.
    assert is_hostile_to(fixture.world, fixture.companion, goblin) is True
    assert is_hostile_to(fixture.world, fixture.companion, fixture.player) is False
    assert is_hostile_to(fixture.world, fixture.player, fixture.companion) is False


def test_summon_inherits_owner_faction() -> None:
    """A summoned creature follows its summoner's faction."""
    fixture = build_tiny_party_world()
    # The summon's own faction string is intentionally a "neutral" placeholder
    # ("wildlife") to prove that inheritance kicks in via the summoner field.
    summon = fixture.world.create_entity()
    fixture.world.positions.add(summon, Position(x=4, y=2))
    fixture.world.factions.add(
        summon,
        Faction(value=FactionId.WILDLIFE.value, summoner=fixture.player),
    )
    # Without combat stats the summon can't trigger battle; add one so
    # the hostility scan is meaningful.
    from src.core.components import CombatStats

    fixture.world.combat_stats.add(
        summon,
        CombatStats(
            armor_class=10,
            hit_points=5,
            max_hit_points=5,
            strength=10,
            dexterity=10,
            constitution=10,
        ),
    )
    goblin = add_enemy(fixture.world, 5, 2, name="goblin", glyph="g")
    fixture.world.factions.require(goblin).value = FactionId.DUNGEON.value

    # Summon should treat goblin as hostile (inherits player_party).
    assert is_hostile_to(fixture.world, summon, goblin) is True
    # Summon should NOT be hostile to its summoner or other party members.
    assert is_hostile_to(fixture.world, summon, fixture.player) is False
    assert is_hostile_to(fixture.world, summon, fixture.companion) is False
    # And the party should not be hostile to its own summon.
    assert is_hostile_to(fixture.world, fixture.player, summon) is False


def test_summon_inherits_overrides_from_summoner() -> None:
    """Overrides on the summoner propagate to summoned minions."""
    fixture = build_tiny_party_world()
    shopkeeper = add_enemy(fixture.world, 4, 2, name="shopkeeper", glyph="s")
    fixture.world.factions.require(shopkeeper).value = FactionId.TOWN.value
    # Player has an override making town hostile (after they got
    # caught stealing). Player's summon should see it the same way.
    fixture.world.aggro_overrides.add(
        fixture.player,
        AggroOverrideList(
            overrides=[AggroOverride(FactionId.TOWN, Relation.HOSTILE)]
        ),
    )
    summon = fixture.world.create_entity()
    fixture.world.positions.add(summon, Position(x=5, y=2))
    fixture.world.factions.add(
        summon,
        Faction(value=FactionId.WILDLIFE.value, summoner=fixture.player),
    )
    from src.core.components import CombatStats

    fixture.world.combat_stats.add(
        summon,
        CombatStats(
            armor_class=10,
            hit_points=5,
            max_hit_points=5,
            strength=10,
            dexterity=10,
            constitution=10,
        ),
    )
    # The summon picks up its summoner's aggro override and treats the
    # shopkeeper as hostile.
    assert is_hostile_to(fixture.world, summon, shopkeeper) is True


def test_summoner_chain_is_cycle_safe() -> None:
    """A circular summoner reference resolves without infinite-looping."""
    fixture = build_tiny_party_world()
    # Drop the companion's faction into an UNKNOWN-string mode then
    # point both members at each other to construct the cycle. The
    # awareness layer must give up and return UNKNOWN rather than loop.
    a = fixture.player
    b = fixture.companion
    fixture.world.factions.require(a).summoner = b
    fixture.world.factions.require(b).summoner = a
    # Just calling is_hostile_to must not hang. The exact answer is
    # implementation-defined for the loop case; either NEUTRAL or
    # HOSTILE is acceptable so long as the call returns.
    assert is_hostile_to(fixture.world, a, b) in (True, False)


# ---------------------------------------------------------------------------
# Default table singleton
# ---------------------------------------------------------------------------


def test_default_relation_table_singleton_matches_factory() -> None:
    """The module singleton equals a fresh factory call."""
    fresh = default_relation_table()
    for a in FactionId:
        for b in FactionId:
            assert DEFAULT_RELATION_TABLE.relation(a, b) is fresh.relation(a, b)


# ---------------------------------------------------------------------------
# Save / load round-trip for aggro overrides + summoner
# ---------------------------------------------------------------------------


def test_aggro_overrides_round_trip_through_save() -> None:
    from src.core.world import World

    fixture = build_tiny_party_world()
    shopkeeper = add_enemy(fixture.world, 4, 2, name="shopkeeper", glyph="s")
    fixture.world.factions.require(shopkeeper).value = FactionId.TOWN.value
    fixture.world.aggro_overrides.add(
        shopkeeper,
        AggroOverrideList(
            overrides=[AggroOverride(FactionId.PLAYER_PARTY, Relation.HOSTILE)]
        ),
    )

    payload = fixture.world.to_dict()
    rebuilt = World.from_dict(payload)
    rebuilt_overrides = rebuilt.aggro_overrides.get(shopkeeper)
    assert rebuilt_overrides is not None
    assert rebuilt_overrides.relation_to(FactionId.PLAYER_PARTY) is Relation.HOSTILE


def test_faction_summoner_round_trips_through_save() -> None:
    from src.core.world import World

    fixture = build_tiny_party_world()
    summon = fixture.world.create_entity()
    fixture.world.positions.add(summon, Position(x=4, y=2))
    fixture.world.factions.add(
        summon,
        Faction(value=FactionId.WILDLIFE.value, summoner=fixture.player),
    )

    payload = fixture.world.to_dict()
    rebuilt = World.from_dict(payload)
    faction = rebuilt.factions.get(summon)
    assert faction is not None
    assert faction.summoner == fixture.player
    assert faction.value == FactionId.WILDLIFE.value


# ---------------------------------------------------------------------------
# Legacy string compatibility
# ---------------------------------------------------------------------------


def test_legacy_player_alias_resolves_to_player_party() -> None:
    assert FactionId.from_value("player") is FactionId.PLAYER_PARTY


def test_legacy_enemy_alias_resolves_to_dungeon() -> None:
    assert FactionId.from_value("enemy") is FactionId.DUNGEON


def test_unknown_faction_falls_back_to_legacy_different_string_rule() -> None:
    """Pre-M28 fixtures with ad-hoc faction strings still work."""
    fixture = build_tiny_party_world()
    # Construct an entity with a faction string the catalog doesn't know.
    rogue = add_enemy(fixture.world, 4, 2, name="rogue", glyph="r")
    fixture.world.factions.require(rogue).value = "cultist"
    # The legacy rule applied: different string vs ``player`` → hostile.
    # (``player`` aliases to PLAYER_PARTY but ``cultist`` is UNKNOWN, so
    # the legacy fallback in the awareness predicate kicks in.)
    assert is_hostile_to(fixture.world, fixture.player, rogue) is True


def test_summon_with_recruited_companion_inherits_party_faction() -> None:
    """A companion's pet inherits via summoner chain even after recruitment."""
    fixture = build_tiny_party_world()
    pet = fixture.world.create_entity()
    fixture.world.positions.add(pet, Position(x=4, y=2))
    # Pet declares its own faction as wildlife but is "owned" by the
    # companion. The companion is in PLAYER_PARTY, so the pet inherits.
    fixture.world.factions.add(
        pet,
        Faction(value=FactionId.WILDLIFE.value, summoner=fixture.companion),
    )
    from src.core.components import CombatStats

    fixture.world.combat_stats.add(
        pet,
        CombatStats(
            armor_class=10,
            hit_points=3,
            max_hit_points=3,
            strength=8,
            dexterity=10,
            constitution=8,
        ),
    )
    assert is_hostile_to(fixture.world, fixture.player, pet) is False
    assert hostiles_requiring_battle(fixture.world, fixture.party + [pet]) == []
