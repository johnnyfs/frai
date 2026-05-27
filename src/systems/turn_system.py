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
        if action.actor != world.player_entity():
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
            actor_action = (
                action
                if entry.entity == action.actor
                else self._decide_npc_action(entry.entity, world, positions, dead)
            )
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
        player = world.player_entity()
        if player in dead or player not in positions or entity not in positions:
            return None
        x, y = positions[entity]
        player_x, player_y = positions[player]
        dx = _sign(player_x - x)
        dy = _sign(player_y - y)
        if max(abs(player_x - x), abs(player_y - y)) <= 1:
            return AttackAttempt(actor=entity, target=player)
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
        tile = world.tile_at(*destination)
        if tile.blocks_movement:
            return [EmitMessage("Blocked.")] if action.actor == world.player_entity() else []

        occupant = _occupant_at(destination, positions, dead, exclude=action.actor)
        if occupant is not None and world.blockers.has(occupant):
            if _is_hostile(action.actor, occupant, world) and initial_positions.get(occupant) == destination:
                return self.combat.resolve_attack(
                    AttackAttempt(action.actor, occupant),
                    world,
                    target_hit_points=hit_points.get(occupant),
                )
            return [EmitMessage("Blocked.")] if action.actor == world.player_entity() else []

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


def _is_hostile(actor: EntityId, target: EntityId, world: World) -> bool:
    actor_faction = world.factions.get(actor)
    target_faction = world.factions.get(target)
    return (
        actor_faction is not None
        and target_faction is not None
        and actor_faction.value != target_faction.value
        and world.combat_stats.has(target)
    )


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
