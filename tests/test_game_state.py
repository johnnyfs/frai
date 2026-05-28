"""Tests for the GameState aggregate (M49).

The container itself is purely structural: it owns world / party /
turn / mode / messages / memory / facing and exposes a JSON-safe
``to_dict`` / ``from_dict`` round-trip. Save/load proper (M16) builds
on this shape; these tests only exercise the shape.
"""

from __future__ import annotations

import json

import pytest

from src.app import create_app
from src.core.character_creation import CharacterCreationState
from src.core.entity import EntityId
from src.core.game_state import GAME_STATE_SCHEMA_VERSION, GameState
from src.core.modes import PlayMode, UIMode
from src.core.party_state import PartyState
from src.core.time import SECONDS_PER_TURN
from src.core.turn_controller import TurnController
from src.core.vision import PartyMemory
from src.core.world import World


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_create_app_constructs_game_state_with_expected_fields() -> None:
    app = create_app()
    state = app.game_state
    assert state.schema_version == GAME_STATE_SCHEMA_VERSION
    assert isinstance(state.world, World)
    assert isinstance(state.party, PartyState)
    assert isinstance(state.turn, TurnController)
    assert state.party.size == 4
    assert state.ui_mode is UIMode.start
    assert state.messages.current == ""
    assert state.facing == (1, 0)


def test_game_state_post_init_binds_turn_to_party() -> None:
    """The PartyState handed to GameState must be the same instance
    the TurnController borrows. Otherwise active_index / focused_index
    drift between the camera and the turn rotation.
    """
    party = PartyState.from_members([EntityId(1), EntityId(2)])
    foreign_party = PartyState.from_members([EntityId(99)])
    turn = TurnController(
        party_state=foreign_party,
        hostiles_probe=lambda: False,
        can_take_turn=lambda _entity: True,
    )
    state = GameState(world=_empty_world(), party=party, turn=turn)
    assert state.turn.party_state is party


def test_game_state_play_mode_property_mirrors_turn_controller() -> None:
    app = create_app()
    state = app.game_state
    state.turn.play_mode = PlayMode.voluntary_turn
    assert state.play_mode is PlayMode.voluntary_turn
    state.play_mode = PlayMode.explore
    assert state.turn.play_mode is PlayMode.explore


def test_game_state_clock_and_schedule_delegate_to_world() -> None:
    app = create_app()
    state = app.game_state
    assert state.clock is state.world.clock
    assert state.schedule is state.world.schedule


# ---------------------------------------------------------------------------
# App delegation
# ---------------------------------------------------------------------------


def test_app_world_party_turn_delegate_to_game_state() -> None:
    app = create_app()
    assert app.world is app.game_state.world
    assert app.party is app.game_state.party
    assert app.turn is app.game_state.turn
    assert app.ui_mode is app.game_state.ui_mode


def test_app_ui_mode_setter_writes_through_to_game_state() -> None:
    app = create_app()
    app.ui_mode = UIMode.play
    assert app.game_state.ui_mode is UIMode.play


def test_app_memory_setter_writes_through_to_game_state() -> None:
    app = create_app()
    new_memory = PartyMemory()
    app.memory = new_memory
    assert app.game_state.memory is new_memory


def test_app_facing_setter_writes_through_to_game_state() -> None:
    app = create_app()
    app.facing = (-1, 0)
    assert app.game_state.facing == (-1, 0)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_to_dict_payload_is_json_serializable() -> None:
    """No callables, no entity refs, no curses surfaces."""
    app = create_app()
    payload = app.game_state.to_dict()
    # Round-trip through json to assert pure-JSON shape.
    json_text = json.dumps(payload)
    assert isinstance(json_text, str)
    # Top-level schema_version is always present.
    assert payload["schema_version"] == GAME_STATE_SCHEMA_VERSION
    # The "world" key is included by default at and after M16.
    assert "world" in payload
    # Required top-level fields the issue lists.
    for key in (
        "schema_version",
        "ui_mode",
        "play_mode",
        "party",
        "turn",
        "messages",
        "memory",
        "clock",
        "schedule",
        "facing",
        "character_creation_state",
        "world",
    ):
        assert key in payload, f"missing {key}"


def test_to_dict_without_world_omits_world_key() -> None:
    """``include_world=False`` preserves the M49 partial shape."""
    app = create_app()
    payload = app.game_state.to_dict(include_world=False)
    assert "world" not in payload


def test_to_dict_captures_facing_and_modes() -> None:
    app = create_app()
    app.facing = (-1, 1)
    app.ui_mode = UIMode.play
    app.play_mode = PlayMode.voluntary_turn
    payload = app.game_state.to_dict()
    assert payload["ui_mode"] == "play"
    assert payload["play_mode"] == "voluntary_turn"
    assert payload["facing"] == [-1, 1]


def test_to_dict_captures_clock_advancement() -> None:
    app = create_app()
    app.world.clock.advance_seconds(SECONDS_PER_TURN * 3)
    payload = app.game_state.to_dict()
    assert payload["clock"]["elapsed_seconds"] == SECONDS_PER_TURN * 3


def test_round_trip_preserves_top_level_fields() -> None:
    app = create_app()
    # Mutate a handful of fields so the round trip has something to do.
    app.facing = (0, -1)
    app.ui_mode = UIMode.play
    app.play_mode = PlayMode.voluntary_turn
    app.messages.emit("hello world")
    app.world.clock.advance_seconds(120)

    payload = app.game_state.to_dict()
    rebuilt = GameState.from_dict(payload, world=app.world, turn=app.turn)

    assert rebuilt.schema_version == GAME_STATE_SCHEMA_VERSION
    assert rebuilt.ui_mode is UIMode.play
    assert rebuilt.play_mode is PlayMode.voluntary_turn
    assert rebuilt.facing == (0, -1)
    assert rebuilt.messages.current == "hello world"
    assert rebuilt.clock.elapsed_seconds == 120
    assert rebuilt.party.size == app.party.size
    # The PartyState equality test is strict: members/index/focus/order.
    assert rebuilt.party == app.party


def test_from_dict_with_minimal_payload_uses_defaults() -> None:
    """A dict that carries only ``schema_version`` must rehydrate."""
    world = _empty_world()
    party = PartyState.from_members([])
    turn = TurnController(
        party_state=party,
        hostiles_probe=lambda: False,
        can_take_turn=lambda _entity: True,
    )
    state = GameState.from_dict(
        {"schema_version": GAME_STATE_SCHEMA_VERSION},
        world=world,
        turn=turn,
    )
    assert state.schema_version == GAME_STATE_SCHEMA_VERSION
    assert state.ui_mode is UIMode.start
    assert state.play_mode is PlayMode.explore
    assert state.facing == (1, 0)
    assert state.messages.current == ""
    assert state.character_creation_state is None
    assert state.memory.visible == frozenset()


def test_from_dict_rejects_unknown_schema_version() -> None:
    with pytest.raises(ValueError):
        GameState.from_dict(
            {"schema_version": 999},
            world=_empty_world(),
            turn=_dummy_turn(),
        )


def test_from_dict_round_trips_character_creation_state() -> None:
    app = create_app()
    app.character_creation_state = CharacterCreationState(
        step="class",
        cursor=2,
        race="Elf",
        character_class="Fighter",
        cantrips=("Light",),
        spells=(),
        skills=("Athletics", "Insight"),
        base_attributes={"STR": 15, "DEX": 13, "CON": 12, "INT": 10, "WIS": 11, "CHA": 9},
    )
    payload = app.game_state.to_dict()
    rebuilt = GameState.from_dict(payload, world=app.world, turn=app.turn)
    assert rebuilt.character_creation_state is not None
    assert rebuilt.character_creation_state.race == "Elf"
    assert rebuilt.character_creation_state.character_class == "Fighter"
    assert rebuilt.character_creation_state.skills == ("Athletics", "Insight")


def test_from_dict_round_trips_party_memory() -> None:
    app = create_app()
    app.memory.set_visible({(1, 1), (2, 1), (3, 1)})
    payload = app.game_state.to_dict()
    rebuilt = GameState.from_dict(payload, world=app.world, turn=app.turn)
    assert rebuilt.memory.visible == frozenset({(1, 1), (2, 1), (3, 1)})


# ---------------------------------------------------------------------------
# Mutation through the aggregate
# ---------------------------------------------------------------------------


def test_recruit_through_game_state_party_updates_app_view() -> None:
    """Mutations on ``app.game_state.party`` are observable via the
    back-compat ``app.party`` property."""
    app = create_app()
    new_member = EntityId(9999)
    app.game_state.party.recruit(new_member)
    assert new_member in app.party.members


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_world() -> World:
    return World(width=4, height=4, tiles=[[_floor() for _ in range(4)] for _ in range(4)])


def _floor():
    # Local import so the test module doesn't need to know about Tile
    # at module level; this keeps the fixture set tiny.
    from src.map.tiles import FLOOR
    return FLOOR


def _dummy_turn() -> TurnController:
    party = PartyState.from_members([])
    return TurnController(
        party_state=party,
        hostiles_probe=lambda: False,
        can_take_turn=lambda _entity: True,
    )
