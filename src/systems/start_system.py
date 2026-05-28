import random

from src.core.actions import Action, StartChoice
from src.core.character_creation import (
    CharacterSheet,
    CLASSES,
    RACES,
    initial_character_creation_state,
    next_step,
    to_character_sheet,
    with_selection,
)
from src.core.dispatcher import DispatchResult
from src.core.effects import EmitMessage, SetCharacterSheet, SetMode
from src.core.modes import UIMode
from src.core.world import World


class StartSystem:
    def handle(self, action: Action, world: World) -> DispatchResult:
        if not isinstance(action, StartChoice):
            return DispatchResult()

        if action.create:
            return DispatchResult(
                effects=[
                    SetMode(
                        UIMode.character_creation,
                        character_creation_state=initial_character_creation_state(),
                    )
                ],
                cancel=True,
            )

        sheet = yolo_sheet()
        return DispatchResult(
            effects=[
                SetCharacterSheet(world.player_entity(), sheet),
                EmitMessage(f"YOLO: {sheet.race} {sheet.character_class}."),
                SetMode(UIMode.play),
            ],
            cancel=True,
        )


def yolo_sheet(rng: random.Random | None = None) -> CharacterSheet:
    """Generate a randomized starter character sheet.

    Accepts an optional :class:`random.Random` so the playtest harness
    (M37) and any other reproducibility-sensitive caller can pin the
    output. Defaults to a fresh, system-seeded ``random.Random()`` so
    interactive YOLO starts vary across launches, which is what a
    player expects.
    """
    if rng is None:
        rng = random.Random()
    state = initial_character_creation_state(rng=rng)
    state = with_selection(state, rng.choice(RACES).name)
    character_class = rng.choice(CLASSES)
    state = with_selection(state, character_class.name)
    state = with_selection(state, rng.choice(character_class.specializations))
    for choices, count in (
        (character_class.cantrip_choices, character_class.cantrip_count),
        (character_class.spell_choices, character_class.spell_count),
        (character_class.skill_choices, character_class.skill_count),
    ):
        for choice in rng.sample(list(choices), count):
            state = with_selection(state, choice)
        if count:
            state = next_step(state)
    while state.step != "confirm":
        state = next_step(state)
    return to_character_sheet(state)
