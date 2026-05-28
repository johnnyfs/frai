from dataclasses import dataclass

import pytest

from src.core.modes import PlayMode
from src.core.time import (
    SECONDS_PER_HOUR,
    SECONDS_PER_LONG_REST,
    SECONDS_PER_MINUTE,
    SECONDS_PER_ROUND,
    SECONDS_PER_SHORT_REST,
    SECONDS_PER_TURN,
    Schedule,
    ScheduledEvent,
    WorldTime,
    advance,
)


@dataclass(frozen=True, slots=True)
class _TaggedEvent(ScheduledEvent):
    tag: str = ""


def test_unit_conversions_are_explicit() -> None:
    assert SECONDS_PER_ROUND == 6
    assert SECONDS_PER_TURN == 60
    assert SECONDS_PER_MINUTE == 60
    assert SECONDS_PER_HOUR == 3600
    assert SECONDS_PER_SHORT_REST == 10 * 60
    assert SECONDS_PER_LONG_REST == 8 * 3600


def test_world_time_unit_views() -> None:
    clock = WorldTime()
    clock.advance_rounds(2)
    clock.advance_turns(1)
    clock.advance_hours(1)

    expected = 2 * SECONDS_PER_ROUND + SECONDS_PER_TURN + SECONDS_PER_HOUR
    assert clock.elapsed_seconds == expected
    assert clock.rounds == expected // SECONDS_PER_ROUND
    assert clock.minutes == expected // SECONDS_PER_MINUTE
    assert clock.hours == 1


def test_world_time_rejects_negative_advance() -> None:
    clock = WorldTime()
    with pytest.raises(ValueError):
        clock.advance_seconds(-1)


def test_advance_fires_due_schedule_entries() -> None:
    clock = WorldTime()
    schedule = Schedule()
    schedule.schedule(due_at=300, event=_TaggedEvent(kind="five-min", tag="A"))
    schedule.schedule(due_at=600, event=_TaggedEvent(kind="ten-min", tag="B"))

    fired_first = advance(clock, seconds=10 * SECONDS_PER_MINUTE, schedule=schedule)

    assert [event.kind for event in fired_first] == ["five-min", "ten-min"]
    assert len(schedule) == 0
    assert clock.elapsed_seconds == 600


def test_non_due_entries_remain() -> None:
    clock = WorldTime()
    schedule = Schedule()
    schedule.schedule(due_at=120, event=_TaggedEvent(kind="soon"))
    schedule.schedule(due_at=600, event=_TaggedEvent(kind="later"))

    fired = advance(clock, seconds=180, schedule=schedule)

    assert [event.kind for event in fired] == ["soon"]
    assert len(schedule) == 1
    upcoming = list(schedule.upcoming())
    assert upcoming[0][0] == 600
    assert upcoming[0][1].kind == "later"


def test_advance_invokes_applier_for_each_fired_event() -> None:
    clock = WorldTime()
    schedule = Schedule()
    schedule.schedule(due_at=SECONDS_PER_ROUND, event=_TaggedEvent(kind="r1"))
    schedule.schedule(due_at=SECONDS_PER_ROUND * 3, event=_TaggedEvent(kind="r3"))

    seen: list[str] = []
    advance(
        clock,
        seconds=SECONDS_PER_ROUND * 3,
        schedule=schedule,
        apply_event=lambda event: seen.append(event.kind),
    )

    assert seen == ["r1", "r3"]


def test_three_round_condition_expires_after_three_rounds() -> None:
    clock = WorldTime()
    schedule = Schedule()
    schedule.schedule(
        due_at=SECONDS_PER_ROUND * 3,
        event=_TaggedEvent(kind="condition-expire"),
    )

    # Two rounds pass: nothing fires yet.
    fired_after_two = advance(clock, seconds=SECONDS_PER_ROUND * 2, schedule=schedule)
    assert fired_after_two == []
    assert len(schedule) == 1

    # Third round: condition expires.
    fired_after_three = advance(clock, seconds=SECONDS_PER_ROUND, schedule=schedule)
    assert [event.kind for event in fired_after_three] == ["condition-expire"]


def test_rest_helpers_advance_bulk_time() -> None:
    clock = WorldTime()
    clock.advance_short_rest()
    assert clock.elapsed_seconds == SECONDS_PER_SHORT_REST

    clock.advance_long_rest()
    assert clock.elapsed_seconds == SECONDS_PER_SHORT_REST + SECONDS_PER_LONG_REST


def test_world_time_roundtrip() -> None:
    clock = WorldTime()
    clock.advance_minutes(7)
    restored = WorldTime.from_dict(clock.to_dict())
    assert restored.elapsed_seconds == clock.elapsed_seconds


def test_app_explore_move_ticks_world_clock() -> None:
    from src.app import create_app

    app = create_app()
    app.handle_key(ord("y"))
    player, companion = app.party[:2]
    for entity in list(app.world.creatures.values):
        app.world.remove_entity(entity)
    app.world.positions.require(player).x = 160
    app.world.positions.require(player).y = 40
    app.world.positions.require(companion).x = 162
    app.world.positions.require(companion).y = 40

    starting = app.world.clock.elapsed_seconds
    app.handle_key(ord("h"))

    assert app.play_mode is PlayMode.explore
    assert app.world.clock.elapsed_seconds == starting + SECONDS_PER_TURN


def test_app_round_tick_advances_clock_after_party_turn() -> None:
    from src.app import create_app

    app = create_app()
    app.handle_key(ord("y"))
    app.voluntary_turn_based = True
    app.play_mode = PlayMode.turn_based
    # Round tick fires only when the last party member has acted.
    app.active_party_index = len(app.party) - 1
    starting = app.world.clock.elapsed_seconds

    app.advance_party_turn()

    assert app.world.clock.elapsed_seconds == starting + SECONDS_PER_ROUND


def test_schedule_dict_preserves_due_times_in_order() -> None:
    schedule = Schedule()
    schedule.schedule(due_at=900, event=_TaggedEvent(kind="late"))
    schedule.schedule(due_at=60, event=_TaggedEvent(kind="early"))

    snapshot = schedule.to_dict()
    entries = snapshot["entries"]
    assert isinstance(entries, list)
    assert [entry["due_at"] for entry in entries] == [60, 900]
    assert [entry["kind"] for entry in entries] == ["early", "late"]
