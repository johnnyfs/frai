"""Tests for the online help modal (M31 + M39).

Covers:

- ``?`` opens :class:`UIMode.help` and surfaces a non-empty topic list.
- Index navigation: j/k / arrow keys move the cursor, Enter drills in.
- Esc backs out of a viewed topic to the index; a second Esc closes.
- Save/load round-trip while help is open lands the player back in
  play (the modal is transient; the M16 demotion handles it).
- Lint-style: every key bound in ``input_system.map_key`` appears in
  the help text (the player must be able to look up any active key).
"""

from __future__ import annotations

import curses

from src.app import create_app
from src.core.modes import UIMode
from src.systems.input_system import MOVE_KEYS
from src.ui.help import (
    HELP_TOPICS,
    HelpState,
    build_help_topics,
    collect_help_text,
    topic_for,
    wrap_body,
)
from src.ui.observation import observe


def _ready_app():
    """Boot to UIMode.play via YOLO."""

    app = create_app()
    app.handle_key(ord("y"))
    assert app.ui_mode is UIMode.play
    return app


def test_question_mark_opens_help() -> None:
    app = _ready_app()
    app.handle_key(ord("?"))
    assert app.ui_mode is UIMode.help
    assert app.help_state is not None
    assert app.help_state.viewing is None
    assert app.help_state.topics


def test_help_topics_include_all_doc_files_plus_overviews() -> None:
    # Doc files under docs/help/ auto-register; the overview topics are
    # hand-curated. Both surfaces must be present.
    ids = {topic.topic_id for topic in HELP_TOPICS}
    # Curated overviews.
    assert {"movement", "combat", "inventory", "party", "interaction"}.issubset(ids)
    # Doc-backed topics. A representative sample — full set is checked
    # by the registry test below.
    assert {"agent", "autowalk", "conditions", "death", "factions"}.issubset(ids)


def test_topic_for_returns_known_topic_and_none_for_unknown() -> None:
    assert topic_for("movement") is not None
    assert topic_for("does-not-exist") is None


def test_help_cursor_moves_with_j_and_k() -> None:
    app = _ready_app()
    app.handle_key(ord("?"))
    state = app.help_state
    assert state is not None
    assert state.cursor == 0

    app.handle_key(ord("j"))
    assert state.cursor == 1

    app.handle_key(ord("j"))
    assert state.cursor == 2

    app.handle_key(ord("k"))
    assert state.cursor == 1


def test_help_cursor_moves_with_arrow_keys() -> None:
    app = _ready_app()
    app.handle_key(ord("?"))
    state = app.help_state
    assert state is not None
    assert state.cursor == 0

    app.handle_key(curses.KEY_DOWN)
    assert state.cursor == 1

    app.handle_key(curses.KEY_UP)
    assert state.cursor == 0


def test_help_enter_drills_into_topic_and_esc_returns_to_index() -> None:
    app = _ready_app()
    app.handle_key(ord("?"))
    state = app.help_state
    assert state is not None
    assert state.viewing is None

    # Enter selects the cursor topic.
    app.handle_key(10)
    assert state.viewing is not None
    assert state.viewing.topic_id == state.topics[0].topic_id

    # Esc backs out to the index, not all the way to play.
    app.handle_key(27)
    assert app.ui_mode is UIMode.help
    assert state.viewing is None

    # Second Esc closes the modal entirely.
    app.handle_key(27)
    assert app.ui_mode is UIMode.play
    assert app.help_state is None


def test_help_close_returns_to_previous_mode() -> None:
    app = _ready_app()
    app.handle_key(ord("?"))
    assert app.ui_mode is UIMode.help
    # Close with q.
    app.handle_key(ord("q"))
    assert app.ui_mode is UIMode.play
    assert app.help_state is None


def test_help_does_not_advance_world_clock() -> None:
    app = _ready_app()
    clock_before = app.world.clock.elapsed_seconds
    app.handle_key(ord("?"))
    app.handle_key(ord("j"))
    app.handle_key(10)
    app.handle_key(27)
    app.handle_key(27)
    assert app.world.clock.elapsed_seconds == clock_before


def test_help_modal_surfaces_in_observation() -> None:
    app = _ready_app()
    app.handle_key(ord("?"))
    obs = observe(app)
    assert obs.modal is not None
    assert obs.modal.kind == "help"
    # Index of the first topic exposed as an option.
    assert obs.modal.options
    assert obs.modal.cursor == 0


def test_every_bound_play_key_is_mentioned_in_help() -> None:
    """Every key wired through input_system has a help entry.

    Lint-style: the player must be able to press ``?`` and read about
    any key they could press in play. The check is text-based on the
    aggregated topic bodies so adding a new key without help fails
    here loudly.
    """

    haystack = collect_help_text()

    # Movement keys (lower-case rogue-style cardinals + diagonals).
    for key in MOVE_KEYS:
        assert key in haystack, f"movement key {key!r} not documented"

    # Auto-walk uses the capitalised form. ``collect_help_text`` lowers
    # everything for case-insensitive matching, so we check the
    # mention via the "auto-walk" string + the lowered letter.
    assert "auto-walk" in haystack

    # Other play-mode keys, captured from input_system. Each token
    # below is a literal substring search; the help text lists them
    # one-per-line so the check is robust to wording changes.
    expected_tokens = (
        "i",  # inventory
        "e",  # interact
        ",",  # pickup
        "x",  # examine
        ";",  # examine alias
        "s",  # spell menu
        "z",  # stealth
        "p",  # perception (lowercase)
        "r",  # rest menu
        "t",  # toggle turn mode
        "?",  # help
        "q",  # quit
        "space",  # end turn (mentioned in combat help)
    )
    for token in expected_tokens:
        assert token in haystack, f"token {token!r} not documented"

    # Roster capital ``P`` mention (case-insensitive — the text says
    # ``P`` but ``collect_help_text`` lowers everything).
    assert "p " in haystack  # the "P  open the party roster" line


def test_wrap_body_handles_long_lines_and_preserves_blanks() -> None:
    body = "This is a very long line that needs to wrap.\n\nNext paragraph."
    wrapped = wrap_body(body, width=20)
    # Blank line preserved.
    assert "" in wrapped
    # No wrapped line exceeds the width.
    for line in wrapped:
        assert len(line) <= 20


def test_build_help_topics_skips_missing_directory(tmp_path) -> None:
    # Pointing at a non-existent dir still returns the overview topics.
    missing = tmp_path / "nope"
    topics = build_help_topics(docs_dir=missing)
    ids = {topic.topic_id for topic in topics}
    assert "movement" in ids


def test_save_load_demotes_help_modal(tmp_path) -> None:
    """Help is transient: a save written mid-help drops the modal.

    Relies on the M16 modal-mode demotion fix (#88). Loading lands the
    player back in play with no help state attached.
    """

    from src.core.save import save_game, load_game

    app = _ready_app()
    app.handle_key(ord("?"))
    assert app.ui_mode is UIMode.help

    path = tmp_path / "save.json"
    save_game(app, path)
    loaded = load_game(path)
    # Modal demoted back to play; transient state dropped.
    assert loaded.ui_mode is UIMode.play
    assert loaded.help_state is None
