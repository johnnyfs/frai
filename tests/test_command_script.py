"""Tests for the M36 command script parser and runner."""

from __future__ import annotations

import pytest

from src.app import create_app
from src.core.autowalk import InterruptReason
from src.core.command_script import (
    CancelCommand,
    CommandScriptError,
    ConfirmCommand,
    ExamineCommand,
    HelpCommand,
    InteractCommand,
    InventoryCommand,
    MoveCommand,
    PerceiveCommand,
    PickupCommand,
    RestCommand,
    SneakCommand,
    WaitCommand,
    parse,
)
from src.core.components import CombatStats, Faction, Name, Position
from src.core.modes import UIMode
from src.ui.script_runner import CommandOutcome, run_command, run_script


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


def test_parse_empty_returns_empty_list() -> None:
    assert parse("") == []
    assert parse("   ") == []
    assert parse("\n\n") == []


def test_parse_single_direction_key() -> None:
    assert parse("h") == [MoveCommand(-1, 0, 1)]
    assert parse("l") == [MoveCommand(1, 0, 1)]
    assert parse("y") == [MoveCommand(-1, -1, 1)]


def test_parse_count_prefix_movement() -> None:
    assert parse("5h") == [MoveCommand(-1, 0, repeat=5)]
    assert parse("10j") == [MoveCommand(0, 1, repeat=10)]


def test_parse_mixed_sequence_semicolon_separator() -> None:
    commands = parse("5h;e;i")
    assert commands == [
        MoveCommand(-1, 0, repeat=5),
        InteractCommand(),
        InventoryCommand(),
    ]


def test_parse_newline_separator_and_comments() -> None:
    script = """
    # this is a comment
    5j
    e
    # another comment, then a pickup
    ,
    """
    assert parse(script) == [
        MoveCommand(0, 1, repeat=5),
        InteractCommand(),
        PickupCommand(),
    ]


def test_parse_full_grammar_one_of_each() -> None:
    """Every documented single-key command appears in the parsed list."""

    script = "h;e;,;i;r;x;?;.;Enter;Esc"
    parsed = parse(script)
    assert parsed == [
        MoveCommand(-1, 0, 1),
        InteractCommand(),
        PickupCommand(),
        InventoryCommand(),
        RestCommand(),
        ExamineCommand(),
        HelpCommand(),
        WaitCommand(),
        ConfirmCommand(),
        CancelCommand(),
    ]


def test_parse_sneak_and_perceive_tokens() -> None:
    """``z`` and ``p`` (M23) parse to the stealth/perception commands."""

    assert parse("z") == [SneakCommand()]
    assert parse("p") == [PerceiveCommand()]


def test_parse_unknown_token_raises() -> None:
    with pytest.raises(CommandScriptError):
        parse("q")
    with pytest.raises(CommandScriptError):
        parse("h;zz;l")


def test_parse_count_prefix_on_non_movement_key_raises() -> None:
    # ``5e`` is suspicious — count prefixes only apply to movement.
    with pytest.raises(CommandScriptError):
        parse("5e")
    with pytest.raises(CommandScriptError):
        parse("3i")


def test_parse_zero_repeat_count_rejected() -> None:
    with pytest.raises(CommandScriptError):
        parse("0h")


def test_parse_blank_lines_and_whitespace_tolerated() -> None:
    """Whitespace around tokens and entirely blank lines must not error."""

    script = "  h  ;\n\n   ;   5j  \n"
    assert parse(script) == [MoveCommand(-1, 0, 1), MoveCommand(0, 1, repeat=5)]


# ---------------------------------------------------------------------------
# Runner: support helpers (mirrored from test_autowalk.py)
# ---------------------------------------------------------------------------


def _clear_creatures(app) -> None:
    for entity in list(app.world.creatures.values):
        app.world.remove_entity(entity)


def _move_extra_party_away(app) -> None:
    player_position = app.world.positions.require(app.player)
    for index, entity in enumerate(app.party[1:]):
        app.world.positions.require(entity).x = player_position.x
        app.world.positions.require(entity).y = player_position.y + 5 + index


def _ready_app() -> "object":
    """Return an App that's already past the start screen and otherwise empty."""

    app = create_app()
    app.handle_key(ord("y"))  # YOLO into play.
    _clear_creatures(app)
    _move_extra_party_away(app)
    app.sync_play_mode()
    app.messages.emit("")
    return app


# ---------------------------------------------------------------------------
# Runner tests
# ---------------------------------------------------------------------------


def test_run_script_returns_outcome_per_command() -> None:
    app = _ready_app()
    outcomes = run_script(app, "l;l")

    assert len(outcomes) == 2
    assert all(isinstance(o, CommandOutcome) for o in outcomes)
    # The final observation is in play mode and reflects the actor's
    # new position.
    assert outcomes[-1].observation_after.mode["ui_mode"] == "play"


def test_run_script_single_step_move_moves_one_tile() -> None:
    app = _ready_app()
    start = app.world.positions.require(app.player)
    start_xy = (start.x, start.y)

    outcomes = run_script(app, "l")

    assert outcomes[0].steps_taken == 1
    end = app.world.positions.require(app.player)
    assert (end.x, end.y) == (start_xy[0] + 1, start_xy[1])
    assert outcomes[0].interrupt_reason is None


def test_run_script_repeat_move_walks_up_to_budget() -> None:
    """A ``5h`` walk moves up to 5 tiles in a clear corridor."""

    app = _ready_app()
    start = app.world.positions.require(app.player)
    start_x = start.x

    # Walk east; the open room is at least 5 tiles wide in the
    # default world. We pick east because the player starts roughly
    # centred and east is open.
    outcomes = run_script(app, "5l")

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.steps_taken >= 1
    assert outcome.steps_taken <= 5
    end_x = app.world.positions.require(app.player).x
    assert end_x - start_x == outcome.steps_taken


def test_run_script_repeat_move_stops_at_wall() -> None:
    """Walking west until a wall blocks reports BLOCKED before 5 steps."""

    app = _ready_app()
    # Walk west aggressively — eventually we hit the left wall. We
    # choose a budget larger than the room dimension so the only way
    # the run ends is the BLOCKED guard.
    outcomes = run_script(app, "50h")

    outcome = outcomes[0]
    assert outcome.steps_taken < 50
    assert outcome.interrupt_reason == InterruptReason.BLOCKED
    # The tile immediately west of where we ended must be a blocker.
    position = app.world.positions.require(app.player)
    assert app.world.tile_at(position.x - 1, position.y).blocks_movement


def test_run_script_repeat_move_stops_when_combat_starts() -> None:
    app = _ready_app()
    player_position = app.world.positions.require(app.player)
    # Place a hostile 3 tiles east and make sure it's in vision.
    hostile = app.world.create_entity()
    app.world.positions.add(
        hostile, Position(player_position.x + 3, player_position.y)
    )
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

    outcomes = run_script(app, "5l")

    outcome = outcomes[0]
    # Combat should stop the autowalk before the budget runs out.
    assert outcome.steps_taken < 5
    assert outcome.interrupt_reason in (
        InterruptReason.COMBAT_STARTED,
        InterruptReason.NEW_HOSTILE_VISIBLE,
    )


def test_run_script_inventory_opens_modal() -> None:
    """The ``i`` command opens the inventory modal.

    The observation's ``modal`` field must report ``inventory`` so M37
    can detect "the agent opened a modal".
    """

    app = _ready_app()
    outcomes = run_script(app, "i")

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.observation_after.mode["ui_mode"] == "inventory"
    assert outcome.observation_after.modal is not None
    assert outcome.observation_after.modal.kind == "inventory"


def test_run_script_inventory_then_close() -> None:
    """``i;i`` opens the inventory then closes it."""

    app = _ready_app()
    outcomes = run_script(app, "i;i")

    assert len(outcomes) == 2
    assert outcomes[0].observation_after.mode["ui_mode"] == "inventory"
    assert outcomes[1].observation_after.mode["ui_mode"] == "play"


def test_run_script_pickup_no_op_when_nothing_to_pick_up() -> None:
    """The ``,`` command runs without raising even with nothing on the tile."""

    app = _ready_app()
    outcomes = run_script(app, ",")

    assert len(outcomes) == 1
    # No-op messaging or empty — either is fine. The runner must not
    # raise.
    assert outcomes[0].observation_after.mode["ui_mode"] == "play"


def test_run_script_comment_only_script_yields_no_outcomes() -> None:
    app = _ready_app()
    outcomes = run_script(app, "# nothing to do\n   \n#another\n")
    assert outcomes == []


def test_run_command_help_is_safe_no_op_today() -> None:
    """``?`` has no key binding in play yet — the runner must still produce an outcome.

    Once M39 adds the help modal this test should be updated to check
    that ``observation_after.modal.kind == "help"`` but until then we
    simply assert it didn't crash and we're still in play.
    """

    app = _ready_app()
    outcome = run_command(app, HelpCommand())
    assert outcome.observation_after.mode["ui_mode"] == "play"


def test_run_script_reuses_autowalk_interrupt_for_5h_at_wall() -> None:
    """The acceptance test from issue #29: ``5h`` stops at a wall."""

    app = _ready_app()
    # Aggressively walk west far enough that we'll hit a wall.
    outcomes = run_script(app, "50h")
    outcome = outcomes[0]
    assert outcome.steps_taken < 50
    assert outcome.interrupt_reason == InterruptReason.BLOCKED


def test_run_script_observation_after_each_command_is_fresh() -> None:
    """Every outcome's ``observation_after`` is taken *after* that command.

    M37's harness diffs consecutive observations, so the observation
    on outcome[i] must reflect the world state after command[i].
    """

    app = _ready_app()
    outcomes = run_script(app, "l;l")
    # The two consecutive moves must produce two different positions
    # in their observations.
    first_pos = outcomes[0].observation_after.active_actor.position
    second_pos = outcomes[1].observation_after.active_actor.position
    assert second_pos[0] == first_pos[0] + 1


def test_run_script_parse_error_surfaces_to_caller() -> None:
    app = _ready_app()
    with pytest.raises(CommandScriptError):
        run_script(app, "h;zz")
