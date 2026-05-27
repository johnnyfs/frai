from dataclasses import dataclass, field
import textwrap

from src.core.config import PLAYFIELD_WIDTH

MORE_PROMPT = "--more--"
MORE_SUFFIX = f" {MORE_PROMPT}"
MESSAGE_PAGE_WIDTH = PLAYFIELD_WIDTH - len(MORE_SUFFIX)


@dataclass(slots=True)
class MessageState:
    current: str = ""
    pending: list[str] = field(default_factory=list)

    def emit(self, text: str) -> None:
        if not text:
            self.current = ""
            self.pending.clear()
            return

        wrap_width = MESSAGE_PAGE_WIDTH if len(text) > PLAYFIELD_WIDTH else PLAYFIELD_WIDTH
        chunks = textwrap.wrap(text, width=wrap_width, break_long_words=True)
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
