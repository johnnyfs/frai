from src.core.character_creation import (
    CLASSES,
    CharacterCreationState,
    choice_for_key,
    can_advance,
    initial_character_creation_state,
    keymap_for_step,
    next_step,
    previous_step,
    total_attributes,
    with_selection,
)


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
