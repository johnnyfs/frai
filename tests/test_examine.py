"""Integration tests for the M21 examine command.

These tests exercise the full open-examine -> move cursor -> confirm /
cancel flow through the App. The pure description composer is tested
in ``tests/test_descriptions.py``; the tests here check the wiring:

* ``x`` (and ``;``) opens a look-only targeting modal.
* Confirm emits description text without consuming any resource or
  advancing the world clock / turn.
* The memory-aware examine path surfaces "last seen" / "unknown"
  refusals as expected when the cursor walks off the visible set.
* The structured observation snapshot surfaces an ``examine=...``
  token so the M37 harness can read "what's at the cursor".
"""

from __future__ import annotations

from src.app import create_app
from src.core.actions import ExamineRequest
from src.core.modes import UIMode
from src.systems.input_system import map_key
from src.ui.observation import observe


def _open_examine(app, key: int = ord("x")) -> None:
    """Open the examine modal via the same key path as the player.

    Asserts the targeting modal is now active so subsequent test code
    can rely on ``app.targeting`` being non-None.
    """

    app.ui_mode = UIMode.play
    app.handle_key(key)
    assert app.ui_mode is UIMode.targeting
    assert app.targeting is not None


def test_x_key_maps_to_examine_request() -> None:
    """``x`` in play mode parses to an :class:`ExamineRequest`."""

    app = create_app()
    app.ui_mode = UIMode.play
    actor = app.active_actor()
    action = map_key(ord("x"), UIMode.play, actor)
    assert isinstance(action, ExamineRequest)
    assert action.actor == actor


def test_semicolon_also_opens_examine() -> None:
    """The legacy NetHack ``;`` alias parses to the same action."""

    app = create_app()
    app.ui_mode = UIMode.play
    actor = app.active_actor()
    action = map_key(ord(";"), UIMode.play, actor)
    assert isinstance(action, ExamineRequest)


def test_examine_opens_targeting_modal() -> None:
    """Pressing ``x`` enters :class:`UIMode.targeting` without an action."""

    app = create_app()
    before_round = app.world.clock.rounds
    before_seconds = app.world.clock.elapsed_seconds
    _open_examine(app)
    # Mode flipped, clock did not.
    assert app.ui_mode is UIMode.targeting
    assert app.world.clock.rounds == before_round
    assert app.world.clock.elapsed_seconds == before_seconds


def test_examine_does_not_advance_turn() -> None:
    """Opening + cursor moves + confirm does not consume any resource."""

    app = create_app()
    before_round = app.world.clock.rounds
    before_seconds = app.world.clock.elapsed_seconds
    _open_examine(app)
    # Move the cursor a few cells, then confirm.
    app.handle_key(ord("l"))
    app.handle_key(ord("j"))
    app.handle_key(10)  # Enter
    # Examine never advances the clock.
    assert app.world.clock.rounds == before_round
    assert app.world.clock.elapsed_seconds == before_seconds
    # Modal closed back to play.
    assert app.ui_mode is UIMode.play
    assert app.targeting is None


def test_examine_confirm_emits_description() -> None:
    """Confirm emits the terrain description in the message log."""

    app = create_app()
    _open_examine(app)
    app.handle_key(10)  # Enter
    # Some description text landed in the log. We don't pin the exact
    # phrasing here; the prose tests in tests/test_descriptions.py do.
    assert app.messages.current
    assert "You see" in app.messages.current or "last seen" in app.messages.current


def test_examine_cancel_does_not_emit_cancellation_banner() -> None:
    """Esc closes examine silently — no "Targeting cancelled." banner.

    The examine flow suppresses the cancel banner so the description
    text the player just read is not clobbered. Confirming the
    examine emits a description; then cancelling later doesn't
    overwrite it. (The open-banner clobber is expected — that's the
    examine label the player just chose to surface.)
    """

    app = create_app()
    _open_examine(app)
    # Confirm to emit description text.
    app.handle_key(10)
    description = app.messages.current
    assert "You see" in description or "don't know" in description
    # Re-open examine and immediately cancel. The previous description
    # text was clobbered by the new open-banner — that's expected —
    # but the cancel itself must NOT add a "Targeting cancelled."
    # banner (the cancel_message override is empty).
    app.handle_key(ord("x"))
    assert app.ui_mode is UIMode.targeting
    # While in the targeting modal the current message is the examine
    # label. Cancel.
    app.handle_key(27)
    assert app.ui_mode is UIMode.play
    # No "Targeting cancelled." was emitted — the previous label is
    # still the visible message.
    assert app.messages.current != "Targeting cancelled."


def test_examine_unknown_tile_reports_refusal() -> None:
    """A cursor on an unknown tile emits the "you don't know" refusal."""

    app = create_app()
    # Force the party memory to consider everything unknown.
    app.memory.visible = frozenset()
    app.memory.tiles.clear()
    _open_examine(app)
    # The cursor opens on the active actor's tile (which is unknown
    # because we just cleared memory). Confirm and check the refusal.
    app.handle_key(10)
    assert "don't know" in app.messages.current.lower()


def test_examine_remembered_tile_uses_last_seen_marker() -> None:
    """A cursor on a remembered tile shows the "last seen" marker."""

    from src.core.vision import RememberedTile

    app = create_app()
    actor = app.active_actor()
    origin = app.world.positions.require(actor)
    target = (origin.x + 3, origin.y)
    # Force the target into the remembered set: in memory tiles dict
    # but NOT in the visible frozenset.
    app.memory.visible = frozenset()
    app.memory.remember(target[0], target[1], RememberedTile(glyph="."))
    _open_examine(app)
    # Walk cursor to the remembered tile.
    app.handle_key(ord("l"))
    app.handle_key(ord("l"))
    app.handle_key(ord("l"))
    app.handle_key(10)  # Enter
    assert "last seen" in app.messages.current.lower()


def test_examine_creature_on_visible_tile_describes_creature() -> None:
    """Examining a tile with a hostile creature surfaces its name."""

    from src.core.combat import weapon_for_name
    from src.core.components import (
        CombatStats,
        Creature,
        Faction,
        Name,
        Position,
        Presentation,
    )

    app = create_app()
    actor = app.active_actor()
    origin = app.world.positions.require(actor)
    target = (origin.x + 1, origin.y)
    # Spawn a frog on the adjacent tile (which is in the visible set
    # because the active actor is right next to it).
    frog = app.world.create_entity()
    app.world.positions.add(frog, Position(target[0], target[1]))
    app.world.presentations.add(frog, Presentation(":"))
    app.world.names.add(frog, Name("frog"))
    app.world.factions.add(frog, Faction("enemy"))
    app.world.creatures.add(frog, Creature(kind="frog", attack_verb="bites"))
    app.world.combat_stats.add(
        frog,
        CombatStats(
            armor_class=10,
            hit_points=3,
            max_hit_points=3,
            strength=10,
            dexterity=10,
            constitution=10,
        ),
    )
    app.world.weapons.add(frog, weapon_for_name("dagger"))
    app.refresh_vision()
    _open_examine(app)
    # Walk cursor one tile east.
    app.handle_key(ord("l"))
    app.handle_key(10)
    text = app.messages.current
    # Concatenated pending pages: the wrapped message may have spilled
    # the creature name to a later page. Concatenate the full surface.
    text_full = text + " " + " ".join(app.messages.pending)
    assert "frog" in text_full


def test_examine_observation_surfaces_examine_token() -> None:
    """The structured observation surfaces an ``examine=...`` option.

    The M37 harness reads the modal options to learn "what's at the
    cursor" without separately confirming. This token mirrors the
    confirm-text so an agent doesn't need to advance the modal.
    """

    app = create_app()
    _open_examine(app)
    snapshot = observe(app)
    assert snapshot.modal is not None
    assert snapshot.modal.kind == "targeting"
    examine_options = [
        opt for opt in snapshot.modal.options if opt.startswith("examine=")
    ]
    assert examine_options
    # The token carries the same prose the confirm would emit.
    assert "You see" in examine_options[0] or "don't know" in examine_options[0]


def test_examine_does_not_change_active_actor() -> None:
    """Examine must not rotate the party turn."""

    app = create_app()
    before_actor = app.active_actor()
    _open_examine(app)
    app.handle_key(ord("l"))
    app.handle_key(10)
    assert app.active_actor() == before_actor


def test_examine_state_is_not_persisted_in_game_state() -> None:
    """``GameState.to_dict`` does not carry the examine cursor.

    Examine reuses targeting, and targeting is transient runtime state
    — the M16 save shape carries nothing about it.
    """

    app = create_app()
    _open_examine(app)
    payload = app.game_state.to_dict(include_world=False)
    assert "targeting" not in payload
    assert "examine" not in payload
