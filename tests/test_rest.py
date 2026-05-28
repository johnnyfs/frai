"""Tests for M34 rest system and shelter zones."""

from __future__ import annotations

from src.app import create_app
from src.core.components import (
    CombatStats,
    Faction,
    Name,
    Position,
)
from src.core.effects import EmitMessage
from src.core.factions import FactionId
from src.core.modes import PlayMode, UIMode
from src.core.shelter import (
    RestPermission,
    RestRisk,
    ShelterZone,
)
from src.core.spells import SpellSlots
from src.core.world import World
from src.systems.rest_system import (
    ENCOUNTER_CHECK_DC,
    attempt_long_rest,
    attempt_short_rest,
)
from src.systems.zone_system import tick_zone_transitions


def _bare_app() -> "object":
    """Build a minimal default App in explore mode with no hostiles."""

    app = create_app()
    app.handle_key(ord("y"))
    # Remove every creature so the play mode lands in explore.
    for entity in list(app.world.creatures.values):
        app.world.remove_entity(entity)
    app.sync_play_mode()
    assert app.play_mode is PlayMode.explore
    return app


def _put_party_at(app, x: int, y: int) -> None:
    """Place the active actor at ``(x, y)`` and re-tick zone state."""

    actor = app.active_actor()
    position = app.world.positions.require(actor)
    position.x = x
    position.y = y
    # Force a fresh transition pass so the next assertion has a clean
    # ZoneOccupancyState baseline.
    app.world.zone_occupancy.current_zone_id = None


def _add_zone(app, **kwargs) -> ShelterZone:
    """Register a shelter zone on the app's world and return it."""

    defaults: dict = {
        "zone_id": "test_zone",
        "left": 0,
        "top": 0,
        "width": 3,
        "height": 3,
    }
    defaults.update(kwargs)
    zone = ShelterZone(**defaults)
    app.world.shelter_zones.add(zone)
    return zone


# ---------------------------------------------------------------------------
# Permission / refusal paths
# ---------------------------------------------------------------------------


def test_rest_outside_shelter_is_refused() -> None:
    app = _bare_app()
    actor = app.active_actor()
    position = app.world.positions.require(actor)
    # No zones registered: every rest kind refuses with a clear banner.
    effects = attempt_short_rest(app)
    assert len(effects) == 1
    assert isinstance(effects[0], EmitMessage)
    assert "shelter" in effects[0].text.lower()
    long_effects = attempt_long_rest(app)
    assert "shelter" in long_effects[0].text.lower()
    # Position untouched.
    final_position = app.world.positions.require(actor)
    assert (final_position.x, final_position.y) == (position.x, position.y)


def test_rest_during_combat_refused() -> None:
    app = _bare_app()
    _add_zone(app, zone_id="z", left=0, top=0, width=200, height=200)
    actor = app.active_actor()
    position = app.world.positions.require(actor)
    # Spawn a hostile next to the party to flip into turn_based.
    hostile = app.world.create_entity()
    app.world.positions.add(hostile, Position(position.x + 1, position.y))
    app.world.names.add(hostile, Name("ambusher"))
    app.world.factions.add(hostile, Faction(FactionId.DUNGEON.value))
    app.world.combat_stats.add(
        hostile,
        CombatStats(
            armor_class=10,
            hit_points=5,
            max_hit_points=5,
            strength=10,
            dexterity=10,
            constitution=10,
        ),
    )
    app.sync_play_mode()
    assert app.play_mode is PlayMode.turn_based
    effects = attempt_short_rest(app)
    assert len(effects) == 1
    assert "combat" in effects[0].text.lower()


def test_rest_permission_short_only_refuses_long() -> None:
    app = _bare_app()
    _add_zone(
        app,
        zone_id="glade",
        left=0,
        top=0,
        width=200,
        height=200,
        rest_permission=RestPermission.SHORT_ONLY,
    )
    long_effects = attempt_long_rest(app)
    assert "does not permit a long rest" in long_effects[0].text
    short_effects = attempt_short_rest(app)
    # Short rest path on a non-cost zone always succeeds; first effect
    # is the summary message.
    assert any(
        isinstance(e, EmitMessage) and "short rest" in e.text.lower()
        for e in short_effects
    )


def test_rest_permission_long_only_refuses_short() -> None:
    app = _bare_app()
    _add_zone(
        app,
        zone_id="bedroom",
        left=0,
        top=0,
        width=200,
        height=200,
        rest_permission=RestPermission.LONG_ONLY,
    )
    short_effects = attempt_short_rest(app)
    assert "does not permit a short rest" in short_effects[0].text


def test_rest_permission_none_refuses_both() -> None:
    app = _bare_app()
    _add_zone(
        app,
        zone_id="cursed",
        left=0,
        top=0,
        width=200,
        height=200,
        rest_permission=RestPermission.NONE,
    )
    short_effects = attempt_short_rest(app)
    assert "does not permit a short rest" in short_effects[0].text
    long_effects = attempt_long_rest(app)
    assert "does not permit a long rest" in long_effects[0].text


def test_rest_risk_forbidden_refuses_even_with_permission() -> None:
    app = _bare_app()
    _add_zone(
        app,
        zone_id="haunted",
        left=0,
        top=0,
        width=200,
        height=200,
        rest_permission=RestPermission.BOTH,
        rest_risk=RestRisk.FORBIDDEN,
    )
    effects = attempt_long_rest(app)
    assert "unsafe to rest" in effects[0].text


# ---------------------------------------------------------------------------
# Recovery semantics
# ---------------------------------------------------------------------------


def test_short_rest_restores_half_missing_hp() -> None:
    app = _bare_app()
    _add_zone(app, zone_id="z", left=0, top=0, width=200, height=200)
    # Damage the lead party member so the recovery is observable.
    leader = app.party.members[0]
    stats = app.world.combat_stats.require(leader)
    max_hp = stats.max_hit_points
    stats.hit_points = max(1, max_hp - 10)
    hp_before = stats.hit_points
    missing_before = max_hp - hp_before

    effects = attempt_short_rest(app)
    app.apply_effects(effects)

    hp_after = app.world.combat_stats.require(leader).hit_points
    recovered = hp_after - hp_before
    assert recovered == max(1, missing_before // 2)


def test_long_rest_fully_heals_party_and_refills_slots() -> None:
    app = _bare_app()
    _add_zone(
        app,
        zone_id="tavern",
        left=0,
        top=0,
        width=200,
        height=200,
        rest_permission=RestPermission.BOTH,
    )

    # Damage every member.
    for member in app.party.members:
        stats = app.world.combat_stats.get(member)
        if stats is None:
            continue
        stats.hit_points = max(1, stats.max_hit_points // 2)

    # Give the leader a spell slot ledger and spend it.
    leader = app.party.members[0]
    slots = SpellSlots.from_pairs({1: 2})
    slots.consume(1)
    app.world.spell_slots.add(leader, slots)
    assert slots.remaining(1) == 1

    effects = attempt_long_rest(app)
    app.apply_effects(effects)

    for member in app.party.members:
        stats = app.world.combat_stats.get(member)
        if stats is None:
            continue
        assert stats.hit_points == stats.max_hit_points
    assert app.world.spell_slots.require(leader).remaining(1) == 2


def test_long_rest_advances_clock_by_eight_hours() -> None:
    app = _bare_app()
    _add_zone(app, zone_id="z", left=0, top=0, width=200, height=200)
    before = app.world.clock.elapsed_seconds

    effects = attempt_long_rest(app)
    app.apply_effects(effects)

    elapsed = app.world.clock.elapsed_seconds - before
    assert elapsed == 8 * 60 * 60


def test_short_rest_advances_clock_by_ten_minutes() -> None:
    app = _bare_app()
    _add_zone(app, zone_id="z", left=0, top=0, width=200, height=200)
    before = app.world.clock.elapsed_seconds

    effects = attempt_short_rest(app)
    app.apply_effects(effects)

    elapsed = app.world.clock.elapsed_seconds - before
    assert elapsed == 10 * 60


# ---------------------------------------------------------------------------
# Cost / requirements / uses
# ---------------------------------------------------------------------------


def test_rest_cost_deducted_from_active_actor() -> None:
    app = _bare_app()
    _add_zone(
        app,
        zone_id="inn",
        left=0,
        top=0,
        width=200,
        height=200,
        cost=5,
    )
    actor = app.active_actor()
    inventory = app.world.inventories.require(actor)
    starting_gold = inventory.gold
    assert starting_gold >= 5  # the starter gold suffices

    effects = attempt_short_rest(app)
    app.apply_effects(effects)

    assert app.world.inventories.require(actor).gold == starting_gold - 5


def test_rest_cost_refuses_when_actor_cannot_afford() -> None:
    app = _bare_app()
    _add_zone(
        app,
        zone_id="inn",
        left=0,
        top=0,
        width=200,
        height=200,
        cost=100,
    )
    actor = app.active_actor()
    inventory = app.world.inventories.require(actor)
    inventory.gold = 1  # not enough
    effects = attempt_short_rest(app)
    assert "afford" in effects[0].text.lower()
    # Gold untouched.
    assert app.world.inventories.require(actor).gold == 1


def test_rest_uses_remaining_decrement_and_exhaust() -> None:
    app = _bare_app()
    zone = _add_zone(
        app,
        zone_id="single_use",
        left=0,
        top=0,
        width=200,
        height=200,
        uses_remaining=1,
    )

    # First rest succeeds; uses_remaining drops to 0.
    effects = attempt_short_rest(app)
    app.apply_effects(effects)
    assert zone.uses_remaining == 0

    # Second rest is refused with the "used up" banner.
    second = attempt_short_rest(app)
    assert "used up" in second[0].text.lower()


# ---------------------------------------------------------------------------
# Risky shelter encounter check
# ---------------------------------------------------------------------------


class _FixedRng:
    """Drop-in RNG that returns a fixed integer for randint calls."""

    def __init__(self, value: int) -> None:
        self.value = value

    def randint(self, a: int, b: int) -> int:
        return self.value


def test_risky_rest_interrupted_on_low_roll() -> None:
    app = _bare_app()
    _add_zone(
        app,
        zone_id="risky",
        left=0,
        top=0,
        width=200,
        height=200,
        rest_risk=RestRisk.ENCOUNTER_CHECK,
    )
    actor = app.active_actor()
    # Damage so we can confirm no recovery on interrupt.
    stats = app.world.combat_stats.require(actor)
    stats.hit_points = 1
    before_hp = stats.hit_points
    before_gold = app.world.inventories.require(actor).gold

    rng = _FixedRng(ENCOUNTER_CHECK_DC - 1)
    effects = attempt_long_rest(app, rng=rng)
    app.apply_effects(effects)

    assert any(
        isinstance(e, EmitMessage) and "interrupted" in e.text.lower()
        for e in effects
    )
    # No recovery: HP unchanged. No gold spent.
    assert app.world.combat_stats.require(actor).hit_points == before_hp
    assert app.world.inventories.require(actor).gold == before_gold


def test_risky_rest_succeeds_on_high_roll() -> None:
    app = _bare_app()
    _add_zone(
        app,
        zone_id="risky",
        left=0,
        top=0,
        width=200,
        height=200,
        rest_risk=RestRisk.ENCOUNTER_CHECK,
    )
    actor = app.active_actor()
    stats = app.world.combat_stats.require(actor)
    stats.hit_points = max(1, stats.max_hit_points // 2)

    rng = _FixedRng(ENCOUNTER_CHECK_DC + 5)
    effects = attempt_long_rest(app, rng=rng)
    app.apply_effects(effects)

    assert app.world.combat_stats.require(actor).hit_points == stats.max_hit_points


# ---------------------------------------------------------------------------
# Zone entry / exit messages
# ---------------------------------------------------------------------------


def test_zone_entry_message_fires_once_on_entry() -> None:
    app = _bare_app()
    leader = app.party.members[0]
    _put_party_at(app, 10, 10)
    _add_zone(
        app,
        zone_id="greeting",
        left=12,
        top=10,
        width=3,
        height=3,
        entry_message="A warm welcome.",
        exit_message="Farewell.",
    )

    # First tick before entering: no messages, no occupancy change.
    effects = tick_zone_transitions(app)
    assert effects == []

    # Move the leader into the zone.
    app.world.positions.require(leader).x = 12
    effects = tick_zone_transitions(app)
    assert any(
        isinstance(e, EmitMessage) and "warm welcome" in e.text.lower()
        for e in effects
    )

    # Tick again with no move: no second entry message.
    again = tick_zone_transitions(app)
    assert again == []

    # Move out of the zone: exit message fires once.
    app.world.positions.require(leader).x = 10
    exit_effects = tick_zone_transitions(app)
    assert any(
        isinstance(e, EmitMessage) and "farewell" in e.text.lower()
        for e in exit_effects
    )


def test_zone_transition_via_apply_effects_emits_entry_message() -> None:
    """End-to-end: a move through `apply_effects` triggers ZoneSystem."""

    app = _bare_app()
    leader = app.party.members[0]
    _put_party_at(app, 10, 10)
    _add_zone(
        app,
        zone_id="auto",
        left=11,
        top=10,
        width=3,
        height=3,
        entry_message="You step inside.",
    )
    from src.core.effects import MoveEntity

    app.apply_effects([MoveEntity(leader, 11, 10)])
    # The entry message should be the current message on screen.
    assert "step inside" in app.messages.current.lower()


# ---------------------------------------------------------------------------
# Save/load round-trip
# ---------------------------------------------------------------------------


def test_world_to_dict_round_trips_shelter_zones() -> None:
    width, height = 30, 20
    world = World(
        width=width,
        height=height,
        tiles=[[__import__("src.map.tiles", fromlist=["FLOOR"]).FLOOR for _ in range(width)] for _ in range(height)],
    )
    world.shelter_zones.add(
        ShelterZone(
            zone_id="z1",
            left=1, top=1, width=4, height=4,
            rest_permission=RestPermission.BOTH,
            rest_risk=RestRisk.ENCOUNTER_CHECK,
            entry_message="Hi.",
            exit_message="Bye.",
            cost=7,
            requirements=("quest.key",),
            uses_remaining=2,
            label="Test Inn",
        )
    )
    world.zone_occupancy.current_zone_id = "z1"

    rebuilt = World.from_dict(world.to_dict())
    assert len(rebuilt.shelter_zones.zones) == 1
    rebuilt_zone = rebuilt.shelter_zones.zones[0]
    assert rebuilt_zone.zone_id == "z1"
    assert rebuilt_zone.rest_permission is RestPermission.BOTH
    assert rebuilt_zone.rest_risk is RestRisk.ENCOUNTER_CHECK
    assert rebuilt_zone.cost == 7
    assert rebuilt_zone.requirements == ("quest.key",)
    assert rebuilt_zone.uses_remaining == 2
    assert rebuilt_zone.entry_message == "Hi."
    assert rebuilt.zone_occupancy.current_zone_id == "z1"


def test_uses_remaining_persists_through_full_save_load() -> None:
    app = _bare_app()
    zone = _add_zone(
        app,
        zone_id="finite",
        left=0,
        top=0,
        width=200,
        height=200,
        uses_remaining=3,
    )
    effects = attempt_short_rest(app)
    app.apply_effects(effects)
    assert zone.uses_remaining == 2

    payload = app.world.to_dict()
    rebuilt = World.from_dict(payload)
    rebuilt_zone = rebuilt.shelter_zones.by_id("finite")
    assert rebuilt_zone is not None
    assert rebuilt_zone.uses_remaining == 2


# ---------------------------------------------------------------------------
# UI / input wiring
# ---------------------------------------------------------------------------


def test_r_key_opens_rest_menu_in_explore_play() -> None:
    app = _bare_app()
    app.handle_key(ord("r"))
    assert app.ui_mode is UIMode.rest_menu


def test_r_key_refused_in_combat() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    # World ships with hostiles so play_mode is turn_based.
    assert app.play_mode is PlayMode.turn_based
    app.handle_key(ord("r"))
    # Modal does NOT open.
    assert app.ui_mode is UIMode.play
    assert "combat" in app.messages.current.lower()


def test_rest_menu_short_choice_runs_short_rest() -> None:
    app = _bare_app()
    _add_zone(app, zone_id="z", left=0, top=0, width=200, height=200)
    leader = app.party.members[0]
    stats = app.world.combat_stats.require(leader)
    stats.hit_points = max(1, stats.max_hit_points - 6)
    before_hp = stats.hit_points

    app.handle_key(ord("r"))
    assert app.ui_mode is UIMode.rest_menu
    app.handle_key(ord("s"))

    assert app.ui_mode is UIMode.play
    assert app.world.combat_stats.require(leader).hit_points > before_hp


def test_rest_menu_cancel_closes_modal_without_resting() -> None:
    app = _bare_app()
    _add_zone(app, zone_id="z", left=0, top=0, width=200, height=200)
    before_clock = app.world.clock.elapsed_seconds

    app.handle_key(ord("r"))
    assert app.ui_mode is UIMode.rest_menu
    app.handle_key(ord("q"))
    assert app.ui_mode is UIMode.play
    assert app.world.clock.elapsed_seconds == before_clock


# ---------------------------------------------------------------------------
# Default skeleton wiring
# ---------------------------------------------------------------------------


def test_skeleton_world_registers_tavern_and_glade_zones() -> None:
    from src.world.content.skeleton import build_world_skeleton

    built = build_world_skeleton()
    tavern = built.world.shelter_zones.by_id("tavern_room")
    glade = built.world.shelter_zones.by_id("forest_glade")
    assert tavern is not None
    assert tavern.rest_permission is RestPermission.BOTH
    assert tavern.cost == 5
    assert glade is not None
    assert glade.rest_permission is RestPermission.SHORT_ONLY
    assert glade.cost == 0


# ---------------------------------------------------------------------------
# Observation snapshot
# ---------------------------------------------------------------------------


def test_observation_surfaces_shelter_snapshot_when_party_in_zone() -> None:
    from src.ui.observation import observe

    app = _bare_app()
    _add_zone(
        app,
        zone_id="surface",
        left=0,
        top=0,
        width=200,
        height=200,
        rest_permission=RestPermission.LONG_ONLY,
        rest_risk=RestRisk.ENCOUNTER_CHECK,
        cost=12,
        uses_remaining=3,
        label="Test Inn",
    )
    obs = observe(app)
    assert obs.shelter is not None
    assert obs.shelter.zone_id == "surface"
    assert obs.shelter.label == "Test Inn"
    assert obs.shelter.rest_permission == "long_only"
    assert obs.shelter.rest_risk == "encounter_check"
    assert obs.shelter.cost == 12
    assert obs.shelter.uses_remaining == 3


def test_observation_shelter_none_when_outside_zone() -> None:
    from src.ui.observation import observe

    app = _bare_app()
    obs = observe(app)
    assert obs.shelter is None


def test_observation_to_dict_round_trips_shelter_field() -> None:
    from src.ui.observation import Observation, observe

    app = _bare_app()
    _add_zone(
        app,
        zone_id="round_trip",
        left=0,
        top=0,
        width=200,
        height=200,
        label="Rebuilt",
    )
    obs = observe(app)
    payload = obs.to_dict()
    rebuilt = Observation.from_dict(payload)
    assert rebuilt.shelter is not None
    assert rebuilt.shelter.zone_id == "round_trip"
