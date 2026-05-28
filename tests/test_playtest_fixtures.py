"""Smoke + behaviour tests for the M38 scenario fixtures.

Every fixture registered in :mod:`src.testing.fixtures` needs at least
one test that demonstrates:

1. The fixture loads through the M37 harness without raising.
2. The observation snapshot at t=0 reports the expected entities (or
   the lack thereof, for fixtures whose payload starts outside LOS).
3. A short command script drives the fixture toward its intended
   outcome — combat resolution, lock pick, etc. — without trip-wiring
   anything else.
4. Identical seeds produce identical observations at t=0.
5. Save/load round-trips after loading the fixture (M16 contract).

The tests deliberately stay light on assertions about specific HP /
roll values: a single seeded ``random.Random`` is shared by every
fixture and any narrow check would fight the upstream RNG-stream
contract. Instead they assert *structural* facts (entity count,
faction, modal state) that survive a re-seed cleanly.
"""

from __future__ import annotations

import pytest

from src.core.modes import PlayMode, UIMode
from src.testing import PlaytestHarness, SCENARIOS
import src.testing.fixtures  # noqa: F401 — registers fixtures


# ---------------------------------------------------------------------------
# Catalog sanity
# ---------------------------------------------------------------------------


_EXPECTED_FIXTURES = (
    "combat_simple",
    "combat_archer",
    "door_locked",
    "trap_armed",
    "container_loot",
    "shop_basic",
    "vision_corridor",
    "hostile_far",
    "open_terrain",
    "spell_encounter",
)


def test_all_required_fixtures_registered() -> None:
    """The M38 issue lists nine required fixtures — assert all of them
    show up in the registry. This is the contract the ``/playtest``
    standing agent depends on."""
    missing = [name for name in _EXPECTED_FIXTURES if name not in SCENARIOS]
    assert missing == []


@pytest.mark.parametrize("name", _EXPECTED_FIXTURES)
def test_fixture_no_spawn_collision_at_t0(name: str) -> None:
    """Regression for #77: no two entities may share a tile at t=0.

    The original bug had the deterministic companion-placement helper
    drop a companion onto the same tile a scenario builder hardcoded
    for its scenario entity (kobold / door / shopkeeper). The t=0
    observation then reported a "You displaced X." line and the
    intended subsystem never fired.
    """
    harness = PlaytestHarness(scenario_name=name, dev_mode=False)
    positions: dict[tuple[int, int], list[str]] = {}
    for entity, pos in harness.app.world.positions.values.items():
        key = (pos.x, pos.y)
        name_component = harness.app.world.names.get(entity)
        label = name_component.value if name_component else f"entity_{entity}"
        positions.setdefault(key, []).append(label)
    collisions = {tile: labels for tile, labels in positions.items() if len(labels) > 1}
    assert collisions == {}, (
        f"Fixture {name!r} spawned multiple entities on the same tile: {collisions}"
    )


@pytest.mark.parametrize("name", _EXPECTED_FIXTURES)
def test_fixture_loads_via_harness(name: str) -> None:
    """Every fixture must instantiate cleanly through the harness."""
    harness = PlaytestHarness(scenario_name=name, dev_mode=False)
    assert harness.scenario is not None
    assert harness.scenario.name == name
    obs = harness.observe()
    # All fixtures start in the play screen; harness construction must
    # not leave us stuck on a modal.
    assert obs.mode["ui_mode"] == UIMode.play.value
    # Four-member party (player + three companions from the M6 helper).
    assert len(obs.party) == 4


@pytest.mark.parametrize("name", _EXPECTED_FIXTURES)
def test_fixture_deterministic_observation(name: str) -> None:
    """Two harnesses with the same seed must produce equal t=0
    observations for the same fixture."""
    a = PlaytestHarness(scenario_name=name, seed=7, dev_mode=False)
    b = PlaytestHarness(scenario_name=name, seed=7, dev_mode=False)
    assert a.observe().to_dict() == b.observe().to_dict()


@pytest.mark.parametrize("name", ["combat_simple", "shop_basic", "open_terrain"])
def test_fixture_seed_threads_through_builder(name: str) -> None:
    """Different seeds must reach the fixture builder (loot rolls,
    starter sheets) so two seeds produce DIFFERENT post-action outcomes.

    Without seed threading, every fixture used a hardcoded seed and the
    harness ``seed`` parameter was silently ignored.
    """
    a = PlaytestHarness(scenario_name=name, seed=7, dev_mode=False)
    b = PlaytestHarness(scenario_name=name, seed=42, dev_mode=False)
    # The same fixture geometry holds, but party HP/stats from the seeded
    # YOLO sheet roll should diverge for at least one of the three names.
    a_party = a.observe().to_dict()["party"]
    b_party = b.observe().to_dict()["party"]
    assert a_party != b_party, (
        f"Fixture '{name}' produced identical t=0 party state under seeds "
        f"7 and 42 — seed not threaded into builder."
    )


@pytest.mark.parametrize("name", _EXPECTED_FIXTURES)
def test_fixture_save_load_round_trip(name: str, tmp_path) -> None:
    """Each fixture must survive a save/load round-trip — the M16
    contract for any in-engine state."""
    harness = PlaytestHarness(scenario_name=name, dev_mode=False)
    before = harness.observe().to_dict()
    path = harness.save(tmp_path / f"{name}.json")
    harness.load(path)
    after = harness.observe().to_dict()
    # The world clock and observation projection both survive; we
    # compare the structural shape rather than full equality because
    # load drops some transient fields (autowalk request, etc.) by
    # design.
    assert before["party"] == after["party"]
    assert sorted(e["name"] for e in before["visible_entities"]) == sorted(
        e["name"] for e in after["visible_entities"]
    )


# ---------------------------------------------------------------------------
# Combat fixtures
# ---------------------------------------------------------------------------


def test_combat_simple_forces_turn_based_with_two_kobolds() -> None:
    """``combat_simple`` puts the party in immediate turn-based combat
    with two adjacent kobolds. The observation surface should report
    both hostiles in ``visible_entities``."""
    harness = PlaytestHarness(scenario_name="combat_simple", dev_mode=False)
    obs = harness.observe()
    assert obs.mode["play_mode"] == PlayMode.turn_based.value
    assert obs.combat is not None
    kobolds = [e for e in obs.visible_entities if e.kind == "kobold"]
    assert len(kobolds) == 2
    for kobold in kobolds:
        assert kobold.faction == "dungeon"
        assert kobold.distance == 1


def test_combat_archer_places_ranged_hostile_at_distance() -> None:
    """``combat_archer`` parks a ranged kobold archer at the eastern
    edge of the fixture room. It should be visible (LOS is line-of-
    sight in an open room) but several tiles away."""
    harness = PlaytestHarness(scenario_name="combat_archer", dev_mode=False)
    obs = harness.observe()
    archers = [e for e in obs.visible_entities if e.kind == "kobold_archer"]
    assert len(archers) == 1
    assert archers[0].faction == "dungeon"
    # Archer is parked 6 tiles east — far enough to exercise ranged AI.
    assert archers[0].distance >= 5


# ---------------------------------------------------------------------------
# Door / trap / container / shop fixtures
# ---------------------------------------------------------------------------


def test_door_locked_exposes_a_locked_door_in_observation() -> None:
    """``door_locked`` should surface a locked door as a non-creature
    visible entity tagged ``door``."""
    harness = PlaytestHarness(scenario_name="door_locked", dev_mode=False)
    obs = harness.observe()
    doors = [e for e in obs.visible_entities if e.kind == "door"]
    assert len(doors) == 1
    assert "locked" in doors[0].name


def test_door_locked_attempt_pick_emits_lock_message() -> None:
    """Walking into the locked door should not bypass it — the
    interaction emits one of the M9 lock-pick message strings."""
    harness = PlaytestHarness(scenario_name="door_locked", dev_mode=False)
    # The locked door is one tile east of the player; face east + interact.
    outcomes = harness.run("e")
    last_message = outcomes[-1].last_message
    # M9 emits either "Unlocked and opened." or "Lock pick failed." or
    # the refusal banner depending on the roll. The fixture forces a
    # Rogue with Sleight of Hand so the refusal banner should not fire.
    assert any(
        token in last_message
        for token in ("Unlocked", "failed", "It's locked")
    ), f"unexpected lock message: {last_message!r}"


def test_trap_armed_lets_disarm_check_fire() -> None:
    """Interacting toward the trap should produce one of the M9/M26
    trap outcomes (disarm, trigger, or refusal)."""
    harness = PlaytestHarness(scenario_name="trap_armed", dev_mode=False)
    obs = harness.observe()
    traps = [e for e in obs.visible_entities if e.kind == "trap"]
    assert len(traps) == 1
    # The trap is two tiles east — the player must step once before
    # the interact can reach it.
    outcomes = harness.run("l;e")
    last_message = outcomes[-1].last_message
    assert any(
        token in last_message
        for token in ("disarmed", "triggered", "sense danger")
    ), f"unexpected trap message: {last_message!r}"


def test_container_loot_chest_is_closed_at_start() -> None:
    """``container_loot`` should expose a closed chest holding the
    seeded inventory."""
    harness = PlaytestHarness(scenario_name="container_loot", dev_mode=False)
    obs = harness.observe()
    containers = [e for e in obs.visible_entities if e.kind == "container"]
    assert len(containers) == 1
    # Chest holds the seeded loot — verify via the App's component store
    # because the inventory contents aren't projected into the
    # Observation.
    chest_id = containers[0].id
    inventory = harness.app.world.inventories.require(chest_id)
    assert inventory.gold == 25
    item_ids = {stack.item_id for stack in inventory.items}
    assert "weapon.dagger" in item_ids


def test_container_loot_interact_opens_chest() -> None:
    """Walking up to the chest and interacting should flip its
    ``Container.is_open`` flag."""
    harness = PlaytestHarness(scenario_name="container_loot", dev_mode=False)
    # Step east once to be adjacent (chest is two east of spawn).
    outcomes = harness.run("l;e")
    # Find the chest entity post-interact and assert it's now open.
    containers = [
        e for e in outcomes[-1].observation_after.visible_entities
        if e.kind == "container"
    ]
    assert len(containers) == 1
    chest_id = containers[0].id
    assert harness.app.world.containers.require(chest_id).is_open is True


def test_shop_basic_exposes_shopkeeper_with_stocked_inventory() -> None:
    """``shop_basic`` should leave the party in explore mode (the
    shopkeeper is NPC, not enemy) with a Shop component on the
    visible NPC."""
    harness = PlaytestHarness(scenario_name="shop_basic", dev_mode=False)
    obs = harness.observe()
    assert obs.mode["play_mode"] == PlayMode.explore.value
    shops = [e for e in obs.visible_entities if e.kind == "shop"]
    assert len(shops) == 1
    shop_id = shops[0].id
    shop_inventory = harness.app.world.inventories.require(shop_id)
    assert shop_inventory.gold == 200
    item_ids = {stack.item_id for stack in shop_inventory.items}
    assert "weapon.club" in item_ids
    assert "armor.leather" in item_ids


# ---------------------------------------------------------------------------
# Vision / movement fixtures
# ---------------------------------------------------------------------------


def test_vision_corridor_hides_distant_hostile_at_spawn() -> None:
    """In ``vision_corridor`` the hostile sits at the east end of a
    long thin room. M19 LOS clips at radius 10, so a hostile at
    distance > 10 should NOT appear in ``visible_entities`` from the
    west-end spawn."""
    harness = PlaytestHarness(scenario_name="vision_corridor", dev_mode=False)
    obs = harness.observe()
    # No kobold should be in the visible set at t=0.
    assert [e for e in obs.visible_entities if e.kind == "kobold"] == []
    # ...but combat mode is on because ``hostiles_requiring_battle``
    # is global (not LOS-filtered) — documents the current behaviour
    # so a future M28 faction/awareness change shows up as a regression.
    assert obs.mode["play_mode"] == PlayMode.turn_based.value


def test_hostile_far_keeps_hostile_outside_los_at_spawn() -> None:
    """``hostile_far`` parks a kobold in a corner > 10 tiles from the
    party. Again the LOS projection should hide it."""
    harness = PlaytestHarness(scenario_name="hostile_far", dev_mode=False)
    obs = harness.observe()
    assert [e for e in obs.visible_entities if e.kind == "kobold"] == []
    # And confirm the hostile actually exists in the world.
    kobolds = [
        entity for entity, creature in harness.app.world.creatures.values.items()
        if creature.kind == "kobold"
    ]
    assert len(kobolds) == 1


def test_open_terrain_supports_long_autowalk() -> None:
    """``open_terrain`` is an empty room with no hostiles; a repeat-
    move script should consume several steps without an early
    interrupt."""
    harness = PlaytestHarness(scenario_name="open_terrain", dev_mode=False)
    # ``10l`` autowalks 10 east. The room is 30 wide so the wall is at
    # least 20 tiles away — we expect the full ten steps unless terrain
    # surprises us.
    outcomes = harness.run("10l")
    assert len(outcomes) == 1
    assert outcomes[0].steps_taken >= 5  # Generous floor; the room is big.
    # An ``out_of_steps`` reason at the end is the well-behaved exit.
    # ``blocked`` is acceptable if a companion ended up in the way.
    from src.core.autowalk import InterruptReason  # local import to avoid top noise
    assert outcomes[0].interrupt_reason in (
        InterruptReason.OUT_OF_STEPS,
        InterruptReason.BLOCKED,
    )


# ---------------------------------------------------------------------------
# Catalog helper
# ---------------------------------------------------------------------------


def test_fixtures_catalog_export_matches_registry() -> None:
    """``src.testing.fixtures.CATALOG`` should reflect the same names
    that the M37 registry holds (after the import side-effect runs).
    Documents the wiring contract — if a test sees a fixture name in
    one and not the other, registration is broken."""
    from src.testing.fixtures import CATALOG

    for name in CATALOG:
        assert name in SCENARIOS, f"{name} in CATALOG but missing from SCENARIOS"
