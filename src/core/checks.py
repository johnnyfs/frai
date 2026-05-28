"""Generic d20 check machinery.

Provides typed `Check` / `Save` descriptions, an `AdvantageState` enum with SRD
composition rules, and a pure `roll_check` function that takes an explicit
`random.Random` source so callers control determinism.

This module has no game-state dependencies beyond plain data and an RNG. It is
imported by gameplay systems (locks, traps, perception, stealth, athletics,
saves, etc.) which translate world state into a `Check` and apply the
`CheckResult`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import random
from typing import Sequence


class AdvantageState(str, Enum):
    """Per-roll advantage state.

    Per SRD: if a roll has both advantage and disadvantage from any source,
    they cancel and the roll is treated as `NORMAL` regardless of how many
    of each. `combine_advantage` enforces this.
    """

    NORMAL = "normal"
    ADVANTAGE = "advantage"
    DISADVANTAGE = "disadvantage"


def combine_advantage(*states: AdvantageState) -> AdvantageState:
    """Combine multiple advantage sources per SRD composition rules.

    Any advantage cancels with any disadvantage to NORMAL. Otherwise the
    non-NORMAL state wins. All-NORMAL stays NORMAL.
    """
    has_adv = any(state is AdvantageState.ADVANTAGE for state in states)
    has_dis = any(state is AdvantageState.DISADVANTAGE for state in states)
    if has_adv and has_dis:
        return AdvantageState.NORMAL
    if has_adv:
        return AdvantageState.ADVANTAGE
    if has_dis:
        return AdvantageState.DISADVANTAGE
    return AdvantageState.NORMAL


@dataclass(frozen=True, slots=True)
class Check:
    """A typed d20 ability/skill check.

    Fields:
        actor: opaque entity id (the system that constructs the check owns
            interpretation; this module never dereferences it).
        ability: ability name (e.g. "DEX", "STR") for diagnostics only.
        ability_modifier: pre-computed ability modifier (e.g. +2 for DEX 14).
        proficiency: True if the actor is proficient in the relevant skill,
            in which case `proficiency_bonus` is added.
        proficiency_bonus: proficiency bonus to add when `proficiency` is True.
        dc: difficulty class to meet or beat.
        modifiers: any extra ad-hoc modifiers (situational, magical, etc.).
        advantage_state: pre-composed advantage state for this roll. Callers
            with multiple sources should call `combine_advantage` first.
    """

    actor: object
    ability: str
    ability_modifier: int
    dc: int
    proficiency: bool = False
    proficiency_bonus: int = 0
    modifiers: Sequence[int] = field(default_factory=tuple)
    advantage_state: AdvantageState = AdvantageState.NORMAL


@dataclass(frozen=True, slots=True)
class Save:
    """A saving throw - a thin wrapper that maps to a `Check`.

    Saves use the same d20 math as checks; the type distinction is only
    semantic (so callers can tell saves and skill/ability checks apart in
    logs, UI, and rule-specific modifiers).
    """

    actor: object
    ability: str
    ability_modifier: int
    dc: int
    proficiency: bool = False
    proficiency_bonus: int = 0
    modifiers: Sequence[int] = field(default_factory=tuple)
    advantage_state: AdvantageState = AdvantageState.NORMAL

    def to_check(self) -> Check:
        return Check(
            actor=self.actor,
            ability=self.ability,
            ability_modifier=self.ability_modifier,
            dc=self.dc,
            proficiency=self.proficiency,
            proficiency_bonus=self.proficiency_bonus,
            modifiers=tuple(self.modifiers),
            advantage_state=self.advantage_state,
        )


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Outcome of a `roll_check` call.

    Fields:
        success: True iff `total >= dc`.
        total: final total including d20, modifiers, ability mod, and (if
            proficient) proficiency bonus.
        natural: the d20 face value actually used (after advantage/disadvantage
            resolution). 1 and 20 are exposed so callers can implement
            critical success/failure rules if they want.
        rolls: the raw d20 rolls made (one for normal, two for adv/dis).
    """

    success: bool
    total: int
    natural: int
    rolls: tuple[int, ...]


def roll_check(check: Check, rng: random.Random) -> CheckResult:
    """Resolve a `Check` using the supplied RNG.

    Pure with respect to `(check, rng_state)`: identical inputs (including
    seed) yield identical outputs. The RNG is the only source of randomness.
    """
    if check.advantage_state is AdvantageState.NORMAL:
        rolls = (rng.randint(1, 20),)
        natural = rolls[0]
    else:
        first = rng.randint(1, 20)
        second = rng.randint(1, 20)
        rolls = (first, second)
        natural = max(first, second) if check.advantage_state is AdvantageState.ADVANTAGE else min(first, second)

    bonus = check.ability_modifier + sum(check.modifiers)
    if check.proficiency:
        bonus += check.proficiency_bonus
    total = natural + bonus
    return CheckResult(success=total >= check.dc, total=total, natural=natural, rolls=rolls)


def roll_save(save: Save, rng: random.Random) -> CheckResult:
    """Resolve a `Save` via `roll_check` against its corresponding `Check`."""
    return roll_check(save.to_check(), rng)


__all__ = [
    "AdvantageState",
    "Check",
    "CheckResult",
    "Save",
    "combine_advantage",
    "roll_check",
    "roll_save",
]
