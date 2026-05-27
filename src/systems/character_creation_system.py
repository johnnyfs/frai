from dataclasses import replace

from src.core.actions import Action, CharacterCreationCommand
from src.core.character_creation import (
    can_advance,
    choice_for_key,
    next_step,
    previous_step,
    roll_attributes,
    to_character_sheet,
    with_selection,
)
from src.core.dispatcher import DispatchResult
from src.core.effects import EmitMessage, SetCharacterSheet, SetMode
from src.core.modes import CharacterCreationMode, NormalMode
from src.core.world import World


class CharacterCreationSystem:
    def handle(self, action: Action, world: World) -> DispatchResult:
        if not isinstance(action, CharacterCreationCommand):
            return DispatchResult()

        state = action.state
        command = action.command

        if command == "confirm" and state.step == "confirm":
            sheet = to_character_sheet(state)
            player = world.player_entity()
            return DispatchResult(
                effects=[
                    SetCharacterSheet(player, sheet),
                    EmitMessage(f"Welcome, {sheet.race} {sheet.character_class}."),
                    SetMode(NormalMode()),
                ],
                cancel=True,
            )
        if command == "back":
            new_state = previous_step(state)
        elif command == "reroll" and state.step == "attributes":
            new_state = replace(state, base_attributes=roll_attributes())
        elif command == "choose" and action.key is not None:
            choice = choice_for_key(state, action.key)
            new_state = with_selection(state, choice) if choice is not None else state
        elif command == "confirm" and state.step == "attributes":
            new_state = next_step(state)
        elif command == "confirm" and state.step in ("cantrips", "spells", "skills") and can_advance(state):
            new_state = next_step(state)
        else:
            new_state = state

        return DispatchResult(effects=[SetMode(CharacterCreationMode(new_state))], cancel=True)
