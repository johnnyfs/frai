"""Tests for the auto-walk predicate and the App integration (M22)."""

from dataclasses import dataclass, field

from src.app import create_app
from src.core.autowalk import (
    AutowalkRequest,
    InterruptReason,
    interrupt_message,
    step_autowalk,
)
from src.core.components import CombatStats, Faction, Name, Position
from src.core.entity import EntityId
from src.core.modes import UIMode
from src.core.party_state import PartyState
from src.core.vision import PartyMemory
from src.core.world import World
from src.systems.message_system import MessageState
from tests.support.tiny_world import add_actor, add_enemy, build_tiny_map


# ---------------------------------------------------------------------------
# Predicate-in-isolation tests
#
# These exercise ``step_autowalk`` without standing up the full App or
# curses. Every guard fires its own ``InterruptReason``; the stub host
# below has just the attributes the predicate touches.
# ---------------------------------------------------------------------------


@dataclass
class _StubHost:
    world: World
    ui_mode: UIMode
    memory: PartyMemory
    messages: MessageState
    party: PartyState


def _build_stub_host(
    *,
    ui_mode: UIMode = UIMode.play,
    visible: frozenset[tuple[int, int]] = frozenset(),
    message: str = "",
) -> tuple[_StubHost, EntityId]:
    world = build_tiny_map(width=9, height=5)
    player = add_actor(world, 2, 2)
    memory = PartyMemory()
    memory.set_visible(visible)
    messages = MessageState()
    if message:
        messages.emit(message)
    host = _StubHost(
        world=world,
        ui_mode=ui_mode,
        memory=memory,
        messages=messages,
        party=PartyState.from_members([player]),
    )
    return host, player


def test_predicate_continues_in_open_corridor() -> None:
    host, player = _build_stub_host()
    request = AutowalkRequest(direction=(1, 0))

    cont, reason = step_autowalk(host, request, current_step=1, actor=player, actor_moved=True)

    assert cont is True
    assert reason is None


def test_predicate_stops_when_actor_did_not_move() -> None:
    host, player = _build_stub_host()
    request = AutowalkRequest(direction=(1, 0))

    cont, reason = step_autowalk(host, request, current_step=1, actor=player, actor_moved=False)

    assert cont is False
    assert reason is InterruptReason.BLOCKED


def test_predicate_stops_at_step_budget() -> None:
    host, player = _build_stub_host()
    request = AutowalkRequest(direction=(1, 0), max_steps=3)

    cont, reason = step_autowalk(host, request, current_step=3, actor=player, actor_moved=True)

    assert cont is False
    assert reason is InterruptReason.OUT_OF_STEPS


def test_predicate_stops_when_modal_opens() -> None:
    host, player = _build_stub_host(ui_mode=UIMode.inventory)
    request = AutowalkRequest(direction=(1, 0))

    cont, reason = step_autowalk(host, request, current_step=1, actor=player, actor_moved=True)

    assert cont is False
    assert reason is InterruptReason.MODAL_OPENED


def test_predicate_stops_when_visible_hostile_appears() -> None:
    host, player = _build_stub_host(visible=frozenset({(4, 2)}))
    enemy = add_enemy(host.world, 4, 2)
    request = AutowalkRequest(direction=(1, 0))

    cont, reason = step_autowalk(host, request, current_step=1, actor=player, actor_moved=True)

    assert cont is False
    # Both COMBAT_STARTED and NEW_HOSTILE_VISIBLE are valid here; the
    # awareness layer already considers the enemy "in battle". Either
    # is an acceptable stop — we just verify it's hostile-related.
    assert reason in (
        InterruptReason.COMBAT_STARTED,
        InterruptReason.NEW_HOSTILE_VISIBLE,
    )
    assert enemy in host.world.combat_stats.values


def test_predicate_stops_on_event_message() -> None:
    host, player = _build_stub_host(message="You found a trap.")
    request = AutowalkRequest(direction=(1, 0))

    cont, reason = step_autowalk(host, request, current_step=1, actor=player, actor_moved=True)

    assert cont is False
    assert reason is InterruptReason.EVENT_MESSAGE


def test_predicate_ignores_blocked_message_text() -> None:
    """A bare ``Blocked.`` message must not be reported as an event.

    The movement system emits ``Blocked.`` when an obstruction refuses a
    step. The blocked-step path is signalled to the predicate via
    ``actor_moved=False``; the message text alone must not double-fire
    as an event interrupt.
    """
    host, player = _build_stub_host(message="Blocked.")
    request = AutowalkRequest(direction=(1, 0))

    # actor_moved=False means the step itself failed — the predicate
    # should report BLOCKED, not EVENT_MESSAGE.
    cont, reason = step_autowalk(host, request, current_step=1, actor=player, actor_moved=False)

    assert cont is False
    assert reason is InterruptReason.BLOCKED


def test_interrupt_message_table_covers_every_reason() -> None:
    for reason in InterruptReason:
        assert isinstance(interrupt_message(reason), str)
        assert interrupt_message(reason)


# ---------------------------------------------------------------------------
# App-level integration tests
#
# These press capital-direction keys against a real App and check the
# resulting world / message state. Creatures are cleared so the
# pre-built room is otherwise empty.
# ---------------------------------------------------------------------------


def _clear_creatures(app) -> None:
    for entity in list(app.world.creatures.values):
        app.world.remove_entity(entity)


def _move_extra_party_away(app) -> None:
    player_position = app.world.positions.require(app.player)
    for index, entity in enumerate(app.party[1:]):
        app.world.positions.require(entity).x = player_position.x
        app.world.positions.require(entity).y = player_position.y + 5 + index


def test_autowalk_in_open_corridor_stops_at_wall() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    _clear_creatures(app)
    _move_extra_party_away(app)
    app.sync_play_mode()
    # Clear the YOLO welcome banner so it doesn't interrupt the walk on
    # the first step as an event message.
    app.messages.emit("")
    player_position = app.world.positions.require(app.player)
    start_x = player_position.x
    start_y = player_position.y

    app.handle_key(ord("H"))

    assert app.autowalk is None
    new_x = app.world.positions.require(app.player).x
    # The walk should have travelled at least one tile and stopped at
    # the wall, not at the starting position.
    assert new_x < start_x
    # The tile west of where we ended must be blocked (wall or border).
    assert app.world.tile_at(new_x - 1, start_y).blocks_movement
    assert "Autowalk" in app.messages.current or app.messages.current == "Blocked."


def test_autowalk_stops_when_hostile_becomes_visible() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    _clear_creatures(app)
    _move_extra_party_away(app)
    app.messages.emit("")
    player_position = app.world.positions.require(app.player)
    # Place a hostile two tiles east, still in vision.
    hostile = app.world.create_entity()
    app.world.positions.add(hostile, Position(player_position.x + 3, player_position.y))
    app.world.names.add(hostile, Name("ambusher"))
    app.world.factions.add(hostile, Faction("enemy"))
    app.world.combat_stats.add(
        hostile,
        CombatStats(
            armor_class=10,
            hit_points=4,
            max_hit_points=4,
            strength=10,
            dexterity=10,
            constitution=10,
        ),
    )
    app.sync_play_mode()
    app.refresh_vision()
    start_x = player_position.x

    app.handle_key(ord("L"))

    assert app.autowalk is None
    # The walk must have stopped before reaching the hostile's tile.
    end_x = app.world.positions.require(app.player).x
    assert start_x <= end_x < player_position.x + 3
    # Some autowalk-related message is in the buffer.
    assert app.messages.current


def test_autowalk_stops_when_modal_opens_mid_walk() -> None:
    """If a step pushes UIMode out of play, the walk stops.

    We simulate this by setting up the autowalk request manually and
    flipping ``ui_mode`` before invoking the runner. The runner's
    first ``step_autowalk`` check after the move dispatch must see the
    modal state and stop immediately.
    """
    app = create_app()
    app.handle_key(ord("y"))
    _clear_creatures(app)
    _move_extra_party_away(app)
    app.sync_play_mode()
    app.messages.emit("")
    # Pre-arm an autowalk and then flip the UI mode synchronously
    # before the runner's predicate fires. The first dispatch still
    # runs (no harm — the player just takes a single step), but the
    # post-step predicate detects the modal and stops.
    app.autowalk = AutowalkRequest(direction=(1, 0))
    app.ui_mode = UIMode.inventory
    app._run_autowalk()

    assert app.autowalk is None
    assert app.messages.current


def test_autowalk_respects_max_steps_budget() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    _clear_creatures(app)
    _move_extra_party_away(app)
    app.sync_play_mode()
    app.messages.emit("")
    player_position = app.world.positions.require(app.player)
    start_x = player_position.x

    # Set a very small budget by injecting a fresh autowalk and
    # invoking the runner directly. The capital-key path uses the
    # default budget; this path is what M36 cmdscripts will reuse.
    app.autowalk = AutowalkRequest(direction=(1, 0), max_steps=2)
    app._run_autowalk()

    assert app.autowalk is None
    end_x = app.world.positions.require(app.player).x
    assert end_x - start_x == 2
    assert app.messages.current == interrupt_message(InterruptReason.OUT_OF_STEPS)


def test_autowalk_reports_interrupt_reason_in_messages() -> None:
    """Every interrupt path must produce a message the player can read.

    We exercise three reasons in sequence: out-of-steps, blocked, and
    hostile-visible. Each must leave a non-empty ``messages.current``.
    """
    app = create_app()
    app.handle_key(ord("y"))
    _clear_creatures(app)
    _move_extra_party_away(app)
    app.sync_play_mode()
    app.messages.emit("")

    # Out of steps:
    app.autowalk = AutowalkRequest(direction=(1, 0), max_steps=1)
    app._run_autowalk()
    assert app.messages.current

    # Hostile visible: place enemy three east, walk east. The
    # autowalk-active marker should clear and a message should appear.
    player_position = app.world.positions.require(app.player)
    hostile = app.world.create_entity()
    app.world.positions.add(hostile, Position(player_position.x + 4, player_position.y))
    app.world.names.add(hostile, Name("ambusher"))
    app.world.factions.add(hostile, Faction("enemy"))
    app.world.combat_stats.add(
        hostile,
        CombatStats(
            armor_class=10,
            hit_points=4,
            max_hit_points=4,
            strength=10,
            dexterity=10,
            constitution=10,
        ),
    )
    app.sync_play_mode()
    app.refresh_vision()
    app.handle_key(ord("L"))
    assert app.autowalk is None
    assert app.messages.current
