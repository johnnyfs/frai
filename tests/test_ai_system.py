from src.core.components import AI, AIBehaviorType
from src.core.effects import Effect
from src.systems.ai_system import EnemyAISystem, chebyshev_distance
from src.systems.combat_system import CombatSystem
from tests.support.tiny_world import (
    SequenceRng,
    add_actor,
    add_enemy,
    apply_world_effects,
    build_tiny_map,
)


def _run_enemy_ai(
    system: EnemyAISystem,
    world,
    party,
) -> list[Effect]:
    effects: list[Effect] = []

    def apply(effects_to_apply: list[Effect]) -> None:
        effects.extend(effects_to_apply)
        apply_world_effects(world, effects_to_apply)

    system.run_enemy_activations(world, party, apply)
    return effects


def test_default_chase_ai_preserves_frog_chase_and_melee_behavior() -> None:
    world = build_tiny_map(width=15, height=5)
    player = add_actor(world, 10, 2)
    enemy = add_enemy(world, 2, 2)
    system = EnemyAISystem(combat=CombatSystem(rng=SequenceRng([20, 1])))

    _run_enemy_ai(system, world, [player])

    assert world.positions.require(enemy).x == 9
    assert world.positions.require(enemy).y == 2
    assert world.combat_stats.require(player).hit_points == 9


def test_flee_ai_moves_away_from_nearest_party_member() -> None:
    world = build_tiny_map(width=9, height=5)
    player = add_actor(world, 2, 2)
    enemy = add_enemy(world, 4, 2, ai=AI(AIBehaviorType.FLEE))
    starting_distance = chebyshev_distance(world, enemy, player)

    _run_enemy_ai(EnemyAISystem(), world, [player])

    assert chebyshev_distance(world, enemy, player) > starting_distance
    assert world.positions.require(enemy).x > 4


def test_wander_ai_takes_one_deterministic_random_legal_step() -> None:
    world = build_tiny_map(width=8, height=5)
    player = add_actor(world, 6, 2)
    enemy = add_enemy(world, 3, 2, ai=AI(AIBehaviorType.WANDER))
    system = EnemyAISystem(rng=SequenceRng([4]))

    _run_enemy_ai(system, world, [player])

    assert world.positions.require(enemy).x == 4
    assert world.positions.require(enemy).y == 2


def test_ranged_ai_attacks_without_closing_when_target_is_in_range() -> None:
    world = build_tiny_map(width=9, height=5)
    player = add_actor(world, 5, 2)
    enemy = add_enemy(
        world,
        2,
        2,
        ai=AI(AIBehaviorType.RANGED, attack_range=3, preferred_range=2),
    )
    system = EnemyAISystem(combat=CombatSystem(rng=SequenceRng([20, 1])))

    _run_enemy_ai(system, world, [player])

    assert world.positions.require(enemy).x == 2
    assert world.combat_stats.require(player).hit_points == 9


def test_ai_does_not_crash_or_move_when_chase_path_is_blocked() -> None:
    world = build_tiny_map(width=7, height=5)
    player = add_actor(world, 2, 2)
    enemy = add_enemy(world, 4, 2, ai=AI(AIBehaviorType.CHASE))
    add_actor(world, 3, 2, name="crate", glyph="#", faction="neutral")

    _run_enemy_ai(EnemyAISystem(), world, [player])

    assert world.positions.require(enemy).x == 4
    assert world.positions.require(enemy).y == 2
