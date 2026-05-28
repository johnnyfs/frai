from dataclasses import dataclass, field
import curses
import random

from src.core.action_context import ActionContext, ActionResolver, make_default_resolver
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
from src.core.conditions import tick_conditions
from src.core.game_state import GameState
from src.core.items import add_item, armor_item_id_for_name, weapon_item_id_for_name
from src.core.effects import (
    Effect,
    EmitMessage,
    MoveEntity,
)
from src.core.effects_applier import EffectApplier
from src.core.entity import EntityId
from src.core.factions import FactionId
from src.core.actions import (
    Action,
    DropItemAttempt,
    EndTurn,
    ExamineRequest,
    InteractAttempt,
    MoveAttempt,
    PickupAttempt,
    ToggleTurnMode,
)
from src.core.descriptions import examine_tile
from src.core.autowalk import (
    AutowalkRequest,
    InterruptReason,
    interrupt_message,
    step_autowalk,
)
from src.core.modes import PlayMode, UIMode, is_turn_based_play, play_mode_for_state
from src.core.party import CompanionDefinition, companion_definitions_for_player_class
from src.core.party_state import PartyState
from src.core.targeting import TargetingState, any_tile
from src.core.time import SECONDS_PER_ROUND, SECONDS_PER_TURN, advance as advance_world_clock
from src.core.turn_controller import TurnController
from src.core.turns import ActivationState
from src.core.vision import PartyMemory
from src.core.world import World
from src.map.room_builder import BuiltRoom, build_room_world
from src.systems.game_over_system import GameOverSystem
from src.systems.input_system import MOVE_KEYS, map_key
from src.systems.inventory_system import InventorySystem
from src.systems.character_creation_system import CharacterCreationSystem
from src.systems.ai_system import EnemyAISystem
from src.systems.awareness_system import hostiles_requiring_battle
from src.systems.combat_system import CombatSystem
from src.systems.interaction_system import InteractionSystem
from src.systems.loot_system import LootSystem
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
    # M46 phased resolver. Wraps ``dispatcher`` as the default
    # ``resolve`` phase, so existing systems flow through it unchanged
    # while M11/M24/M29 hooks plug into ``pre_check``, ``post_resolve``,
    # and reaction-hook seams without touching App further.
    action_resolver: ActionResolver | None = None
    # Auto-walk runtime state (M22). When ``autowalk`` is non-None the
    # main key handler runs repeated single-step moves until the
    # ``step_autowalk`` predicate fires. This is transient state and is
    # never persisted — save/load drops any in-progress walk.
    autowalk: AutowalkRequest | None = None
    # M20 transient targeting modal. When non-None and ``ui_mode`` is
    # :class:`UIMode.targeting`, the App handles cursor movement and
    # confirm/cancel keys instead of routing them through ``map_key``.
    # This field is intentionally absent from :class:`GameState` (M16
    # save shape) — targeting is a per-input modal and a save written
    # mid-modal drops the in-flight selection.
    targeting: TargetingState | None = None
    loot_rng: random.Random = field(default_factory=random.Random)
    effect_applier: EffectApplier = field(init=False)

    def __post_init__(self) -> None:
        self.effect_applier = EffectApplier(self)
        if self.action_resolver is None:
            self.action_resolver = make_default_resolver(self.dispatcher)
        self.refresh_vision()

    def resolve_action(self, action: Action) -> list[Effect]:
        """Run ``action`` through the M46 phased resolver.

        Equivalent to ``self.dispatcher.dispatch(action, self.world)``
        for the default wiring, but lets pre/post hooks, replacements,
        and reaction hooks fire. New code paths should prefer this entry
        point; the bare ``dispatcher.dispatch`` call still exists for
        callers (and tests) that need the unphased path.
        """
        resolver = self.action_resolver
        assert resolver is not None  # __post_init__ guarantees this
        context = ActionContext(
            actor=self.active_actor(),
            action=action,
            world=self.world,
            turn=self.turn,
        )
        attempt = resolver.resolve(context)
        return list(attempt.effects)

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
        # M20 targeting modal owns all input while ``ui_mode == targeting``.
        # Cursor movement, confirm, and cancel are handled here so the
        # normal action dispatch path never advances the world while a
        # cursor selection is pending.
        if self.ui_mode is UIMode.targeting and self.targeting is not None:
            self._handle_targeting_key(key)
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
        # The `d` drop key (M30) is resolved here in inventory mode
        # because picking the dropped stack requires reading the
        # actor's inventory.
        if self.ui_mode is UIMode.inventory and 0 <= key <= 255:
            try:
                pressed = chr(key).lower()
            except ValueError:
                pressed = ""
            if pressed == "d":
                drop_action = self._resolve_inventory_drop_key()
                if drop_action is not None:
                    self.apply_effects(self._handle_pickup_or_drop(drop_action))
                else:
                    self.apply_effects([EmitMessage("Nothing to drop.")])
                self.sync_play_mode()
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
        if isinstance(action, ExamineRequest) and self.ui_mode is UIMode.play:
            # M21: open the targeting modal in look-only mode. No
            # action is dispatched, no clock advance, no turn consumed
            # — confirm just emits description text via the message log.
            self.begin_examine()
            return
        if isinstance(action, PickupAttempt) and self.ui_mode is UIMode.play:
            self.apply_effects(self._handle_pickup_or_drop(action))
            if not is_turn_based_play(self.turn.play_mode):
                self._tick_world_clock(SECONDS_PER_TURN)
            self.sync_play_mode()
            return
        if isinstance(action, DropItemAttempt):
            # Drop is legal from both play and inventory modals. In
            # turn-based play it consumes the actor's action like a pickup.
            self.apply_effects(self._handle_pickup_or_drop(action))
            if self.ui_mode is UIMode.play and not is_turn_based_play(
                self.turn.play_mode
            ):
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
        effects = self.resolve_action(action)
        self.apply_effects(effects)
        self.sync_play_mode()

    # ------------------------------------------------------------------
    # Turn-mode and action handlers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Targeting (M20)
    # ------------------------------------------------------------------

    def begin_examine(self) -> None:
        """Open the M21 examine cursor (a look-only targeting modal).

        Examine reuses the M20 :class:`TargetingState` plumbing — the
        cursor, the predicate gate, and the input handler are all
        identical. What differs is the ``on_confirm`` callback: instead
        of building an :class:`Action`, it composes description text
        via :func:`examine_tile` (which is memory-aware: visible tiles
        get a live description, remembered tiles a "last seen" prefix,
        unknown tiles a single refusal line) and emits each line into
        the message log. Confirm returns ``None`` so the targeting
        layer treats it as a silent close — no resource is consumed
        and the world is unchanged.

        The cursor's range is the active actor's vision radius so the
        player can walk the cursor over the entire memory frontier
        without confirm rejecting an out-of-range tile. We use the
        :data:`DEFAULT_VISION_RADIUS` from :mod:`src.core.vision` so a
        future vision-radius rework lands in both places at once.
        """

        from src.core.vision import DEFAULT_VISION_RADIUS

        actor = self.active_actor()
        position = self.world.positions.get(actor)
        if position is None:
            # Defensive: no active actor position means there's nothing
            # to anchor the cursor against. Emit a refusal so the
            # player isn't left wondering.
            self.messages.emit("There is nothing to examine.")
            return
        origin = (position.x, position.y)

        def _on_examine_confirm(cell: tuple[int, int]) -> None:
            lines = examine_tile(self.world, self.memory, cell[0], cell[1])
            # Join into a single emit so the message pager handles the
            # multi-line case uniformly. ``MessageState.emit`` wraps
            # long strings automatically.
            text = " ".join(lines) if lines else ""
            if text:
                self.messages.emit(text)
            # Examine never dispatches an action.
            return None

        state = TargetingState(
            origin=origin,
            cursor=origin,
            range=DEFAULT_VISION_RADIUS,
            on_confirm=_on_examine_confirm,
            predicate=any_tile,
            label="Examine: pick a tile (Enter to look, Esc to cancel).",
            # Suppress the default "Targeting cancelled." banner so the
            # description text the confirm callback just emitted stays
            # in the log. An Esc/q press still closes the modal cleanly;
            # we just don't overwrite the player's reason for opening it.
            cancel_message="",
        )
        self.begin_targeting(state)

    def begin_targeting(self, state: TargetingState) -> None:
        """Push ``state`` and switch the screen to :class:`UIMode.targeting`.

        Records the prior :class:`UIMode` on the state (only if not
        already set by the caller) so a subsequent ``cancel_targeting``
        knows where to return. Entering the modal does NOT advance the
        turn or consume any action resource — confirm/cancel is the
        only path that produces (or doesn't produce) an action.

        The ``label`` on the state is emitted into the message log so
        the player sees a hint like "Target a tile (range 6)."
        """

        if state.previous_mode is None:
            state.previous_mode = self.ui_mode
        self.targeting = state
        self.ui_mode = UIMode.targeting
        if state.label:
            self.messages.emit(state.label)

    def cancel_targeting(self) -> None:
        """Exit targeting without dispatching an action.

        Restores the prior :class:`UIMode` (defaulting to :class:`UIMode.play`
        if none was recorded — which is the normal entry path), drops
        the in-flight state, and emits the state's ``cancel_message``
        (default: ``"Targeting cancelled."``). The M21 examine flow
        overrides ``cancel_message`` to an empty string so the
        description text the on_confirm callback emitted is not
        overwritten. No resource is consumed; no turn is advanced.
        """

        state = self.targeting
        previous = state.previous_mode if state is not None else None
        cancel_message = (
            state.cancel_message if state is not None else "Targeting cancelled."
        )
        self.targeting = None
        self.ui_mode = previous if previous is not None else UIMode.play
        if cancel_message:
            self.messages.emit(cancel_message)

    def _handle_targeting_key(self, key: int) -> None:
        """Cursor / confirm / cancel input while ``UIMode.targeting``.

        Movement keys (h/j/k/l + diagonals) move the cursor. Enter/Space
        confirms; Esc/q cancels. Anything else is ignored — we
        deliberately do not fall through to ``map_key`` so the player
        can't, say, open the inventory mid-target.
        """

        state = self.targeting
        if state is None:
            # Defensive: targeting mode without state is a bug. Restore
            # play so the user isn't stuck.
            self.ui_mode = UIMode.play
            return

        # Direction keys: lowercase rogue-style cardinals + diagonals.
        try:
            key_char = chr(key).lower() if 0 <= key <= 255 else ""
        except ValueError:
            key_char = ""

        if key_char in MOVE_KEYS:
            dx, dy = MOVE_KEYS[key_char]
            state.move_cursor(dx, dy)
            return

        # Confirm: Enter or Space.
        if key in (curses.KEY_ENTER, 10, 13) or key_char == " ":
            action, refusal = state.confirm(self.world)
            if action is not None:
                self._dispatch_targeted_action(action)
                return
            if refusal is not None:
                self.messages.emit(refusal)
                return
            # ``(None, None)`` — predicate passed but the builder chose
            # not to dispatch. Treat as a silent cancel.
            self.cancel_targeting()
            return

        # Cancel: Esc or q.
        if key == 27 or key_char == "q":
            self.cancel_targeting()
            return

    def _dispatch_targeted_action(self, action: Action) -> None:
        """Exit targeting cleanly and dispatch ``action`` through the resolver.

        The modal closes before dispatch so the action's effects (and
        any messages it emits) land in the play screen rather than over
        the targeting prompt. Resource consumption (action, movement,
        etc.) is the responsibility of the dispatched action — confirm
        itself does not charge anything.
        """

        previous_mode = (
            self.targeting.previous_mode if self.targeting is not None else None
        )
        self.targeting = None
        self.ui_mode = previous_mode if previous_mode is not None else UIMode.play
        effects = self.resolve_action(action)
        self.apply_effects(effects)
        self.sync_play_mode()

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
            effects = self.resolve_action(action)
            self.turn.consume_action()
            return effects
        return self.resolve_action(action)

    def _resolve_inventory_drop_key(self) -> DropItemAttempt | None:
        """Choose what the active actor drops when `d` is pressed in inventory.

        MVP behavior: drop the first non-equipped stack (quantity 1).
        When a cursor-driven inventory modal lands (follow-up issue) this
        helper can be deleted; until then it gives the player a path to
        get rid of an item without leaving the modal.
        """
        actor = self.active_actor()
        inventory = self.world.inventories.get(actor)
        if inventory is None or not inventory.items:
            return None
        equipment = self.world.equipment.get(actor)
        equipped: set[str] = set()
        if equipment is not None:
            for item_id in (equipment.weapon_item_id, equipment.armor_item_id):
                if item_id is not None:
                    equipped.add(item_id)
        for stack in inventory.items:
            if stack.item_id in equipped:
                continue
            return DropItemAttempt(
                actor=actor, item_id=stack.item_id, quantity=1
            )
        return None

    def _handle_pickup_or_drop(self, action: Action) -> list[Effect]:
        """Dispatch pickup/drop, consuming the action in turn-based play.

        Pickup and drop share the same action-economy contract as
        interact: they cost the actor's action when the party is in
        turn-based play, and are free in explore mode (the M22 / M49
        explore tick happens in ``handle_key``).
        """
        if (
            self.ui_mode is UIMode.play
            and is_turn_based_play(self.turn.play_mode)
        ):
            if self.turn.active_activation.action_used:
                return [EmitMessage("Action already used.")]
            effects = self.resolve_action(action)
            self.turn.consume_action()
            return effects
        return self.resolve_action(action)

    def _handle_explore_move(self, action: MoveAttempt) -> list[Effect]:
        displacement = _party_displacement(self.world, self.party.members, action)
        if displacement is not None:
            return displacement
        previous_positions = [
            (entity, self.world.positions.require(entity).x, self.world.positions.require(entity).y)
            for entity in self.party.follow_order
            if self.world.positions.has(entity)
        ]
        effects = self.resolve_action(action)
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
            effects = self.resolve_action(action)
            self.turn.consume_action()
            return effects

        cost = movement_cost_for_attempt(self.world, action)
        if not self.turn.can_consume_movement(cost):
            return [EmitMessage("No movement remaining.")]

        effects = self.resolve_action(action)
        if any(
            isinstance(effect, MoveEntity) and effect.entity == action.actor
            for effect in effects
        ):
            self.turn.consume_movement(cost)
        return effects

    def _run_autowalk(self) -> InterruptReason | None:
        """Drive an in-progress autowalk to completion or interrupt.

        Each iteration synthesises one ``MoveAttempt`` in the walk's
        direction, dispatches it through the same path a manual move
        uses, and then asks ``step_autowalk`` whether to continue. The
        autowalk-active state field is cleared before we return so a
        subsequent key press starts fresh, and an interrupt message is
        emitted so the player knows why the walk stopped.

        Returns the :class:`InterruptReason` that ended the walk so the
        M36 script runner (and any future debug tooling) can branch on
        a structured value instead of grepping the message log. The
        player-facing key-press path ignores the return value — it
        already has the banner the runner emitted.

        Implementation note: we read the active actor's position before
        and after each dispatch to decide whether the actor moved. The
        movement system already emits ``"Blocked."`` when a step is
        refused, but reading positions directly avoids coupling to the
        message text in the happy path.
        """

        request = self.autowalk
        if request is None:
            return None
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
        return last_reason

    def advance_party_turn(self) -> None:
        self.turn.end_turn_with_enemy_phase(
            run_enemy_phase=self.run_enemy_activations,
            tick_round=self._tick_round_boundary,
        )
        # Vision is recomputed for whichever party member is now active.
        # `apply_effects` already refreshes after the enemy phase, but
        # the no-enemy rotation path needs an explicit kick.
        self.refresh_vision()

    def _tick_world_clock(self, seconds: int) -> None:
        # Time-advance hook. Explore-mode moves/interactions tick a
        # minute; turn-based round ticks fire after the enemy phase.
        # M24 conditions: clock-driven (Minutes) expirations and the
        # explore-mode "turn" boundary tick here so any clock advance
        # — pickups, drops, interactions, single moves — gets a chance
        # to expire buffs/debuffs. Round ticks for ROUNDS-policy
        # conditions are routed through ``_tick_round_boundary`` from
        # ``advance_party_turn``.
        advance_world_clock(self.world.clock, seconds, self.world.schedule)
        self._tick_clock_conditions()
        if seconds >= SECONDS_PER_TURN:
            self._tick_turn_conditions()

    def _tick_round_boundary(self) -> None:
        """End-of-round bookkeeping: tick the clock and run round-tick
        condition handlers (e.g. ``burning`` damage).

        Round-boundary tick effects are dispatched through the normal
        :class:`EffectApplier` so messages, damage, and death share the
        same flow as any other gameplay effect.
        """
        self._tick_world_clock(SECONDS_PER_ROUND)
        actors = self._condition_actors()
        effects = tick_conditions(self.world, actors, boundary="round")
        if effects:
            self.apply_effects(effects)

    def _tick_clock_conditions(self) -> None:
        actors = self._condition_actors()
        if not actors:
            return
        effects = tick_conditions(self.world, actors, boundary="clock")
        if effects:
            self.apply_effects(effects)

    def _tick_turn_conditions(self) -> None:
        actors = self._condition_actors()
        if not actors:
            return
        effects = tick_conditions(self.world, actors, boundary="turn")
        if effects:
            self.apply_effects(effects)

    def _condition_actors(self) -> list[EntityId]:
        """Every entity that currently has a condition store.

        Ticks fire only against actors that have something to tick, so
        a world full of monsters doesn't iterate every store on every
        clock advance.
        """
        return list(self.world.conditions.values.keys())

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
    built, party = _build_party_world(width=width, height=height, rng=rng)
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
            LootSystem(),
            movement,
            combat,
        ]
    )
    party_state = PartyState.from_members(party)
    loot_rng = rng if rng is not None else random.Random()
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
        loot_rng=loot_rng,
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


def _build_party_world(
    width: int,
    height: int,
    *,
    rng: random.Random | None = None,
) -> tuple[BuiltRoom, list[EntityId]]:
    """Build the starting room + party.

    The ``rng`` parameter threads into :func:`yolo_sheet` so a caller
    that needs a reproducible starting party (the M37 playtest harness,
    most prominently) can pin both the world layout and the YOLO class
    roll to the same seed. ``None`` keeps the legacy
    ``random.Random()`` behaviour the interactive launcher relies on.
    """
    built = build_room_world(width=width, height=height)
    built.world.blockers.add(built.player, BlocksMovement("occupied"))
    player_sheet = yolo_sheet(rng=rng)
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
    world.factions.add(entity, Faction(FactionId.PLAYER_PARTY.value))
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
    """Return the hostile entity (if any) at ``action``'s destination tile.

    Routes through the awareness predicate so the App's pre-dispatch
    "bump turns into an attack" check honors the M28 faction model
    (overrides, summoner inheritance, relation table).
    """
    from src.systems.awareness_system import is_hostile_to

    position = world.positions.require(action.actor)
    destination_x = position.x + action.dx
    destination_y = position.y + action.dy
    for entity in world.entities_at(destination_x, destination_y):
        if is_hostile_to(world, action.actor, entity):
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
        targeting = app.targeting
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
            targeting_cursor=targeting.cursor if targeting is not None else None,
            targeting_origin=targeting.origin if targeting is not None else None,
            targeting_range=targeting.range if targeting is not None else 0,
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
