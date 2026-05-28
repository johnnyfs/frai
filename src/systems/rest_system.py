"""Rest system (M34).

Short rests and long rests resolve here. The system is a small set of
pure-ish functions that take the live :class:`~src.app.App`, apply
permission/cost/risk checks against the active
:class:`~src.core.shelter.ShelterZone` at the active actor's tile, and
return a list of effects for the caller to apply.

Why not a dispatcher ``System``?
================================

Rest is a player-initiated, App-aware verb (it touches the time clock,
spell slots, conditions, gold, optional encounter checks) and ends up
mutating multiple component stores at once. Wiring it as a Dispatcher
:class:`~src.core.dispatcher.System` would split the orchestration
between the system and the App handler; instead we expose two
standalone functions that the App calls when the player chooses a
rest kind. They produce typed effects the standard
:class:`~src.core.effects_applier.EffectApplier` consumes.

Refusal contract
----------------

Every refusal path emits exactly one :class:`EmitMessage` and returns
no other effects, so the App can apply the result unconditionally
without worrying about partial state mutations. Successes always end
with a single summary message (e.g. ``"You take a short rest."``)
followed by any per-actor recovery messages.

RNG
---

The encounter check is driven by a caller-supplied
:class:`random.Random`. ``attempt_short_rest`` / ``attempt_long_rest``
take an optional ``rng`` parameter so tests can pin the result; the App
threads its ``loot_rng`` in by default so seeded fixtures stay
deterministic.
"""

from __future__ import annotations

import random
from typing import Iterable

from src.core.conditions import tick_conditions
from src.core.effects import (
    ApplyHealing,
    Effect,
    EmitMessage,
)
from src.core.entity import EntityId
from src.core.modes import PlayMode, UIMode
from src.core.shelter import RestKind, RestRisk
from src.core.time import SECONDS_PER_LONG_REST, SECONDS_PER_SHORT_REST


# Probability a risky encounter-check rest is interrupted, expressed as
# the integer threshold the d20 roll must MEET-OR-EXCEED to succeed.
# A roll < this number interrupts the rest. The constant is exposed so
# tests can compute expected outcomes without re-deriving the cutoff.
ENCOUNTER_CHECK_DC: int = 11


def attempt_short_rest(app, rng: random.Random | None = None) -> list[Effect]:
    """Resolve a short-rest attempt for ``app``'s active party.

    Returns the flat list of effects the caller should apply via the
    standard EffectApplier. The list is empty only when the implementation
    has a bug; every refusal path returns a single :class:`EmitMessage`.
    """

    return _attempt_rest(app, RestKind.SHORT, rng)


def attempt_long_rest(app, rng: random.Random | None = None) -> list[Effect]:
    """Resolve a long-rest attempt for ``app``'s active party.

    See :func:`attempt_short_rest` for the calling contract.
    """

    return _attempt_rest(app, RestKind.LONG, rng)


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


def _attempt_rest(
    app,
    kind: RestKind,
    rng: random.Random | None,
) -> list[Effect]:
    if app.ui_mode is not UIMode.play:
        return [EmitMessage("You cannot rest from this screen.")]

    if app.play_mode is not PlayMode.explore:
        return [EmitMessage("You cannot rest while in combat.")]

    actor = app.active_actor()
    position = app.world.positions.get(actor)
    if position is None:
        return [EmitMessage("You cannot rest right now.")]

    zone = app.world.shelter_zones.at(position.x, position.y)
    if zone is None:
        return [EmitMessage("There is no shelter here to rest in.")]

    permission = zone.rest_permission
    if kind is RestKind.SHORT and not permission.allows_short():
        return [EmitMessage("This shelter does not permit a short rest.")]
    if kind is RestKind.LONG and not permission.allows_long():
        return [EmitMessage("This shelter does not permit a long rest.")]

    if zone.rest_risk is RestRisk.FORBIDDEN:
        return [EmitMessage("It is unsafe to rest here.")]

    # Cost is taken from the active actor's purse. We check before
    # consuming any other state so a refusal leaves the world untouched.
    inventory = app.world.inventories.get(actor)
    if zone.cost > 0:
        if inventory is None or inventory.gold < zone.cost:
            return [EmitMessage(f"You cannot afford the {zone.cost}gp cost.")]

    if zone.requirements:
        missing = _missing_requirements(app, actor, zone.requirements)
        if missing:
            label = ", ".join(missing)
            return [EmitMessage(f"You are missing: {label}.")]

    if zone.uses_remaining is not None and zone.uses_remaining <= 0:
        return [EmitMessage("This shelter has been used up.")]

    # M29: stable PCs (3-success downed actors) restore to 1 HP at the
    # start of every rest, before recovery / refill runs against them.
    # Done before the encounter check so a failed rest still nudges
    # them off the dying state — the SRD lets a stable character regain
    # 1 HP after 1d4 hours of rest, even if interrupted.
    from src.core.death_saves import stabilize_pcs_on_rest

    stabilize_effects = stabilize_pcs_on_rest(app.world, list(app.party.members))

    # All checks passed — commit the rest.
    encounter_rng = rng if rng is not None else getattr(app, "loot_rng", random.Random())
    interrupted = (
        zone.rest_risk is RestRisk.ENCOUNTER_CHECK
        and _encounter_check_interrupted(encounter_rng)
    )

    if interrupted:
        # Risky shelter rolled badly: time still passes (the party tried
        # to bed down), gold is NOT spent (they didn't get the service),
        # and no recovery happens. A future M14/M15 encounter deck will
        # spawn the actual interrupting monster; today we just emit a
        # clear refusal with structured banner text. M29 stable
        # recovery still applies — a downed-but-stable PC regains 1 HP
        # because their state machine fired before the encounter roll.
        _advance_clock_for_kind(app, kind)
        return [*stabilize_effects, EmitMessage(_interruption_message(kind))]

    # Deduct cost first so the message ordering reads as "paid, rested".
    # M29 stable-restore lands before the cost banner so the player
    # sees "X recovers from unconsciousness." before "You pay 5gp."
    effects: list[Effect] = list(stabilize_effects)
    if zone.cost > 0 and inventory is not None:
        inventory.gold -= zone.cost
        effects.append(EmitMessage(f"You pay {zone.cost}gp."))

    # Consume one charge on use-capped shelters. The check above already
    # refused exhausted zones, so this branch only ever decrements.
    if zone.uses_remaining is not None:
        zone.consume_use()

    # Apply recovery to every party member; emit one summary line.
    effects.append(EmitMessage(_summary_message(kind)))
    if kind is RestKind.SHORT:
        _apply_short_rest_recovery(app, effects)
    else:
        _apply_long_rest_recovery(app, effects)

    # Advance the clock and tick any clock-driven conditions. We do this
    # BEFORE clearing UNTIL_REST conditions on a long rest so a
    # MINUTES-policy condition that was already due expires through its
    # normal path rather than getting absorbed by the rest sweep.
    _advance_clock_for_kind(app, kind)

    # Long rest clears every UNTIL_REST condition on every actor that has
    # a condition store. The condition module exposes the ``"long_rest"``
    # boundary directly so we don't reimplement the sweep here.
    if kind is RestKind.LONG:
        cleared_effects = tick_conditions(
            app.world,
            list(app.world.conditions.values.keys()),
            boundary="long_rest",
        )
        # ``tick_conditions`` returns Effect-like instructions; for the
        # ``long_rest`` boundary it's currently always an empty list
        # because UNTIL_REST conditions have no tick handler. We extend
        # defensively so future handlers (e.g. "exhaustion goes down by
        # 1 on a long rest") slot in here without code changes.
        effects.extend(cleared_effects)

    return effects


def _apply_short_rest_recovery(app, effects: list[Effect]) -> None:
    """Restore half of each party member's missing HP.

    SRD short rest spends Hit Dice; we simplify to "you regain up to
    half of missing HP". That keeps the M34 scope tight while still
    making the verb feel useful. M25 (leveling) can revisit this and
    introduce real Hit Dice spend.
    """

    for member in app.party.members:
        stats = app.world.combat_stats.get(member)
        if stats is None:
            continue
        missing = stats.max_hit_points - stats.hit_points
        if missing <= 0:
            continue
        recovered = max(1, missing // 2)
        effects.append(ApplyHealing(member, recovered))


def _apply_long_rest_recovery(app, effects: list[Effect]) -> None:
    """Fully heal every party member and refill spell slots.

    This intentionally does NOT clear UNTIL_REST conditions inline; the
    caller does that through :func:`~src.core.conditions.tick_conditions`
    so the condition-tick driver stays the single source of truth.
    """

    for member in app.party.members:
        stats = app.world.combat_stats.get(member)
        if stats is not None and stats.hit_points < stats.max_hit_points:
            effects.append(
                ApplyHealing(member, stats.max_hit_points - stats.hit_points)
            )
        slots = app.world.spell_slots.get(member)
        if slots is not None:
            # Refill in-place; the EffectApplier ConsumeSpellSlot path
            # mirrors the same store, so direct mutation here is
            # consistent with how the rest of the engine treats the
            # ledger. We don't emit a typed effect for the refill
            # because no other system needs to react to it.
            slots.reset_to_max()


def _advance_clock_for_kind(app, kind: RestKind) -> None:
    """Advance the world clock by the duration of ``kind``.

    Uses :meth:`~src.app.App._tick_world_clock` so any clock-driven
    side-effects (MINUTES condition expirations, scheduled events) fire
    through the same pipeline as a normal turn advance.
    """

    seconds = (
        SECONDS_PER_SHORT_REST if kind is RestKind.SHORT else SECONDS_PER_LONG_REST
    )
    # _tick_world_clock is a private helper but the only sanctioned
    # path that fires clock-driven conditions; we call through it for
    # symmetry with explore-mode moves and the M44 round-boundary tick.
    app._tick_world_clock(seconds)


def _encounter_check_interrupted(rng: random.Random) -> bool:
    """Roll a d20; an interrupt fires on a roll below the DC.

    A single roll keeps the M34 scope tight. M14/M15 will replace this
    with a proper encounter-table draw.
    """

    return rng.randint(1, 20) < ENCOUNTER_CHECK_DC


def _missing_requirements(
    app, actor: EntityId, requirements: Iterable[str]
) -> list[str]:
    """Return the subset of ``requirements`` not held by ``actor``."""

    from src.core.items import has_item

    inventory = app.world.inventories.get(actor)
    if inventory is None:
        return [item_id for item_id in requirements]
    missing: list[str] = []
    for item_id in requirements:
        if not has_item(inventory, item_id, 1):
            missing.append(item_id)
    return missing


def _summary_message(kind: RestKind) -> str:
    if kind is RestKind.SHORT:
        return "You take a short rest."
    return "You take a long rest."


def _interruption_message(kind: RestKind) -> str:
    if kind is RestKind.SHORT:
        return "Your short rest is interrupted by signs of danger."
    return "Your long rest is interrupted by signs of danger."


__all__ = [
    "ENCOUNTER_CHECK_DC",
    "attempt_long_rest",
    "attempt_short_rest",
]
