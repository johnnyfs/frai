"""Unit tests for TurnController / ActivationSystem (M44).

These tests exercise the controller directly with a tiny fixture so the
unit-under-test is decoupled from ``App`` and from curses. The
fixture builds a small "world" of integer entity ids and uses
predicate seams (party provider, hostiles probe, can-take-turn) the
same way ``App`` wires them up.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.entity import EntityId
from src.core.modes import PlayMode
from src.core.turn_controller import TurnController


# ---------------------------------------------------------------------------
# Tiny test fixture
# ---------------------------------------------------------------------------


@dataclass
class _Fixture:
    party: list[EntityId]
    hostiles: bool = False
    dead: set[EntityId] = field(default_factory=set)


def _make_controller(
    party_size: int = 3,
    *,
    hostiles: bool = False,
    play_mode: PlayMode = PlayMode.explore,
) -> tuple[TurnController, _Fixture]:
    fixture = _Fixture(party=[EntityId(i + 1) for i in range(party_size)], hostiles=hostiles)
    controller = TurnController(
        party_provider=lambda: fixture.party,
        hostiles_probe=lambda: fixture.hostiles,
        can_take_turn=lambda entity: entity not in fixture.dead,
        play_mode=play_mode,
    )
    return controller, fixture


# ---------------------------------------------------------------------------
# Per-actor resource reset on start_turn
# ---------------------------------------------------------------------------


def test_start_turn_resets_active_actor_resources() -> None:
    controller, _ = _make_controller(party_size=2, play_mode=PlayMode.turn_based)
    actor = controller.current_actor()

    controller.consume_movement(6)
    controller.consume_action()
    controller.consume_bonus_action()

    controller.start_turn()

    state = controller.activation_for(actor)
    assert state.movement_used == 0
    assert state.action_used is False
    assert state.bonus_action_used is False


def test_start_turn_preserves_extra_action_grants() -> None:
    controller, _ = _make_controller(party_size=1, play_mode=PlayMode.turn_based)
    actor = controller.current_actor()
    state = controller.activation_for(actor)
    state.extra_actions_total = 2
    state.spend_extra_action()

    controller.start_turn()

    assert state.extra_actions_used == 0
    assert state.extra_actions_total == 2


# ---------------------------------------------------------------------------
# Consume blocks the same resource twice
# ---------------------------------------------------------------------------


def test_consume_action_blocks_second_consume() -> None:
    controller, _ = _make_controller(party_size=1, play_mode=PlayMode.turn_based)

    assert controller.consume_action() is True
    assert controller.consume_action() is False
    assert controller.active_activation.action_used is True


def test_consume_movement_respects_remaining_budget() -> None:
    controller, _ = _make_controller(party_size=1, play_mode=PlayMode.turn_based)
    controller.active_activation.movement_total = 6

    assert controller.consume_movement(3) is True
    assert controller.consume_movement(4) is False
    assert controller.active_activation.movement_used == 3


def test_bonus_action_and_reaction_are_tracked_distinctly() -> None:
    controller, _ = _make_controller(party_size=1, play_mode=PlayMode.turn_based)

    assert controller.consume_bonus_action() is True
    assert controller.consume_reaction() is True
    # Each resource can only be spent once, but bonus action and
    # reaction must not interfere with each other.
    assert controller.consume_bonus_action() is False
    assert controller.consume_reaction() is False
    # Action remains untouched.
    assert controller.consume_action() is True


# ---------------------------------------------------------------------------
# Reaction tracking for non-active actors
# ---------------------------------------------------------------------------


def test_reaction_can_fire_on_non_active_actor() -> None:
    controller, fixture = _make_controller(party_size=2, play_mode=PlayMode.turn_based)
    active = controller.current_actor()
    other = fixture.party[1]
    assert active != other

    # Out-of-turn reaction on a non-active party member is legal.
    assert controller.consume_reaction(other) is True
    assert controller.activation_for(other).reaction_used is True
    # The active actor's reaction slot is unaffected.
    assert controller.consume_reaction() is True


# ---------------------------------------------------------------------------
# Request extra action shim
# ---------------------------------------------------------------------------


def test_request_extra_action_grants_and_spends() -> None:
    controller, _ = _make_controller(party_size=1, play_mode=PlayMode.turn_based)
    state = controller.active_activation

    assert state.extra_actions_total == 0
    assert controller.request_extra_action() is True
    assert state.extra_actions_total == 1
    assert state.extra_actions_used == 1


# ---------------------------------------------------------------------------
# Voluntary turn entry/exit
# ---------------------------------------------------------------------------


def test_enter_turn_based_succeeds_when_no_hostiles() -> None:
    controller, _ = _make_controller(party_size=2, hostiles=False)

    assert controller.enter_turn_based() is True
    assert controller.voluntary_turn_based is True
    assert controller.play_mode is PlayMode.voluntary_turn


def test_enter_turn_based_fails_when_hostiles_present() -> None:
    controller, _ = _make_controller(party_size=2, hostiles=True)
    controller.sync_play_mode()

    assert controller.play_mode is PlayMode.turn_based
    # Already turn-based via hostiles. Trying to enter does nothing.
    assert controller.enter_turn_based() is False
    assert controller.voluntary_turn_based is False


def test_exit_turn_based_blocked_while_hostiles_present() -> None:
    controller, fixture = _make_controller(party_size=2, hostiles=False)
    controller.enter_turn_based()
    fixture.hostiles = True
    controller.sync_play_mode()
    assert controller.play_mode is PlayMode.turn_based

    # Can't opt out of turn-based while there are hostiles.
    assert controller.exit_turn_based() is False
    assert controller.play_mode is PlayMode.turn_based


def test_voluntary_entry_and_exit_preserves_party_order() -> None:
    controller, _ = _make_controller(party_size=3, hostiles=False)
    starting_party = list(controller.party)

    controller.enter_turn_based()
    controller.end_turn()  # rotate once
    rotated_actor = controller.current_actor()
    assert rotated_actor == starting_party[1]

    controller.exit_turn_based()

    # Exiting turn-based snaps back to the head of the party so explore
    # mode always follows the player.
    assert controller.play_mode is PlayMode.explore
    assert controller.active_index == 0
    assert list(controller.party) == starting_party


def test_toggle_turn_based_returns_messages() -> None:
    controller, fixture = _make_controller(party_size=2, hostiles=False)

    ok, message = controller.toggle_turn_based()
    assert ok is True
    assert message == "Entered turn-based mode."
    assert controller.play_mode is PlayMode.voluntary_turn

    ok, message = controller.toggle_turn_based()
    assert ok is True
    assert message == "Exited turn-based mode."
    assert controller.play_mode is PlayMode.explore

    # Hostiles arrive: toggle refuses to leave turn-based.
    fixture.hostiles = True
    controller.sync_play_mode()
    ok, message = controller.toggle_turn_based()
    assert ok is False
    assert message == "Cannot exit turn-based mode while hostiles are present."


# ---------------------------------------------------------------------------
# Enemy phase handoff after all PCs end turn
# ---------------------------------------------------------------------------


def test_end_turn_with_enemy_phase_fires_at_round_boundary() -> None:
    controller, _ = _make_controller(party_size=2, hostiles=True)
    controller.sync_play_mode()
    assert controller.play_mode is PlayMode.turn_based
    enemy_runs = 0
    round_ticks = 0

    def run_enemy() -> None:
        nonlocal enemy_runs
        enemy_runs += 1

    def tick() -> None:
        nonlocal round_ticks
        round_ticks += 1

    # First PC rotation: no enemy phase yet.
    wrapped = controller.end_turn_with_enemy_phase(run_enemy, tick)
    assert wrapped is False
    assert enemy_runs == 0
    assert round_ticks == 0

    # Last PC ends turn: enemy phase fires once, round wraps.
    wrapped = controller.end_turn_with_enemy_phase(run_enemy, tick)
    assert wrapped is True
    assert enemy_runs == 1
    assert round_ticks == 1
    assert controller.active_index == 0
    assert controller.round_number == 1


def test_voluntary_turn_round_does_not_run_enemy_phase() -> None:
    controller, _ = _make_controller(party_size=2, hostiles=False)
    controller.enter_turn_based()
    assert controller.play_mode is PlayMode.voluntary_turn
    enemy_runs = 0
    round_ticks = 0

    def run_enemy() -> None:
        nonlocal enemy_runs
        enemy_runs += 1

    def tick() -> None:
        nonlocal round_ticks
        round_ticks += 1

    controller.end_turn_with_enemy_phase(run_enemy, tick)  # to second pc
    controller.end_turn_with_enemy_phase(run_enemy, tick)  # wrap

    assert enemy_runs == 0
    assert round_ticks == 1


def test_end_turn_skips_downed_party_members() -> None:
    controller, fixture = _make_controller(party_size=3, hostiles=True)
    controller.sync_play_mode()
    middle = fixture.party[1]
    fixture.dead.add(middle)

    controller.end_turn_with_enemy_phase(lambda: None, lambda: None)

    assert controller.current_actor() == fixture.party[2]


# ---------------------------------------------------------------------------
# Mode sync resets activation when transitioning
# ---------------------------------------------------------------------------


def test_sync_play_mode_resets_activation_on_transition() -> None:
    controller, fixture = _make_controller(party_size=1, hostiles=True)
    controller.sync_play_mode()
    controller.consume_action()
    controller.consume_movement(6)

    fixture.hostiles = False
    controller.sync_play_mode()

    # Transition out of turn-based clears the carryover budget.
    assert controller.play_mode is PlayMode.explore
    assert controller.active_activation.action_used is False
    assert controller.active_activation.movement_used == 0


# ---------------------------------------------------------------------------
# Serialization shape
# ---------------------------------------------------------------------------


def test_to_dict_captures_round_active_index_and_per_actor_state() -> None:
    controller, fixture = _make_controller(party_size=2, hostiles=True)
    controller.sync_play_mode()
    controller.consume_action()
    controller.consume_movement(3)
    controller.end_turn_with_enemy_phase(lambda: None, lambda: None)

    snapshot = controller.to_dict()

    assert snapshot["active_index"] == 1
    assert snapshot["play_mode"] == PlayMode.turn_based.value
    assert snapshot["round_number"] == 0
    activations = snapshot["activations"]
    # First actor's spent resources survive in the snapshot.
    first_id = str(fixture.party[0])
    assert activations[first_id]["action_used"] is True
    assert activations[first_id]["movement_used"] == 3


def test_reset_clears_per_actor_state() -> None:
    controller, _ = _make_controller(party_size=2, hostiles=True)
    controller.sync_play_mode()
    controller.consume_action()
    controller.consume_movement(6)
    controller.enter_turn_based()
    controller.round_number = 3

    controller.reset()

    assert controller.active_index == 0
    assert controller.voluntary_turn_based is False
    assert controller.play_mode is PlayMode.explore
    assert controller.round_number == 0
    # No carry-over activation budgets remain.
    state = controller.active_activation
    assert state.action_used is False
    assert state.movement_used == 0
