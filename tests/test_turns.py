from src.core.turns import ActivationState, major_mode_for_hostiles, movement_cost


def test_activation_state_tracks_action_once() -> None:
    activation = ActivationState()

    assert activation.spend_action() is True
    assert activation.action_used is True
    assert activation.spend_action() is False


def test_activation_state_tracks_movement_budget() -> None:
    activation = ActivationState(movement_total=6)

    assert activation.spend_movement(3) is True
    assert activation.movement_remaining() == 3
    assert activation.spend_movement(4) is False
    assert activation.movement_used == 3


def test_movement_costs_are_explicit_turn_rules() -> None:
    assert movement_cost(1, 0) == 3.0
    assert movement_cost(0, -1) == 3.0
    assert movement_cost(1, 1) == 4.25


def test_major_mode_is_derived_from_hostile_presence() -> None:
    assert major_mode_for_hostiles(True) == "battle"
    assert major_mode_for_hostiles(False) == "explore"
