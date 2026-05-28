"""Tests for the M46 ActionContext / ActionResolver phased pipeline."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from src.core.action_context import (
    ActionContext,
    ActionResolver,
    Phase,
    PhaseOutcome,
    ResolvedAttempt,
    make_default_resolver,
)
from src.core.actions import AttackAttempt, MoveAttempt
from src.core.dispatcher import Dispatcher
from src.core.effects import DamageEntity, EmitMessage, MoveEntity
from src.core.entity import EntityId

from tests.support.tiny_world import build_tiny_encounter


# ---------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------


def test_action_context_is_frozen() -> None:
    fixture = build_tiny_encounter()
    action = MoveAttempt(fixture.player, 0, 1)
    context = ActionContext(
        actor=fixture.player, action=action, world=fixture.world, turn=None
    )
    with pytest.raises(FrozenInstanceError):
        context.action = MoveAttempt(fixture.player, 1, 0)  # type: ignore[misc]


def test_resolved_attempt_is_frozen() -> None:
    fixture = build_tiny_encounter()
    context = ActionContext(
        actor=fixture.player,
        action=MoveAttempt(fixture.player, 0, 1),
        world=fixture.world,
    )
    attempt = ResolvedAttempt(original=context)
    with pytest.raises(FrozenInstanceError):
        attempt.cancelled = True  # type: ignore[misc]


def test_phase_outcome_is_frozen() -> None:
    outcome = PhaseOutcome()
    with pytest.raises(FrozenInstanceError):
        outcome.cancel = True  # type: ignore[misc]


# ---------------------------------------------------------------------
# Phase walking
# ---------------------------------------------------------------------


def _make_context(world, actor) -> ActionContext:
    return ActionContext(
        actor=actor, action=MoveAttempt(actor, 0, 0), world=world, turn=None
    )


def test_resolver_walks_all_phases_in_order() -> None:
    fixture = build_tiny_encounter()
    resolver = ActionResolver()
    visited: list[Phase] = []
    for phase in Phase:
        # Bind ``phase`` defensively so the lambda captures the loop value.
        def recorder(_ctx, phase=phase) -> PhaseOutcome:
            visited.append(phase)
            return PhaseOutcome()

        resolver.register(phase, recorder)
    resolver.resolve(_make_context(fixture.world, fixture.player))
    assert visited == list(Phase)


def test_resolver_collects_effects_from_each_phase() -> None:
    fixture = build_tiny_encounter()
    resolver = ActionResolver()

    def emit_in_resolve(_ctx) -> PhaseOutcome:
        return PhaseOutcome(effects=(EmitMessage("hello"),))

    def emit_in_post(_ctx) -> PhaseOutcome:
        return PhaseOutcome(effects=(EmitMessage("world"),))

    resolver.register(Phase.RESOLVE, emit_in_resolve)
    resolver.register(Phase.POST_RESOLVE, emit_in_post)

    attempt = resolver.resolve(_make_context(fixture.world, fixture.player))
    assert [e.text for e in attempt.effects if isinstance(e, EmitMessage)] == [
        "hello",
        "world",
    ]


# ---------------------------------------------------------------------
# Replacement
# ---------------------------------------------------------------------


def test_replacement_in_propose_restarts_pipeline_with_new_action() -> None:
    fixture = build_tiny_encounter()
    resolver = ActionResolver()
    seen_actions: list[type] = []

    def record_action(ctx: ActionContext) -> PhaseOutcome:
        seen_actions.append(type(ctx.action))
        return PhaseOutcome()

    def propose_replacement(ctx: ActionContext) -> PhaseOutcome:
        if isinstance(ctx.action, MoveAttempt):
            return PhaseOutcome(
                replacement=AttackAttempt(ctx.actor, fixture.enemy),
            )
        return PhaseOutcome()

    resolver.register(Phase.PRE_CHECK, record_action)
    resolver.register(Phase.PROPOSE, propose_replacement)

    context = ActionContext(
        actor=fixture.player,
        action=MoveAttempt(fixture.player, 1, 0),
        world=fixture.world,
    )
    attempt = resolver.resolve(context)
    # PRE_CHECK saw MoveAttempt first, then AttackAttempt after the
    # propose-phase replacement restarted the pipeline.
    assert seen_actions == [MoveAttempt, AttackAttempt]
    assert attempt.replacement is not None
    assert isinstance(attempt.replacement, AttackAttempt)


def test_runaway_replacement_chain_is_bounded() -> None:
    """A handler that keeps proposing replacements does not loop forever."""

    fixture = build_tiny_encounter()
    resolver = ActionResolver(max_replacement_chain=3)
    call_count = 0

    def loop_forever(ctx: ActionContext) -> PhaseOutcome:
        nonlocal call_count
        call_count += 1
        return PhaseOutcome(
            replacement=MoveAttempt(ctx.actor, 0, 0),
        )

    resolver.register(Phase.PROPOSE, loop_forever)
    attempt = resolver.resolve(_make_context(fixture.world, fixture.player))
    # The chain stops at the configured depth and reports the attempt
    # as cancelled rather than hanging.
    assert attempt.cancelled is True
    assert call_count <= resolver.max_replacement_chain + 1


# ---------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------


def test_cancel_in_pre_check_short_circuits_resolve_phase() -> None:
    fixture = build_tiny_encounter()
    resolver = ActionResolver()
    resolve_ran = False

    def cancel_in_pre(_ctx) -> PhaseOutcome:
        return PhaseOutcome(
            effects=(EmitMessage("not allowed"),), cancel=True
        )

    def resolve_should_skip(_ctx) -> PhaseOutcome:
        nonlocal resolve_ran
        resolve_ran = True
        return PhaseOutcome(effects=(EmitMessage("should not run"),))

    resolver.register(Phase.PRE_CHECK, cancel_in_pre)
    resolver.register(Phase.RESOLVE, resolve_should_skip)

    attempt = resolver.resolve(_make_context(fixture.world, fixture.player))
    assert resolve_ran is False
    assert attempt.cancelled is True
    messages = [e.text for e in attempt.effects if isinstance(e, EmitMessage)]
    assert messages == ["not allowed"]


def test_cancel_still_runs_post_resolve_for_observers() -> None:
    fixture = build_tiny_encounter()
    resolver = ActionResolver()
    post_ran = False

    def cancel_in_pre(_ctx) -> PhaseOutcome:
        return PhaseOutcome(cancel=True)

    def post(_ctx) -> PhaseOutcome:
        nonlocal post_ran
        post_ran = True
        return PhaseOutcome()

    resolver.register(Phase.PRE_CHECK, cancel_in_pre)
    resolver.register(Phase.POST_RESOLVE, post)

    resolver.resolve(_make_context(fixture.world, fixture.player))
    assert post_ran is True


# ---------------------------------------------------------------------
# Reaction hooks
# ---------------------------------------------------------------------


def test_reaction_hook_fires_post_resolve_and_can_emit_effects() -> None:
    fixture = build_tiny_encounter()
    resolver = ActionResolver()

    def emit_damage(_ctx) -> PhaseOutcome:
        return PhaseOutcome(
            effects=(DamageEntity(fixture.enemy, 3),),
        )

    resolver.register(Phase.RESOLVE, emit_damage)

    # A hook that reacts only when damage was dealt. Mirrors the kind
    # of hook M29 (downed) will register: scan effects for damage, emit
    # a follow-up event.
    def damage_reaction(attempt: ResolvedAttempt) -> list:
        for effect in attempt.effects:
            if isinstance(effect, DamageEntity):
                return [EmitMessage(f"reacted to {effect.amount} damage")]
        return []

    resolver.add_reaction(damage_reaction)
    attempt = resolver.resolve(_make_context(fixture.world, fixture.player))
    messages = [e.text for e in attempt.effects if isinstance(e, EmitMessage)]
    assert messages == ["reacted to 3 damage"]


def test_reaction_hook_sees_attempt_cancellation_state() -> None:
    fixture = build_tiny_encounter()
    resolver = ActionResolver()

    def cancel(_ctx) -> PhaseOutcome:
        return PhaseOutcome(cancel=True)

    resolver.register(Phase.PRE_CHECK, cancel)
    seen: list[bool] = []

    def hook(attempt: ResolvedAttempt) -> list:
        seen.append(attempt.cancelled)
        return []

    resolver.add_reaction(hook)
    resolver.resolve(_make_context(fixture.world, fixture.player))
    assert seen == [True]


def test_reaction_hook_default_resolver_starts_empty() -> None:
    """Production wiring registers no hooks — M11/M24/M29 add theirs."""
    fixture = build_tiny_encounter()
    dispatcher = Dispatcher(systems=[])
    resolver = make_default_resolver(dispatcher)
    assert resolver.reaction_hooks == []


# ---------------------------------------------------------------------
# Default dispatcher integration
# ---------------------------------------------------------------------


def test_default_resolver_delegates_resolve_phase_to_dispatcher() -> None:
    """Production behavior: the dispatcher's effect list is the resolver's
    resolve-phase output. This is what guarantees behavior preservation
    against the legacy ``dispatcher.dispatch`` path."""
    from tests.support.tiny_world import build_action_dispatcher

    fixture = build_tiny_encounter()
    dispatcher = build_action_dispatcher()
    resolver = make_default_resolver(dispatcher)

    action = MoveAttempt(fixture.player, 0, 1)
    context = ActionContext(
        actor=fixture.player, action=action, world=fixture.world
    )
    attempt = resolver.resolve(context)
    moves = [e for e in attempt.effects if isinstance(e, MoveEntity)]
    assert len(moves) == 1
    assert moves[0].entity == fixture.player


def test_app_resolve_action_matches_dispatcher_output_for_moves() -> None:
    """Behavior preservation smoke test: routing a MoveAttempt through
    the resolver produces the same MoveEntity effect the bare dispatcher
    would. Failures here mean the M46 wiring perturbs gameplay."""
    from src.app import create_app

    app = create_app()
    actor = app.active_actor()
    # The default world places the player far from any wall, so a
    # neutral direction always produces a MoveEntity.
    action = MoveAttempt(actor=actor, dx=1, dy=0)
    resolver_effects = app.resolve_action(action)
    legacy_effects = app.dispatcher.dispatch(action, app.world)
    assert [type(e) for e in resolver_effects] == [
        type(e) for e in legacy_effects
    ]


# ---------------------------------------------------------------------
# Phase ordering invariants
# ---------------------------------------------------------------------


def test_phase_enum_order_matches_pipeline_documentation() -> None:
    """The pipeline contract: pre_check -> propose -> resolve -> apply_effects -> post_resolve."""
    assert list(Phase) == [
        Phase.PRE_CHECK,
        Phase.PROPOSE,
        Phase.RESOLVE,
        Phase.APPLY_EFFECTS,
        Phase.POST_RESOLVE,
    ]


def test_resolved_attempt_preserves_original_context() -> None:
    fixture = build_tiny_encounter()
    resolver = ActionResolver()
    context = _make_context(fixture.world, fixture.player)
    attempt = resolver.resolve(context)
    assert attempt.original is context
