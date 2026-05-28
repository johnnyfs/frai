"""Tests for the generic d20 check machinery (`src/core/checks.py`).

All tests use seeded `random.Random` instances so behaviour is fully
deterministic.
"""

import random

from src.core.checks import (
    AdvantageState,
    Check,
    Save,
    combine_advantage,
    roll_check,
    roll_save,
)


def _check(
    *,
    dc: int = 10,
    ability_modifier: int = 0,
    proficiency: bool = False,
    proficiency_bonus: int = 2,
    advantage_state: AdvantageState = AdvantageState.NORMAL,
    modifiers: tuple[int, ...] = (),
) -> Check:
    return Check(
        actor="actor",
        ability="DEX",
        ability_modifier=ability_modifier,
        dc=dc,
        proficiency=proficiency,
        proficiency_bonus=proficiency_bonus,
        modifiers=modifiers,
        advantage_state=advantage_state,
    )


def test_dc_10_with_high_ability_and_proficiency_succeeds_on_seed_0() -> None:
    rng = random.Random(0)
    result = roll_check(
        _check(dc=10, ability_modifier=4, proficiency=True, proficiency_bonus=2),
        rng,
    )

    assert result.success is True
    # 4 + 2 = +6 to whatever d20 face.
    assert result.total == result.natural + 6


def test_dc_10_with_low_ability_produces_mixed_outcomes_across_seeds() -> None:
    successes = 0
    fails = 0
    for seed in range(20):
        result = roll_check(
            _check(dc=10, ability_modifier=-1),
            random.Random(seed),
        )
        if result.success:
            successes += 1
        else:
            fails += 1
    # With +(-1) vs DC 10, a roll needs to be >= 11 on a d20 (50% chance).
    # Across 20 seeds we expect both outcomes to appear.
    assert successes > 0
    assert fails > 0


def test_modifier_plus_two_against_dc_10_with_seeded_rng_is_deterministic() -> None:
    first = roll_check(_check(dc=10, ability_modifier=2), random.Random(42))
    second = roll_check(_check(dc=10, ability_modifier=2), random.Random(42))

    assert first == second


def test_advantage_picks_the_higher_of_two_d20s() -> None:
    rng = random.Random(123)
    advantaged = roll_check(
        _check(dc=10, advantage_state=AdvantageState.ADVANTAGE),
        rng,
    )

    assert len(advantaged.rolls) == 2
    assert advantaged.natural == max(advantaged.rolls)


def test_disadvantage_picks_the_lower_of_two_d20s() -> None:
    rng = random.Random(123)
    disadvantaged = roll_check(
        _check(dc=10, advantage_state=AdvantageState.DISADVANTAGE),
        rng,
    )

    assert len(disadvantaged.rolls) == 2
    assert disadvantaged.natural == min(disadvantaged.rolls)


def test_advantage_and_disadvantage_cancel_per_srd() -> None:
    combined = combine_advantage(AdvantageState.ADVANTAGE, AdvantageState.DISADVANTAGE)
    assert combined is AdvantageState.NORMAL

    # Multiple sources of advantage with at least one disadvantage still cancel.
    combined_many = combine_advantage(
        AdvantageState.ADVANTAGE,
        AdvantageState.ADVANTAGE,
        AdvantageState.DISADVANTAGE,
    )
    assert combined_many is AdvantageState.NORMAL


def test_combine_advantage_keeps_the_winning_state_when_only_one_kind_present() -> None:
    assert (
        combine_advantage(AdvantageState.ADVANTAGE, AdvantageState.NORMAL)
        is AdvantageState.ADVANTAGE
    )
    assert (
        combine_advantage(AdvantageState.DISADVANTAGE, AdvantageState.NORMAL)
        is AdvantageState.DISADVANTAGE
    )
    assert combine_advantage() is AdvantageState.NORMAL
    assert (
        combine_advantage(AdvantageState.NORMAL, AdvantageState.NORMAL)
        is AdvantageState.NORMAL
    )


def test_proficiency_adds_proficiency_bonus_only_when_flagged() -> None:
    without = roll_check(
        _check(dc=15, ability_modifier=0, proficiency=False, proficiency_bonus=3),
        random.Random(7),
    )
    with_prof = roll_check(
        _check(dc=15, ability_modifier=0, proficiency=True, proficiency_bonus=3),
        random.Random(7),
    )

    assert with_prof.natural == without.natural
    assert with_prof.total == without.total + 3


def test_extra_modifiers_are_summed_into_total() -> None:
    result = roll_check(
        _check(dc=10, ability_modifier=1, modifiers=(2, -1, 1)),
        random.Random(99),
    )

    assert result.total == result.natural + 1 + (2 - 1 + 1)


def test_roll_save_resolves_to_an_equivalent_check_result() -> None:
    save = Save(
        actor="actor",
        ability="WIS",
        ability_modifier=3,
        dc=12,
        proficiency=True,
        proficiency_bonus=2,
    )
    saved = roll_save(save, random.Random(11))
    equivalent = roll_check(save.to_check(), random.Random(11))

    assert saved == equivalent


def test_advantage_consumes_two_rng_draws_per_check() -> None:
    rng_normal = random.Random(5)
    rng_advantage = random.Random(5)

    roll_check(_check(advantage_state=AdvantageState.NORMAL), rng_normal)
    roll_check(_check(advantage_state=AdvantageState.ADVANTAGE), rng_advantage)

    # After one NORMAL roll vs one ADVANTAGE roll the streams should now
    # diverge: the advantage roll consumed two faces, the normal one only one.
    assert rng_normal.randint(1, 20) != rng_advantage.randint(1, 20)
