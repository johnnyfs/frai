"""Tests for the roster + character-sheet modals (feature C)."""

from __future__ import annotations

import curses

from src.app import create_app
from src.core.character_creation import CharacterSheet
from src.core.combat import combat_stats_for_sheet, starter_armor_for_class, starter_weapon_for_class
from src.core.components import (
    Character,
    Equipment,
    ExperiencePoints,
    Inventory,
    InventoryStack,
)
from src.core.conditions import (
    Condition,
    ConditionKind,
    ConditionStore,
    DurationPolicy,
)
from src.core.modes import UIMode
from src.core.spells import SpellList, SpellSlots
from src.ui.character_sheet import build_view, render_lines
from src.ui.observation import observe
from src.ui.roster import RosterEntry, build_roster, roster_line


def _ready_app():
    app = create_app()
    app.handle_key(ord("y"))
    assert app.ui_mode is UIMode.play
    return app


def test_capital_p_opens_roster() -> None:
    app = _ready_app()
    app.handle_key(ord("P"))
    assert app.ui_mode is UIMode.roster
    assert app.roster_state is not None
    assert app.roster_state.entries
    # Cursor starts at first member.
    assert app.roster_state.cursor == 0


def test_lowercase_p_still_runs_perception_not_roster() -> None:
    app = _ready_app()
    app.handle_key(ord("p"))
    # Perception emits a message; it does NOT open the roster.
    assert app.ui_mode is UIMode.play
    assert app.roster_state is None


def test_roster_cursor_moves_with_j_k_and_arrow_keys() -> None:
    app = _ready_app()
    app.handle_key(ord("P"))
    state = app.roster_state
    assert state is not None
    assert state.cursor == 0

    app.handle_key(ord("j"))
    assert state.cursor == 1

    app.handle_key(curses.KEY_DOWN)
    assert state.cursor == min(2, len(state.entries) - 1)

    app.handle_key(ord("k"))
    assert state.cursor >= 0

    app.handle_key(curses.KEY_UP)
    assert state.cursor >= 0


def test_roster_enter_drills_into_character_sheet() -> None:
    app = _ready_app()
    app.handle_key(ord("P"))
    state = app.roster_state
    assert state is not None
    first = state.entries[0]

    app.handle_key(10)  # Enter
    assert app.ui_mode is UIMode.character_sheet
    assert app.character_sheet_state is not None
    assert app.character_sheet_state.view.entity == first.entity


def test_character_sheet_esc_backs_out_to_roster() -> None:
    app = _ready_app()
    app.handle_key(ord("P"))
    app.handle_key(10)
    assert app.ui_mode is UIMode.character_sheet

    app.handle_key(27)  # Esc
    assert app.ui_mode is UIMode.roster
    assert app.roster_state is not None


def test_roster_esc_returns_to_play() -> None:
    app = _ready_app()
    app.handle_key(ord("P"))
    app.handle_key(27)
    assert app.ui_mode is UIMode.play
    assert app.roster_state is None


def test_roster_q_returns_to_play() -> None:
    app = _ready_app()
    app.handle_key(ord("P"))
    app.handle_key(ord("q"))
    assert app.ui_mode is UIMode.play


def test_opening_roster_does_not_advance_clock() -> None:
    app = _ready_app()
    before = app.world.clock.elapsed_seconds
    app.handle_key(ord("P"))
    app.handle_key(ord("j"))
    app.handle_key(10)
    app.handle_key(27)
    app.handle_key(27)
    assert app.world.clock.elapsed_seconds == before


def test_roster_observation_surfaces_party() -> None:
    app = _ready_app()
    app.handle_key(ord("P"))
    obs = observe(app)
    assert obs.modal is not None
    assert obs.modal.kind == "roster"
    # One option per member.
    assert len(obs.modal.options) == len(app.party.members)


def test_character_sheet_observation_carries_rendered_lines() -> None:
    app = _ready_app()
    app.handle_key(ord("P"))
    app.handle_key(10)
    obs = observe(app)
    assert obs.modal is not None
    assert obs.modal.kind == "character_sheet"
    text = "\n".join(obs.modal.options)
    # Sheet must include some basic fields.
    assert "HP" in text
    assert "AC" in text
    assert "Faction" in text


def test_build_roster_skips_members_with_no_position() -> None:
    app = _ready_app()
    rogue = app.party.members[-1]
    app.world.positions.values.pop(rogue, None)
    entries = build_roster(app.world, app.party.members)
    ids = {entry.entity for entry in entries}
    assert rogue not in ids


def test_roster_line_includes_cursor_marker() -> None:
    entry = RosterEntry(
        entity=1,
        name="Test",
        character_class="Wizard",
        level=3,
        hp=12,
        max_hp=24,
        conditions=("blessed",),
    )
    line = roster_line(entry, selected=True)
    assert line.startswith(">")
    assert "Test" in line
    assert "Wizard L3" in line
    assert "HP 12/24" in line
    assert "blessed" in line

    unselected = roster_line(entry, selected=False)
    assert unselected.startswith(" ")


def _make_wizard_sheet() -> CharacterSheet:
    return CharacterSheet(
        race="Elf",
        character_class="Wizard",
        specialization="Evocation",
        base_attributes={"STR": 8, "DEX": 14, "CON": 12, "INT": 16, "WIS": 12, "CHA": 10},
        attributes={"STR": 8, "DEX": 16, "CON": 12, "INT": 16, "WIS": 12, "CHA": 10},
        cantrips=("Fire Bolt",),
        spells=("Magic Missile",),
        skills=("Arcana",),
        level=1,
    )


def test_character_sheet_view_contains_full_wizard_profile() -> None:
    """A wizard with conditions, inventory, spells, and slots is fully projected."""

    from src.core.party_state import PartyState

    app = create_app()
    app.handle_key(ord("y"))

    # Re-equip the player as a wizard with custom condition + inventory.
    sheet = _make_wizard_sheet()
    armor = starter_armor_for_class("Wizard")
    weapon = starter_weapon_for_class("Wizard")
    world = app.world
    player = app.player
    world.characters.add(player, Character(sheet))
    world.combat_stats.add(player, combat_stats_for_sheet(sheet, armor))
    world.armor.add(player, armor)
    world.weapons.add(player, weapon)

    inventory = Inventory(gold=50)
    inventory.items.append(InventoryStack(item_id="potion_healing", quantity=2))
    inventory.items.append(InventoryStack(item_id="quarterstaff", quantity=1))
    world.inventories.add(player, inventory)
    world.equipment.add(player, Equipment(weapon_item_id="quarterstaff"))
    world.experience_points.add(player, ExperiencePoints(value=0, level=1))

    world.spell_lists.add(player, SpellList(known=("magic_missile", "fire_bolt")))
    world.spell_slots.add(player, SpellSlots.from_pairs({1: 2}))

    condition_store = ConditionStore()
    condition_store.add(
        Condition(
            kind=ConditionKind.BLESSED,
            duration=DurationPolicy.rounds(3),
            rounds_remaining=3,
        )
    )
    world.conditions.add(player, condition_store)

    view = build_view(world, player)
    assert view is not None
    assert view.character_class == "Wizard"
    assert view.specialization == "Evocation"
    assert view.race == "Elf"
    assert "blessed" in view.conditions
    # Spell slots: one ledger level present (1).
    assert any(line.level == 1 and line.maximum == 2 for line in view.spell_slots)
    # Inventory has both stacks.
    item_ids = {entry.item_id for entry in view.inventory}
    assert "potion_healing" in item_ids
    assert "quarterstaff" in item_ids
    # Equipment marks quarterstaff as equipped.
    equipped = {entry.item_id for entry in view.inventory if entry.equipped}
    assert "quarterstaff" in equipped
    # Spells include both ids.
    assert "Magic Missile" in view.spells or "magic_missile" in view.spells
    # Abilities present.
    assert "INT" in view.abilities

    lines = render_lines(view)
    body = "\n".join(lines)
    assert "Wizard" in body
    assert "blessed" in body
    assert "Magic Missile" in body or "magic_missile" in body
    assert "AC" in body


def test_save_load_demotes_roster_modal(tmp_path) -> None:
    """A save mid-roster lands the player back in play on load."""

    from src.core.save import save_game, load_game

    app = _ready_app()
    app.handle_key(ord("P"))
    assert app.ui_mode is UIMode.roster
    path = tmp_path / "save.json"
    save_game(app, path)
    loaded = load_game(path)
    assert loaded.ui_mode is UIMode.play
    assert loaded.roster_state is None
    assert loaded.character_sheet_state is None


def test_save_load_demotes_character_sheet_modal(tmp_path) -> None:
    from src.core.save import save_game, load_game

    app = _ready_app()
    app.handle_key(ord("P"))
    app.handle_key(10)
    assert app.ui_mode is UIMode.character_sheet
    path = tmp_path / "save.json"
    save_game(app, path)
    loaded = load_game(path)
    assert loaded.ui_mode is UIMode.play
    assert loaded.character_sheet_state is None
