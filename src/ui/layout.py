from dataclasses import dataclass

from src.core.config import (
    MESSAGE_ROW,
    MIN_TERMINAL_HEIGHT,
    MIN_TERMINAL_WIDTH,
    PLAYFIELD_HEIGHT,
    PLAYFIELD_TOP,
    PLAYFIELD_WIDTH,
    STATUS_ROW,
)


@dataclass(frozen=True, slots=True)
class Layout:
    width: int
    height: int

    @property
    def is_too_small(self) -> bool:
        return self.width < MIN_TERMINAL_WIDTH or self.height < MIN_TERMINAL_HEIGHT

    @property
    def origin_x(self) -> int:
        if self.width <= PLAYFIELD_WIDTH:
            return 0
        return (self.width - PLAYFIELD_WIDTH) // 2

    @property
    def message_y(self) -> int:
        return MESSAGE_ROW

    @property
    def map_top(self) -> int:
        return PLAYFIELD_TOP

    @property
    def map_bottom(self) -> int:
        return PLAYFIELD_TOP + PLAYFIELD_HEIGHT - 1

    @property
    def status_y(self) -> int:
        return STATUS_ROW

    @property
    def playfield_width(self) -> int:
        return PLAYFIELD_WIDTH

    @property
    def playfield_height(self) -> int:
        return PLAYFIELD_HEIGHT
