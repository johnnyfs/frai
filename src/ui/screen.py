from dataclasses import dataclass
import curses


@dataclass(slots=True)
class Screen:
    stdscr: curses.window

    @property
    def width(self) -> int:
        return self.stdscr.getmaxyx()[1]

    @property
    def height(self) -> int:
        return self.stdscr.getmaxyx()[0]

    def clear(self) -> None:
        self.stdscr.erase()

    def draw_char(self, x: int, y: int, char: str) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            try:
                self.stdscr.addch(y, x, char)
            except curses.error:
                pass

    def print_line(self, y: int, text: str, x: int = 0) -> None:
        if not (0 <= y < self.height):
            return
        if x >= self.width:
            return
        max_length = self.width - x
        if y == self.height - 1:
            max_length = max(0, max_length - 1)
        try:
            self.stdscr.addstr(y, x, text[:max_length])
        except curses.error:
            pass

    def refresh(self) -> None:
        self.stdscr.refresh()
