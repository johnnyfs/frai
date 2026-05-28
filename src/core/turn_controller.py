"""TurnController and ActivationSystem.

Owns the per-actor action economy and party turn rotation that used to
live directly on ``App``. Splitting this out keeps ``App`` focused on
input routing and rendering wiring while the gameplay state machine
(active actor, action/movement/bonus/reaction consumption, enemy phase
handoff, voluntary turn entry/exit) has a single home that future
systems (spells, conditions, reactions) can consume through a clean
API.

Design notes
------------

- Per-actor ``ActivationState`` is keyed by entity id. Live activation
  for the currently active actor is exposed via ``active_activation``
  so callers can still read and mutate the same field-shaped object
  that existed before this refactor.
- Resource consumption helpers (``consume_action``, ``consume_movement``,
  ``consume_bonus_action``, ``consume_reaction``) all return ``True``
  when the resource was successfully spent. ``request_extra_action``
  grants and immediately spends an extra action slot — a thin shim
  until M11 introduces real spell-driven grants.
- Voluntary turn-based entry/exit is owned here. Hostiles in awareness
  range always force ``PlayMode.turn_based`` and clear the voluntary
  flag.
- Save/load: ``round_number``, ``active_index``, ``voluntary_turn_based``
  and the per-actor activation map together form the complete
  serializable shape of the controller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from src.core.entity import EntityId
from src.core.modes import PlayMode, is_turn_based_play, play_mode_for_state
from src.core.turns import ActivationState


PartyProvider = Callable[[], list[EntityId]]
HostilesProbe = Callable[[], bool]
CanTakeTurn = Callable[[EntityId], bool]


@dataclass(slots=True)
class TurnController:
    """Owns active actor, action economy, and turn-mode transitions.

    The controller does not own the world or the party list directly;
    instead it takes light callable seams so ``App`` can keep building
    its party the way it already does (M45 will replace these with a
    proper ``PartyState``).
    """

    party_provider: PartyProvider
    hostiles_probe: HostilesProbe
    can_take_turn: CanTakeTurn
    active_index: int = 0
    voluntary_turn_based: bool = False
    play_mode: PlayMode = PlayMode.explore
    round_number: int = 0
    _activations: dict[EntityId, ActivationState] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Actor lookup
    # ------------------------------------------------------------------

    @property
    def party(self) -> list[EntityId]:
        return self.party_provider()

    def current_actor(self, fallback: EntityId | None = None) -> EntityId:
        """Return the active actor.

        In turn-based play this is the party member at ``active_index``;
        in explore play the caller's ``fallback`` (typically the player
        entity) is returned so non-turn-based contexts still have a
        meaningful "focused" actor.
        """

        if not is_turn_based_play(self.play_mode):
            if fallback is None:
                # Fall back to the head of the party rather than raising
                # so explore-mode rendering still works during startup
                # before the player entity is fully wired.
                party = self.party
                return party[0] if party else EntityId(0)
            return fallback
        party = self.party
        return party[self.active_index]

    def activation_for(self, entity: EntityId) -> ActivationState:
        """Return the activation state for ``entity``, creating it lazily."""
        state = self._activations.get(entity)
        if state is None:
            state = ActivationState()
            self._activations[entity] = state
        return state

    @property
    def active_activation(self) -> ActivationState:
        """The activation state of the actor whose turn is currently up.

        In explore-mode this still tracks the player (returned by
        ``current_actor``) so tests that mutate ``app.activation``
        before any turn-based transition still see consistent state.
        """

        party = self.party
        if not party:
            # No party yet (very early app construction). Use a sentinel
            # so callers reading ``activation`` do not crash.
            return self.activation_for(EntityId(0))
        if not is_turn_based_play(self.play_mode):
            return self.activation_for(party[0])
        return self.activation_for(party[self.active_index])

    # ------------------------------------------------------------------
    # Turn lifecycle
    # ------------------------------------------------------------------

    def start_turn(self, entity: EntityId | None = None) -> None:
        """Reset the action economy for ``entity`` (or the active actor)."""
        target = entity if entity is not None else self.current_actor()
        self.activation_for(target).reset_for_activation()

    def end_turn(self) -> None:
        """Advance to the next party member with a valid turn.

        Mirrors the legacy ``App.advance_party_turn`` semantics: walk
        forward from the current actor, run the enemy phase if every
        remaining party member is unavailable in forced turn-based,
        then wrap to the first eligible party member at the top of the
        next round.
        """

        party = self.party
        if not party:
            return
        for index in range(self.active_index + 1, len(party)):
            if self.can_take_turn(party[index]):
                self.active_index = index
                self.start_turn()
                return
        # End-of-round bookkeeping is owned by the caller (App) so it
        # can run the enemy phase and tick the world clock between the
        # last party action and the first action of the next round.
        self.round_number += 1
        for index, entity in enumerate(party):
            if self.can_take_turn(entity):
                self.active_index = index
                self.start_turn()
                return

    def end_turn_with_enemy_phase(
        self,
        run_enemy_phase: Callable[[], None],
        tick_round: Callable[[], None],
    ) -> bool:
        """End the turn, running enemy phase / round tick at end-of-round.

        Returns ``True`` when the round wrapped (i.e. enemy phase ran).
        This keeps the world-clock and enemy-AI hooks owned by ``App``
        while the rotation logic stays here.
        """

        party = self.party
        if not party:
            return False
        for index in range(self.active_index + 1, len(party)):
            if self.can_take_turn(party[index]):
                self.active_index = index
                self.start_turn()
                return False
        # Round boundary: enemies act, world clock ticks, then the next
        # party round starts.
        if self.play_mode is PlayMode.turn_based:
            run_enemy_phase()
        tick_round()
        self.round_number += 1
        for index, entity in enumerate(party):
            if self.can_take_turn(entity):
                self.active_index = index
                self.start_turn()
                return True
        return True

    # ------------------------------------------------------------------
    # Resource consumption
    # ------------------------------------------------------------------

    def consume_action(self) -> bool:
        return self.active_activation.spend_action()

    def consume_movement(self, feet: float) -> bool:
        return self.active_activation.spend_movement(feet)

    def can_consume_movement(self, feet: float) -> bool:
        return self.active_activation.can_spend_movement(feet)

    def consume_bonus_action(self) -> bool:
        return self.active_activation.spend_bonus_action()

    def consume_reaction(self, entity: EntityId | None = None) -> bool:
        """Spend ``entity``'s reaction (out of turn ok).

        Reactions can fire on any actor's turn (M11 will use this for
        spells like Counterspell). Defaults to the currently active
        actor when no entity is supplied.
        """

        target = entity if entity is not None else self.current_actor()
        return self.activation_for(target).spend_reaction()

    def request_extra_action(self, entity: EntityId | None = None) -> bool:
        """Grant + immediately spend an extra-action slot.

        Provided as a placeholder for future bonus-grant features (e.g.
        Action Surge in M11). Today this widens the active actor's
        extra-action budget by one and consumes it.
        """

        target = entity if entity is not None else self.current_actor()
        state = self.activation_for(target)
        state.extra_actions_total += 1
        return state.spend_extra_action()

    # ------------------------------------------------------------------
    # Mode transitions
    # ------------------------------------------------------------------

    def enter_turn_based(self) -> bool:
        """Try to opt in to voluntary turn-based mode.

        Returns ``True`` when the voluntary flag flipped on. Returns
        ``False`` when hostiles already force turn-based (so the toggle
        is meaningless) — caller can emit the refusal message.
        """

        if self.hostiles_probe():
            self.voluntary_turn_based = False
            return False
        self.voluntary_turn_based = True
        self.sync_play_mode()
        return True

    def exit_turn_based(self) -> bool:
        """Try to leave voluntary turn-based mode.

        Returns ``True`` when the voluntary flag flipped off. Returns
        ``False`` when hostiles are still present (turn-based is
        forced and cannot be exited).
        """

        if self.hostiles_probe():
            self.voluntary_turn_based = False
            return False
        self.voluntary_turn_based = False
        self.sync_play_mode()
        return True

    def toggle_turn_based(self) -> tuple[bool, str]:
        """Toggle voluntary turn-based mode.

        Returns a (succeeded, message) pair. ``succeeded`` is ``False``
        only when hostiles force the mode and the player tried to exit.
        """

        if self.hostiles_probe():
            self.voluntary_turn_based = False
            return False, "Cannot exit turn-based mode while hostiles are present."
        self.voluntary_turn_based = not self.voluntary_turn_based
        self.sync_play_mode()
        if self.voluntary_turn_based:
            return True, "Entered turn-based mode."
        return True, "Exited turn-based mode."

    def sync_play_mode(self) -> bool:
        """Recompute play mode from hostile presence.

        Returns ``True`` when the play mode changed. Hostile presence
        always overrides the voluntary flag.
        """

        hostiles = self.hostiles_probe()
        if hostiles:
            self.voluntary_turn_based = False
        next_mode = play_mode_for_state(hostiles, self.voluntary_turn_based)
        if next_mode is self.play_mode:
            return False
        self.play_mode = next_mode
        # Reset the active actor's activation on every mode flip so a
        # carry-over budget from explore doesn't leak into turn-based
        # play (and vice versa).
        self.active_activation.reset_for_activation()
        if not is_turn_based_play(next_mode):
            self.active_index = 0
        return True

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "active_index": self.active_index,
            "voluntary_turn_based": self.voluntary_turn_based,
            "play_mode": self.play_mode.value,
            "round_number": self.round_number,
            "activations": {
                str(entity): _activation_to_dict(state)
                for entity, state in self._activations.items()
            },
        }

    def reset(self) -> None:
        """Clear all per-actor state. Used by ``App.restart``."""
        self.active_index = 0
        self.voluntary_turn_based = False
        self.play_mode = PlayMode.explore
        self.round_number = 0
        self._activations.clear()


def _activation_to_dict(state: ActivationState) -> dict:
    return {
        "movement_used": state.movement_used,
        "movement_total": state.movement_total,
        "action_used": state.action_used,
        "bonus_action_used": state.bonus_action_used,
        "reaction_used": state.reaction_used,
        "extra_actions_used": state.extra_actions_used,
        "extra_actions_total": state.extra_actions_total,
    }
