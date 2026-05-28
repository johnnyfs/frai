import pytest

from src.core.combat import (
    ability_modifier,
    combat_stats_for_sheet,
    starter_armor_for_class,
    starter_weapon_for_class,
)
from src.core.character_creation import (
    ABILITIES,
    CLASSES,
    RACES,
    CharacterSheet,
    CharacterCreationState,
    choice_for_key,
    can_advance,
    class_by_name,
    initial_character_creation_state,
    keymap_for_step,
    next_step,
    previous_step,
    to_character_sheet,
    total_attributes,
    with_selection,
)


BASE_ATTRIBUTES = {"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10}


def completed_state_for(race_name: str, class_name: str) -> CharacterCreationState:
    state = CharacterCreationState(step="race", base_attributes=dict(BASE_ATTRIBUTES))
    state = with_selection(state, race_name)
    character_class = next(
        character_class for character_class in CLASSES if character_class.name == class_name
    )
    state = with_selection(state, character_class.name)
    state = with_selection(state, character_class.specializations[0])
    for choices, count in (
        (character_class.cantrip_choices, character_class.cantrip_count),
        (character_class.spell_choices, character_class.spell_count),
        (character_class.skill_choices, character_class.skill_count),
    ):
        for choice in choices[:count]:
            state = with_selection(state, choice)
        if count:
            assert can_advance(state)
            state = next_step(state)
    while state.step != "confirm":
        assert can_advance(state)
        state = next_step(state)
    return state


def test_character_creation_tree_can_move_forward_and_back() -> None:
    state = initial_character_creation_state()

    state = with_selection(state, "Dwarf")
    assert state.step == "class"
    assert state.race == "Dwarf"

    state = previous_step(state)
    assert state.step == "race"


def test_class_selection_resets_dependent_choices() -> None:
    state = CharacterCreationState(
        step="class",
        cursor=0,
        race="Human",
        character_class="Wizard",
        specialization="Evocation",
        cantrips=("Light",),
        spells=("Magic Missile",),
        skills=("Arcana",),
        base_attributes={"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
    )

    state = with_selection(state, "Fighter")

    assert state.character_class == "Fighter"
    assert state.specialization is None
    assert state.cantrips == ()
    assert state.spells == ()
    assert state.skills == ()


def test_race_bonuses_apply_to_total_attributes() -> None:
    state = CharacterCreationState(
        race="Human",
        base_attributes={"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
    )

    assert total_attributes(state) == {
        "STR": 11,
        "DEX": 11,
        "CON": 11,
        "INT": 11,
        "WIS": 11,
        "CHA": 11,
    }


def test_race_bindings_match_requested_keys() -> None:
    state = initial_character_creation_state()

    assert keymap_for_step(state) == {
        "d": "Dragonborn",
        "w": "Dwarf",
        "e": "Elf",
        "g": "Gnome",
        "h": "Half-Elf",
        "o": "Half-Orc",
        "f": "Halfling",
        "u": "Human",
        "t": "Tiefling",
    }
    assert choice_for_key(state, "d") == "Dragonborn"


def test_class_bindings_are_single_key_and_do_not_use_back_key() -> None:
    state = CharacterCreationState(step="class", base_attributes={})
    mapping = keymap_for_step(state)

    assert mapping["a"] == "Barbarian"
    assert mapping["d"] == "Bard"
    assert mapping["c"] == "Cleric"
    assert mapping["u"] == "Druid"
    assert mapping["z"] == "Wizard"
    assert "b" not in mapping
    assert set(mapping.values()) == {character_class.name for character_class in CLASSES}


@pytest.mark.parametrize("race", RACES, ids=lambda race: race.name)
@pytest.mark.parametrize("character_class", CLASSES, ids=lambda character_class: character_class.name)
def test_every_srd_race_class_creates_level_one_sheet_with_combat_stats(
    race,
    character_class,
) -> None:
    state = completed_state_for(race.name, character_class.name)
    sheet = to_character_sheet(state)
    armor = starter_armor_for_class(sheet.character_class)
    weapon = starter_weapon_for_class(sheet.character_class)
    stats = combat_stats_for_sheet(sheet, armor)

    assert sheet.level == 1
    assert sheet.race == race.name
    assert sheet.character_class == character_class.name
    assert set(sheet.attributes) == set(ABILITIES)
    assert weapon.name == character_class.starting_equipment.weapon
    assert armor.name == character_class.starting_equipment.armor
    assert stats.max_hit_points == max(
        1,
        character_class.hit_die + ability_modifier(sheet.attributes["CON"]),
    )
    assert stats.hit_points == stats.max_hit_points
    assert stats.proficiency_bonus == 2


@pytest.mark.parametrize(
    ("class_name", "excluded_resource"),
    (
        ("Monk", "ki"),
        ("Cleric", "channel_divinity"),
        ("Druid", "wild_shape"),
        ("Paladin", "spell_slots"),
        ("Ranger", "spell_slots"),
        ("Sorcerer", "sorcery_points"),
    ),
)
def test_level_one_class_metadata_excludes_higher_level_resources(
    class_name: str,
    excluded_resource: str,
) -> None:
    character_class = class_by_name(class_name)

    assert character_class is not None
    assert excluded_resource not in character_class.resource_hooks


def test_combat_stats_for_sheet_rejects_non_level_one_sheet() -> None:
    sheet = CharacterSheet(
        race="Human",
        character_class="Fighter",
        specialization="Champion",
        base_attributes=dict(BASE_ATTRIBUTES),
        attributes=dict(BASE_ATTRIBUTES),
        level=2,
    )

    with pytest.raises(ValueError, match="supports only level 1"):
        combat_stats_for_sheet(sheet)


def test_old_saved_character_sheet_dict_without_level_defaults_to_level_one() -> None:
    saved_sheet = {
        "race": "Human",
        "character_class": "Wizard",
        "specialization": "Evocation",
        "base_attributes": dict(BASE_ATTRIBUTES),
        "attributes": dict(BASE_ATTRIBUTES),
        "cantrips": ("Light",),
        "spells": ("Magic Missile",),
        "skills": ("Arcana",),
    }

    sheet = CharacterSheet(**saved_sheet)

    assert sheet.level == 1
    assert sheet.cantrips == ("Light",)
    assert sheet.spells == ("Magic Missile",)
    assert sheet.skills == ("Arcana",)


def test_positional_optional_character_sheet_fields_do_not_shift_into_level() -> None:
    sheet = CharacterSheet(
        "Human",
        "Wizard",
        "Evocation",
        dict(BASE_ATTRIBUTES),
        dict(BASE_ATTRIBUTES),
        ("Light",),
        ("Magic Missile",),
        ("Arcana",),
    )

    assert sheet.level == 1
    assert sheet.cantrips == ("Light",)
    assert sheet.spells == ("Magic Missile",)
    assert sheet.skills == ("Arcana",)


def test_class_foundation_metadata_is_populated_for_future_rules_hooks() -> None:
    for character_class in CLASSES:
        assert character_class.hit_die in {6, 8, 10, 12}
        assert character_class.role in {"martial", "expert", "arcane", "divine", "primal", "hybrid"}
        assert len(character_class.saving_throw_proficiencies) == 2
        assert set(character_class.saving_throw_proficiencies) <= set(ABILITIES)
        assert character_class.starting_equipment.weapon
        assert character_class.starting_equipment.armor
        if character_class.spell_count or "spell_slots" in character_class.resource_hooks:
            assert character_class.spellcasting_ability in ABILITIES


def test_race_foundation_metadata_is_populated_for_future_movement_rules() -> None:
    for race in RACES:
        assert race.size in {"Small", "Medium"}
        assert race.speed in {25, 30}
