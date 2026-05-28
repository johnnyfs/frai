from src.core.turns import ActivationState, movement_cost


def test_activation_state_tracks_action_once() -> None:
    activation = ActivationState()

    assert activation.spend_action() is True
    assert activation.action_used is True
    assert activation.spend_action() is False


def test_activation_state_tracks_bonus_action_once() -> None:
    activation = ActivationState()

    assert activation.spend_bonus_action() is True
    assert activation.bonus_action_used is True
    assert activation.spend_bonus_action() is False


def test_activation_state_tracks_reaction_once() -> None:
    activation = ActivationState()

    assert activation.spend_reaction() is True
    assert activation.reaction_used is True
    assert activation.spend_reaction() is False


def test_activation_state_tracks_extra_actions() -> None:
    activation = ActivationState(extra_actions_total=1)

    assert activation.extra_actions_remaining() == 1
    assert activation.spend_extra_action() is True
    assert activation.extra_actions_remaining() == 0
    assert activation.spend_extra_action() is False


def test_activation_reset_clears_turn_resources_and_preserves_grants() -> None:
    activation = ActivationState(extra_actions_total=1)
    activation.spend_movement(3)
    activation.spend_action()
    activation.spend_bonus_action()
    activation.spend_reaction()
    activation.spend_extra_action()

    activation.reset_for_turn()

    assert activation.movement_used == 0
    assert activation.action_used is False
    assert activation.bonus_action_used is False
    assert activation.reaction_used is False
    assert activation.extra_actions_used == 0
    assert activation.extra_actions_total == 1


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
