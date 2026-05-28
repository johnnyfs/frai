from collections.abc import Callable
from dataclasses import dataclass, field
import random

from src.core.actions import AttackAttempt, MoveAttempt
from src.core.components import AI, AIBehaviorType
from src.core.effects import Effect, MoveEntity
from src.core.entity import EntityId
from src.core.turns import MOVEMENT_TOTAL_FEET
from src.core.world import World
from src.systems.combat_system import CombatSystem
from src.systems.movement_system import movement_cost_for_attempt


EffectApplier = Callable[[list[Effect]], None]


@dataclass(slots=True)
class EnemyAISystem:
    combat: CombatSystem = field(default_factory=CombatSystem)
    rng: random.Random = field(default_factory=random.Random)
    default_ai: AI = AI(AIBehaviorType.CHASE)

    def run_enemy_activations(
        self,
        world: World,
        party: list[EntityId],
        apply_effects: EffectApplier,
    ) -> None:
        for enemy in list(world.combat_stats.values):
            if not _can_enemy_activate(world, enemy):
                continue
            self.activate_enemy(world, enemy, party, apply_effects)

    def activate_enemy(
        self,
        world: World,
        enemy: EntityId,
        party: list[EntityId],
        apply_effects: EffectApplier,
    ) -> None:
        ai = world.ai.get(enemy) or self.default_ai
        movement_used = 0.0
        action_used = False

        while world.positions.has(enemy):
            target = _nearest_living_party_member(world, enemy, party)
            if target is None:
                return
            movement_remaining = MOVEMENT_TOTAL_FEET - movement_used
            action = self._choose_action(
                world,
                enemy,
                target,
                ai,
                movement_remaining,
                action_used,
            )
            if action is None:
                return
            if isinstance(action, AttackAttempt):
                if action_used:
                    return
                apply_effects(self.combat.resolve_attack(action, world))
                action_used = True
                return

            cost = movement_cost_for_attempt(world, action)
            if cost > movement_remaining:
                return
            position = world.positions.require(enemy)
            apply_effects([MoveEntity(enemy, position.x + action.dx, position.y + action.dy)])
            movement_used += cost
            if ai.behavior == AIBehaviorType.WANDER:
                return

    def _choose_action(
        self,
        world: World,
        enemy: EntityId,
        target: EntityId,
        ai: AI,
        movement_remaining: float,
        action_used: bool,
    ) -> AttackAttempt | MoveAttempt | None:
        distance = chebyshev_distance(world, enemy, target)
        if ai.behavior == AIBehaviorType.FLEE:
            if movement_remaining <= 0:
                return None
            return _step_away_from(world, enemy, target, movement_remaining)

        if ai.behavior == AIBehaviorType.WANDER:
            if distance <= ai.attack_range and not action_used:
                return AttackAttempt(enemy, target)
            if movement_remaining <= 0:
                return None
            return self._random_legal_step(world, enemy, movement_remaining)

        if ai.behavior == AIBehaviorType.RANGED:
            if (
                distance < ai.preferred_range
                and movement_remaining > 0
                and (step := _step_away_from(world, enemy, target, movement_remaining)) is not None
            ):
                return step
            if distance <= ai.attack_range and not action_used:
                return AttackAttempt(enemy, target)
            if movement_remaining <= 0:
                return None
            return _step_toward(world, enemy, target, movement_remaining)

        if distance <= ai.attack_range and not action_used:
            return AttackAttempt(enemy, target)
        if movement_remaining <= 0:
            return None
        return _step_toward(world, enemy, target, movement_remaining)

    def _random_legal_step(
        self,
        world: World,
        enemy: EntityId,
        movement_remaining: float,
    ) -> MoveAttempt | None:
        candidates = _legal_steps(world, enemy, movement_remaining)
        if not candidates:
            return None
        index = self.rng.randint(0, len(candidates) - 1)
        dx, dy = candidates[index]
        return MoveAttempt(enemy, dx, dy)


def _can_enemy_activate(world: World, enemy: EntityId) -> bool:
    if world.player_controlled.has(enemy) or not world.positions.has(enemy):
        return False
    stats = world.combat_stats.get(enemy)
    return stats is not None and stats.hit_points > 0


def _can_take_turn(world: World, entity: EntityId) -> bool:
    stats = world.combat_stats.get(entity)
    return world.positions.has(entity) and (stats is None or stats.hit_points > 0)


def _nearest_living_party_member(
    world: World,
    enemy: EntityId,
    party: list[EntityId],
) -> EntityId | None:
    candidates = [entity for entity in party if _can_take_turn(world, entity)]
    if not candidates:
        return None
    enemy_position = world.positions.require(enemy)
    return min(
        candidates,
        key=lambda entity: max(
            abs(world.positions.require(entity).x - enemy_position.x),
            abs(world.positions.require(entity).y - enemy_position.y),
        ),
    )


def chebyshev_distance(world: World, a: EntityId, b: EntityId) -> int:
    a_position = world.positions.require(a)
    b_position = world.positions.require(b)
    return max(abs(a_position.x - b_position.x), abs(a_position.y - b_position.y))


def _step_toward(
    world: World,
    enemy: EntityId,
    target: EntityId,
    movement_remaining: float,
) -> MoveAttempt | None:
    enemy_position = world.positions.require(enemy)
    target_position = world.positions.require(target)
    dx = _sign(target_position.x - enemy_position.x)
    dy = _sign(target_position.y - enemy_position.y)
    candidates = [(dx, dy)]
    if dx != 0:
        candidates.append((dx, 0))
    if dy != 0:
        candidates.append((0, dy))
    for candidate_dx, candidate_dy in candidates:
        if _is_legal_step(world, enemy, candidate_dx, candidate_dy, movement_remaining):
            return MoveAttempt(enemy, candidate_dx, candidate_dy)
    return None


def _step_away_from(
    world: World,
    enemy: EntityId,
    target: EntityId,
    movement_remaining: float,
) -> MoveAttempt | None:
    current_distance = chebyshev_distance(world, enemy, target)
    candidates = _legal_steps(world, enemy, movement_remaining)
    if not candidates:
        return None

    position = world.positions.require(enemy)
    best = max(
        candidates,
        key=lambda step: _distance_from_point(
            world,
            target,
            position.x + step[0],
            position.y + step[1],
        ),
    )
    if _distance_from_point(world, target, position.x + best[0], position.y + best[1]) <= current_distance:
        return None
    return MoveAttempt(enemy, best[0], best[1])


def _legal_steps(
    world: World,
    enemy: EntityId,
    movement_remaining: float,
) -> list[tuple[int, int]]:
    return [
        (dx, dy)
        for dx, dy in (
            (-1, -1),
            (0, -1),
            (1, -1),
            (-1, 0),
            (1, 0),
            (-1, 1),
            (0, 1),
            (1, 1),
        )
        if _is_legal_step(world, enemy, dx, dy, movement_remaining)
    ]


def _is_legal_step(
    world: World,
    enemy: EntityId,
    dx: int,
    dy: int,
    movement_remaining: float,
) -> bool:
    if dx == 0 and dy == 0:
        return False
    action = MoveAttempt(enemy, dx, dy)
    if movement_cost_for_attempt(world, action) > movement_remaining:
        return False
    position = world.positions.require(enemy)
    destination_x = position.x + dx
    destination_y = position.y + dy
    return not world.blockers_at(destination_x, destination_y)


def _distance_from_point(world: World, target: EntityId, x: int, y: int) -> int:
    target_position = world.positions.require(target)
    return max(abs(target_position.x - x), abs(target_position.y - y))


def _sign(value: int) -> int:
    if value < 0:
        return -1
    if value > 0:
        return 1
    return 0
