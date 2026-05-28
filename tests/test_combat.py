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
    frog = next(entity for entity, name in built.world.names.values.items() if name.value == "frog")
    built.world.positions.require(player).x = 10
    built.world.positions.require(player).y = 10
    built.world.positions.require(frog).x = 11
    built.world.positions.require(frog).y = 10
    built.world.combat_stats.add(player, CombatStats(armor_class=10, hit_points=10, max_hit_points=10, strength=16, dexterity=10, constitution=10))
    built.world.weapons.add(player, weapon_for_name("longsword"))

    result = MovementSystem(ObstructionSystem(), MovementContextResolver()).handle(
        MoveAttempt(player, 1, 0), built.world
    )

    assert result.replacement == AttackAttempt(player, frog)


def test_starting_creatures_are_low_level_and_no_goblin() -> None:
    built = build_room_world(80, 40)
    creatures = {
        name.value: entity
        for entity, name in built.world.names.values.items()
        if built.world.creatures.has(entity)
    }
    creature_entities = list(built.world.creatures.values)

    assert len(creature_entities) == 2
    assert "goblin" not in creatures
    assert set(creatures) == {"frog"}
    assert all(built.world.presentations.require(entity).glyph == ":" for entity in creature_entities)

    for entity in creatures.values():
        assert built.world.combat_stats.require(entity).max_hit_points <= 3


def test_armor_class_applies_dexterity_caps() -> None:
    assert armor_class_for(16, armor_for_name("leather armor")) == 14
    assert armor_class_for(16, armor_for_name("scale mail")) == 16
    assert armor_class_for(16, armor_for_name("chain mail")) == 16
    assert armor_class_for(7, armor_for_name("chain mail")) == 16
    assert armor_class_for(16, armor_for_name("none")) == 13


def test_combat_hit_deals_damage_and_can_kill() -> None:
    built = build_room_world(80, 40)
    player = built.player
    frog = next(entity for entity, name in built.world.names.values.items() if name.value == "frog")
    built.world.names.add(player, Name("you"))
    built.world.factions.add(player, Faction("player"))
    built.world.combat_stats.add(player, CombatStats(armor_class=10, hit_points=10, max_hit_points=10, strength=18, dexterity=10, constitution=10))
    built.world.weapons.add(player, weapon_for_name("longsword"))
    built.world.combat_stats.require(frog).hit_points = 1

    result = CombatSystem(rng=random.Random(0)).handle(AttackAttempt(player, frog), built.world)

    assert any(isinstance(effect, DamageEntity) for effect in result.effects)
    assert any(isinstance(effect, KillEntity) for effect in result.effects)


class SequenceRng:
    def __init__(self, values: list[int]) -> None:
        self.values = values

    def randint(self, low: int, high: int) -> int:
        return self.values.pop(0)


def test_player_attack_message_uses_bare_verb_not_third_person() -> None:
    """Issue #78: "You hits!" / "You slashes!" should read as
    "You hit!" / "You slash!".

    The grammar fix lives in :func:`combat_system._attack_verb`: when
    the actor's display name is ``"you"`` the verb is rendered in its
    bare ("you ___") form. Third-person actors keep the original
    ``-s`` lexicon.
    """

    built = build_room_world(80, 40)
    player = built.player
    frog = next(
        entity
        for entity, name in built.world.names.values.items()
        if name.value == "frog"
    )
    built.world.names.add(player, Name("you"))
    built.world.factions.add(player, Faction("player"))
    built.world.combat_stats.add(
        player,
        CombatStats(
            armor_class=10,
            hit_points=10,
            max_hit_points=10,
            strength=18,
            dexterity=10,
            constitution=10,
        ),
    )
    built.world.weapons.add(player, weapon_for_name("longsword"))
    built.world.combat_stats.require(frog).hit_points = 1

    # Force a hit by using a high d20 + low damage seed.
    result = CombatSystem(rng=random.Random(0)).handle(
        AttackAttempt(player, frog), built.world
    )
    messages = [
        effect.text for effect in result.effects if isinstance(effect, EmitMessage)
    ]
    joined = " ".join(messages)
    # Bare verb forms — no third-person -s on "you ___" attacks.
    assert "You hits" not in joined
    assert "You slashes" not in joined
    assert "You stabs" not in joined
    assert "You bites" not in joined
    # And the bare form is what we render: longsword is slashing.
    assert any("You slash" in message for message in messages)


def test_creature_attack_message_keeps_third_person_form() -> None:
    """Behavior preservation: enemy attacks still read ``The frog bites!``."""

    built = build_room_world(80, 40)
    player = built.player
    frog = next(
        entity
        for entity, name in built.world.names.values.items()
        if name.value == "frog"
    )
    built.world.names.add(player, Name("you"))
    built.world.combat_stats.add(
        player,
        CombatStats(
            armor_class=10,
            hit_points=10,
            max_hit_points=10,
            strength=10,
            dexterity=10,
            constitution=10,
        ),
    )

    result = CombatSystem(rng=random.Random(0)).handle(
        AttackAttempt(frog, player), built.world
    )
    messages = [
        effect.text for effect in result.effects if isinstance(effect, EmitMessage)
    ]
    # The frog's third-person verb still gets the ``-s``.
    assert any("The frog bite" in message for message in messages)
    # And the joined text mentions "frog bites" rather than "frog bite".
    assert any("frog bites" in message for message in messages)


def test_faster_move_preempts_later_move_into_same_square() -> None:
    built = build_room_world(80, 40)
    player = built.player
    frog = next(entity for entity, name in built.world.names.values.items() if name.value == "frog")
    for entity in list(built.world.creatures.values):
        if entity != frog:
            built.world.remove_entity(entity)
    built.world.positions.require(player).x = 40
    built.world.positions.require(player).y = 20
    built.world.positions.require(frog).x = 42
    built.world.positions.require(frog).y = 20
    built.world.combat_stats.add(player, CombatStats(armor_class=10, hit_points=10, max_hit_points=10, strength=16, dexterity=10, constitution=10))
    built.world.weapons.add(player, weapon_for_name("longsword"))
    movement = MovementSystem(ObstructionSystem(), MovementContextResolver())
    turn = TurnSystem(
        movement=movement,
        combat=CombatSystem(rng=random.Random(0)),
        rng=SequenceRng([20, 1]),  # goblin initiative, then player initiative
    )

    result = turn.handle(MoveAttempt(player, 1, 0), built.world)

    assert MoveEntity(frog, 41, 20) in result.effects
    assert EmitMessage("Blocked.") in result.effects
    assert not any(isinstance(effect, DamageEntity) for effect in result.effects)
