from dataclasses import dataclass, field
import curses
import random

from src.core.combat import combat_stats_for_sheet, starter_armor_for_class, starter_weapon_for_class
from src.core.config import MIN_TERMINAL_HEIGHT, MIN_TERMINAL_WIDTH, WORLD_HEIGHT, WORLD_WIDTH
from src.core.dispatcher import Dispatcher
from src.core.components import (
    BlocksMovement,
    Character,
    Equipment,
    Faction,
    Inventory,
    Name,
    PlayerControlled,
    Position,
    Presentation,
)
from src.core.character_creation import CharacterSheet
from src.core.items import add_item, armor_item_id_for_name, weapon_item_id_for_name
from src.core.effects import (
    Effect,
    EmitMessage,
    MoveEntity,
)
from src.core.effects_applier import EffectApplier
from src.core.entity import EntityId
from src.core.actions import EndTurn, InteractAttempt, MoveAttempt, ToggleTurnMode
from src.core.modes import GameMode, GameOverMode, NormalMode, StartChoiceMode
from src.core.party import CompanionDefinition, companion_definitions_for_player_class
from src.core.time import SECONDS_PER_ROUND, SECONDS_PER_TURN, advance as advance_world_clock
from src.core.turns import (
    ActivationState,
    MajorMode,
    is_turn_based,
    major_mode_for_state,
)
from src.core.world import World
from src.map.room_builder import BuiltRoom, build_room_world
from src.systems.game_over_system import GameOverSystem
from src.systems.input_system import map_key
from src.systems.inventory_system import InventorySystem
from src.systems.character_creation_system import CharacterCreationSystem
from src.systems.ai_system import EnemyAISystem
from src.systems.awareness_system import hostiles_requiring_battle
from src.systems.combat_system import CombatSystem
from src.systems.interaction_system import InteractionSystem
from src.systems.message_system import MessageState
from src.systems.movement_system import (
    MovementContextResolver,
    MovementSystem,
    movement_cost_for_attempt,
)
from src.systems.obstruction_system import ObstructionSystem
from src.systems.quit_system import QuitSystem
from src.systems.render_system import render
from src.systems.start_system import StartSystem, yolo_sheet
from src.ui.screen import Screen


@dataclass(slots=True)
class App:
    world: World
    player: EntityId
    party: list[EntityId]
    active_party_index: int
    dispatcher: Dispatcher
    activation: ActivationState = field(default_factory=ActivationState)
    messages: MessageState = field(default_factory=MessageState)
    mode: GameMode = field(default_factory=StartChoiceMode)
    major_mode: MajorMode = "explore"
    facing: tuple[int, int] = (1, 0)
    voluntary_turn_based: bool = False
    running: bool = True
    effect_applier: EffectApplier = field(init=False)

    def __post_init__(self) -> None:
        self.effect_applier = EffectApplier(self)

    @property
    def focus(self) -> EntityId:
        return self.active_actor()

    def active_actor(self) -> EntityId:
        if not is_turn_based(self.major_mode):
            return self.player
        return self.party[self.active_party_index]

    def apply_effects(self, effects: list[Effect]) -> None:
        self.effect_applier.apply_all(effects)

    def handle_key(self, key: int) -> None:
        if self.messages.awaiting_more and not isinstance(self.mode, GameOverMode):
            self.messages.advance()
            return
        self.sync_major_mode()
        action = map_key(key, self.mode, self.active_actor())
        if isinstance(action, EndTurn) and isinstance(self.mode, NormalMode):
            if is_turn_based(self.major_mode):
                self.advance_party_turn()
            return
        if isinstance(action, ToggleTurnMode) and isinstance(self.mode, NormalMode):
            self.apply_effects(self._toggle_turn_mode())
            self.sync_major_mode()
            return
        if isinstance(action, InteractAttempt) and isinstance(self.mode, NormalMode):
            if action.dx == 0 and action.dy == 0:
                action = InteractAttempt(action.actor, self.facing[0], self.facing[1], action.check_result)
            self.apply_effects(self._handle_interaction(action))
            if not is_turn_based(self.major_mode):
                self._tick_world_clock(SECONDS_PER_TURN)
            self.sync_major_mode()
            return
        if isinstance(action, MoveAttempt) and isinstance(self.mode, NormalMode):
            if action.dx != 0 or action.dy != 0:
                self.facing = (action.dx, action.dy)
            if is_turn_based(self.major_mode):
                self.apply_effects(self._handle_active_move(action))
            else:
                self.apply_effects(self._handle_explore_move(action))
                self._tick_world_clock(SECONDS_PER_TURN)
            self.sync_major_mode()
            return
        effects = self.dispatcher.dispatch(action, self.world)
        self.apply_effects(effects)
        self.sync_major_mode()

    def sync_major_mode(self) -> None:
        hostiles_present = bool(hostiles_requiring_battle(self.world, self.party))
        if hostiles_present:
            self.voluntary_turn_based = False
        next_mode = major_mode_for_state(hostiles_present, self.voluntary_turn_based)
        if next_mode == self.major_mode:
            return
        self.major_mode = next_mode
        self.activation.reset_for_activation()
        if not is_turn_based(next_mode):
            self.active_party_index = 0

    def _toggle_turn_mode(self) -> list[Effect]:
        if hostiles_requiring_battle(self.world, self.party):
            self.voluntary_turn_based = False
            return [EmitMessage("Cannot exit turn-based mode while hostiles are present.")]
        self.voluntary_turn_based = not self.voluntary_turn_based
        return [
            EmitMessage(
                "Entered turn-based mode."
                if self.voluntary_turn_based
                else "Exited turn-based mode."
            )
        ]

    def _handle_interaction(self, action: InteractAttempt) -> list[Effect]:
        if is_turn_based(self.major_mode):
            if self.activation.action_used:
                return [EmitMessage("Action already used.")]
            effects = self.dispatcher.dispatch(action, self.world)
            self.activation.spend_action()
            return effects
        return self.dispatcher.dispatch(action, self.world)

    def _handle_explore_move(self, action: MoveAttempt) -> list[Effect]:
        displacement = _party_displacement(self.world, self.party, action)
        if displacement is not None:
            return displacement
        previous_positions = [
            (entity, self.world.positions.require(entity).x, self.world.positions.require(entity).y)
            for entity in self.party
            if self.world.positions.has(entity)
        ]
        effects = self.dispatcher.dispatch(action, self.world)
        if not any(
            isinstance(effect, MoveEntity) and effect.entity == action.actor
            for effect in effects
        ):
            return effects
        for index in range(1, len(previous_positions)):
            entity, _, _ = previous_positions[index]
            _, previous_x, previous_y = previous_positions[index - 1]
            effects.append(MoveEntity(entity, previous_x, previous_y))
        return effects

    def _handle_active_move(self, action: MoveAttempt) -> list[Effect]:
        displacement = _party_displacement(self.world, self.party, action)
        if displacement is not None:
            cost = movement_cost_for_attempt(self.world, action)
            if not self.activation.spend_movement(cost):
                return [EmitMessage("No movement remaining.")]
            return displacement

        target = _hostile_target_for_move(self.world, action)
        if target is not None:
            if self.activation.action_used:
                return [EmitMessage("Action already used.")]
            effects = self.dispatcher.dispatch(action, self.world)
            self.activation.spend_action()
            return effects

        cost = movement_cost_for_attempt(self.world, action)
        if not self.activation.can_spend_movement(cost):
            return [EmitMessage("No movement remaining.")]

        effects = self.dispatcher.dispatch(action, self.world)
        if any(
            isinstance(effect, MoveEntity) and effect.entity == action.actor
            for effect in effects
        ):
            self.activation.spend_movement(cost)
        return effects

    def advance_party_turn(self) -> None:
        if not self.party:
            return
        for index in range(self.active_party_index + 1, len(self.party)):
            if _can_take_turn(self.world, self.party[index]):
                self.active_party_index = index
                self.activation.reset_for_activation()
                return
        if self.major_mode == "battle":
            self.run_enemy_activations()
        self._tick_world_clock(SECONDS_PER_ROUND)
        for index, entity in enumerate(self.party):
            if _can_take_turn(self.world, entity):
                self.active_party_index = index
                self.activation.reset_for_activation()
                return

    def _tick_world_clock(self, seconds: int) -> None:
        # Time-advance hook. Explore-mode moves/interactions tick a
        # minute; turn-based round ticks fire after the enemy phase.
        # Rest and scheduled-effect expiration live in M34 and M24.
        advance_world_clock(self.world.clock, seconds, self.world.schedule)

    def run_enemy_activations(self) -> None:
        combat = next(
            (system for system in self.dispatcher.systems if isinstance(system, CombatSystem)),
            CombatSystem(),
        )
        EnemyAISystem(combat=combat).run_enemy_activations(
            self.world,
            self.party,
            self.apply_effects,
        )

    def restart(self) -> None:
        built, party = _build_party_world(width=self.world.width, height=self.world.height)
        self.world = built.world
        self.player = built.player
        self.party = party
        self.active_party_index = 0
        self.activation = ActivationState()
        self.facing = (1, 0)
        self.voluntary_turn_based = False
        self.major_mode = "explore"
        self.mode = StartChoiceMode()
        self.messages.emit("")


def create_app(
    width: int = WORLD_WIDTH,
    height: int = WORLD_HEIGHT,
    *,
    rng: random.Random | None = None,
) -> App:
    built, party = _build_party_world(width=width, height=height)
    movement = MovementSystem(
        obstruction=ObstructionSystem(),
        context_resolver=MovementContextResolver(),
    )
    combat = CombatSystem()
    interaction_rng = rng if rng is not None else random.Random()
    dispatcher = Dispatcher(
        systems=[
            StartSystem(),
            GameOverSystem(),
            InventorySystem(),
            CharacterCreationSystem(),
            QuitSystem(),
            InteractionSystem(rng=interaction_rng),
            movement,
            combat,
        ]
    )
    return App(
        world=built.world,
        player=built.player,
        party=party,
        active_party_index=0,
        dispatcher=dispatcher,
        major_mode=major_mode_for_state(bool(hostiles_requiring_battle(built.world, party))),
    )


def _build_party_world(width: int, height: int) -> tuple[BuiltRoom, list[EntityId]]:
    built = build_room_world(width=width, height=height)
    built.world.blockers.add(built.player, BlocksMovement("occupied"))
    player_sheet = yolo_sheet()
    _assign_character_sheet(built.world, built.player, player_sheet)
    party = _add_companions_for_player_sheet(built.world, built.player, player_sheet)
    return built, party


def _replace_companions_for_player_sheet(
    world: World,
    player: EntityId,
    party: list[EntityId],
    sheet: CharacterSheet,
) -> list[EntityId]:
    for entity in party[1:]:
        world.remove_entity(entity)
    return _add_companions_for_player_sheet(world, player, sheet)


def _add_companions_for_player_sheet(
    world: World,
    player: EntityId,
    sheet: CharacterSheet,
) -> list[EntityId]:
    party = [player]
    rng = random.Random(0)
    for definition in companion_definitions_for_player_class(sheet.character_class):
        party.append(_add_companion(world, player, definition, rng))
    return party


def _add_companion(
    world: World,
    anchor: EntityId,
    definition: CompanionDefinition,
    rng: random.Random,
) -> EntityId:
    anchor_position = world.positions.require(anchor)
    x, y = _nearby_open_position(world, anchor_position.x, anchor_position.y, rng)
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation("#"))
    world.blockers.add(entity, BlocksMovement("occupied"))
    world.player_controlled.add(entity, PlayerControlled())
    world.names.add(entity, Name(definition.name))
    world.factions.add(entity, Faction("player"))
    _assign_character_sheet(world, entity, definition.sheet)
    return entity


def _nearby_open_position(
    world: World,
    origin_x: int,
    origin_y: int,
    rng: random.Random,
) -> tuple[int, int]:
    for radius in range(1, 8):
        candidates = [
            (origin_x + dx, origin_y + dy)
            for dy in range(-radius, radius + 1)
            for dx in range(-radius, radius + 1)
            if max(abs(dx), abs(dy)) == radius
        ]
        rng.shuffle(candidates)
        for x, y in candidates:
            if world.tile_at(x, y).blocks_movement:
                continue
            if world.entities_at(x, y):
                continue
            return x, y
    raise RuntimeError("Could not place party member near player.")


def _assign_character_sheet(world: World, entity: EntityId, sheet: CharacterSheet) -> None:
    world.characters.add(entity, Character(sheet))
    armor = starter_armor_for_class(sheet.character_class)
    world.armor.add(entity, armor)
    world.combat_stats.add(entity, combat_stats_for_sheet(sheet, armor))
    weapon = starter_weapon_for_class(sheet.character_class)
    world.weapons.add(entity, weapon)

    inventory = Inventory(gold=25)
    weapon_item_id = weapon_item_id_for_name(weapon.name)
    armor_item_id = armor_item_id_for_name(armor.name)
    add_item(inventory, weapon_item_id)
    if armor_item_id is not None:
        add_item(inventory, armor_item_id)
    world.inventories.add(entity, inventory)
    world.equipment.add(
        entity,
        Equipment(weapon_item_id=weapon_item_id, armor_item_id=armor_item_id),
    )


def _hostile_target_for_move(world: World, action: MoveAttempt) -> EntityId | None:
    position = world.positions.require(action.actor)
    destination_x = position.x + action.dx
    destination_y = position.y + action.dy
    actor_faction = world.factions.get(action.actor)
    if actor_faction is None:
        return None
    for entity in world.entities_at(destination_x, destination_y):
        target_faction = world.factions.get(entity)
        if (
            target_faction is not None
            and target_faction.value != actor_faction.value
            and world.combat_stats.has(entity)
        ):
            return entity
    return None


def _party_displacement(
    world: World,
    party: list[EntityId],
    action: MoveAttempt,
) -> list[Effect] | None:
    if action.actor not in party:
        return None
    actor_position = world.positions.require(action.actor)
    destination_x = actor_position.x + action.dx
    destination_y = actor_position.y + action.dy
    for entity in party:
        if entity == action.actor or not world.positions.has(entity):
            continue
        position = world.positions.require(entity)
        if position.x == destination_x and position.y == destination_y:
            return [
                MoveEntity(action.actor, destination_x, destination_y),
                MoveEntity(entity, actor_position.x, actor_position.y),
                EmitMessage(f"You displaced {_displacement_name(world, entity)}."),
            ]
    return None


def _displacement_name(world: World, entity: EntityId) -> str:
    name = world.name_for(entity)
    return "Player" if name == "you" else name


def _can_take_turn(world: World, entity: EntityId) -> bool:
    stats = world.combat_stats.get(entity)
    return world.positions.has(entity) and (stats is None or stats.hit_points > 0)


def _setup_curses(stdscr: curses.window) -> None:
    try:
        curses.curs_set(1)
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
        render(
            Screen(stdscr),
            app.world,
            app.active_actor(),
            app.messages,
            app.mode,
            app.focus,
            app.party,
            app.activation.movement_used,
            app.activation.movement_total,
            app.major_mode,
        )
        key = stdscr.getch()
        if key == curses.KEY_RESIZE:
            continue

        screen = Screen(stdscr)
        if screen.width < MIN_TERMINAL_WIDTH or screen.height < MIN_TERMINAL_HEIGHT:
            if 0 <= key <= 255 and chr(key).lower() == "q":
                app.running = False
            continue

        app.handle_key(key)


def run() -> None:
    curses.wrapper(_run_curses)
