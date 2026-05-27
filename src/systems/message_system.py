from dataclasses import dataclass


@dataclass(slots=True)
class MessageState:
    current: str = ""

    def emit(self, text: str) -> None:
        self.current = text
