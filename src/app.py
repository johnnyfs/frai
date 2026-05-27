from dataclasses import dataclass, field
import curses

from src.core.combat import combat_stats_for_sheet, starter_armor_for_class, starter_weapon_for_class
from src.core.config import PLAYFIELD_HEIGHT, PLAYFIELD_WIDTH
from src.core.dispatcher import Dispatcher
from src.core.components import Character
from src.core.character_creation import initial_character_creation_state
from src.core.effects import (
    DamageEntity,
    Effect,
    EmitMessage,
    KillEntity,
    MoveEntity,
    QuitGame,
    RestartGame,
    SetCharacterSheet,
    SetMode,
)
from src.core.entity import EntityId
from src.core.modes import CharacterCreationMode, GameMode, GameOverMode, StartChoiceMode
from src.core.world import World
from src.map.room_builder import build_room_world
from src.systems.game_over_system import GameOverSystem
from src.systems.input_system import map_key
from src.systems.character_creation_system import CharacterCreationSystem
from src.systems.combat_system import CombatSystem
from src.systems.message_system import MessageState
from src.systems.movement_system import MovementContextResolver, MovementSystem
from src.systems.obstruction_system import ObstructionSystem
from src.systems.quit_system import QuitSystem
from src.systems.render_system import render
from src.systems.start_system import StartSystem
from src.systems.turn_system import TurnSystem
from src.ui.screen import Screen


@dataclass(slots=True)
class App:
    world: World
    player: EntityId
    dispatcher: Dispatcher
    messages: MessageState = field(default_factory=MessageState)
    mode: GameMode = field(default_factory=StartChoiceMode)
    running: bool = True

    def apply_effects(self, effects: list[Effect]) -> None:
        messages: list[str] = []
        for effect in effects:
            if isinstance(effect, MoveEntity):
                position = self.world.positions.require(effect.entity)
                position.x = effect.x
                position.y = effect.y
            elif isinstance(effect, EmitMessage):
                messages.append(effect.text)
            elif isinstance(effect, SetMode):
                self.mode = effect.mode
            elif isinstance(effect, QuitGame):
                self.running = False
            elif isinstance(effect, SetCharacterSheet):
                self.world.characters.add(effect.entity, Character(effect.sheet))
                armor = starter_armor_for_class(effect.sheet.character_class)
                self.world.armor.add(effect.entity, armor)
                self.world.combat_stats.add(
                    effect.entity, combat_stats_for_sheet(effect.sheet, armor)
                )
                self.world.weapons.add(
                    effect.entity, starter_weapon_for_class(effect.sheet.character_class)
                )
            elif isinstance(effect, DamageEntity):
                stats = self.world.combat_stats.get(effect.entity)
                if stats is not None:
                    stats.hit_points = max(0, stats.hit_points - effect.amount)
            elif isinstance(effect, KillEntity):
                if effect.entity == self.player:
                    self.mode = GameOverMode()
                else:
                    self.world.remove_entity(effect.entity)
            elif isinstance(effect, RestartGame):
                self.restart()
        if messages:
            self.messages.emit(" ".join(message for message in messages if message))

    def handle_key(self, key: int) -> None:
        if self.messages.awaiting_more and not isinstance(self.mode, GameOverMode):
            self.messages.advance()
            return
        action = map_key(key, self.mode, self.world.player_entity())
        effects = self.dispatcher.dispatch(action, self.world)
        self.apply_effects(effects)

    def restart(self) -> None:
        built = build_room_world(width=self.world.width, height=self.world.height)
        self.world = built.world
        self.player = built.player
        self.mode = StartChoiceMode()
        self.messages.emit("")


def create_app(width: int = PLAYFIELD_WIDTH, height: int = PLAYFIELD_HEIGHT) -> App:
    built = build_room_world(width=width, height=height)
    movement = MovementSystem(
        obstruction=ObstructionSystem(),
        context_resolver=MovementContextResolver(),
    )
    combat = CombatSystem()
    dispatcher = Dispatcher(
        systems=[
            StartSystem(),
            GameOverSystem(),
            CharacterCreationSystem(),
            QuitSystem(),
            TurnSystem(movement=movement, combat=combat),
            movement,
            combat,
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
