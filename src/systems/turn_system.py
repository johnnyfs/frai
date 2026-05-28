from dataclasses import dataclass, field
import random

from src.core.actions import Action, AttackAttempt, MoveAttempt
from src.core.combat import ability_modifier
from src.core.dispatcher import DispatchResult
from src.core.effects import DamageEntity, Effect, EmitMessage, KillEntity, MoveEntity
from src.core.entity import EntityId
from src.core.world import World
from src.systems.combat_system import CombatSystem
from src.systems.movement_system import MovementSystem


@dataclass(frozen=True, slots=True)
class InitiativeEntry:
    entity: EntityId
    score: int


@dataclass(slots=True)
class TurnSystem:
    movement: MovementSystem
    combat: CombatSystem
    rng: random.Random = field(default_factory=random.Random)

    def handle(self, action: Action, world: World) -> DispatchResult:
        if not isinstance(action, MoveAttempt):
            return DispatchResult()
        if not world.player_controlled.has(action.actor):
            return DispatchResult()

        initiative = self._initiative_order(world)
        effects: list[Effect] = []
        initial_positions = _positions(world)
        positions = dict(initial_positions)
        hit_points = {
            entity: stats.hit_points for entity, stats in world.combat_stats.values.items()
        }
        dead: set[EntityId] = set()
        for entry in initiative:
            if entry.entity in dead:
                continue
            if not world.combat_stats.has(entry.entity):
                continue
            if entry.entity == action.actor:
                actor_action = action
            elif world.player_controlled.has(entry.entity):
                actor_action = None
            else:
                actor_action = self._decide_npc_action(entry.entity, world, positions, dead)
            if actor_action is None:
                continue
            resolved = self._resolve(actor_action, world, positions, initial_positions, hit_points, dead)
            effects.extend(resolved)
            _apply_turn_effects(resolved, positions, hit_points, dead)

        return DispatchResult(effects=effects, cancel=True)

    def _initiative_order(self, world: World) -> list[InitiativeEntry]:
        entries: list[InitiativeEntry] = []
        for entity, stats in world.combat_stats.values.items():
            if world.positions.has(entity):
                entries.append(
                    InitiativeEntry(
                        entity=entity,
                        score=self.rng.randint(1, 20) + ability_modifier(stats.dexterity),
                    )
                )
        entries.sort(key=lambda entry: entry.score, reverse=True)
        return entries

    def _decide_npc_action(
        self,
        entity: EntityId,
        world: World,
        positions: dict[EntityId, tuple[int, int]],
        dead: set[EntityId],
    ) -> Action | None:
        target = _nearest_controlled_target(entity, world, positions, dead)
        if target is None or entity not in positions:
            return None
        x, y = positions[entity]
        target_x, target_y = positions[target]
        dx = _sign(target_x - x)
        dy = _sign(target_y - y)
        if max(abs(target_x - x), abs(target_y - y)) <= 1:
            return AttackAttempt(actor=entity, target=target)
        return MoveAttempt(actor=entity, dx=dx, dy=dy)

    def _resolve(
        self,
        action: Action,
        world: World,
        positions: dict[EntityId, tuple[int, int]],
        initial_positions: dict[EntityId, tuple[int, int]],
        hit_points: dict[EntityId, int],
        dead: set[EntityId],
    ) -> list[Effect]:
        if isinstance(action, AttackAttempt):
            if action.actor in dead or action.target in dead:
                return []
            return self.combat.resolve_attack(
                action,
                world,
                target_hit_points=hit_points.get(action.target),
            )
        if isinstance(action, MoveAttempt):
            return self._resolve_move(action, world, positions, initial_positions, hit_points, dead)
        return []

    def _resolve_move(
        self,
        action: MoveAttempt,
        world: World,
        positions: dict[EntityId, tuple[int, int]],
        initial_positions: dict[EntityId, tuple[int, int]],
        hit_points: dict[EntityId, int],
        dead: set[EntityId],
    ) -> list[Effect]:
        if action.actor in dead or action.actor not in positions:
            return []
        x, y = positions[action.actor]
        destination = (x + action.dx, y + action.dy)
        is_controlled = world.player_controlled.has(action.actor)
        tile = world.tile_at(*destination)
        if tile.blocks_movement:
            return [EmitMessage("Blocked.")] if is_controlled else []

        occupant = _occupant_at(destination, positions, dead, exclude=action.actor)
        if occupant is not None and world.blockers.has(occupant):
            if _is_hostile(action.actor, occupant, world) and initial_positions.get(occupant) == destination:
                return self.combat.resolve_attack(
                    AttackAttempt(action.actor, occupant),
                    world,
                    target_hit_points=hit_points.get(occupant),
                )
            return [EmitMessage("Blocked.")] if is_controlled else []

        return [MoveEntity(action.actor, destination[0], destination[1])]


def _sign(value: int) -> int:
    if value < 0:
        return -1
    if value > 0:
        return 1
    return 0


def _positions(world: World) -> dict[EntityId, tuple[int, int]]:
    return {
        entity: (position.x, position.y)
        for entity, position in world.positions.values.items()
    }


def _occupant_at(
    destination: tuple[int, int],
    positions: dict[EntityId, tuple[int, int]],
    dead: set[EntityId],
    exclude: EntityId,
) -> EntityId | None:
    for entity, position in positions.items():
        if entity != exclude and entity not in dead and position == destination:
            return entity
    return None


def _nearest_controlled_target(
    actor: EntityId,
    world: World,
    positions: dict[EntityId, tuple[int, int]],
    dead: set[EntityId],
) -> EntityId | None:
    if actor not in positions:
        return None
    actor_x, actor_y = positions[actor]
    candidates = [
        entity
        for entity in world.controlled_entities()
        if entity not in dead and entity in positions and world.combat_stats.has(entity)
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda entity: max(
            abs(positions[entity][0] - actor_x),
            abs(positions[entity][1] - actor_y),
        ),
    )


def _is_hostile(actor: EntityId, target: EntityId, world: World) -> bool:
    """Whether ``actor`` should attack ``target`` this turn.

    Delegates to the awareness predicate so the M28 faction relations
    (overrides, summoner inheritance, default table) apply to NPC turn
    decisions the same way they do to player bumps.
    """
    from src.systems.awareness_system import is_hostile_to

    return is_hostile_to(world, actor, target)


def _apply_turn_effects(
    effects: list[Effect],
    positions: dict[EntityId, tuple[int, int]],
    hit_points: dict[EntityId, int],
    dead: set[EntityId],
) -> None:
    for effect in effects:
        if isinstance(effect, MoveEntity):
            positions[effect.entity] = (effect.x, effect.y)
        elif isinstance(effect, DamageEntity):
            hit_points[effect.entity] = max(0, hit_points.get(effect.entity, 0) - effect.amount)
        elif isinstance(effect, KillEntity):
            dead.add(effect.entity)
            positions.pop(effect.entity, None)
