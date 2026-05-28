"""Command script parser for the agentic playtest harness (M36).

This module turns a compact text "script" into a typed list of
``Command`` records that the runner (``src.ui.script_runner``) then
executes against an :class:`~src.app.App`. The grammar is deliberately
minimal — see ``docs/help/agent.md`` for the player/agent-facing
reference.

Grammar (informal)
------------------

A script is a sequence of commands separated by semicolons or newlines.
Lines beginning with ``#`` (after optional leading whitespace) are
comments and ignored. Trailing/leading whitespace around individual
commands is stripped.

Each command is one of:

- A single character key: ``h j k l y u b n e , i r x ? .`` —
  movement (eight Rogue-style directions), interact, pickup, inventory,
  rest, examine, help, wait.
- ``Enter`` / ``Esc`` — confirm / cancel a modal. Spelled as literal
  words rather than control characters so a script is JSON- and
  shell-safe (issue #29 requires "no special characters that break in
  CLI/config").
- ``<N><dir>`` — repeat-movement. ``N`` is a positive integer (decimal
  digits) immediately followed by one of the eight direction letters.
  The count *only* applies to movement keys; ``5e`` and ``3i`` are
  parse errors so that a typo cannot silently send three inventory
  toggles in a row.

The parser raises :class:`CommandScriptError` on any malformed token so
callers see a single, locatable error rather than a half-applied
script. The error message includes the offending token and (1-indexed)
position in the original script.

Why a typed AST?
----------------

The natural alternative is "translate the script directly to a list of
key codes" and reuse ``App.handle_key``. We chose typed ``Command``
records instead so:

- The runner knows that ``MoveCommand(repeat=5)`` should consult the
  M22 ``step_autowalk`` predicate after every step. With raw keys, the
  five presses would each be independent and the repeat-as-autowalk
  contract would be impossible to honour from outside the App.
- Future M37 harness layers can introspect the parsed script (e.g. to
  log "agent ran 5h then i") without re-parsing.
- A future macro/conditional layer (issue mentions "later") can replace
  the parser without touching the runner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias


# Eight Rogue-style direction letters and the (dx, dy) vector each
# resolves to. Kept frozen so a caller cannot accidentally rebind a
# direction by mutating the dict (the parser exposes the same vectors
# the App uses via ``MoveCommand.vector``).
_DIRECTION_VECTORS: dict[str, tuple[int, int]] = {
    "h": (-1, 0),
    "j": (0, 1),
    "k": (0, -1),
    "l": (1, 0),
    "y": (-1, -1),
    "u": (1, -1),
    "b": (-1, 1),
    "n": (1, 1),
}


# Single-character non-movement keys recognised by the script. The
# values are the names of the dataclasses they parse to — keeping the
# table here means new commands need exactly one parser edit.
_SINGLE_KEY_COMMANDS = frozenset({"e", ",", "i", "r", "x", "?", ".", "s", "z", "p"})


# Multi-character "word" tokens that map to specific commands. Spelled
# in literal ASCII so the script survives JSON/CLI round trips.
_WORD_TOKENS = frozenset({"Enter", "Esc"})


class CommandScriptError(ValueError):
    """Raised when a script cannot be parsed.

    The message is human-readable and includes the offending token and
    its position in the source script (1-indexed). Callers (the M37
    harness, CLI ``--script`` flag) should surface it directly.
    """


@dataclass(frozen=True, slots=True)
class MoveCommand:
    """A single-step or repeated movement in one of the eight directions.

    ``repeat`` is the upper bound on how many steps the runner will
    attempt; the M22 autowalk predicate may end the walk earlier (wall,
    hostile, modal, message). ``repeat=1`` is the explicit single-step
    form (e.g. ``parse("h") == [MoveCommand(-1, 0, 1)]``).
    """

    dx: int
    dy: int
    repeat: int = 1

    @property
    def vector(self) -> tuple[int, int]:
        return (self.dx, self.dy)


@dataclass(frozen=True, slots=True)
class InteractCommand:
    """`e` — interact with the tile the active actor is facing."""


@dataclass(frozen=True, slots=True)
class PickupCommand:
    """`,` — pick up items on the active actor's tile."""


@dataclass(frozen=True, slots=True)
class InventoryCommand:
    """`i` — open/close the inventory modal."""


@dataclass(frozen=True, slots=True)
class RestCommand:
    """`r` — open the rest menu (M34).

    The App resolves the key into a :class:`RestMenuRequest`; the
    agent then sends ``s`` or ``l`` (or ``Esc`` to cancel) to pick the
    rest kind. Resting outside a shelter zone emits a refusal banner
    so a script can detect "no shelter here" without inspecting the
    world.
    """


@dataclass(frozen=True, slots=True)
class ExamineCommand:
    """`x` — open the M21 examine cursor.

    The runner forwards the key through ``App.handle_key`` which
    routes through the input system → :class:`ExamineRequest` →
    ``App.begin_examine``. From the script's point of view this is
    identical to any other single-key command; the modal that pops
    up is driven by the same cursor + ``Enter`` / ``Esc`` keys the
    M20 targeting modal accepts.
    """


@dataclass(frozen=True, slots=True)
class HelpCommand:
    """`?` — open help. No-op until M39 help modal lands."""


@dataclass(frozen=True, slots=True)
class WaitCommand:
    """`.` — wait one tick.

    Routed through the same key dispatch as the player ``space`` /
    end-turn key for now; M44 / future "wait" content can split them.
    """


@dataclass(frozen=True, slots=True)
class SpellMenuCommand:
    """`s` — open the spell menu for the active actor (M11)."""


@dataclass(frozen=True, slots=True)
class SneakCommand:
    """`z` — attempt a stealth roll for the active actor (M23)."""


@dataclass(frozen=True, slots=True)
class PerceiveCommand:
    """`p` — attempt a perception roll for the active actor (M23)."""


@dataclass(frozen=True, slots=True)
class ConfirmCommand:
    """``Enter`` — confirm the current modal."""


@dataclass(frozen=True, slots=True)
class CancelCommand:
    """``Esc`` — cancel/close the current modal."""


Command: TypeAlias = (
    MoveCommand
    | InteractCommand
    | PickupCommand
    | InventoryCommand
    | RestCommand
    | ExamineCommand
    | HelpCommand
    | WaitCommand
    | SpellMenuCommand
    | SneakCommand
    | PerceiveCommand
    | ConfirmCommand
    | CancelCommand
)


def parse(script: str) -> list[Command]:
    """Parse ``script`` into a flat list of :class:`Command` records.

    The grammar tolerates semicolons and newlines as separators, blank
    lines, comment lines beginning with ``#``, and arbitrary whitespace
    around tokens. The parser is strict about token *shape* — an
    unrecognised character or a count prefix on a non-movement key
    raises :class:`CommandScriptError`. We prefer "explicit error" over
    "silently skip" so the harness never silently drops an agent's
    command.

    Parameters
    ----------
    script:
        The raw script text. Empty / whitespace-only scripts parse to
        the empty list.

    Returns
    -------
    list[Command]
        The parsed commands in the same order they appear in the
        source. Tokens of the form ``<N><dir>`` produce a single
        :class:`MoveCommand` with ``repeat=N``.
    """

    commands: list[Command] = []
    if not script:
        return commands

    position = 0
    for raw_line in script.splitlines():
        line = raw_line.strip()
        # Skip blank lines and comment lines outright. The comment
        # marker has to be the *first* non-whitespace character on the
        # line so an inline `;` separator before a `#` still parses the
        # earlier commands.
        if not line or line.startswith("#"):
            continue
        for token in line.split(";"):
            position += 1
            token = token.strip()
            if not token:
                continue
            commands.append(_parse_token(token, position))
    return commands


def _parse_token(token: str, position: int) -> Command:
    """Resolve a single, whitespace-stripped token into a Command.

    Raises :class:`CommandScriptError` on any malformed token. ``position``
    is a 1-indexed token counter included in the error message — the
    parser does not surface byte offsets because tokens already give a
    locatable handle for the harness's error reporting.
    """

    # Word tokens (Enter, Esc) come first so they aren't mistaken for a
    # sequence of single-character commands.
    if token in _WORD_TOKENS:
        return _WORD_COMMANDS[token]

    # ``<N><dir>`` form: leading decimal digits followed by exactly one
    # direction letter. We split on the first non-digit to keep the
    # parser predicate-free; explicit indexing also makes the error
    # messages line up with what a player would write.
    digit_run = _leading_digit_run(token)
    if digit_run:
        repeat_text = token[:digit_run]
        rest = token[digit_run:]
        try:
            repeat = int(repeat_text)
        except ValueError as exc:  # pragma: no cover — guarded by digit_run
            raise CommandScriptError(
                f"Invalid repeat count '{repeat_text}' at token {position}."
            ) from exc
        if repeat <= 0:
            raise CommandScriptError(
                f"Repeat count must be positive (token {position}: '{token}')."
            )
        if rest not in _DIRECTION_VECTORS:
            raise CommandScriptError(
                f"Count prefix only valid before a direction key "
                f"(token {position}: '{token}')."
            )
        dx, dy = _DIRECTION_VECTORS[rest]
        return MoveCommand(dx=dx, dy=dy, repeat=repeat)

    if token in _DIRECTION_VECTORS:
        dx, dy = _DIRECTION_VECTORS[token]
        return MoveCommand(dx=dx, dy=dy, repeat=1)

    if token in _SINGLE_KEY_COMMANDS:
        return _SINGLE_KEY_FACTORIES[token]()

    raise CommandScriptError(
        f"Unrecognised command token '{token}' at position {position}."
    )


def _leading_digit_run(token: str) -> int:
    """Return the index of the first non-digit character in ``token``.

    Used by the parser to split ``"10j"`` into ``"10"`` and ``"j"``.
    Returns 0 when the token has no leading digits, which lets the
    caller fall through to the single-key paths cleanly.
    """

    for index, char in enumerate(token):
        if not char.isdigit():
            return index
    # All digits: caller treats this as "no direction supplied" via the
    # later ``rest not in _DIRECTION_VECTORS`` check.
    return len(token)


# Single-key tokens map to zero-arg factories rather than instances so
# every parsed command is a fresh dataclass — important when callers
# attach metadata to a parsed command (the dataclasses are frozen so
# this is conservative, but reusing instances would be a footgun if a
# command grows a mutable field).
_SINGLE_KEY_FACTORIES: dict[str, type[Command]] = {
    "e": InteractCommand,
    ",": PickupCommand,
    "i": InventoryCommand,
    "r": RestCommand,
    "x": ExamineCommand,
    "?": HelpCommand,
    ".": WaitCommand,
    "s": SpellMenuCommand,
    "z": SneakCommand,
    "p": PerceiveCommand,
}


# Word tokens always resolve to the same kind of command, so we can
# share frozen instances. They have no fields today.
_WORD_COMMANDS: dict[str, Command] = {
    "Enter": ConfirmCommand(),
    "Esc": CancelCommand(),
}
