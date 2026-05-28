from dataclasses import dataclass, field
import curses
import random

from src.core.combat import combat_stats_for_sheet, starter_armor_for_class, starter_weapon_for_class
from src.core.config import MIN_TERMINAL_HEIGHT, MIN_TERMINAL_WIDTH, WORLD_HEIGHT, WORLD_WIDTH
from src.core.dispatcher import Dispatcher
from src.core.components import (
    BlocksMovement,
    Character,
    Faction,
    Name,
    PlayerControlled,
    Position,
    Presentation,
)
from src.core.character_creation import CharacterSheet
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
from src.core.actions import AttackAttempt, EndTurn, MoveAttempt, ToggleTurnMode
from src.core.modes import GameMode, GameOverMode, NormalMode, StartChoiceMode
from src.core.turns import (
    ActivationState,
    MajorMode,
    MOVEMENT_TOTAL_FEET,
    is_turn_based,
    major_mode_for_state,
    movement_cost as movement_cost_for_delta,
)
from src.core.world import World
from src.map.room_builder import BuiltRoom, build_room_world
from src.systems.game_over_system import GameOverSystem
from src.systems.input_system import map_key
from src.systems.inventory_system import InventorySystem
from src.systems.character_creation_system import CharacterCreationSystem
from src.systems.combat_system import CombatSystem
from src.systems.message_system import MessageState
from src.systems.movement_system import MovementContextResolver, MovementSystem
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
    voluntary_turn_based: bool = False
    running: bool = True

    @property
    def focus(self) -> EntityId:
        return self.active_actor()

    def active_actor(self) -> EntityId:
        if not is_turn_based(self.major_mode):
            return self.player
        return self.party[self.active_party_index]

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
                _assign_character_sheet(self.world, effect.entity, effect.sheet)
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
        if isinstance(action, MoveAttempt) and isinstance(self.mode, NormalMode):
            if is_turn_based(self.major_mode):
                self.apply_effects(self._handle_active_move(action))
            else:
                self.apply_effects(self._handle_explore_move(action))
            self.sync_major_mode()
            return
        effects = self.dispatcher.dispatch(action, self.world)
        self.apply_effects(effects)
        self.sync_major_mode()

    def sync_major_mode(self) -> None:
        hostiles_present = _hostiles_in_sight(self.world, self.party)
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
        if _hostiles_in_sight(self.world, self.party):
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
            cost = movement_cost(action)
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

        cost = movement_cost(action)
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
        for index, entity in enumerate(self.party):
            if _can_take_turn(self.world, entity):
                self.active_party_index = index
                self.activation.reset_for_activation()
                return

    def run_enemy_activations(self) -> None:
        combat = next(
            (system for system in self.dispatcher.systems if isinstance(system, CombatSystem)),
            CombatSystem(),
        )
        for enemy in list(self.world.combat_stats.values):
            if self.world.player_controlled.has(enemy) or not self.world.positions.has(enemy):
                continue
            movement_used = 0.0
            action_used = False
            while self.world.positions.has(enemy):
                target = _nearest_living_party_member(self.world, enemy, self.party)
                if target is None:
                    return
                if _chebyshev_distance(self.world, enemy, target) <= 1:
                    if not action_used:
                        self.apply_effects(combat.resolve_attack(AttackAttempt(enemy, target), self.world))
                        action_used = True
                    break
                if movement_used >= MOVEMENT_TOTAL_FEET:
                    break
                step = _enemy_step_toward(
                    self.world,
                    enemy,
                    target,
                    MOVEMENT_TOTAL_FEET - movement_used,
                )
                if step is None:
                    break
                dx, dy = step
                movement_used += movement_cost(MoveAttempt(enemy, dx, dy))
                position = self.world.positions.require(enemy)
                self.apply_effects([MoveEntity(enemy, position.x + dx, position.y + dy)])

    def restart(self) -> None:
        built, party = _build_party_world(width=self.world.width, height=self.world.height)
        self.world = built.world
        self.player = built.player
        self.party = party
        self.active_party_index = 0
        self.activation = ActivationState()
        self.voluntary_turn_based = False
        self.major_mode = "explore"
        self.mode = StartChoiceMode()
        self.messages.emit("")


def create_app(width: int = WORLD_WIDTH, height: int = WORLD_HEIGHT) -> App:
    built, party = _build_party_world(width=width, height=height)
    movement = MovementSystem(
        obstruction=ObstructionSystem(),
        context_resolver=MovementContextResolver(),
    )
    combat = CombatSystem()
    dispatcher = Dispatcher(
        systems=[
            StartSystem(),
            GameOverSystem(),
            InventorySystem(),
            CharacterCreationSystem(),
            QuitSystem(),
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
        major_mode=major_mode_for_state(_hostiles_in_sight(built.world, party)),
    )


def _build_party_world(width: int, height: int) -> tuple[BuiltRoom, list[EntityId]]:
    built = build_room_world(width=width, height=height)
    built.world.blockers.add(built.player, BlocksMovement("occupied"))
    companion = _add_yolo_party_member(built.world, built.player, random.Random())
    return built, [built.player, companion]


def _add_yolo_party_member(world: World, anchor: EntityId, rng: random.Random) -> EntityId:
    anchor_position = world.positions.require(anchor)
    x, y = _nearby_open_position(world, anchor_position.x, anchor_position.y, rng)
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation("1"))
    world.blockers.add(entity, BlocksMovement("occupied"))
    world.player_controlled.add(entity, PlayerControlled())
    world.names.add(entity, Name("companion"))
    world.factions.add(entity, Faction("player"))
    _assign_character_sheet(world, entity, yolo_sheet())
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
    world.weapons.add(entity, starter_weapon_for_class(sheet.character_class))


def movement_cost(action: MoveAttempt) -> float:
    return movement_cost_for_delta(action.dx, action.dy)


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


def _hostiles_in_sight(world: World, party: list[EntityId]) -> bool:
    party_factions = {
        faction.value
        for entity in party
        if (faction := world.factions.get(entity)) is not None
    }
    for entity, stats in world.combat_stats.values.items():
        if stats.hit_points <= 0 or not world.positions.has(entity):
            continue
        faction = world.factions.get(entity)
        if faction is not None and faction.value not in party_factions:
            return True
    return False


def _can_take_turn(world: World, entity: EntityId) -> bool:
    stats = world.combat_stats.get(entity)
    return world.positions.has(entity) and (stats is None or stats.hit_points > 0)


def _nearest_living_party_member(
    world: World,
    enemy: EntityId,
    party: list[EntityId],
) -> EntityId | None:
    candidates = [entity for entity in party if _can_take_turn(world, entity)]
    if not candidates:
        return None
    enemy_position = world.positions.require(enemy)
    return min(
        candidates,
        key=lambda entity: max(
            abs(world.positions.require(entity).x - enemy_position.x),
            abs(world.positions.require(entity).y - enemy_position.y),
        ),
    )


def _chebyshev_distance(world: World, a: EntityId, b: EntityId) -> int:
    a_position = world.positions.require(a)
    b_position = world.positions.require(b)
    return max(abs(a_position.x - b_position.x), abs(a_position.y - b_position.y))


def _enemy_step_toward(
    world: World,
    enemy: EntityId,
    target: EntityId,
    movement_remaining: float,
) -> tuple[int, int] | None:
    enemy_position = world.positions.require(enemy)
    target_position = world.positions.require(target)
    dx = _sign(target_position.x - enemy_position.x)
    dy = _sign(target_position.y - enemy_position.y)
    candidates = [(dx, dy)]
    if dx != 0:
        candidates.append((dx, 0))
    if dy != 0:
        candidates.append((0, dy))
    for candidate_dx, candidate_dy in candidates:
        if candidate_dx == 0 and candidate_dy == 0:
            continue
        action = MoveAttempt(enemy, candidate_dx, candidate_dy)
        if movement_cost(action) > movement_remaining:
            continue
        destination_x = enemy_position.x + candidate_dx
        destination_y = enemy_position.y + candidate_dy
        if world.blockers_at(destination_x, destination_y):
            continue
        return candidate_dx, candidate_dy
    return None


def _sign(value: int) -> int:
    if value < 0:
        return -1
    if value > 0:
        return 1
    return 0


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
