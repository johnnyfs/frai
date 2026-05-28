"""Auto-walk / repeated-step movement primitives.

This module owns the data types and the pure step-predicate that govern
NetHack-style "walk in a direction until something interesting happens"
movement. It does *not* drive movement itself — the App loop applies one
``MoveAttempt`` per step and then asks :func:`step_autowalk` whether to
continue.

Separating the predicate from the loop has two payoffs:

- It makes auto-walk testable without curses. The predicate is a pure
  function over world / party / awareness state.
- M36 command-scripting (`<N><dir>` repeated moves) reuses the same
  predicate. A repeated move is conceptually an autowalk with
  ``max_steps=N``; the interrupt reasons are identical.

Interrupt model
---------------

An autowalk continues while every guard predicate passes. A guard returns
an :class:`InterruptReason` to stop the walk:

- ``out_of_steps``: the step count reached the configured ``max_steps``.
- ``modal_opened``: ``ui_mode`` is no longer :class:`UIMode.play` (a
  modal, message pager, or game-over screen has stolen focus).
- ``combat_started``: hostile presence flipped on (the play mode is now
  forced turn-based). Distinct from ``new_hostile_visible`` because some
  hostiles may be remembered but not currently in line of sight.
- ``new_hostile_visible``: a hostile entity is in the party's current
  visible set (``app.memory.visible``).
- ``blocked``: the most recent move attempt did not change the active
  actor's position — a wall, door, or other obstacle stopped us. The
  predicate inspects ``app.messages.current`` for the "Blocked." token
  the movement system emits, plus the actor's position to confirm.
- ``event_message``: the message log shows a non-trivial event message
  the player should acknowledge (anything other than an empty buffer or
  the "Blocked." token). Pending pages also count.
- ``low_hp``: placeholder — never returned today. The seam exists so
  M24 (conditions/statuses) can wire HP/condition gates without
  refactoring the predicate signature.

Save/load
---------

Autowalk is transient runtime state. The :class:`AutowalkRequest` is
held on the App while a walk is in progress and dropped on save. See
M22 / the autowalk help doc for the player-facing contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

from src.core.entity import EntityId
from src.core.modes import UIMode
from src.core.world import World
from src.systems.awareness_system import hostiles_requiring_battle

if TYPE_CHECKING:
    from src.core.vision import PartyMemory
    from src.systems.message_system import MessageState


DEFAULT_MAX_STEPS = 100


class InterruptReason(str, Enum):
    """Why an autowalk stopped. Strings double as observation tokens."""

    OUT_OF_STEPS = "out_of_steps"
    MODAL_OPENED = "modal_opened"
    COMBAT_STARTED = "combat_started"
    NEW_HOSTILE_VISIBLE = "new_hostile_visible"
    BLOCKED = "blocked"
    EVENT_MESSAGE = "event_message"
    LOW_HP = "low_hp"  # placeholder, never returned today


@dataclass(frozen=True, slots=True)
class AutowalkRequest:
    """A live auto-walk: direction + remaining-step budget.

    ``direction`` is one of the eight Rogue-style step vectors. The
    predicate does not validate the magnitude; callers should pass
    ``(-1, 0)`` / ``(0, 1)`` etc. ``max_steps`` is the *absolute* upper
    bound on how many steps the walk is allowed to take. The App owns
    the running counter; this dataclass is intentionally immutable so a
    request snapshot can be saved with a fixture without aliasing.
    """

    direction: tuple[int, int]
    max_steps: int = DEFAULT_MAX_STEPS


class _AutowalkHost(Protocol):
    """Subset of ``App`` the predicate consults.

    Documented as a Protocol so M36 cmdscripts can build an ad-hoc host
    (or a thin App proxy) without depending on the full App class. The
    only state the predicate reads is hostile presence, vision, the UI
    mode, and the message log.
    """

    world: World
    ui_mode: UIMode
    memory: "PartyMemory"
    messages: "MessageState"
    party: object  # PartyState; loose-typed to avoid a circular import.


# Message tokens the movement system emits when a step fails. The
# autowalk predicate does not parse messages in general — it asks
# whether the actor moved. These tokens are only used as a tiebreaker
# when the actor did not move and we need to distinguish "wall in the
# way" from "an event spoke to the player".
_BLOCKED_TOKENS = frozenset({"Blocked.", "No movement remaining."})
_MOVE_NULL_TOKENS = frozenset({"", *(_BLOCKED_TOKENS)})


def step_autowalk(
    host: _AutowalkHost,
    request: AutowalkRequest,
    current_step: int,
    *,
    actor: EntityId,
    actor_moved: bool,
) -> tuple[bool, InterruptReason | None]:
    """Decide whether an in-progress autowalk should continue.

    Call this *after* a step's ``MoveAttempt`` has been dispatched.
    Returns ``(continue, reason)`` where ``continue`` is ``True`` when
    the walk should take another step. When ``continue`` is ``False``
    ``reason`` names which guard fired; when ``True``, ``reason`` is
    ``None``.

    Parameters
    ----------
    host:
        The autowalk host (App-shaped object) — see :class:`_AutowalkHost`.
    request:
        The active :class:`AutowalkRequest`.
    current_step:
        The step counter *after* the latest move attempt. Step 1 is the
        first attempted step.
    actor:
        The actor that the walk is moving (usually the active party
        member). Used to look up vision-based hostiles and to confirm
        the actor's position changed.
    actor_moved:
        ``True`` when the latest dispatch actually moved the actor.
        Required because the predicate cannot reliably re-derive this
        from world state alone — the actor could already have been on
        the target tile via a prior teleport or displacement.

    Notes
    -----
    The order of checks matters for the message a UI would surface:
    ``out_of_steps`` is checked first so that a walk that reached its
    budget reports that rather than the message log it happened to
    leave. ``modal_opened`` and ``combat_started`` are checked next
    because they take any caller out of the play loop. ``blocked``
    fires before ``event_message`` so a wall stop is reported as a
    structural interruption, not "the game spoke to you".
    """

    if current_step >= request.max_steps:
        return False, InterruptReason.OUT_OF_STEPS

    if host.ui_mode is not UIMode.play:
        return False, InterruptReason.MODAL_OPENED

    party_members = _party_members(host)
    hostiles = hostiles_requiring_battle(host.world, party_members)
    if hostiles:
        return False, InterruptReason.COMBAT_STARTED

    visible_hostile = _first_visible_hostile(host, actor, party_members)
    if visible_hostile is not None:
        return False, InterruptReason.NEW_HOSTILE_VISIBLE

    if not actor_moved:
        return False, InterruptReason.BLOCKED

    message = host.messages.current
    if message and message not in _MOVE_NULL_TOKENS:
        return False, InterruptReason.EVENT_MESSAGE
    if host.messages.awaiting_more:
        return False, InterruptReason.EVENT_MESSAGE

    return True, None


def interrupt_message(reason: InterruptReason) -> str:
    """Human-readable single-line message for a stop reason.

    The UI emits this into the message log so the player knows why the
    walk halted. Keep these short and consistent with the rest of the
    movement-system message vocabulary.
    """

    return _INTERRUPT_MESSAGES[reason]


_INTERRUPT_MESSAGES: dict[InterruptReason, str] = {
    InterruptReason.OUT_OF_STEPS: "Autowalk: step limit reached.",
    InterruptReason.MODAL_OPENED: "Autowalk: interrupted.",
    InterruptReason.COMBAT_STARTED: "Autowalk: hostile encountered.",
    InterruptReason.NEW_HOSTILE_VISIBLE: "Autowalk: hostile sighted.",
    InterruptReason.BLOCKED: "Autowalk: blocked.",
    InterruptReason.EVENT_MESSAGE: "Autowalk: interrupted.",
    InterruptReason.LOW_HP: "Autowalk: low HP.",
}


def _party_members(host: _AutowalkHost) -> list[EntityId]:
    """Return the host's party member list.

    The Protocol shape pins ``host.party`` to ``object`` to avoid a
    circular import; in practice it is always a ``PartyState`` and we
    pull ``.members``. A fallback returns an empty list so a malformed
    host yields the conservative "no party, no hostiles" answer
    instead of crashing.
    """

    party = getattr(host, "party", None)
    members = getattr(party, "members", None)
    if isinstance(members, list):
        return members
    return []


def _first_visible_hostile(
    host: _AutowalkHost,
    actor: EntityId,
    party_members: list[EntityId],
) -> EntityId | None:
    """Return a hostile entity currently in the party's visible set.

    Uses the party's vision frontier (``host.memory.visible``) so the
    autowalk only stops for hostiles the party can actually see —
    matching the M19 vision model. Hostiles that are merely "alive
    somewhere" do not interrupt; only ones in line of sight do.
    """

    from src.systems.awareness_system import is_hostile_to

    visible = getattr(getattr(host, "memory", None), "visible", frozenset())
    if not visible:
        return None
    world = host.world
    for entity, stats in world.combat_stats.values.items():
        if stats.hit_points <= 0:
            continue
        if entity == actor or entity in party_members:
            continue
        position = world.positions.get(entity)
        if position is None:
            continue
        if (position.x, position.y) not in visible:
            continue
        # Autowalk interrupts on hostility from either direction so an
        # aggro'd shopkeeper (HOSTILE→party via override) still stops
        # the party even though town remains neutral globally.
        if any(is_hostile_to(world, member, entity) for member in party_members):
            return entity
        if any(is_hostile_to(world, entity, member) for member in party_members):
            return entity
    return None
