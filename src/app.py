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
from src.core.character_creation import CharacterCreationState, CharacterSheet
from src.core.game_state import GameState
from src.core.items import add_item, armor_item_id_for_name, weapon_item_id_for_name
from src.core.effects import (
    Effect,
    EmitMessage,
    MoveEntity,
)
from src.core.effects_applier import EffectApplier
from src.core.entity import EntityId
from src.core.actions import EndTurn, InteractAttempt, MoveAttempt, ToggleTurnMode
from src.core.autowalk import (
    AutowalkRequest,
    InterruptReason,
    interrupt_message,
    step_autowalk,
)
from src.core.modes import PlayMode, UIMode, is_turn_based_play, play_mode_for_state
from src.core.party import CompanionDefinition, companion_definitions_for_player_class
from src.core.party_state import PartyState
from src.core.time import SECONDS_PER_ROUND, SECONDS_PER_TURN, advance as advance_world_clock
from src.core.turn_controller import TurnController
from src.core.turns import ActivationState
from src.core.vision import PartyMemory
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
from src.systems.vision_system import VisionSystem
from src.ui.screen import Screen


@dataclass(slots=True)
class App:
    """Runtime entry point. Owns the GameState aggregate plus the
    transient curses-loop scaffolding (dispatcher, effect applier,
    vision system, the ``running`` loop flag, and the player entity
    id).

    Persistent runtime state — world, party, turn, modes, messages,
    party memory, facing — lives on ``self.game_state`` (M49). The
    back-compat properties below keep existing call sites
    (``app.world``, ``app.party``, ``app.ui_mode``, ...) working
    unchanged while the GameState container becomes the canonical
    source for save/load (M16) and the observation snapshot (M35).
    """

    game_state: GameState
    player: EntityId
    dispatcher: Dispatcher
    running: bool = True
    vision: VisionSystem = field(default_factory=VisionSystem)
    # Auto-walk runtime state (M22). When ``autowalk`` is non-None the
    # main key handler runs repeated single-step moves until the
    # ``step_autowalk`` predicate fires. This is transient state and is
    # never persisted — save/load drops any in-progress walk.
    autowalk: AutowalkRequest | None = None
    effect_applier: EffectApplier = field(init=False)

    def __post_init__(self) -> None:
        self.effect_applier = EffectApplier(self)
        self.refresh_vision()

    def refresh_vision(self) -> None:
        """Recompute the party visible set and update memory.

        Called after movement, door changes, and party rotation. Pure
        projection over current world state — no effects emitted.
        """
        self.vision.tick(self.world, self.party.members, self.memory)

    # ------------------------------------------------------------------
    # Back-compat property surface
    # ------------------------------------------------------------------
    #
    # ``GameState`` owns the persistent fields. These properties keep
    # every call site that read or wrote ``app.world``, ``app.party``,
    # ``app.ui_mode``, etc. working unchanged. New code should prefer
    # ``app.game_state.x`` directly.

    @property
    def world(self) -> World:
        return self.game_state.world

    @world.setter
    def world(self, value: World) -> None:
        self.game_state.world = value

    @property
    def party(self) -> PartyState:
        return self.game_state.party

    @property
    def turn(self) -> TurnController:
        return self.game_state.turn

    @property
    def ui_mode(self) -> UIMode:
        return self.game_state.ui_mode

    @ui_mode.setter
    def ui_mode(self, value: UIMode) -> None:
        self.game_state.ui_mode = value

    @property
    def messages(self) -> MessageState:
        return self.game_state.messages

    @messages.setter
    def messages(self, value: MessageState) -> None:
        self.game_state.messages = value

    @property
    def character_creation_state(self) -> CharacterCreationState | None:
        return self.game_state.character_creation_state

    @character_creation_state.setter
    def character_creation_state(self, value: CharacterCreationState | None) -> None:
        self.game_state.character_creation_state = value

    @property
    def memory(self) -> PartyMemory:
        return self.game_state.memory

    @memory.setter
    def memory(self, value: PartyMemory) -> None:
        self.game_state.memory = value

    @property
    def facing(self) -> tuple[int, int]:
        return self.game_state.facing

    @facing.setter
    def facing(self, value: tuple[int, int]) -> None:
        self.game_state.facing = value

    @property
    def active_party_index(self) -> int:
        return self.party.active_index

    @active_party_index.setter
    def active_party_index(self, value: int) -> None:
        self.party.active_index = value

    @property
    def activation(self) -> ActivationState:
        return self.turn.active_activation

    @activation.setter
    def activation(self, value: ActivationState) -> None:
        # Route through PartyState's active member so the activation
        # map is keyed consistently with whichever actor TurnController
        # currently considers active. The explore-mode fallback (head of
        # party) matches ``active_activation`` exactly.
        actor = self.turn.current_actor(self.player)
        self.turn._activations[actor] = value

    @property
    def voluntary_turn_based(self) -> bool:
        return self.turn.voluntary_turn_based

    @voluntary_turn_based.setter
    def voluntary_turn_based(self, value: bool) -> None:
        self.turn.voluntary_turn_based = value

    @property
    def play_mode(self) -> PlayMode:
        return self.turn.play_mode

    @play_mode.setter
    def play_mode(self, value: PlayMode) -> None:
        self.turn.play_mode = value

    @property
    def focus(self) -> EntityId:
        return self.active_actor()

    def active_actor(self) -> EntityId:
        return self.turn.current_actor(self.player)

    @property
    def current_play_mode(self) -> PlayMode:
        """PlayMode is only meaningful while UIMode == play.

        Reading it from any other UIMode is a programming error: the
        gameplay state machine has no defined semantics behind a modal
        screen. We raise instead of silently returning a stale value.
        """

        if self.ui_mode is not UIMode.play:
            raise RuntimeError(
                f"PlayMode is undefined while ui_mode={self.ui_mode!r}"
            )
        return self.turn.play_mode

    def apply_effects(self, effects: list[Effect]) -> None:
        self.effect_applier.apply_all(effects)
        self.refresh_vision()

    def run_debug_command(self, command: str) -> None:
        """Execute a single debug command line (M33).

        Routes through `src.systems.debug_system.run_debug_command` so the
        resulting effects are applied via the standard `EffectApplier`. The
        dev-mode gate (`FRAI_DEV` env var) is enforced inside the debug
        system; outside dev mode this method emits a refusal message and
        otherwise does nothing.

        The playtest harness (M37) and the future debug-prompt modal both
        call this entry point. We deliberately do not wire a curses-level
        prompt key yet — the mode split (M47) is still in flight and we'd
        rather not add another `NormalMode` branch right now.
        """
        from src.systems.debug_system import run_debug_command as _run

        self.apply_effects(_run(command, self))

    # ------------------------------------------------------------------
    # Input routing
    # ------------------------------------------------------------------

    def handle_key(self, key: int) -> None:
        if self.messages.awaiting_more and self.ui_mode is not UIMode.game_over:
            self.messages.advance()
            return
        self.sync_play_mode()
        # Autowalk (M22): a capital direction key initiates a repeated
        # move in that direction. The detection happens before
        # ``map_key`` lowercases the input. Only valid while we're in
        # the play screen — modal screens ignore the prefix.
        if self.ui_mode is UIMode.play and self.autowalk is None:
            direction = _AUTOWALK_KEYS.get(key)
            if direction is not None:
                self.autowalk = AutowalkRequest(direction=direction)
                self._run_autowalk()
                return
        action = map_key(
            key,
            self.ui_mode,
            self.active_actor(),
            character_creation_state=self.character_creation_state,
        )
        if isinstance(action, EndTurn) and self.ui_mode is UIMode.play:
            if is_turn_based_play(self.turn.play_mode):
                self.advance_party_turn()
            return
        if isinstance(action, ToggleTurnMode) and self.ui_mode is UIMode.play:
            self.apply_effects(self._toggle_turn_mode())
            self.sync_play_mode()
            return
        if isinstance(action, InteractAttempt) and self.ui_mode is UIMode.play:
            if action.dx == 0 and action.dy == 0:
                action = InteractAttempt(action.actor, self.facing[0], self.facing[1], action.check_result)
            self.apply_effects(self._handle_interaction(action))
            if not is_turn_based_play(self.turn.play_mode):
                self._tick_world_clock(SECONDS_PER_TURN)
            self.sync_play_mode()
            return
        if isinstance(action, MoveAttempt) and self.ui_mode is UIMode.play:
            if action.dx != 0 or action.dy != 0:
                self.facing = (action.dx, action.dy)
            if is_turn_based_play(self.turn.play_mode):
                self.apply_effects(self._handle_active_move(action))
            else:
                self.apply_effects(self._handle_explore_move(action))
                self._tick_world_clock(SECONDS_PER_TURN)
            self.sync_play_mode()
            return
        effects = self.dispatcher.dispatch(action, self.world)
        self.apply_effects(effects)
        self.sync_play_mode()

    # ------------------------------------------------------------------
    # Turn-mode and action handlers
    # ------------------------------------------------------------------

    def sync_play_mode(self) -> None:
        """Recompute PlayMode from world state.

        Only the play state machine is affected; UIMode is independent
        of hostile presence, so modal screens (inventory, dialogue,
        etc.) do not change which PlayMode is active when dismissed.
        """

        self.turn.sync_play_mode()

    def _toggle_turn_mode(self) -> list[Effect]:
        _succeeded, message = self.turn.toggle_turn_based()
        return [EmitMessage(message)]

    def _handle_interaction(self, action: InteractAttempt) -> list[Effect]:
        if is_turn_based_play(self.turn.play_mode):
            if self.turn.active_activation.action_used:
                return [EmitMessage("Action already used.")]
            effects = self.dispatcher.dispatch(action, self.world)
            self.turn.consume_action()
            return effects
        return self.dispatcher.dispatch(action, self.world)

    def _handle_explore_move(self, action: MoveAttempt) -> list[Effect]:
        displacement = _party_displacement(self.world, self.party.members, action)
        if displacement is not None:
            return displacement
        previous_positions = [
            (entity, self.world.positions.require(entity).x, self.world.positions.require(entity).y)
            for entity in self.party.follow_order
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
        displacement = _party_displacement(self.world, self.party.members, action)
        if displacement is not None:
            cost = movement_cost_for_attempt(self.world, action)
            if not self.turn.consume_movement(cost):
                return [EmitMessage("No movement remaining.")]
            return displacement

        target = _hostile_target_for_move(self.world, action)
        if target is not None:
            if self.turn.active_activation.action_used:
                return [EmitMessage("Action already used.")]
            effects = self.dispatcher.dispatch(action, self.world)
            self.turn.consume_action()
            return effects

        cost = movement_cost_for_attempt(self.world, action)
        if not self.turn.can_consume_movement(cost):
            return [EmitMessage("No movement remaining.")]

        effects = self.dispatcher.dispatch(action, self.world)
        if any(
            isinstance(effect, MoveEntity) and effect.entity == action.actor
            for effect in effects
        ):
            self.turn.consume_movement(cost)
        return effects

    def _run_autowalk(self) -> None:
        """Drive an in-progress autowalk to completion or interrupt.

        Each iteration synthesises one ``MoveAttempt`` in the walk's
        direction, dispatches it through the same path a manual move
        uses, and then asks ``step_autowalk`` whether to continue. The
        autowalk-active state field is cleared before we return so a
        subsequent key press starts fresh, and an interrupt message is
        emitted so the player knows why the walk stopped.

        Implementation note: we read the active actor's position before
        and after each dispatch to decide whether the actor moved. The
        movement system already emits ``"Blocked."`` when a step is
        refused, but reading positions directly avoids coupling to the
        message text in the happy path.
        """

        request = self.autowalk
        if request is None:
            return
        steps = 0
        max_steps = max(0, request.max_steps)
        last_reason: InterruptReason | None = None
        while steps < max_steps:
            actor = self.active_actor()
            position = self.world.positions.get(actor)
            if position is None:
                last_reason = InterruptReason.BLOCKED
                break
            before = (position.x, position.y)
            self.facing = request.direction
            action = MoveAttempt(actor=actor, dx=request.direction[0], dy=request.direction[1])
            if is_turn_based_play(self.turn.play_mode):
                self.apply_effects(self._handle_active_move(action))
            else:
                self.apply_effects(self._handle_explore_move(action))
                self._tick_world_clock(SECONDS_PER_TURN)
            self.sync_play_mode()
            steps += 1
            after_position = self.world.positions.get(actor)
            after = (after_position.x, after_position.y) if after_position is not None else before
            actor_moved = after != before
            cont, reason = step_autowalk(
                self,
                request,
                steps,
                actor=actor,
                actor_moved=actor_moved,
            )
            if not cont:
                last_reason = reason
                break
        else:
            # Loop exited because ``steps == max_steps`` without an
            # earlier interrupt. ``step_autowalk`` returns this reason on
            # the final iteration above so we should not normally land
            # here, but defensively report the budget exhaustion.
            last_reason = InterruptReason.OUT_OF_STEPS
        self.autowalk = None
        if last_reason is not None:
            # Preserve any event-message text the predicate stopped on.
            # We only append our own banner when the buffer is empty or
            # carrying a non-informative token; otherwise the player
            # already has the relevant message in front of them.
            current = self.messages.current
            if not current or current in {"Blocked.", "No movement remaining."}:
                self.messages.emit(interrupt_message(last_reason))
            elif last_reason in (
                InterruptReason.OUT_OF_STEPS,
                InterruptReason.NEW_HOSTILE_VISIBLE,
                InterruptReason.COMBAT_STARTED,
            ):
                # Always surface these reasons — they aren't otherwise
                # signaled by the message log.
                self.messages.emit(interrupt_message(last_reason))

    def advance_party_turn(self) -> None:
        self.turn.end_turn_with_enemy_phase(
            run_enemy_phase=self.run_enemy_activations,
            tick_round=lambda: self._tick_world_clock(SECONDS_PER_ROUND),
        )
        # Vision is recomputed for whichever party member is now active.
        # `apply_effects` already refreshes after the enemy phase, but
        # the no-enemy rotation path needs an explicit kick.
        self.refresh_vision()

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
            self.party.members,
            self.apply_effects,
        )

    def restart(self) -> None:
        built, party = _build_party_world(width=self.world.width, height=self.world.height)
        self.game_state.world = built.world
        self.player = built.player
        # Mutate the existing PartyState in place so TurnController's
        # reference stays valid. ``turn.reset()`` clears active_index
        # and per-actor activations after.
        self.party.members = party
        self.party.follow_order = list(party)
        self.party.focused_index = None
        self.turn.reset()
        self.facing = (1, 0)
        self.ui_mode = UIMode.start
        self.character_creation_state = None
        self.messages.emit("")
        self.memory = PartyMemory()
        self.refresh_vision()


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
    party_state = PartyState.from_members(party)
    game_state = GameState(
        world=built.world,
        party=party_state,
        turn=_make_turn_controller(party_state),  # patched immediately below
    )
    # Bind probes to the GameState so that ``restart`` (which replaces
    # ``game_state.world``) keeps the controller's hostile-detection and
    # turn-eligibility queries pointing at the *current* world rather
    # than the one we built here.
    game_state.turn.hostiles_probe = lambda: bool(
        hostiles_requiring_battle(game_state.world, party_state.members)
    )
    game_state.turn.can_take_turn = lambda entity: _can_take_turn(
        game_state.world, entity
    )
    game_state.turn.play_mode = play_mode_for_state(
        bool(hostiles_requiring_battle(game_state.world, party_state.members))
    )
    return App(
        game_state=game_state,
        player=built.player,
        dispatcher=dispatcher,
    )


def _make_turn_controller(party_state: PartyState) -> TurnController:
    """Placeholder controller used during GameState construction.

    The probes here are temporary — ``create_app`` overwrites them
    with closures that read through the GameState so they survive
    ``restart``. This shim exists because ``TurnController`` requires
    non-None probes at construction time and we don't have a stable
    handle to the GameState yet.
    """
    return TurnController(
        party_state=party_state,
        hostiles_probe=lambda: False,
        can_take_turn=lambda _entity: True,
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


# Auto-walk key bindings (M22). The keys are the standard Rogue-style
# direction letters in upper case, so ``H/J/K/L`` walk cardinally and
# ``Y/U/B/N`` walk diagonally until interrupted. Detection uses the
# raw curses key (ord) before ``map_key`` lowercases it, so the regular
# lowercase letters keep their single-step semantics.
_AUTOWALK_KEYS: dict[int, tuple[int, int]] = {
    ord("H"): (-1, 0),
    ord("J"): (0, 1),
    ord("K"): (0, -1),
    ord("L"): (1, 0),
    ord("Y"): (-1, -1),
    ord("U"): (1, -1),
    ord("B"): (-1, 1),
    ord("N"): (1, 1),
}


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
            app.ui_mode,
            app.focus,
            app.party.members,
            app.activation.movement_used,
            app.activation.movement_total,
            app.play_mode,
            app.character_creation_state,
            memory=app.memory,
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
