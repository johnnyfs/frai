from src.systems.message_system import MessageState


def test_long_message_is_paginated() -> None:
    messages = MessageState()

    messages.emit("x" * 100)

    assert messages.awaiting_more is True
    first = messages.current
    messages.advance()
    assert messages.current != first
