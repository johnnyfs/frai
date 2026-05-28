"""Conditions, statuses, and durations (M24)."""

from __future__ import annotations

import pytest

from src.app import create_app
from src.core.conditions import (
    Condition,
    ConditionKind,
    ConditionStore,
    DurationKind,
    DurationPolicy,
    apply_condition,
    end_condition,
    tick_conditions,
)
from src.core.effects import ApplyCondition, DamageEntity, EndCondition
from src.core.time import SECONDS_PER_LONG_REST, SECONDS_PER_MINUTE
from src.core.world import World
from src.map.tiles import FLOOR
from tests.support.tiny_world import add_actor, build_tiny_map


def _world() -> World:
    return build_tiny_map()


def test_apply_condition_creates_store_and_records_kind() -> None:
    world = _world()
    actor = add_actor(world, 2, 2)

    stored = apply_condition(
        world,
        actor,
        Condition(kind=ConditionKind.POISONED, duration=DurationPolicy.until_removed()),
    )

    assert stored.kind is ConditionKind.POISONED
    assert world.conditions.has(actor)
    assert world.conditions.require(actor).has(ConditionKind.POISONED)


def test_rounds_policy_expires_after_n_rounds() -> None:
    world = _world()
    actor = add_actor(world, 2, 2)
    apply_condition(
        world,
        actor,
        Condition(kind=ConditionKind.BLESSED, duration=DurationPolicy.rounds(2)),
    )

    # First round tick: still active, countdown drops to 1.
    tick_conditions(world, [actor], boundary="round")
    assert world.conditions.require(actor).has(ConditionKind.BLESSED)
    stored = world.conditions.require(actor).of_kind(ConditionKind.BLESSED)[0]
    assert stored.rounds_remaining == 1

    # Second round: countdown hits zero and the condition is removed.
    tick_conditions(world, [actor], boundary="round")
    assert not world.conditions.require(actor).has(ConditionKind.BLESSED)


def test_turns_policy_expires_on_turn_boundary_only() -> None:
    world = _world()
    actor = add_actor(world, 2, 2)
    apply_condition(
        world,
        actor,
        Condition(kind=ConditionKind.HIDDEN, duration=DurationPolicy.turns(1)),
    )

    # Round ticks don't touch a TURNS-policy condition.
    tick_conditions(world, [actor], boundary="round")
    assert world.conditions.require(actor).has(ConditionKind.HIDDEN)

    # A turn boundary expires it.
    tick_conditions(world, [actor], boundary="turn")
    assert not world.conditions.require(actor).has(ConditionKind.HIDDEN)


def test_minutes_policy_resolves_expires_at_against_clock() -> None:
    world = _world()
    actor = add_actor(world, 2, 2)
    world.clock.advance_seconds(30)  # arbitrary starting offset

    stored = apply_condition(
        world,
        actor,
        Condition(
            kind=ConditionKind.BLESSED,
            duration=DurationPolicy.minutes(5),
        ),
    )

    assert stored.expires_at == 30 + 5 * SECONDS_PER_MINUTE


def test_clock_boundary_expires_minutes_policy() -> None:
    world = _world()
    actor = add_actor(world, 2, 2)
    apply_condition(
        world,
        actor,
        Condition(
            kind=ConditionKind.BLESSED,
            duration=DurationPolicy.minutes(1),
        ),
    )

    world.clock.advance_seconds(SECONDS_PER_MINUTE - 1)
    tick_conditions(world, [actor], boundary="clock")
    assert world.conditions.require(actor).has(ConditionKind.BLESSED)

    world.clock.advance_seconds(1)
    tick_conditions(world, [actor], boundary="clock")
    assert not world.conditions.require(actor).has(ConditionKind.BLESSED)


def test_concentration_breaks_when_a_new_concentration_is_applied() -> None:
    world = _world()
    actor = add_actor(world, 2, 2)

    first = apply_condition(
        world,
        actor,
        Condition(
            kind=ConditionKind.CONCENTRATING,
            duration=DurationPolicy.until_removed(),
            payload={"spell": "bless"},
        ),
    )
    second = apply_condition(
        world,
        actor,
        Condition(
            kind=ConditionKind.CONCENTRATING,
            duration=DurationPolicy.until_removed(),
            payload={"spell": "hold_person"},
        ),
    )

    active = world.conditions.require(actor).of_kind(ConditionKind.CONCENTRATING)
    assert len(active) == 1
    assert active[0].payload["spell"] == "hold_person"
    # The original concentration is gone.
    assert active[0] is not first
    assert active[0] == second


def test_until_rest_clears_on_long_rest_boundary() -> None:
    world = _world()
    actor = add_actor(world, 2, 2)
    apply_condition(
        world,
        actor,
        Condition(
            kind=ConditionKind.POISONED,
            duration=DurationPolicy.until_rest(),
        ),
    )

    # A normal clock tick does not clear an until-rest condition.
    world.clock.advance_seconds(SECONDS_PER_LONG_REST)
    tick_conditions(world, [actor], boundary="clock")
    assert world.conditions.require(actor).has(ConditionKind.POISONED)

    # A long-rest boundary does.
    world.clock.advance_long_rest()
    tick_conditions(world, [actor], boundary="long_rest")
    assert not world.conditions.require(actor).has(ConditionKind.POISONED)


def test_burning_round_tick_emits_damage_effect() -> None:
    world = _world()
    actor = add_actor(world, 2, 2, hit_points=10)
    apply_condition(
        world,
        actor,
        Condition(
            kind=ConditionKind.BURNING,
            duration=DurationPolicy.rounds(3),
            payload={"damage": 2},
        ),
    )

    effects = tick_conditions(world, [actor], boundary="round")

    damage_effects = [e for e in effects if isinstance(e, DamageEntity)]
    assert len(damage_effects) == 1
    assert damage_effects[0].entity == actor
    assert damage_effects[0].amount == 2


def test_end_condition_removes_all_of_kind() -> None:
    world = _world()
    actor = add_actor(world, 2, 2)
    apply_condition(
        world,
        actor,
        Condition(kind=ConditionKind.BLESSED, duration=DurationPolicy.rounds(5)),
    )
    apply_condition(
        world,
        actor,
        Condition(kind=ConditionKind.BLESSED, duration=DurationPolicy.rounds(5)),
    )
    assert len(world.conditions.require(actor).of_kind(ConditionKind.BLESSED)) == 2

    removed = end_condition(world, actor, ConditionKind.BLESSED)
    assert removed == 2
    assert not world.conditions.require(actor).has(ConditionKind.BLESSED)


def test_condition_round_trips_through_save_dict() -> None:
    original = Condition(
        kind=ConditionKind.BURNING,
        duration=DurationPolicy.rounds(3),
        rounds_remaining=2,
        payload={"damage": 1},
    )

    revived = Condition.from_dict(original.to_dict())
    assert revived == original


def test_condition_store_round_trips() -> None:
    store = ConditionStore()
    store.add(
        Condition(kind=ConditionKind.BLESSED, duration=DurationPolicy.rounds(3))
    )
    store.add(
        Condition(
            kind=ConditionKind.BURNING,
            duration=DurationPolicy.until_removed(),
            payload={"damage": 1},
        )
    )

    revived = ConditionStore.from_dict(store.to_dict())
    assert revived.conditions == store.conditions


def test_apply_condition_effect_routes_through_effect_applier() -> None:
    """The applier wires ApplyCondition / EndCondition correctly."""
    app = create_app()
    actor = app.player

    app.apply_effects(
        [
            ApplyCondition(
                entity=actor,
                condition=Condition(
                    kind=ConditionKind.FRIGHTENED,
                    duration=DurationPolicy.rounds(2),
                ),
            )
        ]
    )

    assert app.world.conditions.require(actor).has(ConditionKind.FRIGHTENED)

    app.apply_effects([EndCondition(entity=actor, kind=ConditionKind.FRIGHTENED)])

    assert not app.world.conditions.require(actor).has(ConditionKind.FRIGHTENED)


def test_round_boundary_tick_runs_burning_through_app() -> None:
    """End-to-end: applying burning + advancing the party round deals damage."""
    app = create_app()
    app.handle_key(ord("y"))  # enter play mode (yolo)
    actor = app.player
    stats = app.world.combat_stats.require(actor)
    before_hp = stats.hit_points

    app.apply_effects(
        [
            ApplyCondition(
                entity=actor,
                condition=Condition(
                    kind=ConditionKind.BURNING,
                    duration=DurationPolicy.rounds(3),
                    payload={"damage": 1},
                ),
            )
        ]
    )

    # Trigger the round boundary tick directly so we don't depend on the
    # full round-rotation choreography (party iteration + enemy phase).
    app._tick_round_boundary()

    assert app.world.combat_stats.require(actor).hit_points == before_hp - 1


def test_observation_surfaces_conditions_on_party_members() -> None:
    from src.ui.observation import observe

    app = create_app()
    app.handle_key(ord("y"))
    actor = app.player

    app.apply_effects(
        [
            ApplyCondition(
                entity=actor,
                condition=Condition(
                    kind=ConditionKind.BLESSED,
                    duration=DurationPolicy.rounds(3),
                ),
            )
        ]
    )

    obs = observe(app)
    me = next(member for member in obs.party if member.id == int(actor))
    assert any(c.kind == "blessed" for c in me.conditions)
    blessed = next(c for c in me.conditions if c.kind == "blessed")
    assert blessed.duration == "rounds"
    assert blessed.rounds_remaining == 3


def test_observation_round_trip_preserves_conditions() -> None:
    from src.ui.observation import Observation, observe

    app = create_app()
    app.handle_key(ord("y"))
    app.apply_effects(
        [
            ApplyCondition(
                entity=app.player,
                condition=Condition(
                    kind=ConditionKind.POISONED,
                    duration=DurationPolicy.until_removed(),
                ),
            )
        ]
    )

    obs = observe(app)
    revived = Observation.from_dict(obs.to_dict())
    me = next(member for member in revived.party if member.id == int(app.player))
    assert any(c.kind == "poisoned" for c in me.conditions)


def test_world_remove_entity_drops_condition_store() -> None:
    world = _world()
    actor = add_actor(world, 2, 2)
    apply_condition(
        world,
        actor,
        Condition(kind=ConditionKind.PRONE, duration=DurationPolicy.until_removed()),
    )
    assert world.conditions.has(actor)

    world.remove_entity(actor)
    assert not world.conditions.has(actor)


def test_duration_policy_rejects_negative_amounts() -> None:
    with pytest.raises(ValueError):
        DurationPolicy.rounds(-1)
    with pytest.raises(ValueError):
        DurationPolicy.turns(-2)
    with pytest.raises(ValueError):
        DurationPolicy.minutes(-3)


def test_concentration_handoff_preserves_unrelated_conditions() -> None:
    world = _world()
    actor = add_actor(world, 2, 2)
    apply_condition(
        world,
        actor,
        Condition(kind=ConditionKind.BLESSED, duration=DurationPolicy.rounds(3)),
    )
    apply_condition(
        world,
        actor,
        Condition(
            kind=ConditionKind.CONCENTRATING,
            duration=DurationPolicy.until_removed(),
            payload={"spell": "bless"},
        ),
    )
    apply_condition(
        world,
        actor,
        Condition(
            kind=ConditionKind.CONCENTRATING,
            duration=DurationPolicy.until_removed(),
            payload={"spell": "haste"},
        ),
    )

    store = world.conditions.require(actor)
    # Unrelated blessed condition survives.
    assert store.has(ConditionKind.BLESSED)
    # Only one concentration entry left, and it's haste.
    concentrations = store.of_kind(ConditionKind.CONCENTRATING)
    assert len(concentrations) == 1
    assert concentrations[0].payload["spell"] == "haste"
