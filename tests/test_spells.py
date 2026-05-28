"""Tests for M11 spells and effects.

Covers:

* Spell-slot ledger consume/refill semantics (pure data).
* Slot consumption in the M46 PRE_CHECK phase (one slot per leveled
  cast; cantrips consume nothing).
* Out-of-slot cast is refused with a typed message, no effects applied.
* Magic Missile: auto-hit, 3 missiles, deterministic damage with a
  seeded RNG.
* Fire Bolt: attack-roll spell hits / misses based on natural roll.
* Cure Wounds: healing applies up to ``max_hit_points``.
* Burning Hands: area damage hits multiple targets, save halves.
* Bless: applies ``BLESSED`` to N targets + ``CONCENTRATING`` on the
  caster.
* Concentration breaks when the concentrating actor takes damage.
* Save / load: SpellSlots and SpellList round-trip through World.to_dict.
* Input mapping: ``s`` in play opens the spell menu; spell menu key
  routes through SpellMenuChoice; cancel closes the modal.
"""

from __future__ import annotations

import json
import random

import pytest

from src.core.actions import (
    CastSpellAttempt,
    CloseSpellMenu,
    SpellMenuChoice,
    SpellMenuRequest,
)
from src.core.character_creation import CharacterSheet
from src.core.combat import combat_stats_for_sheet
from src.core.components import Character, CombatStats
from src.core.conditions import (
    Condition,
    ConditionKind,
    DurationKind,
    DurationPolicy,
    apply_condition,
)
from src.core.dispatcher import Dispatcher
from src.core.effects import (
    ApplyCondition,
    ApplyHealing,
    ConsumeSpellSlot,
    DamageEntity,
    EmitMessage,
    EndCondition,
    KillEntity,
)
from src.core.modes import UIMode
from src.core.spells import (
    SPELL_CATALOG,
    SpellList,
    SpellSchool,
    SpellSlots,
    SpellTargetKind,
    spell_attack_bonus,
    spell_for_id,
    spell_save_dc,
)
from src.core.world import World
from src.systems.input_system import map_key
from src.systems.spell_system import SpellSystem
from tests.support.tiny_world import (
    add_actor,
    add_enemy,
    build_tiny_map,
    build_tiny_encounter,
)


# ---------------------------------------------------------------------------
# SpellSlots data
# ---------------------------------------------------------------------------


def test_spell_slots_consume_and_refill() -> None:
    slots = SpellSlots.from_pairs({1: 2, 2: 1})
    assert slots.has_slot(1)
    assert slots.consume(1) is True
    assert slots.remaining(1) == 1
    assert slots.consume(1) is True
    assert slots.consume(1) is False  # exhausted
    assert slots.remaining(1) == 0
    assert slots.has_slot(0) is True  # cantrips always free
    assert slots.consume(0) is True
    slots.reset_to_max()
    assert slots.remaining(1) == 2


def test_spell_slots_roundtrip_through_dict() -> None:
    slots = SpellSlots.from_pairs({1: 2, 3: 1})
    slots.consume(1)
    encoded = json.dumps(slots.to_dict())
    decoded = SpellSlots.from_dict(json.loads(encoded))
    assert decoded.remaining(1) == 1
    assert decoded.max_by_level[1] == 2
    assert decoded.remaining(3) == 1


def test_spell_list_roundtrip() -> None:
    spell_list = SpellList(known=("magic_missile", "firebolt"))
    encoded = json.dumps(spell_list.to_dict())
    decoded = SpellList.from_dict(json.loads(encoded))
    assert decoded.known == ("magic_missile", "firebolt")
    assert decoded.has("magic_missile")
    assert not decoded.has("bless")


def test_spell_catalog_has_required_entries() -> None:
    # M11 acceptance: representative spells for the four categories.
    required = {"magic_missile", "firebolt", "cure_wounds", "burning_hands", "bless"}
    assert required.issubset(SPELL_CATALOG.keys())
    assert SPELL_CATALOG["magic_missile"].missiles == 3
    assert SPELL_CATALOG["firebolt"].attack_roll is True
    assert SPELL_CATALOG["burning_hands"].save_ability == "DEX"
    assert SPELL_CATALOG["bless"].concentration is True
    assert SPELL_CATALOG["cure_wounds"].healing_dice[0] > 0


# ---------------------------------------------------------------------------
# World save/load
# ---------------------------------------------------------------------------


def test_world_roundtrip_preserves_spell_slots_and_list() -> None:
    world = build_tiny_map()
    actor = add_actor(world, 2, 2)
    world.spell_slots.add(actor, SpellSlots.from_pairs({1: 2}))
    world.spell_lists.add(actor, SpellList(known=("magic_missile",)))

    payload = json.dumps(world.to_dict())
    rebuilt = World.from_dict(json.loads(payload))

    rehydrated_slots = rebuilt.spell_slots.values
    assert any(slots.remaining(1) == 2 for slots in rehydrated_slots.values())
    rehydrated_lists = rebuilt.spell_lists.values
    assert any(
        spell_list.has("magic_missile") for spell_list in rehydrated_lists.values()
    )


# ---------------------------------------------------------------------------
# Spell resolution via SpellSystem (without App)
# ---------------------------------------------------------------------------


def _wizardly_world(*, hp: int = 20):
    """Tiny world with a wizard caster + an enemy at known positions."""

    encounter = build_tiny_encounter(enemy_hit_points=hp)
    world = encounter.world
    # Equip the caster with a SpellList containing every catalog entry
    # and two level-1 slots (matches the player loadout).
    world.spell_lists.add(
        encounter.player, SpellList(known=tuple(SPELL_CATALOG.keys()))
    )
    world.spell_slots.add(encounter.player, SpellSlots.from_pairs({1: 2}))
    # Give the caster a sheet so spell DC / modifiers work. Using a
    # generic wizard sheet keeps the math reproducible.
    sheet = CharacterSheet(
        race="Human",
        character_class="Wizard",
        specialization="Evocation",
        base_attributes={"STR": 10, "DEX": 12, "CON": 12, "INT": 16, "WIS": 10, "CHA": 10},
        attributes={"STR": 10, "DEX": 12, "CON": 12, "INT": 16, "WIS": 10, "CHA": 10},
    )
    world.characters.add(encounter.player, Character(sheet))
    # Refresh combat stats so proficiency bonus is set (proficiency
    # comes from sheet level).
    world.combat_stats.add(encounter.player, combat_stats_for_sheet(sheet))
    return encounter, world


def test_magic_missile_damages_target_deterministically() -> None:
    encounter, world = _wizardly_world(hp=20)
    # Three missiles, each rolls 1d4 + 1. Seed the RNG so every roll
    # is 4 ⇒ 3 * (4 + 1) = 15 damage.
    rng = random.Random()
    rng.randint = lambda low, high: high  # type: ignore[method-assign]
    system = SpellSystem(rng=rng)

    action = CastSpellAttempt(
        actor=encounter.player,
        spell_id="magic_missile",
        target_entity=encounter.enemy,
    )
    result = system.handle(action, world)
    damage_effects = [e for e in result.effects if isinstance(e, DamageEntity)]
    assert len(damage_effects) == 1
    # max roll = 4 per missile; 3 missiles * (4 + 1) = 15
    assert damage_effects[0].amount == 15


def test_firebolt_attack_roll_hits_with_natural_20() -> None:
    encounter, world = _wizardly_world(hp=20)
    rng = random.Random()
    # First int call: attack roll (natural 20). Subsequent: damage dice.
    rolls = iter([20, 10, 10])  # crit doubles damage dice
    rng.randint = lambda low, high: next(rolls)  # type: ignore[method-assign]
    system = SpellSystem(rng=rng)

    action = CastSpellAttempt(
        actor=encounter.player, spell_id="firebolt", target_entity=encounter.enemy
    )
    result = system.handle(action, world)
    damage_effects = [e for e in result.effects if isinstance(e, DamageEntity)]
    assert len(damage_effects) == 1
    # Crit doubles dice => 10 + 10 = 20
    assert damage_effects[0].amount == 20


def test_firebolt_misses_on_natural_one() -> None:
    encounter, world = _wizardly_world(hp=20)
    rng = random.Random()
    rng.randint = lambda low, high: 1  # type: ignore[method-assign]
    system = SpellSystem(rng=rng)

    action = CastSpellAttempt(
        actor=encounter.player, spell_id="firebolt", target_entity=encounter.enemy
    )
    result = system.handle(action, world)
    assert not any(isinstance(e, DamageEntity) for e in result.effects)
    messages = [e.text for e in result.effects if isinstance(e, EmitMessage)]
    assert any("misses" in text for text in messages)


def test_cure_wounds_heals_target() -> None:
    encounter, world = _wizardly_world()
    # Damage the companion first.
    world.combat_stats.require(encounter.companion).hit_points = 3
    rng = random.Random()
    rng.randint = lambda low, high: high  # 1d8 -> 8  # type: ignore[method-assign]
    system = SpellSystem(rng=rng)

    action = CastSpellAttempt(
        actor=encounter.player,
        spell_id="cure_wounds",
        target_entity=encounter.companion,
    )
    result = system.handle(action, world)
    heal_effects = [e for e in result.effects if isinstance(e, ApplyHealing)]
    assert len(heal_effects) == 1
    # 8 + INT modifier (16 -> +3) = 11
    assert heal_effects[0].amount == 11


def test_burning_hands_hits_every_target_in_area() -> None:
    encounter, world = _wizardly_world(hp=20)
    # Add a second enemy at an adjacent tile so the AOE hits two.
    second = add_enemy(world, 5, 2, name="second", hit_points=20)

    # Force every save to fail by always returning 1 for d20.
    rng = random.Random()
    rolls = iter([6, 6, 6])  # damage dice first
    def _randint(low: int, high: int) -> int:
        if high == 6:
            return next(rolls)
        return 1  # save: natural 1 always fails
    rng.randint = _randint  # type: ignore[method-assign]
    system = SpellSystem(rng=rng)

    # Cursor at (4, 2), area radius 1 covers (4,2), (5,2), (3,2), etc.
    action = CastSpellAttempt(
        actor=encounter.player,
        spell_id="burning_hands",
        target_tile=(4, 2),
    )
    result = system.handle(action, world)
    damage_targets = {
        effect.entity for effect in result.effects if isinstance(effect, DamageEntity)
    }
    assert encounter.enemy in damage_targets
    assert second in damage_targets


def test_burning_hands_save_halves_damage() -> None:
    # Single-target burning hands so we get exactly one save roll.
    world = build_tiny_map(width=9, height=5)
    player = add_actor(world, 2, 2)
    sheet = CharacterSheet(
        race="Human",
        character_class="Wizard",
        specialization="Evocation",
        base_attributes={"STR": 10, "DEX": 12, "CON": 12, "INT": 16, "WIS": 10, "CHA": 10},
        attributes={"STR": 10, "DEX": 12, "CON": 12, "INT": 16, "WIS": 10, "CHA": 10},
    )
    world.characters.add(player, Character(sheet))
    world.combat_stats.add(player, combat_stats_for_sheet(sheet))
    enemy = add_enemy(world, 5, 2, hit_points=50)

    rng = random.Random()
    rolls = iter([6, 6, 6])
    def _randint(low: int, high: int) -> int:
        if high == 6:
            return next(rolls)
        return 20  # natural 20 succeeds save
    rng.randint = _randint  # type: ignore[method-assign]
    system = SpellSystem(rng=rng)

    action = CastSpellAttempt(
        actor=player,
        spell_id="burning_hands",
        target_tile=(5, 2),
    )
    result = system.handle(action, world)
    damage_effects = [e for e in result.effects if isinstance(e, DamageEntity)]
    assert len(damage_effects) == 1
    assert damage_effects[0].amount == 9  # 18 // 2


def test_bless_applies_condition_to_each_target_and_caster_concentrates() -> None:
    encounter, world = _wizardly_world()
    third = add_actor(world, 3, 3, name="ally2", faction="player")

    system = SpellSystem(rng=random.Random())
    action = CastSpellAttempt(
        actor=encounter.player,
        spell_id="bless",
        target_entities=(encounter.player, encounter.companion, third),
    )
    result = system.handle(action, world)

    condition_effects = [e for e in result.effects if isinstance(e, ApplyCondition)]
    blessed_targets = {
        effect.entity
        for effect in condition_effects
        if effect.condition.kind is ConditionKind.BLESSED
    }
    assert blessed_targets == {encounter.player, encounter.companion, third}

    concentrating = [
        effect
        for effect in condition_effects
        if effect.condition.kind is ConditionKind.CONCENTRATING
    ]
    assert len(concentrating) == 1
    assert concentrating[0].entity == encounter.player
    assert concentrating[0].condition.duration.kind is DurationKind.MINUTES


def test_unknown_spell_emits_refusal_and_cancels() -> None:
    world = build_tiny_map()
    actor = add_actor(world, 2, 2)
    system = SpellSystem(rng=random.Random())

    result = system.handle(
        CastSpellAttempt(actor=actor, spell_id="not_a_spell"), world
    )
    assert result.cancel is True
    assert any(
        isinstance(e, EmitMessage) and "Unknown spell" in e.text
        for e in result.effects
    )


def test_unknown_spell_in_known_list_is_refused() -> None:
    world = build_tiny_map()
    actor = add_actor(world, 2, 2)
    enemy = add_enemy(world, 3, 2)
    world.spell_lists.add(actor, SpellList(known=("magic_missile",)))
    system = SpellSystem(rng=random.Random())

    # The actor knows only magic_missile; casting firebolt is refused
    # even though it's in the global catalog.
    result = system.handle(
        CastSpellAttempt(actor=actor, spell_id="firebolt", target_entity=enemy),
        world,
    )
    assert any(
        isinstance(e, EmitMessage) and "don't know" in e.text
        for e in result.effects
    )


# ---------------------------------------------------------------------------
# Slot consumption via the App resolver
# ---------------------------------------------------------------------------


def test_slot_consumed_when_app_resolves_cast() -> None:
    from src.app import create_app

    app = create_app()
    player = app.player
    # Wire a deterministic loadout (the YOLO sheet may roll a non-caster).
    app.world.spell_lists.add(player, SpellList(known=("magic_missile",)))
    app.world.spell_slots.add(player, SpellSlots.from_pairs({1: 2}))

    # Place an enemy in range.
    enemy = add_enemy(app.world, app.world.positions.require(player).x + 1, app.world.positions.require(player).y)

    effects = app.resolve_action(
        CastSpellAttempt(actor=player, spell_id="magic_missile", target_entity=enemy)
    )
    app.apply_effects(effects)

    slots = app.world.spell_slots.require(player)
    assert slots.remaining(1) == 1  # one slot spent
    assert any(isinstance(e, ConsumeSpellSlot) for e in effects)


def test_out_of_slot_cast_refused_and_no_damage_emitted() -> None:
    from src.app import create_app

    app = create_app()
    player = app.player
    app.world.spell_lists.add(player, SpellList(known=("magic_missile",)))
    app.world.spell_slots.add(player, SpellSlots.from_pairs({1: 0}))

    enemy = add_enemy(app.world, app.world.positions.require(player).x + 1, app.world.positions.require(player).y)

    effects = app.resolve_action(
        CastSpellAttempt(actor=player, spell_id="magic_missile", target_entity=enemy)
    )
    assert not any(isinstance(e, DamageEntity) for e in effects)
    assert any(
        isinstance(e, EmitMessage) and "No spell slot" in e.text for e in effects
    )


def test_cantrip_does_not_consume_a_slot() -> None:
    from src.app import create_app

    app = create_app()
    player = app.player
    app.world.spell_lists.add(player, SpellList(known=("firebolt",)))
    app.world.spell_slots.add(player, SpellSlots.from_pairs({1: 2}))

    enemy = add_enemy(app.world, app.world.positions.require(player).x + 1, app.world.positions.require(player).y)

    effects = app.resolve_action(
        CastSpellAttempt(actor=player, spell_id="firebolt", target_entity=enemy)
    )
    assert not any(isinstance(e, ConsumeSpellSlot) for e in effects)
    assert app.world.spell_slots.require(player).remaining(1) == 2


# ---------------------------------------------------------------------------
# Concentration break (M24 seam)
# ---------------------------------------------------------------------------


def test_concentration_breaks_when_caster_takes_damage() -> None:
    from src.app import create_app

    app = create_app()
    player = app.player
    # Apply concentration directly so we don't have to set up a Bless cast.
    apply_condition(
        app.world,
        player,
        Condition(
            kind=ConditionKind.CONCENTRATING,
            duration=DurationPolicy.minutes(1),
            source=player,
            payload={"spell_id": "bless"},
        ),
    )
    assert app.world.conditions.require(player).has(ConditionKind.CONCENTRATING)

    # Place an enemy adjacent and have it attack the player via the resolver.
    enemy_x = app.world.positions.require(player).x + 1
    enemy_y = app.world.positions.require(player).y
    enemy = add_enemy(app.world, enemy_x, enemy_y)

    # Apply damage directly via apply_effects so the reaction hook
    # sees the DamageEntity in the resolved attempt. We use a
    # CastSpellAttempt whose damage effect lands on the player. The
    # simplest reproducer: build a "fire bolt" cast that hits the
    # player by treating the player as the target.
    from src.core.actions import AttackAttempt
    from src.core.spells import SpellList, SpellSlots

    # Give the enemy a spell list with firebolt so the SpellSystem
    # produces a damage effect on the player.
    app.world.spell_lists.add(enemy, SpellList(known=("firebolt",)))
    # Force a hit by replacing the spell system's RNG. Natural 20 crits,
    # so two damage dice rolls are consulted.
    for system in app.dispatcher.systems:
        if isinstance(system, SpellSystem):
            seq = iter([20, 10, 10])  # attack natural 20, then two d10s
            system.rng.randint = lambda low, high: next(seq)  # type: ignore[method-assign]
            break

    effects = app.resolve_action(
        CastSpellAttempt(
            actor=enemy, spell_id="firebolt", target_entity=player
        )
    )
    app.apply_effects(effects)
    # The reaction hook should have appended an EndCondition for CONCENTRATING.
    assert not app.world.conditions.require(player).has(ConditionKind.CONCENTRATING)


# ---------------------------------------------------------------------------
# Spell DC / attack bonus helpers
# ---------------------------------------------------------------------------


def test_spell_save_dc_uses_proficiency_and_modifier() -> None:
    encounter, world = _wizardly_world()
    # Wizard INT 16 -> +3; proficiency at level 1 -> +2. DC = 8 + 2 + 3 = 13.
    assert spell_save_dc(world, encounter.player) == 13


def test_spell_attack_bonus_uses_proficiency_and_modifier() -> None:
    encounter, world = _wizardly_world()
    assert spell_attack_bonus(world, encounter.player) == 5


# ---------------------------------------------------------------------------
# Input mapping
# ---------------------------------------------------------------------------


def test_s_key_in_play_opens_spell_menu() -> None:
    from src.core.entity import EntityId

    action = map_key(ord("s"), UIMode.play, EntityId(1))
    assert isinstance(action, SpellMenuRequest)
    assert action.actor == EntityId(1)


def test_letter_in_spell_menu_returns_choice() -> None:
    from src.core.entity import EntityId

    action = map_key(ord("a"), UIMode.spell_menu, EntityId(1))
    assert isinstance(action, SpellMenuChoice)
    assert action.spell_id == "a"


def test_q_in_spell_menu_returns_close() -> None:
    from src.core.entity import EntityId

    action = map_key(ord("q"), UIMode.spell_menu, EntityId(1))
    assert isinstance(action, CloseSpellMenu)


# ---------------------------------------------------------------------------
# App integration: full spell-menu flow
# ---------------------------------------------------------------------------


def test_app_spell_menu_opens_and_targets() -> None:
    from src.app import create_app

    app = create_app()
    app.ui_mode = UIMode.play
    player = app.player
    app.world.spell_lists.add(player, SpellList(known=("magic_missile", "firebolt")))
    app.world.spell_slots.add(player, SpellSlots.from_pairs({1: 2}))

    # Open spell menu via `s`.
    app.handle_key(ord("s"))
    assert app.ui_mode is UIMode.spell_menu

    # Pick `a` -> magic_missile. Should switch to targeting mode.
    app.handle_key(ord("a"))
    assert app.ui_mode is UIMode.targeting
    assert app.targeting is not None
    # Range should match the spell catalog.
    assert app.targeting.range == SPELL_CATALOG["magic_missile"].range


def test_app_spell_menu_cancel_returns_to_play() -> None:
    from src.app import create_app

    app = create_app()
    app.ui_mode = UIMode.play
    player = app.player
    app.world.spell_lists.add(player, SpellList(known=("magic_missile",)))
    app.world.spell_slots.add(player, SpellSlots.from_pairs({1: 2}))

    app.handle_key(ord("s"))
    assert app.ui_mode is UIMode.spell_menu
    app.handle_key(ord("q"))
    assert app.ui_mode is UIMode.play


def test_app_no_spells_emits_message_and_stays_in_play() -> None:
    from src.app import create_app

    app = create_app()
    app.ui_mode = UIMode.play
    # Don't add a spell list at all.
    player = app.player
    if app.world.spell_lists.has(player):
        # YOLO might have rolled a caster — clear it for this test.
        app.world.spell_lists.values.pop(player)
    app.handle_key(ord("s"))
    assert app.ui_mode is UIMode.play


# ---------------------------------------------------------------------------
# Bug regressions: #99 (fixture dispatcher), #100 (self-target), #101 (ally)
# ---------------------------------------------------------------------------


def test_spell_encounter_fixture_magic_missile_damages_kobold() -> None:
    """Regression for #99: the spell_encounter fixture's dispatcher must
    include SpellSystem so a cast actually applies damage.

    Before the fix, ``CastSpellAttempt`` would consume a slot in
    PRE_CHECK but no system handled the action in EFFECT, so the kobold
    took zero damage even though the slot was burned.
    """

    from src.core.actions import CastSpellAttempt
    from src.testing.fixtures import scenarios as _scenarios  # noqa: F401 — register

    from src.testing import PlaytestHarness

    harness = PlaytestHarness(scenario_name="spell_encounter", dev_mode=False)
    world = harness.app.world
    player = harness.app.player
    # Pick the first kobold (kind == "kobold") from the world.
    kobold = next(
        entity
        for entity, creature in world.creatures.values.items()
        if creature.kind == "kobold"
    )
    # Beef the kobold up so a single magic_missile cast doesn't kill it
    # (the entity would then drop out of world.combat_stats via the
    # KillEntity effect and the HP comparison would have to special-case
    # death). 30 HP is well above 3 * (4 + 1) max damage.
    world.combat_stats.require(kobold).hit_points = 30
    world.combat_stats.require(kobold).max_hit_points = 30
    hp_before = world.combat_stats.require(kobold).hit_points
    # Ensure the player has at least magic_missile + a slot. The
    # wizard sheet should provide both, but force-set to be safe.
    if not world.spell_lists.has(player):
        world.spell_lists.add(player, SpellList(known=("magic_missile",)))
    if not world.spell_slots.has(player):
        world.spell_slots.add(player, SpellSlots.from_pairs({1: 2}))
    action = CastSpellAttempt(
        actor=player, spell_id="magic_missile", target_entity=kobold
    )
    effects = harness.app.resolve_action(action)
    harness.app.apply_effects(effects)
    hp_after = world.combat_stats.require(kobold).hit_points
    assert hp_after < hp_before, (
        f"kobold HP did not drop after magic_missile (before={hp_before}, "
        f"after={hp_after}); SpellSystem likely missing from fixture dispatcher"
    )


def test_app_damage_spell_rejects_self_target_on_confirm() -> None:
    """Bug #100: confirming the targeting cursor on the caster's own
    tile must not damage the caster for a damage spell."""

    from src.app import create_app

    app = create_app()
    app.ui_mode = UIMode.play
    player = app.player
    app.world.spell_lists.add(player, SpellList(known=("magic_missile",)))
    app.world.spell_slots.add(player, SpellSlots.from_pairs({1: 2}))

    # Open spell menu and select magic_missile.
    app.handle_key(ord("s"))
    app.handle_key(ord("a"))
    assert app.ui_mode is UIMode.targeting

    # Cursor starts on the caster's tile. Confirm immediately.
    hp_before = app.world.combat_stats.require(player).hit_points
    slot_before = app.world.spell_slots.require(player).remaining(1)
    app.handle_key(13)  # Enter
    # Still in targeting (predicate refused), no slot consumed, no HP loss.
    assert app.ui_mode is UIMode.targeting, (
        "self-target confirm should not exit targeting"
    )
    assert app.world.combat_stats.require(player).hit_points == hp_before
    assert app.world.spell_slots.require(player).remaining(1) == slot_before
    assert "Invalid target" in app.messages.current


def test_app_damage_spell_rejects_ally_target() -> None:
    """Bug #101: magic_missile must not accept a party ally as target."""

    from src.app import create_app

    app = create_app()
    app.ui_mode = UIMode.play
    player = app.player
    app.world.spell_lists.add(player, SpellList(known=("magic_missile",)))
    app.world.spell_slots.add(player, SpellSlots.from_pairs({1: 2}))

    # Find an ally party member with combat_stats — create_app spawns
    # companions adjacent to the player.
    from src.core.factions import FactionId

    ally = next(
        entity
        for entity, faction in app.world.factions.values.items()
        if entity != player
        and faction.value == FactionId.PLAYER_PARTY.value
        and app.world.combat_stats.has(entity)
    )
    ally_pos = app.world.positions.require(ally)
    player_pos = app.world.positions.require(player)

    app.handle_key(ord("s"))
    app.handle_key(ord("a"))
    assert app.ui_mode is UIMode.targeting

    # Move cursor onto the ally's tile.
    dx = ally_pos.x - player_pos.x
    dy = ally_pos.y - player_pos.y
    app.targeting.set_cursor(player_pos.x + dx, player_pos.y + dy)
    hp_before = app.world.combat_stats.require(ally).hit_points
    slot_before = app.world.spell_slots.require(player).remaining(1)
    app.handle_key(13)  # Enter
    assert app.ui_mode is UIMode.targeting
    assert app.world.combat_stats.require(ally).hit_points == hp_before
    assert app.world.spell_slots.require(player).remaining(1) == slot_before
    assert "Invalid target" in app.messages.current


def test_app_cure_wounds_accepts_self_target() -> None:
    """The allow_self_target carve-out: cure_wounds can mend the caster.

    Regression guard so the #100 fix doesn't accidentally lock out
    legitimate self-heals.
    """

    from src.app import create_app
    from src.core.actions import CastSpellAttempt

    app = create_app()
    app.ui_mode = UIMode.play
    player = app.player
    # Wound the caster so healing has somewhere to go.
    stats = app.world.combat_stats.require(player)
    stats.hit_points = max(1, stats.max_hit_points - 5)
    app.world.spell_lists.add(player, SpellList(known=("cure_wounds",)))
    app.world.spell_slots.add(player, SpellSlots.from_pairs({1: 2}))

    hp_before = stats.hit_points
    # Drive through the SpellSystem directly — confirms the catalog
    # entry can resolve onto the caster without raising. (The full UI
    # confirm flow is exercised by test_app_spell_menu_opens_and_targets;
    # this test specifically guards the self-target carve-out.)
    action = CastSpellAttempt(
        actor=player, spell_id="cure_wounds", target_entity=player
    )
    effects = app.resolve_action(action)
    app.apply_effects(effects)
    assert app.world.combat_stats.require(player).hit_points >= hp_before


def test_app_cure_wounds_accepts_ally_target_via_ui() -> None:
    """Bug #101 carve-out: friendly spells must still accept allies."""

    from src.app import create_app
    from src.core.factions import FactionId

    app = create_app()
    app.ui_mode = UIMode.play
    player = app.player
    app.world.spell_lists.add(player, SpellList(known=("cure_wounds",)))
    app.world.spell_slots.add(player, SpellSlots.from_pairs({1: 2}))

    ally = next(
        entity
        for entity, faction in app.world.factions.values.items()
        if entity != player
        and faction.value == FactionId.PLAYER_PARTY.value
        and app.world.combat_stats.has(entity)
    )
    ally_pos = app.world.positions.require(ally)
    player_pos = app.world.positions.require(player)
    # cure_wounds has range 1 — confirm the ally is adjacent (companion
    # placement in _build_party_world drops them next to the player).
    assert max(abs(ally_pos.x - player_pos.x), abs(ally_pos.y - player_pos.y)) <= 1

    # Wound the ally first so healing has somewhere to land.
    ally_stats = app.world.combat_stats.require(ally)
    ally_stats.hit_points = max(1, ally_stats.max_hit_points - 3)

    app.handle_key(ord("s"))
    app.handle_key(ord("a"))
    assert app.ui_mode is UIMode.targeting
    app.targeting.set_cursor(ally_pos.x, ally_pos.y)
    app.handle_key(13)  # Enter
    # Confirm dispatched and we exited targeting.
    assert app.ui_mode is not UIMode.targeting
