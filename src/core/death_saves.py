"""Downed actors, death saves, and recovery (M29).

When an actor with :class:`~src.core.components.CombatStats` is reduced
to 0 HP, it does not die outright. Instead the engine applies the SRD
``unconscious`` condition (M24) and seeds a :class:`DeathSaves`
component (M29) that tracks the actor's death-save state machine.

State machine (per SRD-lite):

- HP > 0 ........... normal play.
- HP == 0, dying .. unconscious + DeathSaves attached. The actor rolls a
                    DC-10 CON save at the start of each of their turns.
                    A success increments ``successes``; a failure
                    increments ``failures``. Natural 20 = restore 1 HP +
                    clear unconscious. Natural 1 = +2 failures.
- HP == 0, stable . three successes were rolled. The actor is still
                    unconscious but no longer rolls saves; the next rest
                    restores them to 1 HP.
- dead ............ three failures rolled, or damage dropped the actor
                    below ``-max_hit_points`` (massive damage rule).
                    Routed through the standard :class:`KillEntity`
                    pipeline.

Healing while downed
--------------------

Any positive heal applied to an unconscious actor revives them: HP goes
to the heal amount (capped by max), the unconscious condition is
cleared, and the :class:`DeathSaves` component is removed. This is
implemented in :func:`revive_with_healing` and called by the
``ApplyHealing`` effect handler.

Damage while downed
-------------------

Per SRD, damage on an unconscious actor counts as a death-save failure
(two failures if the damage was a critical hit). M29 keeps this simple:
each :class:`DamageEntity` against an unconscious actor counts as one
failure. Massive damage (amount ≥ max_hit_points) is interpreted as the
crit case and routes through real death.

Save-friendliness
-----------------

:class:`DeathSaves` is a normal component and round-trips through the
World component-store pipeline. The unconscious condition uses the
existing M24 plumbing (``UNTIL_REMOVED`` policy) so save/load already
preserves it. No new save format is introduced.
"""

from __future__ import annotations

import random
from typing import Any

from src.core.checks import AdvantageState, Save, roll_save
from src.core.combat import ability_modifier
from src.core.components import CombatStats, DeathSaves
from src.core.conditions import (
    Condition,
    ConditionKind,
    DurationPolicy,
    apply_condition,
    end_condition,
)
from src.core.effects import (
    ApplyCondition,
    DamageEntity,
    Effect,
    EmitMessage,
    EndCondition,
    KillEntity,
)
from src.core.entity import EntityId


DEATH_SAVE_DC: int = 10
SUCCESSES_TO_STABILIZE: int = 3
FAILURES_TO_DIE: int = 3


def is_unconscious(world: Any, entity: EntityId) -> bool:
    """True iff ``entity`` currently carries the unconscious condition.

    Defensive against worlds that don't have a condition store wired
    (some lightweight test fixtures).
    """
    store = world.conditions.get(entity)
    if store is None:
        return False
    return store.has(ConditionKind.UNCONSCIOUS)


def is_dying(world: Any, entity: EntityId) -> bool:
    """True iff ``entity`` is downed and still rolling (not yet stable)."""
    saves = world.death_saves.get(entity)
    if saves is None:
        return False
    return not saves.stable


def is_stable(world: Any, entity: EntityId) -> bool:
    """True iff ``entity`` is downed but no longer rolling (3 successes)."""
    saves = world.death_saves.get(entity)
    return saves is not None and saves.stable


def begin_downed(world: Any, entity: EntityId) -> list[Effect]:
    """Apply the unconscious condition and seed a :class:`DeathSaves` row.

    Idempotent: if the actor is already unconscious, no new condition is
    applied and the existing :class:`DeathSaves` row is preserved. The
    returned effect list always begins the unconscious condition through
    the standard :class:`ApplyCondition` effect so save/load and the
    M35 observation snapshot stay consistent.
    """

    effects: list[Effect] = []
    if not is_unconscious(world, entity):
        effects.append(
            ApplyCondition(
                entity,
                Condition(
                    kind=ConditionKind.UNCONSCIOUS,
                    duration=DurationPolicy.until_removed(),
                ),
            )
        )
        name = world.name_for(entity)
        subject = "You" if name == "you" else f"The {name}"
        verb = "fall" if name == "you" else "falls"
        effects.append(EmitMessage(f"{subject} {verb} unconscious."))
    if world.death_saves.get(entity) is None:
        world.death_saves.add(entity, DeathSaves())
    return effects


def revive_with_healing(world: Any, entity: EntityId) -> list[Effect]:
    """Clear the downed state when ``entity`` is healed above 0 HP.

    Called from the ApplyHealing effect handler after HP is restored
    (and before the heal message is emitted, so the revival banner
    follows the healing line in player-facing message order).

    Returns the effects the caller should append to the in-flight batch.
    No-op when the entity has neither the unconscious condition nor a
    :class:`DeathSaves` row.
    """

    effects: list[Effect] = []
    had_unconscious = is_unconscious(world, entity)
    had_saves = world.death_saves.get(entity) is not None
    if not (had_unconscious or had_saves):
        return effects
    # End the condition through the standard effect so observers see
    # the same edge they would see for any other condition expiry.
    if had_unconscious:
        effects.append(EndCondition(entity, ConditionKind.UNCONSCIOUS))
    if had_saves:
        # Direct removal — there is no typed effect for "drop a
        # DeathSaves" today and adding one would only be used here.
        world.death_saves.values.pop(entity, None)
    if had_unconscious or had_saves:
        name = world.name_for(entity)
        subject = "You" if name == "you" else f"The {name}"
        verb = "wake" if name == "you" else "wakes"
        effects.append(EmitMessage(f"{subject} {verb} up."))
    return effects


def record_damage_failure(world: Any, entity: EntityId, amount: int) -> list[Effect]:
    """Convert damage on an unconscious actor into a death-save failure.

    The SRD treats damage on a downed character as an automatic failure
    (two failures if the source was a critical hit). M29 uses
    ``amount >= max_hit_points`` as the proxy for the crit branch — any
    blow that would overflow the max HP pool counts as two failures.

    Returns the effects the caller should append to the in-flight batch.
    Resolves to :class:`KillEntity` if the failure tally reaches three.
    """

    saves = world.death_saves.get(entity)
    if saves is None or saves.stable:
        return []
    stats = world.combat_stats.get(entity)
    is_crit = stats is not None and amount >= stats.max_hit_points
    saves.failures += 2 if is_crit else 1
    name = world.name_for(entity)
    subject = "You" if name == "you" else f"The {name}"
    verb_take = "take" if name == "you" else "takes"
    effects: list[Effect] = []
    effects.append(
        EmitMessage(
            f"{subject} {verb_take} damage while down "
            f"({saves.failures}/{FAILURES_TO_DIE} failures)."
        )
    )
    if saves.failures >= FAILURES_TO_DIE:
        verb = "die" if name == "you" else "dies"
        effects.append(EmitMessage(f"{subject} {verb}."))
        effects.append(KillEntity(entity))
    return effects


def roll_death_save(
    world: Any,
    entity: EntityId,
    rng: random.Random,
) -> list[Effect]:
    """Resolve one death save for ``entity``.

    Used by the per-turn driver and the M37 playtest harness. Returns
    the effects the caller should apply (message, possibly an
    :class:`EndCondition` on revival, possibly a :class:`KillEntity` on
    the third failure).
    """

    saves = world.death_saves.get(entity)
    if saves is None or saves.stable:
        return []
    stats = world.combat_stats.get(entity)
    constitution = stats.constitution if stats is not None else 10
    save = Save(
        actor=entity,
        ability="CON",
        ability_modifier=ability_modifier(constitution),
        dc=DEATH_SAVE_DC,
        proficiency=False,
        proficiency_bonus=0,
        modifiers=(),
        advantage_state=AdvantageState.NORMAL,
    )
    result = roll_save(save, rng)
    name = world.name_for(entity)
    subject = "You" if name == "you" else f"The {name}"

    effects: list[Effect] = []

    if result.natural == 20:
        # Crit success: restore 1 HP, clear unconscious, clear saves.
        if stats is not None:
            stats.hit_points = max(1, min(stats.hit_points + 1, stats.max_hit_points))
        world.death_saves.values.pop(entity, None)
        if is_unconscious(world, entity):
            effects.append(EndCondition(entity, ConditionKind.UNCONSCIOUS))
        verb = "wake" if name == "you" else "wakes"
        effects.append(
            EmitMessage(
                f"{subject} roll{'' if name == 'you' else 's'} a natural 20! "
                f"{subject} {verb} with 1 HP."
            )
        )
        return effects

    if result.natural == 1:
        saves.failures += 2
        effects.append(
            EmitMessage(
                f"{subject} roll{'' if name == 'you' else 's'} a natural 1 "
                f"on a death save ({saves.failures}/{FAILURES_TO_DIE} failures)."
            )
        )
    elif result.success:
        saves.successes += 1
        effects.append(
            EmitMessage(
                f"{subject} pass{'' if name == 'you' else 'es'} a death save "
                f"({saves.successes}/{SUCCESSES_TO_STABILIZE} successes)."
            )
        )
    else:
        saves.failures += 1
        effects.append(
            EmitMessage(
                f"{subject} fail{'' if name == 'you' else 's'} a death save "
                f"({saves.failures}/{FAILURES_TO_DIE} failures)."
            )
        )

    if saves.failures >= FAILURES_TO_DIE:
        verb = "die" if name == "you" else "dies"
        effects.append(EmitMessage(f"{subject} {verb}."))
        effects.append(KillEntity(entity))
        return effects
    if saves.successes >= SUCCESSES_TO_STABILIZE:
        saves.stable = True
        verb = "stabilize" if name == "you" else "stabilizes"
        effects.append(EmitMessage(f"{subject} {verb}."))
    return effects


def stabilize_pcs_on_rest(world: Any, party: list[EntityId]) -> list[Effect]:
    """Restore stable PCs to 1 HP at the end of a rest (M29 + M34).

    A stable PC is unconscious but no longer dying. Per the M29 spec,
    the post-rest tick restores them to 1 HP and clears the unconscious
    tag and :class:`DeathSaves` row. Returns the effects the caller
    should append to the in-flight rest batch.
    """

    effects: list[Effect] = []
    for member in party:
        saves = world.death_saves.get(member)
        if saves is None or not saves.stable:
            continue
        stats = world.combat_stats.get(member)
        if stats is not None and stats.hit_points <= 0:
            stats.hit_points = 1
        world.death_saves.values.pop(member, None)
        if is_unconscious(world, member):
            effects.append(EndCondition(member, ConditionKind.UNCONSCIOUS))
        name = world.name_for(member)
        subject = "You" if name == "you" else f"The {name}"
        verb = "recover" if name == "you" else "recovers"
        effects.append(EmitMessage(f"{subject} {verb} from unconsciousness."))
    return effects


def party_wiped(world: Any, party: list[EntityId]) -> bool:
    """True iff every party member is either dead or unconscious.

    Used by the post-effect tick to flip to :class:`UIMode.game_over`.
    A party member is "dead" if they no longer exist in the world (the
    standard ``KillEntity`` path removes them) or carry 0 HP without a
    :class:`DeathSaves` row (defensive fallback for sheets that bypass
    the downed pipeline). They are "unconscious" if they carry the SRD
    condition.
    """

    if not party:
        return False
    for member in party:
        if not world.positions.has(member):
            # Removed from the world — counts as dead/missing.
            continue
        if is_unconscious(world, member):
            continue
        stats = world.combat_stats.get(member)
        if stats is None:
            # No stats → can't be downed; treat as alive.
            return False
        if stats.hit_points > 0:
            return False
    return True


__all__ = [
    "DEATH_SAVE_DC",
    "FAILURES_TO_DIE",
    "SUCCESSES_TO_STABILIZE",
    "begin_downed",
    "is_dying",
    "is_stable",
    "is_unconscious",
    "party_wiped",
    "record_damage_failure",
    "revive_with_healing",
    "roll_death_save",
    "stabilize_pcs_on_rest",
]
