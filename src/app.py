from dataclasses import dataclass, field
import curses

from src.core.config import PLAYFIELD_HEIGHT, PLAYFIELD_WIDTH
from src.core.dispatcher import Dispatcher
from src.core.character_creation import initial_character_creation_state
from src.core.components import Character
from src.core.effects import Effect, EmitMessage, MoveEntity, QuitGame, SetCharacterSheet, SetMode
from src.core.entity import EntityId
from src.core.modes import CharacterCreationMode, GameMode
from src.core.world import World
from src.map.room_builder import build_room_world
from src.systems.input_system import map_key
from src.systems.character_creation_system import CharacterCreationSystem
from src.systems.message_system import MessageState
from src.systems.movement_system import MovementContextResolver, MovementSystem
from src.systems.obstruction_system import ObstructionSystem
from src.systems.quit_system import QuitSystem
from src.systems.render_system import render
from src.ui.screen import Screen


@dataclass(slots=True)
class App:
    world: World
    player: EntityId
    dispatcher: Dispatcher
    messages: MessageState = field(default_factory=MessageState)
    mode: GameMode = field(
        default_factory=lambda: CharacterCreationMode(initial_character_creation_state())
    )
    running: bool = True

    def apply_effects(self, effects: list[Effect]) -> None:
        for effect in effects:
            if isinstance(effect, MoveEntity):
                position = self.world.positions.require(effect.entity)
                position.x = effect.x
                position.y = effect.y
            elif isinstance(effect, EmitMessage):
                self.messages.emit(effect.text)
            elif isinstance(effect, SetMode):
                self.mode = effect.mode
            elif isinstance(effect, QuitGame):
                self.running = False
            elif isinstance(effect, SetCharacterSheet):
                self.world.characters.add(effect.entity, Character(effect.sheet))

    def handle_key(self, key: int) -> None:
        action = map_key(key, self.mode, self.world.player_entity())
        effects = self.dispatcher.dispatch(action, self.world)
        self.apply_effects(effects)


def create_app(width: int = PLAYFIELD_WIDTH, height: int = PLAYFIELD_HEIGHT) -> App:
    built = build_room_world(width=width, height=height)
    dispatcher = Dispatcher(
        systems=[
            CharacterCreationSystem(),
            QuitSystem(),
            MovementSystem(
                obstruction=ObstructionSystem(),
                context_resolver=MovementContextResolver(),
            ),
        ]
    )
    return App(world=built.world, player=built.player, dispatcher=dispatcher)


def _setup_curses(stdscr: curses.window) -> None:
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    stdscr.keypad(True)
    stdscr.nodelay(False)
    curses.noecho()
    curses.cbreak()


def _run_curses(stdscr: curses.window) -> None:
    _setup_curses(stdscr)
    app = create_app()
    while app.running:
        render(Screen(stdscr), app.world, app.world.player_entity(), app.messages, app.mode)
        key = stdscr.getch()
        if key == curses.KEY_RESIZE:
            continue

        screen = Screen(stdscr)
        if screen.width < PLAYFIELD_WIDTH or screen.height < PLAYFIELD_HEIGHT + 2:
            if 0 <= key <= 255 and chr(key).lower() == "q":
                app.running = False
            continue

        app.handle_key(key)


def run() -> None:
    curses.wrapper(_run_curses)
