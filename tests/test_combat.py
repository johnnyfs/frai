import random

from src.core.actions import AttackAttempt, MoveAttempt
from src.core.combat import armor_class_for, armor_for_name, weapon_for_name
from src.core.components import CombatStats, Faction, Name
from src.core.effects import DamageEntity, EmitMessage, KillEntity, MoveEntity
from src.map.room_builder import build_room_world
from src.systems.combat_system import CombatSystem
from src.systems.movement_system import MovementContextResolver, MovementSystem
from src.systems.obstruction_system import ObstructionSystem
from src.systems.turn_system import TurnSystem


def test_moving_into_hostile_replaces_movement_with_attack() -> None:
    built = build_room_world(80, 40)
    player = built.player
    goblin = next(entity for entity, name in built.world.names.values.items() if name.value == "goblin")
    built.world.positions.require(player).x = 10
    built.world.positions.require(player).y = 10
    built.world.positions.require(goblin).x = 11
    built.world.positions.require(goblin).y = 10
    built.world.combat_stats.add(player, CombatStats(armor_class=10, hit_points=10, max_hit_points=10, strength=16, dexterity=10, constitution=10))
    built.world.weapons.add(player, weapon_for_name("longsword"))

    result = MovementSystem(ObstructionSystem(), MovementContextResolver()).handle(
        MoveAttempt(player, 1, 0), built.world
    )

    assert result.replacement == AttackAttempt(player, goblin)


def test_goblin_is_srd_baseline_with_requested_dagger() -> None:
    built = build_room_world(80, 40)
    goblin = next(entity for entity, name in built.world.names.values.items() if name.value == "goblin")

    stats = built.world.combat_stats.require(goblin)
    weapon = built.world.weapons.require(goblin)

    assert built.world.presentations.require(goblin).glyph == "o"
    assert stats.armor_class == 15
    assert stats.hit_points == 10
    assert stats.dexterity == 15
    assert weapon.name == "dagger"


def test_armor_class_applies_dexterity_caps() -> None:
    assert armor_class_for(16, armor_for_name("leather armor")) == 14
    assert armor_class_for(16, armor_for_name("scale mail")) == 16
    assert armor_class_for(16, armor_for_name("chain mail")) == 16
    assert armor_class_for(16, armor_for_name("none")) == 13


def test_combat_hit_deals_damage_and_can_kill() -> None:
    built = build_room_world(80, 40)
    player = built.player
    goblin = next(entity for entity, name in built.world.names.values.items() if name.value == "goblin")
    built.world.names.add(player, Name("you"))
    built.world.factions.add(player, Faction("player"))
    built.world.combat_stats.add(player, CombatStats(armor_class=10, hit_points=10, max_hit_points=10, strength=18, dexterity=10, constitution=10))
    built.world.weapons.add(player, weapon_for_name("longsword"))
    built.world.combat_stats.require(goblin).hit_points = 1

    result = CombatSystem(rng=random.Random(0)).handle(AttackAttempt(player, goblin), built.world)

    assert any(isinstance(effect, DamageEntity) for effect in result.effects)
    assert any(isinstance(effect, KillEntity) for effect in result.effects)


class SequenceRng:
    def __init__(self, values: list[int]) -> None:
        self.values = values

    def randint(self, low: int, high: int) -> int:
        return self.values.pop(0)


def test_faster_move_preempts_later_move_into_same_square() -> None:
    built = build_room_world(80, 40)
    player = built.player
    goblin = next(entity for entity, name in built.world.names.values.items() if name.value == "goblin")
    built.world.positions.require(player).x = 40
    built.world.positions.require(player).y = 20
    built.world.positions.require(goblin).x = 42
    built.world.positions.require(goblin).y = 20
    built.world.combat_stats.add(player, CombatStats(armor_class=10, hit_points=10, max_hit_points=10, strength=16, dexterity=10, constitution=10))
    built.world.weapons.add(player, weapon_for_name("longsword"))
    movement = MovementSystem(ObstructionSystem(), MovementContextResolver())
    turn = TurnSystem(
        movement=movement,
        combat=CombatSystem(rng=random.Random(0)),
        rng=SequenceRng([20, 1]),  # goblin initiative, then player initiative
    )

    result = turn.handle(MoveAttempt(player, 1, 0), built.world)

    assert MoveEntity(goblin, 41, 20) in result.effects
    assert EmitMessage("Blocked.") in result.effects
    assert not any(isinstance(effect, DamageEntity) for effect in result.effects)
