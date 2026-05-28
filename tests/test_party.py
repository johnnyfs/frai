from src.app import create_app
from src.core.character_creation import CLASSES, CharacterSheet
from src.core.effects import SetCharacterSheet
from src.core.party import companion_definitions_for_player_class


def player_sheet_for_class(character_class: str) -> CharacterSheet:
    class_option = next(option for option in CLASSES if option.name == character_class)
    return CharacterSheet(
        race="Human",
        character_class=class_option.name,
        specialization=class_option.specializations[0],
        base_attributes={"STR": 12, "DEX": 12, "CON": 12, "INT": 12, "WIS": 12, "CHA": 12},
        attributes={"STR": 13, "DEX": 13, "CON": 13, "INT": 13, "WIS": 13, "CHA": 13},
        cantrips=class_option.cantrip_choices[: class_option.cantrip_count],
        spells=class_option.spell_choices[: class_option.spell_count],
        skills=class_option.skill_choices[: class_option.skill_count],
    )


def test_rogue_player_gets_martial_divine_arcane_companions() -> None:
    companions = companion_definitions_for_player_class("Rogue")

    assert [companion.role for companion in companions] == ["martial", "divine", "arcane"]
    assert [companion.sheet.character_class for companion in companions] == [
        "Fighter",
        "Cleric",
        "Wizard",
    ]


def test_cleric_player_gets_expert_martial_arcane_companions() -> None:
    companions = companion_definitions_for_player_class("Cleric")

    assert [companion.role for companion in companions] == ["expert", "martial", "arcane"]
    assert [companion.sheet.character_class for companion in companions] == [
        "Rogue",
        "Fighter",
        "Wizard",
    ]


def test_all_player_classes_produce_four_named_party_members() -> None:
    for class_option in CLASSES:
        app = create_app()
        app.apply_effects([SetCharacterSheet(app.player, player_sheet_for_class(class_option.name))])

        assert len(app.party) == 4
        assert app.party[0] == app.player
        assert all(app.world.name_for(entity) for entity in app.party)
        assert len({app.world.name_for(entity) for entity in app.party}) == 4
        assert all(app.world.characters.has(entity) for entity in app.party)
