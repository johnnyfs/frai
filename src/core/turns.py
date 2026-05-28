from dataclasses import dataclass


MOVEMENT_TOTAL_FEET = 30.0
ORTHOGONAL_MOVE_FEET = 3.0
DIAGONAL_MOVE_FEET = 4.25


@dataclass(slots=True)
class ActivationState:
    movement_used: float = 0.0
    movement_total: float = MOVEMENT_TOTAL_FEET
    action_used: bool = False
    bonus_action_used: bool = False
    reaction_used: bool = False
    extra_actions_used: int = 0
    extra_actions_total: int = 0

    def movement_remaining(self) -> float:
        return self.movement_total - self.movement_used

    def can_spend_movement(self, cost: float) -> bool:
        return self.movement_used + cost <= self.movement_total

    def spend_movement(self, cost: float) -> bool:
        if not self.can_spend_movement(cost):
            return False
        self.movement_used += cost
        return True

    def spend_action(self) -> bool:
        if self.action_used:
            return False
        self.action_used = True
        return True

    def spend_bonus_action(self) -> bool:
        if self.bonus_action_used:
            return False
        self.bonus_action_used = True
        return True

    def spend_reaction(self) -> bool:
        if self.reaction_used:
            return False
        self.reaction_used = True
        return True

    def extra_actions_remaining(self) -> int:
        return self.extra_actions_total - self.extra_actions_used

    def spend_extra_action(self) -> bool:
        if self.extra_actions_remaining() <= 0:
            return False
        self.extra_actions_used += 1
        return True

    def reset_for_turn(self) -> None:
        self.movement_used = 0.0
        self.action_used = False
        self.bonus_action_used = False
        self.reaction_used = False
        self.extra_actions_used = 0

    def reset_for_activation(self) -> None:
        self.reset_for_turn()


def movement_cost(dx: int, dy: int) -> float:
    if dx != 0 and dy != 0:
        return DIAGONAL_MOVE_FEET
    return ORTHOGONAL_MOVE_FEET
