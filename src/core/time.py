"""World time and schedule.

`WorldTime` is a single scalar elapsed time, measured in *seconds*. The
conversion constants below describe how higher-level units (rounds,
turns, minutes, hours, short rests, long rests) map onto that scalar.

The module exposes three concerns:

- `WorldTime`: serializable elapsed-time clock with conversion helpers.
- `Schedule`: a min-heap of `(due_at, event)` entries.
- `advance(...)`: advance the clock by a number of seconds and pop any
  schedule entries that have come due, applying them to a provided
  world via a caller-supplied applier.

The module deliberately knows nothing about `World`, `App`, modes, or
effects. Callers wire it into their own update loop. Scheduled events
are described by `ScheduledEvent`, an explicit, serializable dataclass
that callers can extend with subclasses. The applier callback decides
how to translate a `ScheduledEvent` into world mutations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from heapq import heappop, heappush
from typing import Callable, Iterable


# Conversion constants. One round of combat is six seconds (D&D 5e
# convention). A "turn" in explore mode is one minute, matching the
# classic dungeon-crawl turn used for torches and patrols. Rests use
# the SRD durations.
SECONDS_PER_ROUND: int = 6
SECONDS_PER_TURN: int = 60
SECONDS_PER_MINUTE: int = 60
SECONDS_PER_HOUR: int = 60 * 60
SECONDS_PER_SHORT_REST: int = 10 * SECONDS_PER_MINUTE
SECONDS_PER_LONG_REST: int = 8 * SECONDS_PER_HOUR


@dataclass(slots=True)
class WorldTime:
    """Elapsed world time, measured in whole seconds."""

    elapsed_seconds: int = 0

    # -- accessors ------------------------------------------------------

    @property
    def rounds(self) -> int:
        return self.elapsed_seconds // SECONDS_PER_ROUND

    @property
    def turns(self) -> int:
        return self.elapsed_seconds // SECONDS_PER_TURN

    @property
    def minutes(self) -> int:
        return self.elapsed_seconds // SECONDS_PER_MINUTE

    @property
    def hours(self) -> int:
        return self.elapsed_seconds // SECONDS_PER_HOUR

    # -- mutation -------------------------------------------------------

    def advance_seconds(self, seconds: int) -> None:
        if seconds < 0:
            raise ValueError("Cannot advance world time by a negative amount.")
        self.elapsed_seconds += seconds

    def advance_rounds(self, rounds: int = 1) -> None:
        self.advance_seconds(rounds * SECONDS_PER_ROUND)

    def advance_turns(self, turns: int = 1) -> None:
        self.advance_seconds(turns * SECONDS_PER_TURN)

    def advance_minutes(self, minutes: int) -> None:
        self.advance_seconds(minutes * SECONDS_PER_MINUTE)

    def advance_hours(self, hours: int) -> None:
        self.advance_seconds(hours * SECONDS_PER_HOUR)

    def advance_short_rest(self) -> None:
        self.advance_seconds(SECONDS_PER_SHORT_REST)

    def advance_long_rest(self) -> None:
        self.advance_seconds(SECONDS_PER_LONG_REST)

    # -- serialization --------------------------------------------------

    def to_dict(self) -> dict[str, int]:
        return {"elapsed_seconds": self.elapsed_seconds}

    @classmethod
    def from_dict(cls, payload: dict[str, int]) -> "WorldTime":
        return cls(elapsed_seconds=int(payload.get("elapsed_seconds", 0)))


@dataclass(frozen=True, slots=True)
class ScheduledEvent:
    """Base class for things that can be on the schedule.

    Subclasses should remain small, serializable dataclasses. The
    schedule itself does not interpret the event; that is the
    applier's job.
    """

    kind: str


@dataclass(order=True, slots=True)
class _ScheduleEntry:
    due_at: int
    sequence: int
    event: ScheduledEvent = field(compare=False)


@dataclass(slots=True)
class Schedule:
    """Min-heap of `(due_at, event)` schedule entries.

    Entries with the same `due_at` are popped in insertion order so
    behavior is deterministic across runs and save/load cycles.
    """

    _entries: list[_ScheduleEntry] = field(default_factory=list)
    _next_sequence: int = 0

    def schedule(self, due_at: int, event: ScheduledEvent) -> None:
        if due_at < 0:
            raise ValueError("Schedule entries must have non-negative due times.")
        entry = _ScheduleEntry(due_at=due_at, sequence=self._next_sequence, event=event)
        self._next_sequence += 1
        heappush(self._entries, entry)

    def __len__(self) -> int:
        return len(self._entries)

    def __bool__(self) -> bool:
        return bool(self._entries)

    def peek(self) -> _ScheduleEntry | None:
        if not self._entries:
            return None
        return self._entries[0]

    def pop_due(self, now: int) -> list[ScheduledEvent]:
        """Pop and return all events whose `due_at <= now`, in order."""

        ready: list[ScheduledEvent] = []
        while self._entries and self._entries[0].due_at <= now:
            ready.append(heappop(self._entries).event)
        return ready

    def upcoming(self) -> Iterable[tuple[int, ScheduledEvent]]:
        """Iterate scheduled entries in heap order (not strictly sorted)."""

        for entry in self._entries:
            yield entry.due_at, entry.event

    # -- serialization --------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        return {
            "next_sequence": self._next_sequence,
            "entries": [
                {
                    "due_at": entry.due_at,
                    "sequence": entry.sequence,
                    "kind": entry.event.kind,
                }
                for entry in sorted(self._entries)
            ],
        }


def advance(
    clock: WorldTime,
    seconds: int,
    schedule: Schedule | None = None,
    apply_event: Callable[[ScheduledEvent], None] | None = None,
) -> list[ScheduledEvent]:
    """Advance `clock` by `seconds` and fire any due schedule entries.

    Returns the list of events that came due (in fire order). If
    `apply_event` is supplied, each event is also passed to it so the
    caller can perform world mutations in one place. The schedule and
    applier are both optional so callers can advance time without a
    schedule (e.g. tests, or modes that don't yet use one).
    """

    clock.advance_seconds(seconds)
    if schedule is None:
        return []
    fired = schedule.pop_due(clock.elapsed_seconds)
    if apply_event is not None:
        for event in fired:
            apply_event(event)
    return fired
