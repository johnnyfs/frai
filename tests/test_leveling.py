"""Tests for M25 Leveling, XP, rewards.

Covers the pure data layer (XP thresholds, CR-to-XP lookup, per-class
HP gain, per-class slot progression), the effect handlers
(``GrantXP``, ``LevelUp``), the combat-kill XP hook, the M14 quest
reward integration, the level-up modal flow, and save/load round-trip
for the new components.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.app import create_app
from src.core.character_creation import CharacterSheet
from src.core.components import (
    BlocksMovement,
    BossMarker,
    CombatStats,
    Creature,
    ExperiencePoints,
    Faction,
    Inventory,
    LevelUpAvailable,
    LootDrop,
    Name,
    Position,
    Presentation,
)
from src.core.creatures import (
    combat_stats_for_creature,
    creature_component,
    creature_for_key,
    weapon_for_creature,
)
from src.core.effects import GrantXP, KillEntity, LevelUp
from src.core.factions import FactionId
from src.core.items import add_item
from src.core.leveling import (
    DEFAULT_KILL_XP,
    MAX_LEVEL,
    XP_THRESHOLDS,
    hp_gain_for_level_up,
    level_for_xp,
    next_threshold,
    slot_progression_for,
    threshold_for,
    xp_for_kill,
)
from src.core.modes import UIMode
from src.core.quest import QUESTS, QuestState, SUNKEN_GATE_QUEST_ID
from src.core.save import load_game, save_game


# ---------------------------------------------------------------------------
# Pure data layer
# ---------------------------------------------------------------------------


def test_xp_threshold_table_matches_srd_lite_for_levels_2_and_3() -> None:
    assert threshold_for(1) == 0
    assert threshold_for(2) == 300
    assert threshold_for(3) == 900
    # Above the engine cap the helper clamps to the last known
    # threshold rather than raising so callers stay safe.
    assert threshold_for(99) == XP_THRESHOLDS[-1]


def test_next_threshold_returns_none_at_max_level() -> None:
    assert next_threshold(1) == 300
    assert next_threshold(2) == 900
    assert next_threshold(MAX_LEVEL) is None


def test_level_for_xp_clamps_at_max_level() -> None:
    assert level_for_xp(0) == 1
    assert level_for_xp(299) == 1
    assert level_for_xp(300) == 2
    assert level_for_xp(899) == 2
    assert level_for_xp(900) == 3
    # Way above the table: still clamped to MAX_LEVEL.
    assert level_for_xp(1_000_000) == MAX_LEVEL


def test_xp_for_kill_uses_cr_table_and_falls_back_to_default() -> None:
    assert xp_for_kill("goblin") == 50
    assert xp_for_kill("boss_kobold_warlord") == 450
    assert xp_for_kill("frog") == 10
    assert xp_for_kill("unknown_creature_kind") == DEFAULT_KILL_XP


def test_hp_gain_uses_fixed_average_plus_con_modifier() -> None:
    # d8 hit die, CON 10 (mod +0) -> 5 + 0
    assert hp_gain_for_level_up(8, 10) == 5
    # d10 hit die, CON 14 (mod +2) -> 6 + 2
    assert hp_gain_for_level_up(10, 14) == 8
    # Floor at 1: extreme negative CON should not yield a non-positive gain.
    assert hp_gain_for_level_up(6, 1) >= 1


def test_slot_progression_for_full_caster_at_level_2_and_3() -> None:
    # Full casters gain a 3rd L1 slot at level 2 and a L2 slot at level 3.
    assert slot_progression_for("Wizard", 2) == {1: 3}
    assert slot_progression_for("Wizard", 3) == {1: 4, 2: 2}
    # Half casters get their first slot at level 2 and a second at 3.
    assert slot_progression_for("Paladin", 2) == {1: 2}
    assert slot_progression_for("Paladin", 3) == {1: 3}
    # Non-casters and unknown levels return an empty dict.
    assert slot_progression_for("Fighter", 2) == {}
    assert slot_progression_for("Wizard", 99) == {}


# ---------------------------------------------------------------------------
# Effect handlers
# ---------------------------------------------------------------------------


def _make_app_with_player():
    """Build a clean app with a fresh YOLO player and no live enemies."""

    app = create_app()
    app.handle_key(ord("y"))  # finish YOLO start
    for entity in list(app.world.creatures.values):
        app.world.remove_entity(entity)
    app.sync_play_mode()
    app.ui_mode = UIMode.play
    return app


def test_grant_xp_creates_ledger_when_missing() -> None:
    app = _make_app_with_player()
    # Force-remove any pre-attached ledger so we exercise the "no
    # ledger yet" branch deterministically.
    app.world.experience_points.values.pop(app.player, None)

    app.apply_effects([GrantXP(app.player, 100)])

    ledger = app.world.experience_points.require(app.player)
    assert ledger.value == 100
    assert ledger.level == 1
    # No threshold crossed, no pending level-up.
    assert app.world.level_up_pending.get(app.player) is None


def test_grant_xp_crossing_threshold_attaches_level_up_pending() -> None:
    app = _make_app_with_player()
    app.apply_effects([GrantXP(app.player, 300)])

    pending = app.world.level_up_pending.get(app.player)
    assert pending is not None
    assert pending.target_level == 2


def test_grant_xp_multi_level_jump_surfaces_one_pending_at_a_time() -> None:
    app = _make_app_with_player()
    # Granting enough XP for level 3 directly should still surface only
    # the next pending level (2) so the player consumes them one at a
    # time through the modal.
    app.apply_effects([GrantXP(app.player, 1000)])

    pending = app.world.level_up_pending.get(app.player)
    assert pending is not None
    assert pending.target_level == 2


def test_level_up_applies_hp_gain_and_refreshes_proficiency() -> None:
    app = _make_app_with_player()
    app.apply_effects([GrantXP(app.player, 300)])
    stats_before = app.world.combat_stats.require(app.player)
    old_max_hp = stats_before.max_hit_points
    old_proficiency = stats_before.proficiency_bonus

    app.apply_effects([LevelUp(app.player)])

    stats_after = app.world.combat_stats.require(app.player)
    sheet = app.world.characters.require(app.player).sheet
    assert sheet.level == 2
    assert stats_after.max_hit_points > old_max_hp
    # Proficiency at level 2 is still +2 in the SRD; we re-derive to
    # avoid hardcoding the exact bump.
    assert stats_after.proficiency_bonus >= old_proficiency
    # The pending marker is cleared after the confirm.
    assert app.world.level_up_pending.get(app.player) is None


def test_level_up_for_caster_grants_new_spell_slots() -> None:
    """A wizard hitting level 2 should gain a 3rd level-1 slot."""

    app = _make_app_with_player()
    # Force the player's class into a caster so this test is independent
    # of the YOLO roll.
    from src.core.character_creation import CharacterSheet
    from src.core.combat import combat_stats_for_sheet, starter_armor_for_class
    from src.core.spells import (
        SpellList,
        SpellSlots,
        starting_spell_loadout_for_class,
    )

    base_sheet = app.world.characters.require(app.player).sheet
    wizard_sheet = CharacterSheet(
        race=base_sheet.race or "Human",
        character_class="Wizard",
        specialization="Evocation",
        base_attributes=dict(base_sheet.base_attributes),
        attributes=dict(base_sheet.attributes),
        cantrips=("firebolt",),
        spells=("magic_missile",),
        skills=(),
        level=1,
    )
    app.world.characters.require(app.player).sheet = wizard_sheet
    armor = starter_armor_for_class("Wizard")
    app.world.armor.add(app.player, armor)
    app.world.combat_stats.add(
        app.player, combat_stats_for_sheet(wizard_sheet, armor)
    )
    known, slot_pairs = starting_spell_loadout_for_class("Wizard")
    app.world.spell_lists.add(app.player, SpellList(known=tuple(known)))
    app.world.spell_slots.add(app.player, SpellSlots.from_pairs(slot_pairs))

    app.apply_effects([GrantXP(app.player, 300)])
    app.apply_effects([LevelUp(app.player)])

    slots = app.world.spell_slots.require(app.player)
    assert slots.max_by_level.get(1, 0) == 3
    assert slots.remaining(1) == 3


# ---------------------------------------------------------------------------
# Combat XP hook
# ---------------------------------------------------------------------------


def _spawn_goblin_next_to_player(app):
    spec = creature_for_key("goblin")
    position = app.world.positions.require(app.player)
    entity = app.world.create_entity()
    app.world.positions.add(entity, Position(x=position.x + 2, y=position.y))
    app.world.presentations.add(entity, Presentation(spec.glyph))
    app.world.names.add(entity, Name(spec.name))
    app.world.blockers.add(entity, BlocksMovement("occupied"))
    app.world.creatures.add(entity, creature_component(spec))
    app.world.factions.add(entity, Faction(FactionId.DUNGEON.value))
    app.world.combat_stats.add(entity, combat_stats_for_creature(spec))
    app.world.weapons.add(entity, weapon_for_creature(spec))
    return entity


def test_killing_creature_grants_xp_split_across_party() -> None:
    app = _make_app_with_player()
    goblin = _spawn_goblin_next_to_player(app)
    party_size = len(app.party.members)
    pool = xp_for_kill("goblin")
    expected_share = max(1, pool // party_size)

    # Capture starting XP per member.
    starting = {
        member: (app.world.experience_points.get(member).value
                 if app.world.experience_points.has(member) else 0)
        for member in app.party.members
    }

    app.apply_effects([KillEntity(goblin)])

    for member in app.party.members:
        ledger = app.world.experience_points.require(member)
        assert ledger.value == starting[member] + expected_share


def test_killing_creature_does_not_grant_xp_to_dead_party_members() -> None:
    app = _make_app_with_player()
    goblin = _spawn_goblin_next_to_player(app)
    # Knock out the last party member so they don't count as living.
    fallen = app.party.members[-1]
    fallen_stats = app.world.combat_stats.require(fallen)
    fallen_stats.hit_points = 0
    living_count = sum(
        1
        for member in app.party.members
        if app.world.combat_stats.require(member).hit_points > 0
    )

    pool = xp_for_kill("goblin")
    expected_share = max(1, pool // living_count)

    app.apply_effects([KillEntity(goblin)])

    # The fallen member should not have an XP entry (or should be 0).
    fallen_xp = app.world.experience_points.get(fallen)
    assert fallen_xp is None or fallen_xp.value == 0
    # A living member gets the expected share.
    living_member = app.party.members[0]
    assert (
        app.world.experience_points.require(living_member).value
        == expected_share
    )


# ---------------------------------------------------------------------------
# Quest reward integration (M14 wiring)
# ---------------------------------------------------------------------------


def test_quest_reward_grants_real_xp_via_grant_xp_effect() -> None:
    """The M14 quest completion now awards real XP, not a message-only stub."""

    app = _make_app_with_player()
    app.party.quests.set_state(SUNKEN_GATE_QUEST_ID, QuestState.ACCEPTED)

    # Capture starting XP per member.
    starting = {
        member: (app.world.experience_points.get(member).value
                 if app.world.experience_points.has(member) else 0)
        for member in app.party.members
    }

    # Spawn the boss next to the player, pre-seed the chalice into the
    # player's inventory, and kill the boss to complete the quest.
    spec = creature_for_key("boss_kobold_warlord")
    position = app.world.positions.require(app.player)
    boss = app.world.create_entity()
    app.world.positions.add(boss, Position(x=position.x + 2, y=position.y))
    app.world.presentations.add(boss, Presentation(spec.glyph))
    app.world.names.add(boss, Name(spec.name))
    app.world.blockers.add(boss, BlocksMovement("occupied"))
    app.world.creatures.add(boss, creature_component(spec))
    app.world.factions.add(boss, Faction(FactionId.DUNGEON.value))
    app.world.combat_stats.add(boss, combat_stats_for_creature(spec))
    app.world.weapons.add(boss, weapon_for_creature(spec))
    app.world.boss_markers.add(boss, BossMarker(token="sunken_gate_warlord"))
    inventory = app.world.inventories.get(app.player)
    if inventory is None:
        inventory = Inventory()
        app.world.inventories.add(app.player, inventory)
    add_item(inventory, "treasure.golden_chalice")

    app.apply_effects([KillEntity(boss)])

    quest = QUESTS.require(SUNKEN_GATE_QUEST_ID)
    boss_pool = xp_for_kill("boss_kobold_warlord")
    living = [
        member
        for member in app.party.members
        if app.world.combat_stats.require(member).hit_points > 0
    ]
    kill_share = max(1, boss_pool // len(living))
    # The reward XP is added on top of the combat XP.
    expected_per_member = kill_share + quest.reward.xp_per_member
    for member in app.party.members:
        if member not in living:
            continue
        ledger = app.world.experience_points.require(member)
        assert ledger.value == starting[member] + expected_per_member


# ---------------------------------------------------------------------------
# Level-up modal flow
# ---------------------------------------------------------------------------


def test_grant_xp_threshold_opens_level_up_modal_automatically() -> None:
    app = _make_app_with_player()

    app.apply_effects([GrantXP(app.player, 300)])

    assert app.ui_mode is UIMode.level_up


def test_level_up_confirm_key_resolves_level_up_and_closes_modal() -> None:
    app = _make_app_with_player()
    app.apply_effects([GrantXP(app.player, 300)])
    assert app.ui_mode is UIMode.level_up

    # Press y -- confirm the level-up.
    app.handle_key(ord("y"))

    assert app.ui_mode is UIMode.play
    assert app.world.level_up_pending.get(app.player) is None
    sheet = app.world.characters.require(app.player).sheet
    assert sheet.level == 2


def test_level_up_dismiss_key_keeps_marker_and_closes_modal() -> None:
    app = _make_app_with_player()
    app.apply_effects([GrantXP(app.player, 300)])

    # Press q -- dismiss without applying.
    app.handle_key(ord("q"))

    assert app.ui_mode is UIMode.play
    pending = app.world.level_up_pending.get(app.player)
    assert pending is not None
    assert pending.target_level == 2
    # Sheet level unchanged.
    sheet = app.world.characters.require(app.player).sheet
    assert sheet.level == 1


# ---------------------------------------------------------------------------
# Save / load
# ---------------------------------------------------------------------------


def test_experience_and_pending_level_up_roundtrip_through_save_load(
    tmp_path: Path,
) -> None:
    app = _make_app_with_player()
    app.apply_effects([GrantXP(app.player, 300)])
    # Dismiss so the marker stays attached and we exercise the
    # round-trip for both components.
    app.handle_key(ord("q"))

    save_path = tmp_path / "save.json"
    save_game(app, save_path)

    loaded = load_game(save_path)

    ledger = loaded.world.experience_points.require(app.player)
    pending = loaded.world.level_up_pending.get(app.player)
    assert ledger.value == 300
    assert pending is not None
    assert pending.target_level == 2
