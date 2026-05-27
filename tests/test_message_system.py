from src.core.config import PLAYFIELD_WIDTH
from src.systems.message_system import MESSAGE_PAGE_WIDTH, MORE_PROMPT, MessageState


def test_long_message_is_paginated() -> None:
    messages = MessageState()

    messages.emit("x" * (PLAYFIELD_WIDTH + 20))

    assert messages.awaiting_more is True
    assert len(messages.current) <= MESSAGE_PAGE_WIDTH
    first = messages.current
    messages.advance()
    assert messages.current != first


def test_more_prompt_is_short() -> None:
    assert MORE_PROMPT == "--more--"
    assert MESSAGE_PAGE_WIDTH < PLAYFIELD_WIDTH
