from dataclasses import dataclass
from typing import Literal


MOVEMENT_TOTAL_FEET = 30.0
ORTHOGONAL_MOVE_FEET = 3.0
DIAGONAL_MOVE_FEET = 4.25

MajorMode = Literal["explore", "battle"]


@dataclass(slots=True)
class ActivationState:
    movement_used: float = 0.0
    movement_total: float = MOVEMENT_TOTAL_FEET
    action_used: bool = False

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


def movement_cost(dx: int, dy: int) -> float:
    if dx != 0 and dy != 0:
        return DIAGONAL_MOVE_FEET
    return ORTHOGONAL_MOVE_FEET


def major_mode_for_hostiles(hostiles_present: bool) -> MajorMode:
    return "battle" if hostiles_present else "explore"
