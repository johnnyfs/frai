"""Tests for the M23 stealth/perception/noise pipeline.

Covers:

- :class:`AwarenessTracker` ramp + save round-trip.
- :func:`propagate_noise` ramps nearby hostiles and ignores
  friends / out-of-range observers / silent actions.
- :func:`is_aware_of` honors the ``hidden`` condition and the
  per-observer tracker.
- :class:`StealthSystem` resolves sneak / perception with seeded RNG.
- Attack and spell casts ramp nearby hostiles to AWARE.
- The AI's target picker skips a sneaking party member it hasn't
  perceived.
"""

from __future__ import annotations

import random

from src.core.actions import (
    AttackAttempt,
    PerceptionAttempt,
    SneakAttempt,
)
from src.core.conditions import (
    Condition,
    ConditionKind,
    ConditionStore,
    DurationPolicy,
    apply_condition,
)
from src.core.effects import ApplyCondition, EmitMessage, EndCondition
from src.core.stealth import (
    AwarenessState,
    AwarenessTracker,
    DEFAULT_NOISE_RADIUS,
    NoiseLevel,
    get_or_create_tracker,
    propagate_noise,
)
from src.systems.ai_system import EnemyAISystem
from src.systems.awareness_system import is_aware_of
from src.systems.combat_system import CombatSystem
from src.systems.stealth_system import StealthSystem
from tests.support.tiny_world import (
    SequenceRng,
    add_enemy,
    build_tiny_encounter,
    build_tiny_party_world,
)


# ---------------------------------------------------------------------------
# AwarenessTracker
# ---------------------------------------------------------------------------


def test_awareness_tracker_defaults_to_unaware() -> None:
    fixture = build_tiny_encounter()
    tracker = AwarenessTracker()

    assert tracker.state_of(fixture.player) is AwarenessState.UNAWARE


def test_awareness_tracker_ramps_one_way() -> None:
    fixture = build_tiny_encounter()
    tracker = AwarenessTracker()

    assert tracker.ramp_to_at_least(fixture.player, AwarenessState.SUSPICIOUS) is True
    assert tracker.state_of(fixture.player) is AwarenessState.SUSPICIOUS

    # Re-ramping to SUSPICIOUS is a no-op (returns False).
    assert tracker.ramp_to_at_least(fixture.player, AwarenessState.SUSPICIOUS) is False

    # Upgrading to AWARE wins.
    assert tracker.ramp_to_at_least(fixture.player, AwarenessState.AWARE) is True
    assert tracker.state_of(fixture.player) is AwarenessState.AWARE

    # Downgrading is rejected.
    assert tracker.ramp_to_at_least(fixture.player, AwarenessState.SUSPICIOUS) is False
    assert tracker.state_of(fixture.player) is AwarenessState.AWARE


def test_awareness_tracker_round_trips_through_dict() -> None:
    fixture = build_tiny_encounter()
    tracker = AwarenessTracker()
    tracker.set_state(fixture.player, AwarenessState.AWARE)
    tracker.set_state(fixture.companion, AwarenessState.SUSPICIOUS)

    rehydrated = AwarenessTracker.from_dict(tracker.to_dict())
    assert rehydrated.state_of(fixture.player) is AwarenessState.AWARE
    assert rehydrated.state_of(fixture.companion) is AwarenessState.SUSPICIOUS


# ---------------------------------------------------------------------------
# propagate_noise
# ---------------------------------------------------------------------------


def test_propagate_noise_ramps_nearby_hostile_to_aware() -> None:
    fixture = build_tiny_encounter()
    tracker = get_or_create_tracker(fixture.world, fixture.enemy)

    updated = propagate_noise(fixture.world, fixture.player, NoiseLevel.LOUD)

    assert fixture.enemy in updated
    assert tracker.state_of(fixture.player) is AwarenessState.AWARE


def test_propagate_noise_quiet_only_ramps_to_suspicious() -> None:
    fixture = build_tiny_encounter()
    tracker = get_or_create_tracker(fixture.world, fixture.enemy)

    propagate_noise(fixture.world, fixture.player, NoiseLevel.QUIET)

    assert tracker.state_of(fixture.player) is AwarenessState.SUSPICIOUS


def test_propagate_noise_silent_is_noop() -> None:
    fixture = build_tiny_encounter()
    tracker = get_or_create_tracker(fixture.world, fixture.enemy)

    propagate_noise(fixture.world, fixture.player, NoiseLevel.SILENT)

    assert tracker.state_of(fixture.player) is AwarenessState.UNAWARE


def test_propagate_noise_ignores_friendly_observers() -> None:
    """Friends/neutrals don't ramp on player noise."""
    fixture = build_tiny_party_world()
    # The companion shares the player faction; the noise should not
    # ramp them even with a tracker installed.
    tracker = get_or_create_tracker(fixture.world, fixture.companion)

    updated = propagate_noise(fixture.world, fixture.player, NoiseLevel.LOUD)

    assert fixture.companion not in updated
    assert tracker.state_of(fixture.player) is AwarenessState.UNAWARE


def test_propagate_noise_skips_out_of_range_hostiles() -> None:
    fixture = build_tiny_party_world()
    # Spawn the hostile far enough away that DEFAULT_NOISE_RADIUS doesn't
    # reach. We move the player to (1, 1) and place the enemy at
    # (1 + radius + 1, 1) which is just outside earshot.
    fixture.world.positions.require(fixture.player).x = 1
    fixture.world.positions.require(fixture.player).y = 1
    enemy = add_enemy(
        fixture.world, 1 + DEFAULT_NOISE_RADIUS + 1, 1, name="distant-orc"
    )
    tracker = get_or_create_tracker(fixture.world, enemy)

    updated = propagate_noise(fixture.world, fixture.player, NoiseLevel.LOUD)

    assert enemy not in updated
    assert tracker.state_of(fixture.player) is AwarenessState.UNAWARE


# ---------------------------------------------------------------------------
# is_aware_of (LOS + tracker + hidden condition)
# ---------------------------------------------------------------------------


def test_is_aware_of_returns_false_for_hidden_target() -> None:
    fixture = build_tiny_encounter()
    apply_condition(
        fixture.world,
        fixture.player,
        Condition(kind=ConditionKind.HIDDEN, duration=DurationPolicy.until_removed()),
    )

    assert is_aware_of(fixture.world, fixture.enemy, fixture.player) is False


def test_is_aware_of_respects_observer_tracker_aware_overrides_hidden() -> None:
    """Once the observer is aware, the hidden tag no longer fools them."""
    fixture = build_tiny_encounter()
    apply_condition(
        fixture.world,
        fixture.player,
        Condition(kind=ConditionKind.HIDDEN, duration=DurationPolicy.until_removed()),
    )
    tracker = get_or_create_tracker(fixture.world, fixture.enemy)
    tracker.set_state(fixture.player, AwarenessState.AWARE)

    assert is_aware_of(fixture.world, fixture.enemy, fixture.player) is True


def test_is_aware_of_visible_unhidden_target_remains_visible() -> None:
    fixture = build_tiny_encounter()

    assert is_aware_of(fixture.world, fixture.enemy, fixture.player) is True


# ---------------------------------------------------------------------------
# StealthSystem.SneakAttempt
# ---------------------------------------------------------------------------


def test_sneak_attempt_success_applies_hidden_condition() -> None:
    fixture = build_tiny_encounter()
    # Roll a 20 -- guaranteed pass against DC 10.
    system = StealthSystem(rng=SequenceRng([20]))

    result = system.handle(SneakAttempt(actor=fixture.player), fixture.world)

    assert result.cancel is True
    assert any(
        isinstance(effect, ApplyCondition)
        and effect.entity == fixture.player
        and effect.condition.kind is ConditionKind.HIDDEN
        for effect in result.effects
    )


def test_sneak_attempt_failure_emits_end_condition_and_message() -> None:
    fixture = build_tiny_encounter()
    # Roll a 1 -- guaranteed fail.
    system = StealthSystem(rng=SequenceRng([1]))

    result = system.handle(SneakAttempt(actor=fixture.player), fixture.world)

    assert any(
        isinstance(effect, EndCondition)
        and effect.entity == fixture.player
        and effect.kind is ConditionKind.HIDDEN
        for effect in result.effects
    )
    assert any(
        isinstance(effect, EmitMessage) and "fail to hide" in effect.text
        for effect in result.effects
    )


def test_sneak_attempt_failure_strips_existing_hidden_tag() -> None:
    fixture = build_tiny_encounter()
    apply_condition(
        fixture.world,
        fixture.player,
        Condition(kind=ConditionKind.HIDDEN, duration=DurationPolicy.until_removed()),
    )
    system = StealthSystem(rng=SequenceRng([1]))

    result = system.handle(SneakAttempt(actor=fixture.player), fixture.world)

    # The system emits an EndCondition; once the EffectApplier runs it
    # the tag is gone. We assert on the effect rather than running the
    # applier so the test stays pure to the system contract.
    assert any(
        isinstance(effect, EndCondition)
        and effect.entity == fixture.player
        and effect.kind is ConditionKind.HIDDEN
        for effect in result.effects
    )


# ---------------------------------------------------------------------------
# StealthSystem.PerceptionAttempt
# ---------------------------------------------------------------------------


def test_perception_attempt_failure_leaves_hidden_alone() -> None:
    fixture = build_tiny_encounter()
    apply_condition(
        fixture.world,
        fixture.enemy,
        Condition(kind=ConditionKind.HIDDEN, duration=DurationPolicy.until_removed()),
    )
    system = StealthSystem(rng=SequenceRng([1]))

    result = system.handle(PerceptionAttempt(actor=fixture.player), fixture.world)

    assert not any(isinstance(e, EndCondition) for e in result.effects)
    # The enemy should still be hidden after the failed roll.
    store = fixture.world.conditions.get(fixture.enemy)
    assert store is not None and store.has(ConditionKind.HIDDEN)


def test_perception_attempt_success_reveals_hidden_in_los() -> None:
    fixture = build_tiny_encounter()
    apply_condition(
        fixture.world,
        fixture.enemy,
        Condition(kind=ConditionKind.HIDDEN, duration=DurationPolicy.until_removed()),
    )
    system = StealthSystem(rng=SequenceRng([20]))

    result = system.handle(PerceptionAttempt(actor=fixture.player), fixture.world)

    assert any(
        isinstance(effect, EndCondition)
        and effect.entity == fixture.enemy
        and effect.kind is ConditionKind.HIDDEN
        for effect in result.effects
    )
    # And the observer's tracker is now AWARE about the spotted enemy.
    tracker = fixture.world.awareness_trackers.get(fixture.player)
    assert tracker is not None
    assert tracker.state_of(fixture.enemy) is AwarenessState.AWARE


def test_perception_attempt_success_with_no_hidden_emits_clear_message() -> None:
    fixture = build_tiny_encounter()
    system = StealthSystem(rng=SequenceRng([20]))

    result = system.handle(PerceptionAttempt(actor=fixture.player), fixture.world)

    assert any(
        isinstance(effect, EmitMessage) and "nothing hidden" in effect.text
        for effect in result.effects
    )


# ---------------------------------------------------------------------------
# Noise from combat / spells
# ---------------------------------------------------------------------------


def test_attack_emits_noise_ramping_nearby_hostiles_to_aware() -> None:
    fixture = build_tiny_encounter()
    # A second hostile that's not the AttackAttempt target — they
    # should still hear the swing and ramp to AWARE about the attacker.
    bystander = add_enemy(fixture.world, 4, 3, name="orc-bystander")
    tracker = get_or_create_tracker(fixture.world, bystander)

    combat = CombatSystem(rng=random.Random(0))
    combat.resolve_attack(
        AttackAttempt(actor=fixture.player, target=fixture.enemy),
        fixture.world,
    )

    assert tracker.state_of(fixture.player) is AwarenessState.AWARE


def test_attack_clears_attacker_hidden_tag() -> None:
    fixture = build_tiny_encounter()
    apply_condition(
        fixture.world,
        fixture.player,
        Condition(kind=ConditionKind.HIDDEN, duration=DurationPolicy.until_removed()),
    )
    combat = CombatSystem(rng=random.Random(0))

    effects = combat.resolve_attack(
        AttackAttempt(actor=fixture.player, target=fixture.enemy),
        fixture.world,
    )

    assert any(
        isinstance(effect, EndCondition)
        and effect.entity == fixture.player
        and effect.kind is ConditionKind.HIDDEN
        for effect in effects
    )


# ---------------------------------------------------------------------------
# AI integration
# ---------------------------------------------------------------------------


def test_enemy_ai_skips_sneaking_party_members() -> None:
    """A sneaking party member is invisible to the AI target picker."""
    fixture = build_tiny_encounter()
    apply_condition(
        fixture.world,
        fixture.player,
        Condition(kind=ConditionKind.HIDDEN, duration=DurationPolicy.until_removed()),
    )
    apply_condition(
        fixture.world,
        fixture.companion,
        Condition(kind=ConditionKind.HIDDEN, duration=DurationPolicy.until_removed()),
    )

    # Pre-condition: the enemy can't see either party member.
    assert is_aware_of(fixture.world, fixture.enemy, fixture.player) is False
    assert is_aware_of(fixture.world, fixture.enemy, fixture.companion) is False

    applied_effects: list = []

    def _apply(effects):
        applied_effects.extend(effects)

    ai = EnemyAISystem(rng=random.Random(0))
    ai.activate_enemy(fixture.world, fixture.enemy, fixture.party, _apply)

    # No attack / move effects should have been emitted — the enemy
    # didn't find a target.
    assert applied_effects == []


def test_enemy_ai_targets_visible_party_member_when_one_is_hidden() -> None:
    """The AI still chases the visible companion when the player sneaks."""
    fixture = build_tiny_encounter()
    apply_condition(
        fixture.world,
        fixture.player,
        Condition(kind=ConditionKind.HIDDEN, duration=DurationPolicy.until_removed()),
    )
    # Companion stays visible.

    applied_effects: list = []

    def _apply(effects):
        applied_effects.extend(effects)

    ai = EnemyAISystem(rng=random.Random(0))
    ai.activate_enemy(fixture.world, fixture.enemy, fixture.party, _apply)

    # The AI produced *some* effect (attack or move) since the visible
    # companion is a valid target. We don't pin the exact effect to
    # keep the test resilient to AI tweaks.
    assert applied_effects, "AI should have acted against the visible companion."


# ---------------------------------------------------------------------------
# Save / load
# ---------------------------------------------------------------------------


def test_awareness_tracker_survives_world_round_trip() -> None:
    fixture = build_tiny_encounter()
    tracker = get_or_create_tracker(fixture.world, fixture.enemy)
    tracker.set_state(fixture.player, AwarenessState.AWARE)

    payload = fixture.world.to_dict()
    from src.core.world import World

    rebuilt = World.from_dict(payload)
    rebuilt_tracker = rebuilt.awareness_trackers.get(fixture.enemy)
    assert rebuilt_tracker is not None
    assert rebuilt_tracker.state_of(fixture.player) is AwarenessState.AWARE


# ---------------------------------------------------------------------------
# Defensive: store presence
# ---------------------------------------------------------------------------


def test_world_exposes_awareness_trackers_store() -> None:
    fixture = build_tiny_party_world()
    assert hasattr(fixture.world, "awareness_trackers")
    # Default factory yields an empty store.
    assert fixture.world.awareness_trackers.values == {}


def test_hidden_actor_without_observer_tracker_still_invisible() -> None:
    """A hostile with no tracker treats hidden as fully hidden."""
    fixture = build_tiny_encounter()
    # The fixture's enemy has no tracker installed; the default branch
    # in is_aware_of must still consult the hidden condition.
    assert fixture.world.awareness_trackers.get(fixture.enemy) is None
    fixture.world.conditions.add(fixture.player, ConditionStore())
    fixture.world.conditions.require(fixture.player).add(
        Condition(kind=ConditionKind.HIDDEN, duration=DurationPolicy.until_removed())
    )

    assert is_aware_of(fixture.world, fixture.enemy, fixture.player) is False
