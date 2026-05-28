"""Tests for the M20 targeting modal.

The targeting module is intentionally small and the App-side wiring is
even smaller, so the test suite is split into two layers:

* `TargetingState` unit tests cover the in-range / out-of-range cursor
  semantics, predicate evaluation, and the confirm-vs-cancel contract.
* App integration tests cover the full open/confirm and open/cancel
  flows: entering targeting does not advance the turn, the cursor
  consumes movement keys instead of moving the party, and confirm
  routes through ``resolve_action``.
"""

from __future__ import annotations

from src.app import create_app
from src.core.actions import AttackAttempt
from src.core.modes import UIMode
from src.core.targeting import (
    TargetingState,
    any_tile,
    any_visible_entity,
    any_visible_tile,
    chebyshev,
    hostile_entity,
    make_spell_target_predicate,
    make_visible_predicate,
)
from src.core.vision import PartyMemory
from src.core.world import World
from src.map.tiles import FLOOR, VERTICAL_WALL
from src.systems.vision_system import VisionSystem
from tests.support.tiny_world import add_actor, add_enemy, build_tiny_map


# ---------------------------------------------------------------------
# TargetingState unit tests
# ---------------------------------------------------------------------


def _state_at(origin: tuple[int, int], **kwargs) -> TargetingState:
    """Construct a default-permissive :class:`TargetingState` for tests."""

    return TargetingState(
        origin=origin,
        cursor=origin,
        range=kwargs.pop("range", 5),
        on_confirm=kwargs.pop("on_confirm", lambda cell: None),
        predicate=kwargs.pop("predicate", any_tile),
        **kwargs,
    )


def test_chebyshev_matches_max_axis() -> None:
    assert chebyshev((0, 0), (3, 4)) == 4
    assert chebyshev((2, 2), (2, 2)) == 0
    assert chebyshev((-1, -1), (1, 1)) == 2


def test_move_cursor_stays_within_range() -> None:
    state = _state_at((5, 5), range=2)
    assert state.move_cursor(1, 0) is True
    assert state.cursor == (6, 5)
    assert state.move_cursor(1, 0) is True
    assert state.cursor == (7, 5)
    # Stepping out of range is rejected and the cursor does not move.
    assert state.move_cursor(1, 0) is False
    assert state.cursor == (7, 5)


def test_set_cursor_respects_range() -> None:
    state = _state_at((5, 5), range=2)
    assert state.set_cursor(6, 6) is True
    assert state.cursor == (6, 6)
    assert state.set_cursor(10, 10) is False
    assert state.cursor == (6, 6)


def test_zero_range_locks_cursor_on_origin() -> None:
    state = _state_at((4, 4), range=0)
    assert state.move_cursor(1, 0) is False
    assert state.move_cursor(0, 1) is False
    assert state.cursor == (4, 4)


def test_confirm_rejects_out_of_range_target() -> None:
    state = _state_at((0, 0), range=1)
    # Force the cursor out of range to simulate a stale state.
    state.cursor = (5, 5)
    action, refusal = state.confirm(build_tiny_map())
    assert action is None
    assert refusal == "Target out of range."


def test_confirm_rejects_predicate_failure() -> None:
    state = _state_at(
        (2, 2),
        range=3,
        predicate=lambda world, x, y, origin: False,
    )
    state.set_cursor(3, 2)
    action, refusal = state.confirm(build_tiny_map())
    assert action is None
    assert refusal == "Invalid target."


def test_confirm_emits_action_when_predicate_passes() -> None:
    world = build_tiny_map()
    player = add_actor(world, 2, 2)
    enemy = add_enemy(world, 3, 2)
    state = _state_at(
        (2, 2),
        range=3,
        predicate=lambda world, x, y, origin: True,
        on_confirm=lambda cell: AttackAttempt(actor=player, target=enemy),
    )
    state.set_cursor(3, 2)
    action, refusal = state.confirm(world)
    assert refusal is None
    assert isinstance(action, AttackAttempt)
    assert action.target == enemy


def test_confirm_returns_double_none_when_builder_returns_none() -> None:
    """A predicate-passing on_confirm that returns None signals 'silent cancel'."""

    state = _state_at((1, 1), range=2, on_confirm=lambda cell: None)
    state.set_cursor(2, 1)
    action, refusal = state.confirm(build_tiny_map())
    assert action is None
    assert refusal is None


# ---------------------------------------------------------------------
# Predicate library tests
# ---------------------------------------------------------------------


def test_any_visible_tile_uses_los() -> None:
    width, height = 9, 5
    tiles = [[FLOOR for _ in range(width)] for _ in range(height)]
    for y in range(1, height - 1):
        tiles[y][4] = VERTICAL_WALL
    world = World(width=width, height=height, tiles=tiles)
    # Visible: same side as origin.
    assert any_visible_tile(world, 3, 2, (2, 2)) is True
    # Wall itself: visible (LOS ray ends on it).
    assert any_visible_tile(world, 4, 2, (2, 2)) is True
    # Behind the wall: not visible.
    assert any_visible_tile(world, 6, 2, (2, 2)) is False


def test_any_visible_entity_requires_entity_on_cell() -> None:
    world = build_tiny_map()
    add_actor(world, 2, 2)
    enemy = add_enemy(world, 3, 2)
    assert any_visible_entity(world, 3, 2, (2, 2)) is True
    # Empty floor tile: predicate fails.
    assert any_visible_entity(world, 4, 2, (2, 2)) is False
    _ = enemy


def test_hostile_entity_predicate_rejects_party() -> None:
    world = build_tiny_map()
    add_actor(world, 2, 2, faction="player")
    add_enemy(world, 3, 2)
    add_actor(world, 4, 2, name="ally", faction="player", glyph="#")
    # Hostile enemy across the floor: predicate passes.
    assert hostile_entity(world, 3, 2, (2, 2)) is True
    # Allied actor: predicate fails.
    assert hostile_entity(world, 4, 2, (2, 2)) is False


def test_make_visible_predicate_uses_observer_los() -> None:
    width, height = 9, 5
    tiles = [[FLOOR for _ in range(width)] for _ in range(height)]
    for y in range(1, height - 1):
        tiles[y][4] = VERTICAL_WALL
    world = World(width=width, height=height, tiles=tiles)
    observer = add_actor(world, 2, 2)
    predicate = make_visible_predicate(observer, radius=8)
    assert predicate(world, 3, 2, (2, 2)) is True
    assert predicate(world, 6, 2, (2, 2)) is False


# ---------------------------------------------------------------------
# App-level integration tests
# ---------------------------------------------------------------------


def _begin_smoke_targeting(app, **overrides) -> tuple[TargetingState, list]:
    """Open targeting with a permissive any-tile predicate.

    The state ``on_confirm`` defaults to a callback that records the
    confirmed cell into a returned list so tests can assert that
    confirm fired without dispatching a real action. Tests that need a
    real action override the callback explicitly.
    """

    actor = app.active_actor()
    origin = app.world.positions.require(actor)
    confirmed: list[tuple[int, int]] = []

    def _on_confirm(cell):
        confirmed.append(cell)
        return None  # silent confirm — no action dispatched

    state = TargetingState(
        origin=(origin.x, origin.y),
        cursor=(origin.x, origin.y),
        range=overrides.pop("range", 5),
        on_confirm=overrides.pop("on_confirm", _on_confirm),
        predicate=overrides.pop("predicate", any_tile),
        label=overrides.pop("label", "Target a tile (range 5)"),
    )
    app.begin_targeting(state)
    return state, confirmed


def test_begin_targeting_sets_mode_and_emits_label() -> None:
    app = create_app()
    app.ui_mode = UIMode.play
    state, _ = _begin_smoke_targeting(app, label="Target a tile (range 6)")
    assert app.ui_mode is UIMode.targeting
    assert app.targeting is state
    assert state.previous_mode is UIMode.play
    assert app.messages.current == "Target a tile (range 6)"


def test_targeting_does_not_advance_turn() -> None:
    """Entering targeting must not consume any action resource."""

    app = create_app()
    app.ui_mode = UIMode.play
    before_round = app.world.clock.rounds
    before_seconds = app.world.clock.elapsed_seconds
    before_actor = app.active_actor()
    _begin_smoke_targeting(app)
    # Move the cursor a couple of cells.
    app.handle_key(ord("l"))
    app.handle_key(ord("j"))
    assert app.world.clock.rounds == before_round
    assert app.world.clock.elapsed_seconds == before_seconds
    assert app.active_actor() == before_actor
    assert app.ui_mode is UIMode.targeting


def test_cursor_movement_does_not_move_party() -> None:
    app = create_app()
    app.ui_mode = UIMode.play
    actor = app.active_actor()
    origin = app.world.positions.require(actor)
    before = (origin.x, origin.y)
    _begin_smoke_targeting(app)
    app.handle_key(ord("l"))
    app.handle_key(ord("j"))
    # Party member still at the original tile.
    after = app.world.positions.require(actor)
    assert (after.x, after.y) == before
    # Cursor moved by (1, 1).
    assert app.targeting is not None
    assert app.targeting.cursor == (before[0] + 1, before[1] + 1)


def test_cursor_clamps_to_range() -> None:
    app = create_app()
    app.ui_mode = UIMode.play
    _begin_smoke_targeting(app, range=2)
    # Walk the cursor east three times; only the first two should land.
    for _ in range(3):
        app.handle_key(ord("l"))
    assert app.targeting is not None
    origin = app.targeting.origin
    assert app.targeting.cursor == (origin[0] + 2, origin[1])


def test_cancel_targeting_restores_previous_mode_and_consumes_no_action() -> None:
    app = create_app()
    app.ui_mode = UIMode.play
    before_round = app.world.clock.rounds
    _, confirmed = _begin_smoke_targeting(app)
    app.handle_key(27)  # ESC
    assert app.ui_mode is UIMode.play
    assert app.targeting is None
    assert app.world.clock.rounds == before_round
    # The on_confirm callback never fired.
    assert confirmed == []


def test_confirm_exits_modal_and_records_cell() -> None:
    app = create_app()
    app.ui_mode = UIMode.play
    _, confirmed = _begin_smoke_targeting(app, range=3)
    app.handle_key(ord("l"))
    app.handle_key(ord("l"))
    origin = app.targeting.origin if app.targeting is not None else (0, 0)
    # Press Enter to confirm.
    app.handle_key(10)
    # No targeting state left; mode restored.
    assert app.ui_mode is UIMode.play
    assert app.targeting is None
    # The confirm callback received the cursor's final cell.
    assert confirmed == [(origin[0] + 2, origin[1])]


def test_confirm_out_of_range_keeps_modal_open() -> None:
    app = create_app()
    app.ui_mode = UIMode.play
    state, _ = _begin_smoke_targeting(app, range=1)
    # Force the cursor out of range to simulate an illegal commit.
    state.cursor = (state.origin[0] + 5, state.origin[1])
    app.handle_key(10)  # Enter
    assert app.ui_mode is UIMode.targeting
    assert app.targeting is state
    assert app.messages.current == "Target out of range."


def test_confirm_dispatches_action_through_resolver() -> None:
    """A real action returned by on_confirm flows through resolve_action.

    We install a reaction hook on the M46 resolver and assert it fires
    after confirm — that's the canonical signal that confirm went
    through ``app.resolve_action`` rather than constructing effects in
    place.
    """

    app = create_app()
    app.ui_mode = UIMode.play
    actor = app.active_actor()
    seen: list = []

    assert app.action_resolver is not None
    app.action_resolver.add_reaction(lambda attempt: (seen.append(attempt.original.action) or []))

    state = TargetingState(
        origin=(0, 0),
        cursor=(0, 0),
        range=1,
        on_confirm=lambda cell: AttackAttempt(actor=actor, target=actor),
        predicate=lambda world, x, y, origin: True,
    )
    app.begin_targeting(state)
    app.handle_key(10)
    assert app.ui_mode is UIMode.play
    assert any(isinstance(action, AttackAttempt) for action in seen)


def test_targeting_state_is_not_persisted_on_game_state() -> None:
    """GameState (M16 save) carries no targeting fields."""

    app = create_app()
    app.ui_mode = UIMode.play
    _begin_smoke_targeting(app)
    payload = app.game_state.to_dict(include_world=False)
    # No targeting key anywhere in the serialised aggregate.
    assert "targeting" not in payload
    # UI mode is the only place targeting could leak; tests open the
    # modal and confirm/cancel before serialising in real flows.
    assert payload["ui_mode"] in {UIMode.play.value, UIMode.targeting.value}


def test_targeting_observation_reports_modal_and_actions() -> None:
    """The structured observation surfaces the targeting modal."""

    from src.ui.observation import observe

    app = create_app()
    app.ui_mode = UIMode.play
    _begin_smoke_targeting(app)
    snapshot = observe(app)
    assert snapshot.modal is not None
    assert snapshot.modal.kind == "targeting"
    assert "targeting.confirm" in snapshot.available_actions
    assert "targeting.cancel" in snapshot.available_actions


def test_visible_predicate_against_live_world() -> None:
    """``make_visible_predicate`` honours the M19 vision walker."""

    width, height = 9, 5
    tiles = [[FLOOR for _ in range(width)] for _ in range(height)]
    for y in range(1, height - 1):
        tiles[y][4] = VERTICAL_WALL
    world = World(width=width, height=height, tiles=tiles)
    observer = add_actor(world, 2, 2)
    memory = PartyMemory()
    VisionSystem(radius=8).tick(world, [observer], memory)
    predicate = make_visible_predicate(observer, radius=8)
    # In-LOS tile: passes.
    assert predicate(world, 3, 2, (2, 2)) is True
    # Behind wall: rejected.
    assert predicate(world, 6, 2, (2, 2)) is False


# ---------------------------------------------------------------------
# make_spell_target_predicate — bug #100 / #101 regressions
# ---------------------------------------------------------------------


def test_spell_target_predicate_rejects_caster_self_for_damage() -> None:
    """Bug #100: damage spells must not accept the caster's own tile.

    Enter-Enter on the spell menu used to confirm the cursor sitting on
    the caster — Magic Missile would kill itself.
    """

    world = build_tiny_map()
    caster = add_actor(world, 2, 2)
    predicate = make_spell_target_predicate(
        caster, radius=6, require_hostile=True, allow_self_target=False
    )
    assert predicate(world, 2, 2, (2, 2)) is False


def test_spell_target_predicate_accepts_caster_when_self_target_allowed() -> None:
    """Healing spells (allow_self_target=True) may target the caster."""

    world = build_tiny_map()
    caster = add_actor(world, 2, 2)
    predicate = make_spell_target_predicate(
        caster, radius=6, require_hostile=False, allow_self_target=True
    )
    assert predicate(world, 2, 2, (2, 2)) is True


def test_spell_target_predicate_rejects_friendly_for_damage_spell() -> None:
    """Bug #101: damage spells must not be allowed to strike party allies."""

    world = build_tiny_map()
    caster = add_actor(world, 2, 2, faction="player")
    add_actor(world, 3, 2, name="ally", faction="player", glyph="#")
    predicate = make_spell_target_predicate(
        caster, radius=6, require_hostile=True, allow_self_target=False
    )
    assert predicate(world, 3, 2, (2, 2)) is False


def test_spell_target_predicate_accepts_hostile_for_damage_spell() -> None:
    """The standard happy path: a hostile in LOS is a legal damage target."""

    world = build_tiny_map()
    caster = add_actor(world, 2, 2, faction="player")
    add_enemy(world, 4, 2)
    predicate = make_spell_target_predicate(
        caster, radius=6, require_hostile=True, allow_self_target=False
    )
    assert predicate(world, 4, 2, (2, 2)) is True


def test_spell_target_predicate_accepts_ally_for_friendly_spell() -> None:
    """Cure Wounds-style spells should accept an ally tile."""

    world = build_tiny_map()
    caster = add_actor(world, 2, 2, faction="player")
    add_actor(world, 3, 2, name="ally", faction="player", glyph="#")
    predicate = make_spell_target_predicate(
        caster, radius=6, require_hostile=False, allow_self_target=True
    )
    assert predicate(world, 3, 2, (2, 2)) is True


def test_spell_target_predicate_rejects_hostile_for_friendly_spell() -> None:
    """Healing spells should not be aimed at hostile creatures."""

    world = build_tiny_map()
    caster = add_actor(world, 2, 2, faction="player")
    add_enemy(world, 4, 2)
    predicate = make_spell_target_predicate(
        caster, radius=6, require_hostile=False, allow_self_target=True
    )
    assert predicate(world, 4, 2, (2, 2)) is False


def test_spell_target_predicate_rejects_empty_tile() -> None:
    """A tile with no combat-statted entity is not a valid spell target."""

    world = build_tiny_map()
    caster = add_actor(world, 2, 2, faction="player")
    predicate = make_spell_target_predicate(
        caster, radius=6, require_hostile=True, allow_self_target=False
    )
    assert predicate(world, 4, 2, (2, 2)) is False
