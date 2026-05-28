"""Stealth and perception resolver (M23).

Turns :class:`~src.core.actions.SneakAttempt` and
:class:`~src.core.actions.PerceptionAttempt` into the flat list of
effects the M46 resolver applies. The actual ramp/check arithmetic
lives here; the data catalog (states, noise levels, propagation) lives
in :mod:`src.core.stealth`.

Design constraints
------------------

- **RNG is injected.** Pinned random for tests; an unseeded default in
  production wiring. Mirrors the M9 / M11 systems.
- **Conditions, not flags.** Hidden is a :class:`ConditionKind`, so
  the M24 save-friendliness and observation projection get it for
  free. Stealth fail clears any prior hidden so the actor can't stack
  failed attempts to stay hidden indefinitely.
- **Perception writes the active actor's tracker.** Spotting a hidden
  creature sets the active actor's :class:`AwarenessTracker` state to
  ``aware`` *and* strips the global ``hidden`` tag from the spotted
  creature. M23 doesn't yet model "hidden from observer A but not
  observer B" — that's a deliberate scope cut, the per-observer split
  ships when the per-target stealth roll lands in a follow-up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import random

from src.core.actions import Action, PerceptionAttempt, SneakAttempt
from src.core.checks import AdvantageState, Check, roll_check
from src.core.combat import ability_modifier
from src.core.conditions import Condition, ConditionKind, DurationPolicy
from src.core.dispatcher import DispatchResult
from src.core.effects import (
    ApplyCondition,
    Effect,
    EmitMessage,
    EndCondition,
)
from src.core.entity import EntityId
from src.core.stealth import (
    AwarenessState,
    get_or_create_tracker,
)
from src.core.world import World


# Skill names that confer proficiency for each check (matches the M5
# character-creation SKILLS catalog).
_STEALTH_ABILITY = "DEX"
_STEALTH_SKILL = "Stealth"
_PERCEPTION_ABILITY = "WIS"
_PERCEPTION_SKILL = "Perception"


@dataclass(slots=True)
class StealthSystem:
    """Resolve :class:`SneakAttempt` and :class:`PerceptionAttempt`.

    Routes through the standard :class:`DispatchResult` shape so the
    dispatcher / M46 resolver doesn't need any stealth-specific glue.
    """

    rng: random.Random = field(default_factory=random.Random)

    def handle(self, action: Action, world: World) -> DispatchResult:
        if isinstance(action, SneakAttempt):
            return DispatchResult(
                effects=self._resolve_sneak(action, world), cancel=True
            )
        if isinstance(action, PerceptionAttempt):
            return DispatchResult(
                effects=self._resolve_perception(action, world), cancel=True
            )
        return DispatchResult()

    # ------------------------------------------------------------------
    # SneakAttempt
    # ------------------------------------------------------------------

    def _resolve_sneak(self, action: SneakAttempt, world: World) -> list[Effect]:
        actor = action.actor
        ability_score = _ability_score(world, actor, _STEALTH_ABILITY)
        if ability_score is None:
            return [EmitMessage("You can't focus on stealth right now.")]
        success = _roll(
            world,
            actor,
            ability=_STEALTH_ABILITY,
            skill=_STEALTH_SKILL,
            dc=action.dc,
            rng=self.rng,
        )
        actor_subject = _subject(world, actor)
        if success:
            condition = Condition(
                kind=ConditionKind.HIDDEN,
                duration=DurationPolicy.until_removed(),
                source=actor,
            )
            return [
                ApplyCondition(actor, condition),
                EmitMessage(f"{actor_subject} slip into the shadows."),
            ]
        # Failed sneak: if the actor was already hidden, the attempt
        # also blows their cover. The check fails the same way either
        # way; the EndCondition is a no-op when there was no prior
        # hidden tag.
        return [
            EndCondition(actor, ConditionKind.HIDDEN),
            EmitMessage(f"{actor_subject} fail to hide."),
        ]

    # ------------------------------------------------------------------
    # PerceptionAttempt
    # ------------------------------------------------------------------

    def _resolve_perception(
        self, action: PerceptionAttempt, world: World
    ) -> list[Effect]:
        actor = action.actor
        ability_score = _ability_score(world, actor, _PERCEPTION_ABILITY)
        if ability_score is None:
            return [EmitMessage("You can't focus on your surroundings.")]

        success = _roll(
            world,
            actor,
            ability=_PERCEPTION_ABILITY,
            skill=_PERCEPTION_SKILL,
            dc=action.dc,
            rng=self.rng,
        )
        actor_subject = _subject(world, actor)
        if not success:
            return [EmitMessage(f"{actor_subject} fail to notice anything new.")]

        # Determine what's currently visible to the actor. The
        # perception check is geometry-aware: a hidden creature behind
        # a wall stays hidden regardless of the roll.
        visible_cells = _visible_cells(world, actor)
        hidden_targets: list[EntityId] = []
        for entity, store in world.conditions.values.items():
            if entity == actor or not store.has(ConditionKind.HIDDEN):
                continue
            position = world.positions.get(entity)
            if position is None:
                continue
            if (position.x, position.y) not in visible_cells:
                continue
            hidden_targets.append(entity)

        if not hidden_targets:
            return [EmitMessage(f"{actor_subject} scan the area — nothing hidden.")]

        tracker = get_or_create_tracker(world, actor)
        for target in hidden_targets:
            tracker.set_state(target, AwarenessState.AWARE)

        effects: list[Effect] = []
        for target in hidden_targets:
            effects.append(EndCondition(target, ConditionKind.HIDDEN))
            name = world.name_for(target)
            effects.append(EmitMessage(f"You spot {name}!"))
        return effects


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ability_score(world: World, actor: EntityId, ability: str) -> int | None:
    """Return the actor's score for ``ability`` (sheet first, stats next).

    Falls back to the M24 ``CombatStats`` mapping for the three saved
    abilities (STR/DEX/CON) when the actor has no character sheet.
    Returns ``None`` when neither source has the score — the caller
    emits a refusal message rather than crashing.
    """

    character = world.characters.get(actor)
    if character is not None:
        score = character.sheet.attributes.get(ability)
        if score is not None:
            return int(score)
    stats = world.combat_stats.get(actor)
    if stats is None:
        return None
    return {
        "STR": stats.strength,
        "DEX": stats.dexterity,
        "CON": stats.constitution,
        # The other three abilities live only on the sheet; for the
        # M23 perception fallback (a sheet-less hostile making a check)
        # we approximate WIS / INT / CHA at 10. Real values come from
        # the character sheet branch above.
        "WIS": 10,
        "INT": 10,
        "CHA": 10,
    }.get(ability)


def _roll(
    world: World,
    actor: EntityId,
    *,
    ability: str,
    skill: str,
    dc: int,
    rng: random.Random,
) -> bool:
    """Roll a skill check using the same plumbing as M9 interactions.

    Picks proficiency from the character sheet when available;
    sheet-less actors roll without proficiency. The proficiency bonus
    comes from :class:`CombatStats` (or the default ``2`` when stats
    are missing — vanishingly rare given that this path requires combat
    stats for the ability score anyway).
    """

    score = _ability_score(world, actor, ability) or 10
    character = world.characters.get(actor)
    proficient = bool(character is not None and skill in character.sheet.skills)
    stats = world.combat_stats.get(actor)
    proficiency_bonus = stats.proficiency_bonus if stats is not None else 2
    check = Check(
        actor=actor,
        ability=ability,
        ability_modifier=ability_modifier(score),
        dc=dc,
        proficiency=proficient,
        proficiency_bonus=proficiency_bonus,
        advantage_state=AdvantageState.NORMAL,
    )
    return roll_check(check, rng).success


def _visible_cells(world: World, actor: EntityId) -> set[tuple[int, int]]:
    """Return the actor's currently-visible cells via the M19 LOS walker.

    Pure function; no party-memory side effect. The radius is the
    M19 default — the same radius the App uses for vision ticks — so
    perception and vision agree on "what can I see right now".
    """

    from src.core.vision import DEFAULT_VISION_RADIUS, compute_visible_tiles

    return compute_visible_tiles(world, actor, radius=DEFAULT_VISION_RADIUS)


def _subject(world: World, actor: EntityId) -> str:
    """Format an actor as a sentence subject (``You`` / ``The orc``)."""

    name = world.name_for(actor)
    return "You" if name == "you" else f"The {name}"


__all__ = ["StealthSystem"]
