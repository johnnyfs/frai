from dataclasses import dataclass, field
import textwrap

from src.core.config import PLAYFIELD_WIDTH


@dataclass(slots=True)
class MessageState:
    current: str = ""
    pending: list[str] = field(default_factory=list)

    def emit(self, text: str) -> None:
        if not text:
            self.current = ""
            self.pending.clear()
            return

        chunks = textwrap.wrap(text, width=PLAYFIELD_WIDTH, break_long_words=True)
        if not chunks:
            chunks = [""]
        self.current = chunks[0]
        self.pending = chunks[1:]

    @property
    def awaiting_more(self) -> bool:
        return bool(self.pending)

    def advance(self) -> None:
        if self.pending:
            self.current = self.pending.pop(0)
