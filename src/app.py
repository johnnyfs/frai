from dataclasses import dataclass, field
import curses
import random

from src.core.action_context import (
    ActionContext,
    ActionResolver,
    Phase,
    PhaseOutcome,
    ResolvedAttempt,
    make_default_resolver,
)
from src.core.combat import combat_stats_for_sheet, starter_armor_for_class, starter_weapon_for_class
from src.core.config import MIN_TERMINAL_HEIGHT, MIN_TERMINAL_WIDTH, WORLD_HEIGHT, WORLD_WIDTH
from src.core.dispatcher import Dispatcher
from src.core.components import (
    BlocksMovement,
    Character,
    Equipment,
    ExperiencePoints,
    Faction,
    Inventory,
    Name,
    PlayerControlled,
    Position,
    Presentation,
)
from src.core.character_creation import CharacterCreationState, CharacterSheet
from src.core.conditions import ConditionKind, tick_conditions
from src.core.dialogue import (
    AcceptQuestEffect,
    CloseDialogueEffect,
    DialogueOption,
    DialogueState,
    OpenShopEffect,
    RecruitEffect,
)
from src.core.quest import QUESTS, accept_quest
from src.core.game_state import GameState
from src.core.items import add_item, armor_item_id_for_name, weapon_item_id_for_name
from src.core.effects import (
    ConsumeSpellSlot,
    DamageEntity,
    Effect,
    EmitMessage,
    EndCondition,
    LevelUp,
    MoveEntity,
)
from src.core.effects_applier import EffectApplier
from src.core.entity import EntityId
from src.core.factions import FactionId
from src.core.actions import (
    Action,
    CastSpellAttempt,
    CloseRestMenu,
    CloseSpellMenu,
    DropItemAttempt,
    EndTurn,
    ExamineRequest,
    InteractAttempt,
    LevelUpConfirm,
    LevelUpDismiss,
    MoveAttempt,
    PickupAttempt,
    RestMenuChoice,
    RestMenuRequest,
    SpellMenuChoice,
    SpellMenuRequest,
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
from src.core.spells import (
    SPELL_CATALOG,
    SpellTargetKind,
    spell_for_id,
)
from src.core.targeting import (
    TargetingState,
    any_tile,
    any_visible_tile,
    make_spell_target_predicate,
)
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
from src.systems.rest_system import attempt_long_rest, attempt_short_rest
from src.systems.spell_system import SpellSystem
from src.systems.start_system import StartSystem, yolo_sheet
from src.systems.stealth_system import StealthSystem
from src.systems.vision_system import VisionSystem
from src.systems.zone_system import tick_zone_transitions
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
    # M13 transient dialogue modal state. When non-None and
    # ``ui_mode`` is :class:`UIMode.dialogue`, the App owns option
    # input directly (number keys, arrow keys, Enter, Esc). Like
    # ``targeting``, this field is intentionally absent from the
    # save aggregate -- a save written mid-conversation simply drops
    # the in-flight modal; the player lands back at the play screen.
    dialogue: DialogueState | None = None
    # M13 shop-partner tracking. Set when a dialogue option opens
    # the shop screen so the M17 shop UI knows which shopkeeper the
    # player is dealing with. Transient -- cleared on shop close.
    shop_partner: EntityId | None = None
    loot_rng: random.Random = field(default_factory=random.Random)
    effect_applier: EffectApplier = field(init=False)

    def __post_init__(self) -> None:
        self.effect_applier = EffectApplier(self)
        if self.action_resolver is None:
            self.action_resolver = make_default_resolver(self.dispatcher)
        # M11 spell hooks. Slot consumption lives in PRE_CHECK so a
        # failed resolve (e.g. unknown spell, no slot) doesn't burn a
        # slot. The reaction hook ends concentration when the caster
        # takes damage in the same resolved attempt — this is the M24
        # seam for "damage on a concentrating caster breaks concentration"
        # (the SRD rule is "save or break"; M11 keeps the simpler "any
        # damage breaks" until M29 brings the save-driven branch).
        self.action_resolver.register(Phase.PRE_CHECK, self._spell_pre_check)
        self.action_resolver.add_reaction(self._concentration_break_reaction)
        self.refresh_vision()

    def _spell_pre_check(self, context: ActionContext) -> PhaseOutcome:
        """Consume the caster's spell slot before the spell system runs (M11).

        Cantrips (level 0) skip slot consumption entirely. A leveled
        spell with no slot remaining is cancelled here — the resolver
        short-circuits past the dispatcher so the spell system never
        runs, no message is emitted by the spell system, and no
        condition / damage effect is produced. The pre-check itself
        emits ``"No spell slot available."``.

        Unknown spell ids fall through unmodified; the spell system
        will emit its own ``"Unknown spell ..."`` refusal.
        """

        action = context.action
        if not isinstance(action, CastSpellAttempt):
            return PhaseOutcome()
        try:
            spell = spell_for_id(action.spell_id)
        except KeyError:
            return PhaseOutcome()
        if spell.level <= 0:
            return PhaseOutcome()  # cantrip
        slots = context.world.spell_slots.get(action.actor)
        if slots is None or not slots.has_slot(spell.level):
            return PhaseOutcome(
                effects=(EmitMessage("No spell slot available."),),
                cancel=True,
            )
        # Spend the slot via a typed effect so save/load and observation
        # stay consistent. The actual decrement lands in
        # ``EffectApplier`` when the batch applies — the pre-check is
        # the only place that decides whether the slot is *available*,
        # and the applier is the only place that updates the ledger.
        return PhaseOutcome(
            effects=(ConsumeSpellSlot(action.actor, spell.level),)
        )

    def _concentration_break_reaction(self, attempt: "ResolvedAttempt") -> list[Effect]:
        """End concentration when a concentrating caster takes damage (M11/M24).

        Walks the resolved attempt's effects for any
        :class:`DamageEntity` whose target is currently concentrating
        and appends an :class:`EndCondition` to clear the concentration
        condition. The hook is intentionally cautious — it does not
        clear any of the conditions the concentration was sustaining
        (those clear on their own duration; the SRD lets short-duration
        buffs ride out their last second when their source drops). M11
        keeps the scope narrow; M24 follow-ups can broaden the cascade.
        """

        world = attempt.original.world
        extra: list[Effect] = []
        for effect in attempt.effects:
            if not isinstance(effect, DamageEntity):
                continue
            if effect.amount <= 0:
                continue
            store = world.conditions.get(effect.entity)
            if store is None or not store.has(ConditionKind.CONCENTRATING):
                continue
            extra.append(EndCondition(effect.entity, ConditionKind.CONCENTRATING))
            extra.append(EmitMessage("Concentration breaks."))
        return extra

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
        # M34: surface shelter-zone entry / exit messages once per
        # transition. ZoneSystem reads the party leader's tile after
        # the effect batch has applied, so it sees the post-move
        # position regardless of how the move was emitted (single
        # step, autowalk, party displacement). The returned messages
        # are applied through a second pass so any future zone
        # effects (e.g. M14 quest triggers) flow through the same
        # EffectApplier.
        zone_effects = tick_zone_transitions(self)
        if zone_effects:
            self.effect_applier.apply_all(zone_effects)
        # M25: an effect batch may have granted XP and crossed a
        # threshold; pop the level-up modal so the player consumes
        # the pending level-up immediately. Skips when a different
        # modal is already on screen.
        self.maybe_open_level_up_modal()

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
        # M13 dialogue modal owns all input while ``ui_mode == dialogue``.
        # Number keys (1..9) and arrow keys + Enter select an option;
        # Esc / q close the dialogue with no effect. We deliberately
        # don't fall through to ``map_key`` here for the same reason
        # the targeting modal owns its keys -- stray inventory/quit
        # keys must not leak through a stacked modal.
        if self.ui_mode is UIMode.dialogue and self.dialogue is not None:
            self._handle_dialogue_key(key)
            return
        # M13 shop modal owns its keys until the full buy/sell UI
        # lands (M17). Until then ``Esc`` / ``q`` close it so the
        # player is never trapped, and ``b`` / ``s`` are reserved
        # for the eventual buy / sell actions.
        if self.ui_mode is UIMode.shop:
            self._handle_shop_key(key)
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
        if isinstance(action, SpellMenuRequest) and self.ui_mode is UIMode.play:
            self._open_spell_menu()
            return
        if isinstance(action, CloseSpellMenu) and self.ui_mode is UIMode.spell_menu:
            self._close_spell_menu()
            return
        if isinstance(action, SpellMenuChoice) and self.ui_mode is UIMode.spell_menu:
            self._handle_spell_menu_choice(action)
            return
        if isinstance(action, RestMenuRequest) and self.ui_mode is UIMode.play:
            self._open_rest_menu()
            return
        if isinstance(action, CloseRestMenu) and self.ui_mode is UIMode.rest_menu:
            self._close_rest_menu()
            return
        if isinstance(action, RestMenuChoice) and self.ui_mode is UIMode.rest_menu:
            self._handle_rest_menu_choice(action)
            return
        if isinstance(action, LevelUpConfirm) and self.ui_mode is UIMode.level_up:
            self._handle_level_up_confirm()
            return
        if isinstance(action, LevelUpDismiss) and self.ui_mode is UIMode.level_up:
            self._close_level_up_modal()
            return
        if isinstance(action, InteractAttempt) and self.ui_mode is UIMode.play:
            if action.dx == 0 and action.dy == 0:
                action = InteractAttempt(action.actor, self.facing[0], self.facing[1], action.check_result)
            self.apply_effects(self._handle_interaction(action))
            # M13: dialogue open is a pure UI modal -- no world time
            # advances and no action is consumed (the consume check
            # already lives in ``_handle_interaction`` for non-NPC
            # interactions). Detect the mode flip and skip the tick.
            opened_modal = self.ui_mode is not UIMode.play
            if not opened_modal and not is_turn_based_play(self.turn.play_mode):
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

    # ------------------------------------------------------------------
    # Spell menu (M11)
    # ------------------------------------------------------------------

    def _spell_menu_entries(self) -> list[tuple[str, str]]:
        """Return ``(letter, spell_id)`` pairs for the active actor's
        spell list.

        Letters are assigned sequentially (``a``, ``b``, ``c``, ...).
        Empty list when the actor has no :class:`SpellList`.
        """

        actor = self.active_actor()
        spell_list = self.world.spell_lists.get(actor)
        if spell_list is None:
            return []
        return [(chr(ord("a") + index), spell_id) for index, spell_id in enumerate(spell_list.known)]

    def _open_spell_menu(self) -> None:
        entries = self._spell_menu_entries()
        if not entries:
            self.apply_effects([EmitMessage("You know no spells.")])
            return
        self.ui_mode = UIMode.spell_menu
        labels = ", ".join(
            f"{letter}) {SPELL_CATALOG[spell_id].name}"
            for letter, spell_id in entries
        )
        self.messages.emit(f"Cast: {labels} (q to cancel)")

    def _close_spell_menu(self) -> None:
        self.ui_mode = UIMode.play
        self.messages.emit("Spell menu closed.")

    def _handle_spell_menu_choice(self, action: SpellMenuChoice) -> None:
        """Resolve a spell letter into the appropriate cast flow.

        Spells that need a target (single-entity, area) open a
        :class:`TargetingState` via ``begin_targeting``. The
        :class:`CastSpellAttempt` is built in the targeting
        ``on_confirm`` callback so cancellation truly costs nothing
        (no slot consumed, no turn advanced). Friendly-group spells
        target the party deterministically — picking the first
        ``group_size`` party members within range — to keep the M11
        scope small; a cursor-driven multi-pick UI is a follow-up.
        """

        entries = self._spell_menu_entries()
        entry_map = {letter: spell_id for letter, spell_id in entries}
        spell_id = entry_map.get(action.spell_id)
        if spell_id is None:
            self.messages.emit(f"No spell on '{action.spell_id}'.")
            return
        try:
            spell = spell_for_id(spell_id)
        except KeyError:
            self.messages.emit(f"Unknown spell '{spell_id}'.")
            return

        # Close the menu before opening the next modal so observers
        # (renderer, observation) see consistent state.
        self.ui_mode = UIMode.play

        actor = self.active_actor()

        if spell.target_kind is SpellTargetKind.SINGLE_ENTITY:
            self._begin_single_entity_target(actor, spell.spell_id)
            return
        if spell.target_kind is SpellTargetKind.AREA_RADIUS:
            self._begin_area_target(actor, spell.spell_id)
            return
        if spell.target_kind is SpellTargetKind.FRIENDLY_GROUP:
            self._cast_friendly_group(actor, spell)
            return

        self.messages.emit("This spell cannot be cast here.")

    def _begin_single_entity_target(self, actor: EntityId, spell_id: str) -> None:
        spell = spell_for_id(spell_id)
        position = self.world.positions.require(actor)
        require_hostile = spell.is_damage_spell
        allow_self_target = spell.allow_self_target

        def _pick_target(cell: tuple[int, int]) -> EntityId | None:
            """Pick the legal target on ``cell``.

            Mirrors :func:`make_spell_target_predicate` so the confirm
            callback never returns an entity the predicate would have
            rejected. Self is only returned when ``allow_self_target``;
            damage spells skip friendlies, friendly spells skip
            hostiles.
            """

            from src.systems.awareness_system import is_hostile_to

            for entity in self.world.entities_at(cell[0], cell[1]):
                if not self.world.combat_stats.has(entity):
                    continue
                if entity == actor:
                    if allow_self_target:
                        return entity
                    continue
                if require_hostile:
                    if is_hostile_to(self.world, actor, entity):
                        return entity
                else:
                    if not is_hostile_to(self.world, actor, entity):
                        return entity
            return None

        def _on_confirm(cell: tuple[int, int]) -> Action | None:
            target = _pick_target(cell)
            if target is None:
                self.messages.emit("No target there.")
                return None
            return CastSpellAttempt(
                actor=actor, spell_id=spell_id, target_entity=target
            )

        self.begin_targeting(
            TargetingState(
                origin=(position.x, position.y),
                cursor=(position.x, position.y),
                range=spell.range,
                on_confirm=_on_confirm,
                predicate=make_spell_target_predicate(
                    actor,
                    radius=spell.range,
                    require_hostile=require_hostile,
                    allow_self_target=allow_self_target,
                ),
                label=f"Target {spell.name} (range {spell.range})",
            )
        )

    def _begin_area_target(self, actor: EntityId, spell_id: str) -> None:
        spell = spell_for_id(spell_id)
        position = self.world.positions.require(actor)

        def _on_confirm(cell: tuple[int, int]) -> Action | None:
            return CastSpellAttempt(
                actor=actor, spell_id=spell_id, target_tile=cell
            )

        self.begin_targeting(
            TargetingState(
                origin=(position.x, position.y),
                cursor=(position.x, position.y),
                range=spell.range,
                on_confirm=_on_confirm,
                predicate=any_visible_tile,
                label=f"Target {spell.name} (range {spell.range}, radius {spell.area_radius})",
            )
        )

    def _cast_friendly_group(self, actor: EntityId, spell) -> None:
        """Resolve a friendly-group spell against the first N party members.

        Picks up to ``spell.group_size`` party members within
        Chebyshev range of the caster. The action is dispatched
        immediately — no targeting modal — because the M11 scope keeps
        the multi-pick UI simple. A future follow-up can replace this
        with a cursor-driven selection (M21 or a new modal).
        """

        position = self.world.positions.require(actor)
        targets: list[EntityId] = []
        for member in self.party.members:
            if len(targets) >= spell.group_size:
                break
            member_position = self.world.positions.get(member)
            if member_position is None:
                continue
            distance = max(
                abs(member_position.x - position.x),
                abs(member_position.y - position.y),
            )
            if distance > spell.range:
                continue
            targets.append(member)
        if not targets:
            self.messages.emit("No friendly targets in range.")
            return
        attempt = CastSpellAttempt(
            actor=actor, spell_id=spell.spell_id, target_entities=tuple(targets)
        )
        effects = self.resolve_action(attempt)
        self.apply_effects(effects)
        self.sync_play_mode()

    # ------------------------------------------------------------------
    # Rest menu (M34)
    # ------------------------------------------------------------------

    def _open_rest_menu(self) -> None:
        """Switch to the rest-selection modal.

        Refusing to open while in combat keeps the modal cheap: an
        agentic playtester (or a player who pressed ``r`` mid-fight)
        gets a clear refusal instead of stepping into a modal that
        would just refuse the only two options anyway.
        """

        if self.play_mode is not PlayMode.explore:
            self.apply_effects([EmitMessage("You cannot rest while in combat.")])
            return
        self.ui_mode = UIMode.rest_menu
        self.messages.emit("Rest: s) short, l) long (q to cancel)")

    def _close_rest_menu(self) -> None:
        self.ui_mode = UIMode.play
        self.messages.emit("Rest menu closed.")

    def _handle_rest_menu_choice(self, action: RestMenuChoice) -> None:
        """Resolve the player's rest-kind pick through the rest system.

        Close the modal first so the resulting message lands in the
        play screen rather than over the rest prompt — same pattern
        the spell menu uses.
        """

        self.ui_mode = UIMode.play
        if action.kind == "short":
            effects = attempt_short_rest(self, rng=self.loot_rng)
        elif action.kind == "long":
            effects = attempt_long_rest(self, rng=self.loot_rng)
        else:
            self.messages.emit(f"Unknown rest kind '{action.kind}'.")
            return
        self.apply_effects(effects)
        self.sync_play_mode()

    # ------------------------------------------------------------------
    # Level-up modal (M25)
    # ------------------------------------------------------------------

    def _first_party_member_with_pending_level_up(self) -> EntityId | None:
        """Return the first party member carrying a ``LevelUpAvailable``.

        Surface order is recruitment order so the player sees their
        own character first when ding'ing alongside companions. ``None``
        means nobody is pending — the App skips opening the modal.
        """

        for member in self.party.members:
            if self.world.level_up_pending.has(member):
                return member
        return None

    def maybe_open_level_up_modal(self) -> bool:
        """Open the level-up modal if any party member has a pending level-up.

        Returns ``True`` when the modal was opened. The App calls this
        after applying an effect batch that may have granted XP (kill,
        quest reward) so the modal pops automatically. Combat / dialogue
        modals win — we won't open while the player is in a different
        modal. The forced turn-based mode is fine: the modal is paused
        play, not a new actor's turn.
        """

        if self.ui_mode is not UIMode.play:
            return False
        if self._first_party_member_with_pending_level_up() is None:
            return False
        self.ui_mode = UIMode.level_up
        return True

    def _close_level_up_modal(self) -> None:
        self.ui_mode = UIMode.play
        self.messages.emit("Level-up deferred.")

    def _handle_level_up_confirm(self) -> None:
        """Resolve the pending level-up on the first pending party member.

        Routes through the standard effect pipeline so the level-up
        message and any spell-slot / HP gains land in the same flow
        as other gameplay effects. The modal closes after the effect
        applies; if another party member still has a pending level-up,
        the modal reopens so the player can confirm them one at a time.
        """

        member = self._first_party_member_with_pending_level_up()
        if member is None:
            self.ui_mode = UIMode.play
            return
        # Close the modal before applying so the resulting messages
        # land in the play screen, matching the spell/rest modal pattern.
        self.ui_mode = UIMode.play
        self.apply_effects([LevelUp(member)])
        # Another member may also be pending; reopen the modal so the
        # player works through them sequentially.
        self.maybe_open_level_up_modal()

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
        # M13: if the targeted tile holds an NPC, open the dialogue
        # modal instead of running the door/lock/trap/container
        # interaction. Dialogue is a UI modal, not a world action -
        # it does not consume the actor's action even in turn-based
        # play.
        if self._try_open_dialogue_from_interaction(action):
            return []
        if is_turn_based_play(self.turn.play_mode):
            if self.turn.active_activation.action_used:
                return [EmitMessage("Action already used.")]
            effects = self.resolve_action(action)
            self.turn.consume_action()
            return effects
        return self.resolve_action(action)

    # ------------------------------------------------------------------
    # Dialogue (M13)
    # ------------------------------------------------------------------

    def _try_open_dialogue_from_interaction(self, action: InteractAttempt) -> bool:
        """If ``action`` targets an NPC, open dialogue and return True.

        Returns ``False`` when there is no NPC at the targeted tile so
        the normal interaction path (doors/locks/traps/containers)
        can run. Opening the dialogue itself does not consume the
        actor's action - it's a UI modal.
        """

        position = self.world.positions.get(action.actor)
        if position is None:
            return False
        target_x = position.x + action.dx
        target_y = position.y + action.dy
        npc_entity, npc_dialogue = self._find_npc_dialogue_at(target_x, target_y)
        if npc_entity is None or npc_dialogue is None:
            return False
        self.begin_dialogue(npc_entity, npc_dialogue.tree)
        return True

    def _find_npc_dialogue_at(self, x: int, y: int):
        """Return ``(entity, NPCDialogue)`` for the first NPC at ``(x, y)``."""

        for entity in self.world.entities_at(x, y):
            if not self.world.npcs.has(entity):
                continue
            dialogue = self.world.npc_dialogues.get(entity)
            if dialogue is None:
                continue
            return entity, dialogue
        return None, None

    def begin_dialogue(self, speaker: EntityId, tree) -> None:
        """Open the dialogue modal with ``speaker`` as the NPC.

        Records the previous :class:`UIMode` so closing the modal
        returns the player to wherever they were (typically the play
        screen). Emits the opening line into the message log so the
        player has a record once the modal closes.
        """

        from src.core.dialogue import DialogueTree

        if not isinstance(tree, DialogueTree):
            raise TypeError(f"begin_dialogue expects DialogueTree, got {type(tree)!r}")
        previous = self.ui_mode.value
        self.dialogue = DialogueState.begin(
            speaker=speaker,
            tree=tree,
            previous_mode=previous,
        )
        self.ui_mode = UIMode.dialogue

    def close_dialogue(self) -> None:
        """Close the dialogue modal, restoring the previous UI mode.

        The player is restored to whichever screen they were on when
        the modal opened (typically :class:`UIMode.play`). The
        in-flight :class:`DialogueState` is cleared.
        """

        state = self.dialogue
        previous = state.previous_mode if state is not None else None
        self.dialogue = None
        try:
            restored = UIMode(previous) if previous is not None else UIMode.play
        except ValueError:
            restored = UIMode.play
        self.ui_mode = restored

    def select_dialogue_option(self, index: int) -> None:
        """Select option ``index`` (0-based) on the current node.

        Out-of-range selections are silently ignored. The selected
        option's effect (if any) is resolved, then navigation to
        ``next_node`` happens (if set), otherwise the modal closes.
        """

        state = self.dialogue
        if state is None:
            return
        node = state.node()
        if not (0 <= index < len(node.options)):
            return
        option = node.options[index]
        self._apply_dialogue_option(option)

    def _apply_dialogue_option(self, option: DialogueOption) -> None:
        """Resolve ``option``'s effect and follow its ``next_node``."""

        state = self.dialogue
        if state is None:
            return
        # Effects first so a recruit/shop hand-off completes before
        # we navigate. RecruitEffect implicitly closes the modal so
        # we early-return after it fires.
        effect = option.effect
        if isinstance(effect, RecruitEffect):
            self._apply_recruit_effect(state.speaker)
            self.close_dialogue()
            return
        if isinstance(effect, OpenShopEffect):
            self._apply_open_shop_effect(state.speaker)
            return
        if isinstance(effect, AcceptQuestEffect):
            self._apply_accept_quest_effect(effect.quest_id)
            # Fall through to navigation so the accept can land on a
            # follow-up node (the quest_offer_tree's "accepted" node).
        # An explicit CloseDialogueEffect is treated like no effect;
        # navigation rules below handle the close.
        _ = effect if isinstance(effect, CloseDialogueEffect) else None

        if option.next_node is None:
            self.close_dialogue()
            return
        state.advance_to(option.next_node)

    def _apply_recruit_effect(self, npc_entity: EntityId) -> None:
        """Add ``npc_entity`` to the party and remove it from the world.

        The NPC marker, dialogue payload, presentation, and blocker
        are stripped so the entity is no longer treated as a
        standing NPC. The renderer projects party glyphs via
        :class:`PartyState.members`, so the entity will now render
        as ``#`` (or ``@`` if it ever becomes the lead).
        """

        if self.party.is_member(npc_entity):
            self.messages.emit("Already in your party.")
            return
        self.party.recruit(npc_entity)
        world = self.world
        # Strip NPC-only marker/payload; keep position, character
        # sheet, and combat stats so the entity is a working party
        # member from the next tick.
        world.npcs.values.pop(npc_entity, None)
        world.npc_dialogues.values.pop(npc_entity, None)
        # Switch faction to the party so awareness queries treat the
        # new member as friendly. The previous faction (typically
        # "town") is dropped wholesale -- M28 doesn't need the
        # original on a party member.
        world.factions.add(npc_entity, Faction(FactionId.PLAYER_PARTY.value))
        # Mark as player-controlled and refresh vision so the
        # camera/UI immediately treats them as part of the party.
        world.player_controlled.add(npc_entity, PlayerControlled())
        name = world.name_for(npc_entity)
        self.messages.emit(f"{name} joined your party.")
        self.refresh_vision()

    def _apply_accept_quest_effect(self, quest_id: str) -> None:
        """Mark ``quest_id`` as accepted on the party quest log (M14).

        Emits the quest's accept message and victory condition into
        the message log so the player has a record once the dialogue
        modal closes. Unknown quest ids emit a warning rather than
        raising — content typos shouldn't crash the game.
        """

        quest = QUESTS.get(quest_id)
        if quest is None:
            self.messages.emit(f"Unknown quest: {quest_id}.")
            return
        log = self.party.quests
        changed = accept_quest(log, quest_id)
        if not changed:
            # Already accepted/completed — silent no-op rather than a
            # noisy "you already took this quest" message.
            return
        self.messages.emit(quest.accept_message)
        self.messages.emit(f"Victory: {quest.victory_condition}")

    def _apply_open_shop_effect(self, shopkeeper: EntityId) -> None:
        """Switch the UI to the shop screen for ``shopkeeper``.

        Does not perform any inventory mutations -- the shop screen
        itself (M17 follow-up) drives buy/sell. We just record the
        shopkeeper id and flip the mode.
        """

        if not self.world.shops.has(shopkeeper):
            self.messages.emit("They are not running a shop.")
            self.close_dialogue()
            return
        self.shop_partner = shopkeeper
        self.dialogue = None
        self.ui_mode = UIMode.shop

    def close_shop(self) -> None:
        """Close the shop modal and return to play.

        Clears the transient ``shop_partner`` so a subsequent open
        starts fresh. The shop screen does not consume any action
        economy or world time, so we simply flip the mode back.
        """

        self.shop_partner = None
        self.ui_mode = UIMode.play

    def _handle_shop_key(self, key: int) -> None:
        """Input dispatch while the shop modal is open.

        The full buy/sell UI is an M17 follow-up; this handler exists
        so the modal isn't a key trap. ``Esc`` / ``q`` close the
        modal and return to play. ``b`` / ``s`` are reserved buy /
        sell keys that currently emit a placeholder message so the
        player gets feedback rather than silence.
        """

        try:
            key_char = chr(key).lower() if 0 <= key <= 255 else ""
        except ValueError:
            key_char = ""

        if key == 27 or key_char == "q":
            self.close_shop()
            return
        if key_char == "b":
            self.messages.emit("Buy not yet implemented.")
            return
        if key_char == "s":
            self.messages.emit("Sell not yet implemented.")
            return

    def _handle_dialogue_key(self, key: int) -> None:
        """Number / arrow / Enter / Esc input while the dialogue modal is open.

        Bindings:

        - ``1..9``: select the matching option (1 == first)
        - ``Esc`` or ``q``: close the modal (no effect)
        - ``Enter`` / ``Space``: on a no-option terminal node, close
          the modal; otherwise treated as "select option 1" so a
          single-option node (info NPC) feels natural to dismiss.

        Anything else is ignored.
        """

        state = self.dialogue
        if state is None:
            self.ui_mode = UIMode.play
            return

        try:
            key_char = chr(key).lower() if 0 <= key <= 255 else ""
        except ValueError:
            key_char = ""

        # Cancel.
        if key == 27 or key_char == "q":
            self.close_dialogue()
            return

        node = state.node()
        # Number-key selection: 1..9 maps to option index 0..8.
        if key_char.isdigit() and key_char != "0":
            index = int(key_char) - 1
            self.select_dialogue_option(index)
            return

        # Enter / Space: select option 1 on a single-option node, or
        # close the modal on a terminal node.
        if key in (curses.KEY_ENTER, 10, 13) or key_char == " ":
            if not node.options:
                self.close_dialogue()
                return
            self.select_dialogue_option(0)
            return

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
    spell_rng = rng if rng is not None else random.Random()
    stealth_rng = rng if rng is not None else random.Random()
    dispatcher = Dispatcher(
        systems=[
            StartSystem(),
            GameOverSystem(),
            InventorySystem(),
            CharacterCreationSystem(),
            QuitSystem(),
            InteractionSystem(rng=interaction_rng),
            LootSystem(),
            SpellSystem(rng=spell_rng),
            StealthSystem(rng=stealth_rng),
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
    # M25 XP ledger. Every PC starts at level 1 with 0 XP; the
    # leveling system grants XP on kills (combat) and quest rewards
    # and attaches LevelUpAvailable when a threshold is crossed.
    world.experience_points.add(
        entity, ExperiencePoints(value=0, level=sheet.level)
    )
    _assign_spell_loadout(world, entity, sheet)


def _assign_spell_loadout(world: World, entity: EntityId, sheet: CharacterSheet) -> None:
    """Attach the M11 spell list + slot ledger appropriate for the class.

    Non-casters get nothing. Casters receive every catalog spell so the
    representative M11 playtest exercises the whole action path
    without needing to pick spells in character creation. M25 (leveling)
    will replace this default loadout with the proper SRD spells-known
    progression and the character-creation cantrip / spell picks the
    player made.
    """

    from src.core.spells import (
        SpellList,
        SpellSlots,
        starting_spell_loadout_for_class,
    )

    known, slot_pairs = starting_spell_loadout_for_class(sheet.character_class)
    if not known:
        return
    world.spell_lists.add(entity, SpellList(known=tuple(known)))
    world.spell_slots.add(entity, SpellSlots.from_pairs(slot_pairs))


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
            dialogue=app.dialogue,
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
