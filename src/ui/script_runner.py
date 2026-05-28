"""Run a parsed command script against an :class:`~src.app.App` (M36).

The runner is the bridge between the typed ``Command`` records produced
by :mod:`src.core.command_script` and the App's input pipeline. It is
the second piece of the agentic playtest stack — paired with the M35
``observe()`` snapshot, the runner gives M37's harness a way to send a
compact text command sequence and read back what happened.

Two paths through ``handle_key``
--------------------------------

Most commands are forwarded to ``app.handle_key`` exactly as if a
player had pressed the corresponding key — the existing key-to-action
mapping in :mod:`src.systems.input_system` is already the single source
of truth for "what does ``i`` do here?", and we do not want the script
runner to diverge from it.

Repeat-movement (``MoveCommand(repeat=N)`` with ``N>1``) is the one
exception: instead of pressing the direction key ``N`` times, we
configure an :class:`AutowalkRequest` on the App and let
``App._run_autowalk`` drive the loop. That re-uses the M22
``step_autowalk`` predicate for free, so the interrupt vocabulary
(wall, hostile, modal, message) is identical to player auto-walk. The
only difference is the upper-bound on ``max_steps`` — the script sets
it to the parsed repeat count rather than the default 100.

Outcome shape
-------------

Each command produces a :class:`CommandOutcome` with:

- ``command`` — the original parsed Command instance (so a harness can
  match outcomes back to script positions without re-parsing).
- ``steps_taken`` — for movement, the number of tiles actually moved.
  Always 1 for non-movement commands.
- ``observation_after`` — a :class:`~src.ui.observation.Observation`
  taken *after* the command settles. M37 diffs two consecutive
  observations to detect "what changed".
- ``interrupt_reason`` — the :class:`InterruptReason` that ended a
  repeat move, or ``None`` for any other outcome. Single-step moves
  never set this field; they either move or they don't, and that's
  visible in ``steps_taken``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.core.autowalk import AutowalkRequest, InterruptReason
from src.core.command_script import (
    CancelCommand,
    Command,
    CommandScriptError,
    ConfirmCommand,
    ExamineCommand,
    HelpCommand,
    InteractCommand,
    InventoryCommand,
    MoveCommand,
    PickupCommand,
    RestCommand,
    SpellMenuCommand,
    WaitCommand,
    parse as parse_script,
)
from src.core.modes import UIMode
from src.ui.observation import Observation, observe


# Curses key codes used by the runner. ``handle_key`` reads ASCII
# values, so we encode each command as the canonical key a player would
# press. The autowalk path bypasses key codes entirely — it sets
# ``app.autowalk`` and calls the internal runner.
_KEY_INTERACT = ord("e")
_KEY_PICKUP = ord(",")
_KEY_INVENTORY = ord("i")
_KEY_REST = ord("r")
_KEY_EXAMINE = ord("x")
_KEY_HELP = ord("?")
_KEY_WAIT = ord(".")
_KEY_SPELL_MENU = ord("s")  # M11 spell menu opener.
_KEY_SPACE = ord(" ")  # ``EndTurn`` / message advance.
_KEY_ENTER = 10  # ASCII LF — ``handle_key`` treats this as a no-op today.
_KEY_ESC = 27  # ASCII ESC — same, reserved for M39 / M41.


# Mapping from each direction vector to the lowercase movement key
# ``input_system.MOVE_KEYS`` resolves. Used for the single-step move
# path (``repeat=1``).
_VECTOR_TO_KEY: dict[tuple[int, int], int] = {
    (-1, 0): ord("h"),
    (0, 1): ord("j"),
    (0, -1): ord("k"),
    (1, 0): ord("l"),
    (-1, -1): ord("y"),
    (1, -1): ord("u"),
    (-1, 1): ord("b"),
    (1, 1): ord("n"),
}


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """What happened after a single command in a script.

    Frozen so a caller (harness, test) can stash outcomes in a list and
    diff them after the full script runs. ``observation_after`` is the
    canonical M35 snapshot at the moment the command settled.
    """

    command: Command
    steps_taken: int
    observation_after: Observation
    interrupt_reason: InterruptReason | None = None

    # Optional message that *only* this command emitted, surfaced as a
    # convenience so a harness doesn't need to diff observations to
    # find "what did the game say in response to this command". When
    # the command was a no-op the field is the empty string.
    last_message: str = ""

    # Default-factory so the field stays optional in JSON-style consumers.
    extras: dict[str, Any] = field(default_factory=dict)


def run_script(app: Any, script: str) -> list[CommandOutcome]:
    """Parse and run ``script`` against ``app``.

    Equivalent to::

        commands = parse(script)
        outcomes = [run_command(app, c) for c in commands]

    but in a single helper so harness callers don't need to import the
    parser. Parsing errors propagate as :class:`CommandScriptError`
    untouched — the harness should treat that as "agent sent garbage"
    and report it without mutating any further state.
    """

    commands = parse_script(script)
    outcomes: list[CommandOutcome] = []
    for command in commands:
        outcomes.append(run_command(app, command))
    return outcomes


def run_command(app: Any, command: Command) -> CommandOutcome:
    """Execute a single :class:`Command` and return its outcome.

    Dispatches on the command type. Movement commands with ``repeat>1``
    take the autowalk path; everything else routes through
    ``app.handle_key``. The function is intentionally chatty about
    state — the harness depends on a fresh ``observe(app)`` after every
    command so it can diff against the previous frame.
    """

    # If the previous command left a message pager / "more" prompt
    # open, the player would normally have to advance it before any
    # further input — we mimic the same behaviour so a script's second
    # command isn't silently swallowed by ``handle_key``.
    _advance_message_pager_if_open(app)

    if isinstance(command, MoveCommand):
        return _run_move(app, command)
    if isinstance(command, InteractCommand):
        return _run_simple_key(app, command, _KEY_INTERACT)
    if isinstance(command, PickupCommand):
        return _run_simple_key(app, command, _KEY_PICKUP)
    if isinstance(command, InventoryCommand):
        return _run_simple_key(app, command, _KEY_INVENTORY)
    if isinstance(command, RestCommand):
        return _run_simple_key(app, command, _KEY_REST)
    if isinstance(command, ExamineCommand):
        return _run_simple_key(app, command, _KEY_EXAMINE)
    if isinstance(command, HelpCommand):
        return _run_simple_key(app, command, _KEY_HELP)
    if isinstance(command, WaitCommand):
        # ``.`` is conventionally "wait one tick". The play screen has
        # no dedicated wait key today, so we route through ``space``
        # which is ``EndTurn`` — the closest semantic match. When M44
        # introduces a real wait action we'll split them.
        return _run_simple_key(app, command, _KEY_SPACE)
    if isinstance(command, SpellMenuCommand):
        return _run_simple_key(app, command, _KEY_SPELL_MENU)
    if isinstance(command, ConfirmCommand):
        return _run_simple_key(app, command, _KEY_ENTER)
    if isinstance(command, CancelCommand):
        return _run_simple_key(app, command, _KEY_ESC)
    # The dataclass union covers every branch above; if a future
    # command lands without a runner update, fail loud so the
    # missing-case bug shows up immediately.
    raise CommandScriptError(f"No runner for command {type(command).__name__}")


# -- internal helpers ----------------------------------------------------


def _run_simple_key(app: Any, command: Command, key: int) -> CommandOutcome:
    """Forward ``key`` to ``app.handle_key`` and snapshot afterwards.

    Used by every non-movement command. ``steps_taken`` is always 1
    here — the command either fired or it didn't, and that distinction
    is visible in ``observation_after`` (e.g. a UI mode change for
    inventory). We intentionally don't try to detect "key was
    ignored" — input_system already returns None silently for
    unrecognised keys in the current UI mode, and that's the same
    behaviour the harness would see from a real keyboard.
    """

    before_message = app.messages.current if app.messages else ""
    app.handle_key(key)
    after_message = app.messages.current if app.messages else ""
    last_message = after_message if after_message and after_message != before_message else ""
    return CommandOutcome(
        command=command,
        steps_taken=1,
        observation_after=observe(app),
        interrupt_reason=None,
        last_message=last_message,
    )


def _run_move(app: Any, command: MoveCommand) -> CommandOutcome:
    """Run a movement command (single-step or repeat)."""

    if command.repeat == 1:
        return _run_single_move(app, command)
    return _run_repeat_move(app, command)


def _run_single_move(app: Any, command: MoveCommand) -> CommandOutcome:
    """Send one direction key through ``handle_key``.

    We measure ``steps_taken`` by comparing the active actor's
    position before and after the dispatch — that's identical to the
    way M22's autowalk runner detects "the wall blocked us" and avoids
    any reliance on message text.
    """

    actor = _safe_actor(app)
    before = _actor_position(app, actor)
    key = _VECTOR_TO_KEY[command.vector]
    before_message = app.messages.current if app.messages else ""
    app.handle_key(key)
    after = _actor_position(app, actor)
    steps = 1 if (before is not None and after is not None and after != before) else 0
    after_message = app.messages.current if app.messages else ""
    last_message = after_message if after_message and after_message != before_message else ""
    return CommandOutcome(
        command=command,
        steps_taken=steps,
        observation_after=observe(app),
        interrupt_reason=None,
        last_message=last_message,
    )


def _run_repeat_move(app: Any, command: MoveCommand) -> CommandOutcome:
    """Run an N-step move via the M22 autowalk machinery.

    The script's ``<N><dir>`` form is conceptually identical to a
    capital-direction autowalk with ``max_steps=N``. Rather than
    reproduce the loop body here, we set ``app.autowalk`` and call the
    App's existing runner — that goes through the same
    ``step_autowalk`` predicate, the same explore/turn-based branching,
    and the same message emission code path the keyboard autowalk
    uses. Anything we'd add here would duplicate the autowalk
    machinery and risk drifting from the player-facing semantics.

    We snapshot the active actor's position before and after so the
    outcome reports an accurate ``steps_taken`` even when the autowalk
    runner clears its own request and never tells us how many steps it
    consumed.
    """

    # Only valid while ``play`` owns the screen — a modal would refuse
    # the dispatch and the predicate would immediately return
    # ``modal_opened``. Doing the check up front gives us a sensible
    # outcome without spinning the App through a no-op dispatch.
    if app.ui_mode is not UIMode.play:
        return CommandOutcome(
            command=command,
            steps_taken=0,
            observation_after=observe(app),
            interrupt_reason=InterruptReason.MODAL_OPENED,
            last_message=app.messages.current if app.messages else "",
        )

    actor = _safe_actor(app)
    start = _actor_position(app, actor)
    request = AutowalkRequest(direction=command.vector, max_steps=command.repeat)
    app.autowalk = request
    before_message = app.messages.current if app.messages else ""
    # ``_run_autowalk`` returns the interrupt reason that ended the
    # walk. We trust that value rather than re-deriving from message
    # text — the autowalk runner emits its own banner ("Autowalk:
    # blocked.") into the log, which would otherwise be mis-classified
    # as ``event_message`` if we tried to infer the reason after the
    # fact.
    reason = app._run_autowalk()
    end = _actor_position(app, actor)
    if start is not None and end is not None:
        # Steps walked, measured by Chebyshev distance from start.
        # Diagonal moves are still 1 step each, so a 5y walk that
        # actually moved 3 tiles north-west reports steps_taken=3.
        steps = max(abs(end[0] - start[0]), abs(end[1] - start[1]))
    else:
        steps = 0

    after_message = app.messages.current if app.messages else ""
    last_message = after_message if after_message and after_message != before_message else ""
    return CommandOutcome(
        command=command,
        steps_taken=steps,
        observation_after=observe(app),
        interrupt_reason=reason,
        last_message=last_message,
    )


def _advance_message_pager_if_open(app: Any) -> None:
    """Acknowledge a pending ``--MORE--`` message if one is open.

    The play loop expects the player to press *any* key to advance a
    message pager; ``handle_key`` enforces that by consuming the next
    key for the pager rather than dispatching it. A script's commands
    are meaningful only after the pager is cleared, so we advance it
    here. This is a small departure from "the script is a sequence of
    keystrokes" — but it matches the harness contract (one command =
    one logical action) and keeps the script terse.
    """

    state = getattr(app, "messages", None)
    if state is None:
        return
    while state.awaiting_more:
        state.advance()


def _safe_actor(app: Any) -> Any | None:
    """Return the active actor id, or None if the world has none."""

    try:
        return app.active_actor()
    except (IndexError, AttributeError):
        return None


def _actor_position(app: Any, actor: Any) -> tuple[int, int] | None:
    """Snapshot ``actor``'s current tile as an ``(x, y)`` pair."""

    if actor is None:
        return None
    position = app.world.positions.get(actor)
    if position is None:
        return None
    return (position.x, position.y)
