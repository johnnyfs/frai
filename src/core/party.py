from dataclasses import dataclass
from typing import Literal

from src.core.character_creation import CharacterSheet, require_class

ClassicPartyRole = Literal["martial", "expert", "divine", "arcane"]

CLASSIC_PARTY_ROLES: tuple[ClassicPartyRole, ...] = (
    "martial",
    "expert",
    "divine",
    "arcane",
)


@dataclass(frozen=True, slots=True)
class CompanionDefinition:
    name: str
    role: ClassicPartyRole
    sheet: CharacterSheet


def companion_definitions_for_player_class(
    player_class: str,
) -> tuple[CompanionDefinition, ...]:
    return tuple(
        _copy_definition(COMPANION_DEFINITIONS[role])
        for role in companion_roles_for_player_class(player_class)
    )


def companion_roles_for_player_class(
    player_class: str,
) -> tuple[ClassicPartyRole, ClassicPartyRole, ClassicPartyRole]:
    covered_role = classic_role_for_player_class(player_class)
    if covered_role == "expert":
        return ("martial", "divine", "arcane")
    if covered_role == "divine":
        return ("expert", "martial", "arcane")
    if covered_role == "arcane":
        return ("expert", "martial", "divine")
    return ("expert", "divine", "arcane")


def classic_role_for_player_class(player_class: str) -> ClassicPartyRole:
    class_option = require_class(player_class)
    if class_option.role in CLASSIC_PARTY_ROLES:
        return class_option.role
    if player_class == "Druid":
        return "divine"
    if player_class in ("Paladin", "Ranger"):
        return "martial"
    return "martial"


def _sheet(
    race: str,
    character_class: str,
    specialization: str,
    base_attributes: dict[str, int],
    cantrips: tuple[str, ...] = (),
    spells: tuple[str, ...] = (),
    skills: tuple[str, ...] = (),
) -> CharacterSheet:
    return CharacterSheet(
        race=race,
        character_class=character_class,
        specialization=specialization,
        base_attributes=base_attributes,
        attributes=dict(base_attributes),
        cantrips=cantrips,
        spells=spells,
        skills=skills,
    )


def _copy_definition(definition: CompanionDefinition) -> CompanionDefinition:
    return CompanionDefinition(
        name=definition.name,
        role=definition.role,
        sheet=CharacterSheet(
            race=definition.sheet.race,
            character_class=definition.sheet.character_class,
            specialization=definition.sheet.specialization,
            base_attributes=dict(definition.sheet.base_attributes),
            attributes=dict(definition.sheet.attributes),
            cantrips=definition.sheet.cantrips,
            spells=definition.sheet.spells,
            skills=definition.sheet.skills,
            level=definition.sheet.level,
        ),
    )


COMPANION_DEFINITIONS: dict[ClassicPartyRole, CompanionDefinition] = {
    "martial": CompanionDefinition(
        name="Brann",
        role="martial",
        sheet=_sheet(
            "Human",
            "Fighter",
            "Champion",
            {"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
            skills=("Athletics", "Perception"),
        ),
    ),
    "expert": CompanionDefinition(
        name="Nyx",
        role="expert",
        sheet=_sheet(
            "Halfling",
            "Rogue",
            "Thief",
            {"STR": 10, "DEX": 16, "CON": 12, "INT": 14, "WIS": 10, "CHA": 10},
            skills=("Stealth", "Perception", "Sleight of Hand", "Investigation"),
        ),
    ),
    "divine": CompanionDefinition(
        name="Mereth",
        role="divine",
        sheet=_sheet(
            "Dwarf",
            "Cleric",
            "Life",
            {"STR": 12, "DEX": 10, "CON": 14, "INT": 10, "WIS": 16, "CHA": 10},
            cantrips=("Guidance", "Sacred Flame", "Spare the Dying"),
            spells=("Bless", "Cure Wounds", "Healing Word", "Shield of Faith"),
            skills=("Medicine", "Religion"),
        ),
    ),
    "arcane": CompanionDefinition(
        name="Ilyra",
        role="arcane",
        sheet=_sheet(
            "Elf",
            "Wizard",
            "Evocation",
            {"STR": 8, "DEX": 14, "CON": 12, "INT": 16, "WIS": 12, "CHA": 10},
            cantrips=("Fire Bolt", "Mage Hand", "Light"),
            spells=(
                "Burning Hands",
                "Detect Magic",
                "Mage Armor",
                "Magic Missile",
                "Shield",
                "Sleep",
            ),
            skills=("Arcana", "Investigation"),
        ),
    ),
}
