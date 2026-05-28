from collections.abc import Iterable
from dataclasses import dataclass
import random

from src.core.actions import Action
from src.core.combat import weapon_for_name
from src.core.components import (
    BlocksMovement,
    CombatStats,
    Creature,
    Faction,
    Name,
    PlayerControlled,
    Position,
    Presentation,
)
from src.core.dispatcher import Dispatcher
from src.core.effects import DamageEntity, Effect, EmitMessage, KillEntity, MoveEntity
from src.core.entity import EntityId
from src.core.world import World
from src.map.tiles import FLOOR, HORIZONTAL_WALL, VERTICAL_WALL
from src.systems.combat_system import CombatSystem
from src.systems.movement_system import MovementContextResolver, MovementSystem
from src.systems.obstruction_system import ObstructionSystem


class SequenceRng(random.Random):
    def __init__(self, values: list[int]) -> None:
        super().__init__(0)
        self.values = list(values)

    def randint(self, low: int, high: int) -> int:
        if not self.values:
            raise AssertionError(f"SequenceRng exhausted for randint({low}, {high})")
        value = self.values.pop(0)
        if not low <= value <= high:
            raise AssertionError(f"SequenceRng value {value} outside randint({low}, {high})")
        return value


@dataclass(frozen=True, slots=True)
class TinyParty:
    world: World
    player: EntityId
    companion: EntityId
    party: list[EntityId]


@dataclass(frozen=True, slots=True)
class TinyEncounter:
    world: World
    player: EntityId
    companion: EntityId
    enemy: EntityId
    party: list[EntityId]


def build_tiny_map(width: int = 7, height: int = 5) -> World:
    if width < 3 or height < 3:
        raise ValueError("Tiny maps need room for a wall border and at least one floor tile.")

    tiles = [[FLOOR for _ in range(width)] for _ in range(height)]
    for x in range(width):
        tiles[0][x] = HORIZONTAL_WALL
        tiles[height - 1][x] = HORIZONTAL_WALL
    for y in range(height):
        tiles[y][0] = VERTICAL_WALL
        tiles[y][width - 1] = VERTICAL_WALL
    return World(width=width, height=height, tiles=tiles)


def add_actor(
    world: World,
    x: int,
    y: int,
    *,
    name: str = "you",
    glyph: str = "@",
    faction: str = "player",
    controlled: bool = True,
    blocks_movement: bool = True,
    hit_points: int = 10,
    armor_class: int = 10,
    strength: int = 16,
    dexterity: int = 10,
    constitution: int = 10,
    weapon: str = "longsword",
) -> EntityId:
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation(glyph))
    world.names.add(entity, Name(name))
    world.factions.add(entity, Faction(faction))
    world.combat_stats.add(
        entity,
        CombatStats(
            armor_class=armor_class,
            hit_points=hit_points,
            max_hit_points=hit_points,
            strength=strength,
            dexterity=dexterity,
            constitution=constitution,
        ),
    )
    world.weapons.add(entity, weapon_for_name(weapon))
    if controlled:
        world.player_controlled.add(entity, PlayerControlled())
    if blocks_movement:
        world.blockers.add(entity, BlocksMovement("occupied"))
    return entity


def add_party_member(
    world: World,
    x: int,
    y: int,
    *,
    name: str = "companion",
    glyph: str = "1",
) -> EntityId:
    return add_actor(world, x, y, name=name, glyph=glyph, faction="player")


def add_enemy(
    world: World,
    x: int,
    y: int,
    *,
    name: str = "frog",
    glyph: str = ":",
    kind: str = "frog",
    attack_verb: str = "bites",
    hit_points: int = 3,
    armor_class: int = 10,
    weapon: str = "dagger",
) -> EntityId:
    enemy = add_actor(
        world,
        x,
        y,
        name=name,
        glyph=glyph,
        faction="enemy",
        controlled=False,
        hit_points=hit_points,
        armor_class=armor_class,
        strength=10,
        dexterity=10,
        constitution=10,
        weapon=weapon,
    )
    world.creatures.add(enemy, Creature(kind=kind, attack_verb=attack_verb))
    return enemy


def build_tiny_party_world() -> TinyParty:
    world = build_tiny_map()
    player = add_actor(world, 2, 2)
    companion = add_party_member(world, 3, 2)
    return TinyParty(world=world, player=player, companion=companion, party=[player, companion])


def build_tiny_encounter(*, enemy_hit_points: int = 3) -> TinyEncounter:
    fixture = build_tiny_party_world()
    enemy = add_enemy(fixture.world, 4, 2, hit_points=enemy_hit_points)
    return TinyEncounter(
        world=fixture.world,
        player=fixture.player,
        companion=fixture.companion,
        enemy=enemy,
        party=fixture.party,
    )


def build_action_dispatcher(rng: random.Random | None = None) -> Dispatcher:
    return Dispatcher(
        systems=[
            MovementSystem(
                obstruction=ObstructionSystem(),
                context_resolver=MovementContextResolver(),
            ),
            CombatSystem(rng=rng if rng is not None else random.Random(0)),
        ]
    )


def resolve_action(action: Action, world: World, *, rng: random.Random | None = None) -> list[Effect]:
    return build_action_dispatcher(rng).dispatch(action, world)


def apply_world_effects(world: World, effects: Iterable[Effect]) -> None:
    for effect in effects:
        if isinstance(effect, MoveEntity):
            position = world.positions.require(effect.entity)
            position.x = effect.x
            position.y = effect.y
        elif isinstance(effect, DamageEntity):
            stats = world.combat_stats.require(effect.entity)
            stats.hit_points = max(0, stats.hit_points - effect.amount)
        elif isinstance(effect, KillEntity):
            world.remove_entity(effect.entity)
        elif isinstance(effect, EmitMessage):
            continue
        else:
            raise AssertionError(f"Unsupported test effect: {effect!r}")
