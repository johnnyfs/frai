from dataclasses import dataclass, field, replace
import random
from typing import Literal

ABILITIES = ("STR", "DEX", "CON", "INT", "WIS", "CHA")
ClassRole = Literal["martial", "expert", "arcane", "divine", "primal", "hybrid"]
CreatureSize = Literal["Small", "Medium"]

CreatorStep = Literal[
    "race",
    "class",
    "specialization",
    "cantrips",
    "spells",
    "skills",
    "attributes",
    "confirm",
]

CreatorCommand = Literal["back", "reroll", "confirm", "choose"]


@dataclass(frozen=True, slots=True)
class RaceOption:
    name: str
    bonuses: dict[str, int]
    size: CreatureSize = "Medium"
    speed: int = 30


@dataclass(frozen=True, slots=True)
class StartingEquipment:
    weapon: str
    armor: str


@dataclass(frozen=True, slots=True)
class ClassOption:
    name: str
    specialization_label: str
    specializations: tuple[str, ...]
    skill_count: int
    skill_choices: tuple[str, ...]
    hit_die: int
    role: ClassRole
    saving_throw_proficiencies: tuple[str, ...]
    starting_equipment: StartingEquipment
    cantrip_count: int = 0
    cantrip_choices: tuple[str, ...] = ()
    spell_count: int = 0
    spell_choices: tuple[str, ...] = ()
    spellcasting_ability: str | None = None
    resource_hooks: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CharacterSheet:
    race: str
    character_class: str
    specialization: str
    base_attributes: dict[str, int]
    attributes: dict[str, int]
    cantrips: tuple[str, ...] = ()
    spells: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    level: int = 1


@dataclass(frozen=True, slots=True)
class CharacterCreationState:
    step: CreatorStep = "race"
    cursor: int = 0
    race: str | None = None
    character_class: str | None = None
    specialization: str | None = None
    cantrips: tuple[str, ...] = ()
    spells: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    base_attributes: dict[str, int] = field(default_factory=dict)


RACES: tuple[RaceOption, ...] = (
    RaceOption("Dragonborn", {"STR": 2, "CHA": 1}),
    RaceOption("Dwarf", {"CON": 2}, speed=25),
    RaceOption("Elf", {"DEX": 2}),
    RaceOption("Gnome", {"INT": 2}, size="Small", speed=25),
    RaceOption("Half-Elf", {"CHA": 2}),
    RaceOption("Half-Orc", {"STR": 2, "CON": 1}),
    RaceOption("Halfling", {"DEX": 2}, size="Small", speed=25),
    RaceOption("Human", {"STR": 1, "DEX": 1, "CON": 1, "INT": 1, "WIS": 1, "CHA": 1}),
    RaceOption("Tiefling", {"INT": 1, "CHA": 2}),
)

SKILLS: tuple[str, ...] = (
    "Acrobatics",
    "Animal Handling",
    "Arcana",
    "Athletics",
    "Deception",
    "History",
    "Insight",
    "Intimidation",
    "Investigation",
    "Medicine",
    "Nature",
    "Perception",
    "Performance",
    "Persuasion",
    "Religion",
    "Sleight of Hand",
    "Stealth",
    "Survival",
)

ARCANE_CANTRIPS = (
    "Acid Splash",
    "Dancing Lights",
    "Fire Bolt",
    "Light",
    "Mage Hand",
    "Mending",
    "Message",
    "Minor Illusion",
    "Prestidigitation",
    "Ray of Frost",
    "Shocking Grasp",
)

DIVINE_CANTRIPS = (
    "Guidance",
    "Light",
    "Mending",
    "Resistance",
    "Sacred Flame",
    "Spare the Dying",
    "Thaumaturgy",
)

DRUID_CANTRIPS = (
    "Druidcraft",
    "Guidance",
    "Mending",
    "Poison Spray",
    "Produce Flame",
    "Resistance",
    "Shillelagh",
)

ARCANE_SPELLS = (
    "Burning Hands",
    "Charm Person",
    "Detect Magic",
    "Disguise Self",
    "Identify",
    "Mage Armor",
    "Magic Missile",
    "Shield",
    "Silent Image",
    "Sleep",
    "Thunderwave",
)

DIVINE_SPELLS = (
    "Bless",
    "Command",
    "Cure Wounds",
    "Detect Magic",
    "Guiding Bolt",
    "Healing Word",
    "Sanctuary",
    "Shield of Faith",
)

DRUID_SPELLS = (
    "Animal Friendship",
    "Cure Wounds",
    "Detect Magic",
    "Entangle",
    "Faerie Fire",
    "Goodberry",
    "Healing Word",
    "Speak with Animals",
)

CLASSES: tuple[ClassOption, ...] = (
    ClassOption(
        "Barbarian",
        "Primal Path",
        ("Berserker",),
        2,
        ("Animal Handling", "Athletics", "Intimidation", "Nature", "Perception", "Survival"),
        12,
        "martial",
        ("STR", "CON"),
        StartingEquipment("greataxe", "none"),
        resource_hooks=("rage",),
    ),
    ClassOption(
        "Bard",
        "College",
        ("Lore",),
        3,
        SKILLS,
        8,
        "expert",
        ("DEX", "CHA"),
        StartingEquipment("rapier", "leather armor"),
        2,
        ARCANE_CANTRIPS,
        4,
        ARCANE_SPELLS,
        "CHA",
        ("spell_slots", "bardic_inspiration"),
    ),
    ClassOption(
        "Cleric",
        "Domain",
        ("Life",),
        2,
        ("History", "Insight", "Medicine", "Persuasion", "Religion"),
        8,
        "divine",
        ("WIS", "CHA"),
        StartingEquipment("mace", "scale mail"),
        3,
        DIVINE_CANTRIPS,
        4,
        DIVINE_SPELLS,
        "WIS",
        ("spell_slots",),
    ),
    ClassOption(
        "Druid",
        "Circle",
        ("Land",),
        2,
        (
            "Arcana",
            "Animal Handling",
            "Insight",
            "Medicine",
            "Nature",
            "Perception",
            "Religion",
            "Survival",
        ),
        8,
        "primal",
        ("INT", "WIS"),
        StartingEquipment("scimitar", "leather armor"),
        2,
        DRUID_CANTRIPS,
        4,
        DRUID_SPELLS,
        "WIS",
        ("spell_slots",),
    ),
    ClassOption(
        "Fighter",
        "Martial Archetype",
        ("Champion",),
        2,
        (
            "Acrobatics",
            "Animal Handling",
            "Athletics",
            "History",
            "Insight",
            "Intimidation",
            "Perception",
            "Survival",
        ),
        10,
        "martial",
        ("STR", "CON"),
        StartingEquipment("longsword", "chain mail"),
        resource_hooks=("second_wind",),
    ),
    ClassOption(
        "Monk",
        "Monastic Tradition",
        ("Open Hand",),
        2,
        ("Acrobatics", "Athletics", "History", "Insight", "Religion", "Stealth"),
        8,
        "martial",
        ("STR", "DEX"),
        StartingEquipment("shortsword", "none"),
    ),
    ClassOption(
        "Paladin",
        "Sacred Oath",
        ("Devotion",),
        2,
        ("Athletics", "Insight", "Intimidation", "Medicine", "Persuasion", "Religion"),
        10,
        "hybrid",
        ("WIS", "CHA"),
        StartingEquipment("longsword", "chain mail"),
        spellcasting_ability="CHA",
        resource_hooks=("lay_on_hands",),
    ),
    ClassOption(
        "Ranger",
        "Archetype",
        ("Hunter",),
        3,
        (
            "Animal Handling",
            "Athletics",
            "Insight",
            "Investigation",
            "Nature",
            "Perception",
            "Stealth",
            "Survival",
        ),
        10,
        "hybrid",
        ("STR", "DEX"),
        StartingEquipment("shortsword", "scale mail"),
        spellcasting_ability="WIS",
    ),
    ClassOption(
        "Rogue",
        "Archetype",
        ("Thief",),
        4,
        (
            "Acrobatics",
            "Athletics",
            "Deception",
            "Insight",
            "Intimidation",
            "Investigation",
            "Perception",
            "Performance",
            "Persuasion",
            "Sleight of Hand",
            "Stealth",
        ),
        8,
        "expert",
        ("DEX", "INT"),
        StartingEquipment("rapier", "leather armor"),
        resource_hooks=("sneak_attack",),
    ),
    ClassOption(
        "Sorcerer",
        "Origin",
        ("Draconic Bloodline",),
        2,
        ("Arcana", "Deception", "Insight", "Intimidation", "Persuasion", "Religion"),
        6,
        "arcane",
        ("CON", "CHA"),
        StartingEquipment("dagger", "none"),
        4,
        ARCANE_CANTRIPS,
        2,
        ARCANE_SPELLS,
        "CHA",
        ("spell_slots",),
    ),
    ClassOption(
        "Warlock",
        "Patron",
        ("Fiend",),
        2,
        ("Arcana", "Deception", "History", "Intimidation", "Investigation", "Nature", "Religion"),
        8,
        "arcane",
        ("WIS", "CHA"),
        StartingEquipment("quarterstaff", "leather armor"),
        2,
        ARCANE_CANTRIPS,
        2,
        ARCANE_SPELLS,
        "CHA",
        ("pact_magic",),
    ),
    ClassOption(
        "Wizard",
        "School",
        ("Evocation",),
        2,
        ("Arcana", "History", "Insight", "Investigation", "Medicine", "Religion"),
        6,
        "arcane",
        ("INT", "WIS"),
        StartingEquipment("quarterstaff", "none"),
        3,
        ARCANE_CANTRIPS,
        6,
        ARCANE_SPELLS,
        "INT",
        ("spell_slots", "arcane_recovery"),
    ),
)


def roll_attributes(rng: random.Random | None = None) -> dict[str, int]:
    roller = rng or random.Random()
    values: list[int] = []
    for _ in ABILITIES:
        rolls = sorted(roller.randint(1, 6) for _ in range(4))
        values.append(sum(rolls[1:]))
    return dict(zip(ABILITIES, values, strict=True))


def initial_character_creation_state(
    rng: random.Random | None = None,
) -> CharacterCreationState:
    """Build the first character-creation state.

    ``rng`` is forwarded to :func:`roll_attributes`. The interactive
    launcher passes ``None`` (so the player gets fresh attribute rolls
    on each new game); the M37 playtest harness and any future
    deterministic test runner pin reproducibility by passing a seeded
    RNG. Leaving the parameter optional keeps every existing caller
    working unchanged.
    """
    return CharacterCreationState(base_attributes=roll_attributes(rng))


def race_by_name(name: str | None) -> RaceOption | None:
    return next((race for race in RACES if race.name == name), None)


def class_by_name(name: str | None) -> ClassOption | None:
    return next((character_class for character_class in CLASSES if character_class.name == name), None)


def require_class(name: str) -> ClassOption:
    character_class = class_by_name(name)
    if character_class is None:
        raise KeyError(f"Unknown character class: {name}")
    return character_class


def visible_steps(state: CharacterCreationState) -> tuple[CreatorStep, ...]:
    steps: list[CreatorStep] = ["race", "class"]
    character_class = class_by_name(state.character_class)
    if character_class is not None:
        steps.append("specialization")
        if character_class.cantrip_count:
            steps.append("cantrips")
        if character_class.spell_count:
            steps.append("spells")
        if character_class.skill_count:
            steps.append("skills")
        steps.extend(["attributes", "confirm"])
    return tuple(steps)


def step_title(state: CharacterCreationState) -> str:
    character_class = class_by_name(state.character_class)
    titles = {
        "race": "Choose Race",
        "class": "Choose Class",
        "specialization": f"Choose {character_class.specialization_label if character_class else 'Specialization'}",
        "cantrips": f"Choose Cantrips ({len(state.cantrips)}/{character_class.cantrip_count if character_class else 0})",
        "spells": f"Choose Spells ({len(state.spells)}/{character_class.spell_count if character_class else 0})",
        "skills": f"Choose Skills ({len(state.skills)}/{character_class.skill_count if character_class else 0})",
        "attributes": "Review Attributes",
        "confirm": "Confirm Character",
    }
    return titles[state.step]


def choices_for_step(state: CharacterCreationState) -> tuple[str, ...]:
    character_class = class_by_name(state.character_class)
    if state.step == "race":
        return tuple(race.name for race in RACES)
    if state.step == "class":
        return tuple(character_class.name for character_class in CLASSES)
    if state.step == "specialization" and character_class is not None:
        return character_class.specializations
    if state.step == "cantrips" and character_class is not None:
        return character_class.cantrip_choices
    if state.step == "spells" and character_class is not None:
        return character_class.spell_choices
    if state.step == "skills" and character_class is not None:
        return character_class.skill_choices
    return ()


EXPLICIT_KEYS: dict[CreatorStep, dict[str, str]] = {
    "race": {
        "Dragonborn": "d",
        "Dwarf": "w",
        "Elf": "e",
        "Gnome": "g",
        "Half-Elf": "h",
        "Half-Orc": "o",
        "Halfling": "f",
        "Human": "u",
        "Tiefling": "t",
    },
    "class": {
        "Barbarian": "a",
        "Bard": "d",
        "Cleric": "c",
        "Druid": "u",
        "Fighter": "f",
        "Monk": "m",
        "Paladin": "p",
        "Ranger": "n",
        "Rogue": "o",
        "Sorcerer": "s",
        "Warlock": "w",
        "Wizard": "z",
    },
}

RESERVED_CREATION_KEYS = frozenset({"b", "y", "r"})


def key_for_choice(state: CharacterCreationState, choice: str) -> str:
    explicit = EXPLICIT_KEYS.get(state.step, {})
    if choice in explicit:
        return explicit[choice]

    used: set[str] = set(RESERVED_CREATION_KEYS)
    for existing in choices_for_step(state):
        explicit_key = explicit.get(existing)
        if explicit_key is not None:
            used.add(explicit_key)
        if existing == choice:
            break
        used.add(_derived_key(existing, used))
    return _derived_key(choice, used)


def keymap_for_step(state: CharacterCreationState) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for choice in choices_for_step(state):
        key = key_for_choice(state, choice)
        mapping[key] = choice
    return mapping


def choice_for_key(state: CharacterCreationState, key: str) -> str | None:
    return keymap_for_step(state).get(key)


def _derived_key(choice: str, used: set[str]) -> str:
    normalized = "".join(character.lower() for character in choice if character.isalpha())
    for character in normalized:
        if character not in used:
            return character
    for character in "abcdefghijklmnopqrstuvwxyz":
        if character not in used:
            return character
    raise ValueError(f"No available binding key for {choice!r}.")


def selected_for_step(state: CharacterCreationState) -> tuple[str, ...]:
    if state.step == "race":
        return tuple([state.race]) if state.race else ()
    if state.step == "class":
        return tuple([state.character_class]) if state.character_class else ()
    if state.step == "specialization":
        return tuple([state.specialization]) if state.specialization else ()
    if state.step == "cantrips":
        return state.cantrips
    if state.step == "spells":
        return state.spells
    if state.step == "skills":
        return state.skills
    return ()


def required_count(state: CharacterCreationState) -> int:
    character_class = class_by_name(state.character_class)
    if state.step in ("race", "class", "specialization"):
        return 1
    if character_class is None:
        return 0
    if state.step == "cantrips":
        return character_class.cantrip_count
    if state.step == "spells":
        return character_class.spell_count
    if state.step == "skills":
        return character_class.skill_count
    return 0


def can_advance(state: CharacterCreationState) -> bool:
    if state.step in ("attributes", "confirm"):
        return True
    return len(selected_for_step(state)) >= required_count(state)


def total_attributes(state: CharacterCreationState) -> dict[str, int]:
    race = race_by_name(state.race)
    totals = dict(state.base_attributes)
    if race is not None:
        for ability, bonus in race.bonuses.items():
            totals[ability] = totals.get(ability, 0) + bonus
    return totals


def to_character_sheet(state: CharacterCreationState) -> CharacterSheet:
    if not state.race or not state.character_class or not state.specialization:
        raise ValueError("Character creation is incomplete.")
    return CharacterSheet(
        race=state.race,
        character_class=state.character_class,
        specialization=state.specialization,
        base_attributes=dict(state.base_attributes),
        attributes=total_attributes(state),
        cantrips=state.cantrips,
        spells=state.spells,
        skills=state.skills,
    )


def move_to_step(state: CharacterCreationState, step: CreatorStep) -> CharacterCreationState:
    return replace(state, step=step, cursor=0)


def next_step(state: CharacterCreationState) -> CharacterCreationState:
    steps = visible_steps(state)
    index = steps.index(state.step)
    return move_to_step(state, steps[min(index + 1, len(steps) - 1)])


def previous_step(state: CharacterCreationState) -> CharacterCreationState:
    steps = visible_steps(state)
    index = steps.index(state.step)
    return move_to_step(state, steps[max(index - 1, 0)])


def with_selection(state: CharacterCreationState, choice: str) -> CharacterCreationState:
    if state.step == "race":
        return next_step(replace(state, race=choice))
    if state.step == "class":
        return next_step(
            replace(
                state,
                character_class=choice,
                specialization=None,
                cantrips=(),
                spells=(),
                skills=(),
            )
        )
    if state.step == "specialization":
        return next_step(replace(state, specialization=choice))

    selected = list(selected_for_step(state))
    if choice in selected:
        selected.remove(choice)
    elif len(selected) < required_count(state):
        selected.append(choice)

    if state.step == "cantrips":
        return replace(state, cantrips=tuple(selected))
    if state.step == "spells":
        return replace(state, spells=tuple(selected))
    if state.step == "skills":
        return replace(state, skills=tuple(selected))
    return state
