"""Tests for the M37 playtest harness.

The harness is a thin wrapper around ``create_app`` + M35 observation +
M36 script runner + M16 save/load. These tests assert that the wrapper
behaves the way an agentic playtester (and CI integration test) will
rely on:

- Construction does not require curses and does not raise.
- Same seed + same script produces an identical observation sequence.
- Repeat-move scripts (``5h``) produce the right number of outcomes
  (or fewer when interrupted by terrain).
- ``save()`` + ``load()`` round-trip preserves observable state.
- ``debug()`` runs an M33 command when dev mode is on.
- ``assert_predicate`` raises with the provided message on a falsey
  predicate.
- The scenario registry refuses unknown names.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.modes import UIMode
from src.testing.harness import PlaytestHarness, PredicateAssertionError
from src.testing.scenarios import SCENARIOS, Scenario, get_scenario, register


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_harness_init_produces_working_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare ``PlaytestHarness()`` should give an App that can answer
    ``observe()`` without crashing. This is the smoke test the M37
    spec calls for and the contract every fixture builds on."""
    monkeypatch.delenv("FRAI_DEV", raising=False)
    harness = PlaytestHarness(dev_mode=False)
    obs = harness.observe()
    # Start screen is the default UI for ``create_app``.
    assert obs.mode["ui_mode"] == UIMode.start.value
    assert obs.world_time.seconds == 0


def test_harness_init_enables_dev_mode_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``dev_mode=True`` (the default) flips ``FRAI_DEV`` on so debug
    commands can run."""
    monkeypatch.delenv("FRAI_DEV", raising=False)
    PlaytestHarness()
    assert os_env_get("FRAI_DEV") == "1"


def test_harness_seed_is_exposed_for_repro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FRAI_DEV", raising=False)
    harness = PlaytestHarness(seed=42, dev_mode=False)
    assert harness.seed == 42


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_same_seed_and_script_gives_identical_observation_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two harnesses constructed with the same seed and driven by the
    same script must produce equal observations at every step. This is
    the bedrock guarantee an agentic playtester depends on for
    reproducible bug reports."""
    monkeypatch.delenv("FRAI_DEV", raising=False)
    harness_a = PlaytestHarness(seed=123, dev_mode=False)
    harness_b = PlaytestHarness(seed=123, dev_mode=False)
    harness_a.app.ui_mode = UIMode.play
    harness_b.app.ui_mode = UIMode.play
    script = "l;l;l"

    outs_a = harness_a.run(script)
    outs_b = harness_b.run(script)

    assert len(outs_a) == len(outs_b) == 3
    for left, right in zip(outs_a, outs_b):
        # to_dict() flattens the dataclass for value equality without
        # depending on dataclass __eq__ being honoured by every nested
        # type (it is, today, but pinning value-equality is the more
        # durable contract).
        assert left.observation_after.to_dict() == right.observation_after.to_dict()


# ---------------------------------------------------------------------------
# Script execution
# ---------------------------------------------------------------------------


def test_run_produces_one_outcome_per_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-repeat script of N commands returns N outcomes. The M36
    runner already enforces this; we assert here so the harness wiring
    doesn't accidentally drop outcomes."""
    monkeypatch.delenv("FRAI_DEV", raising=False)
    harness = PlaytestHarness(dev_mode=False)
    harness.app.ui_mode = UIMode.play
    outcomes = harness.run("l;l;l;l;l")
    assert len(outcomes) == 5


def test_run_repeat_move_returns_one_outcome_with_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``5h`` is one *command* (a MoveCommand with repeat=5), so the
    harness returns one CommandOutcome whose ``steps_taken`` reflects
    how many tiles the walk actually traversed (capped by terrain)."""
    monkeypatch.delenv("FRAI_DEV", raising=False)
    harness = PlaytestHarness(dev_mode=False)
    harness.app.ui_mode = UIMode.play
    outcomes = harness.run("5h")
    assert len(outcomes) == 1
    # Steps may be 5 or fewer (walls / occupants); 5 is the upper
    # bound, 0 means the wall is immediately west, which is also
    # acceptable in some seeds. The point is the cap is honoured.
    assert 0 <= outcomes[0].steps_taken <= 5


# ---------------------------------------------------------------------------
# Save / load
# ---------------------------------------------------------------------------


def test_save_and_load_round_trip_preserves_player_position(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The standard M16 round-trip wrapped through the harness. We
    move the player one tile so the post-load position isn't trivially
    the spawn coordinates, then assert it survives a save/load."""
    monkeypatch.delenv("FRAI_DEV", raising=False)
    harness = PlaytestHarness(dev_mode=False)
    harness.app.ui_mode = UIMode.play
    harness.run("l")  # one step east, may or may not succeed
    before_pos = _player_pos(harness)

    path = tmp_path / "round-trip.json"
    written = harness.save(path)
    assert written == path

    harness.load(path)
    after_pos = _player_pos(harness)
    assert before_pos == after_pos


def test_save_default_path_writes_to_tempdir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``save()`` with no argument should land somewhere writable so
    quick-and-dirty sessions don't crash on missing parents."""
    monkeypatch.delenv("FRAI_DEV", raising=False)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    harness = PlaytestHarness(seed=7, dev_mode=False)
    path = harness.save()
    assert path.exists()
    assert "seed7" in path.name


# ---------------------------------------------------------------------------
# Debug commands
# ---------------------------------------------------------------------------


def test_debug_tp_moves_player_when_dev_mode_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``FRAI_DEV=1`` (which the default harness sets) the M33
    ``tp`` debug command should move the player."""
    monkeypatch.delenv("FRAI_DEV", raising=False)
    harness = PlaytestHarness()  # dev_mode=True by default
    harness.app.ui_mode = UIMode.play
    message = harness.debug("tp 5 5")
    assert "Teleported" in message
    assert _player_pos(harness) == (5, 5)


def test_debug_refuses_when_dev_mode_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``dev_mode=False`` must not silently flip the env var on. The
    debug system returns a refusal banner instead."""
    monkeypatch.delenv("FRAI_DEV", raising=False)
    harness = PlaytestHarness(dev_mode=False)
    harness.app.ui_mode = UIMode.play
    message = harness.debug("tp 5 5")
    assert "disabled" in message.lower()


# ---------------------------------------------------------------------------
# assert_predicate
# ---------------------------------------------------------------------------


def test_assert_predicate_passes_on_truthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FRAI_DEV", raising=False)
    harness = PlaytestHarness(dev_mode=False)
    # Identity check that won't depend on world geometry.
    harness.assert_predicate(lambda app: app.world is not None)


def test_assert_predicate_raises_with_message_on_falsey(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FRAI_DEV", raising=False)
    harness = PlaytestHarness(dev_mode=False)
    with pytest.raises(PredicateAssertionError, match="custom failure note"):
        harness.assert_predicate(lambda app: False, "custom failure note")


def test_assert_predicate_wraps_predicate_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a predicate raises (e.g. KeyError from a bad component
    access) the harness surfaces the error as a predicate failure so
    the test trace makes it obvious which assertion broke."""
    monkeypatch.delenv("FRAI_DEV", raising=False)
    harness = PlaytestHarness(dev_mode=False)

    def boom(_app: object) -> bool:
        raise KeyError("missing component")

    with pytest.raises(PredicateAssertionError, match="missing component"):
        harness.assert_predicate(boom)


# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------


def test_scenario_registry_is_empty_in_m37(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M38 will populate this; until then unknown scenario names must
    raise so a typo doesn't silently get the default world."""
    monkeypatch.delenv("FRAI_DEV", raising=False)
    # The global registry is empty in this milestone — guard against
    # cross-test pollution by snapshotting and restoring.
    with pytest.raises(KeyError, match="Unknown scenario"):
        PlaytestHarness(scenario_name="never-registered", dev_mode=False)


def test_scenario_registry_register_and_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The register/get path works end-to-end: a scenario builder mutates
    the App and the harness sees the change at t=0."""
    monkeypatch.delenv("FRAI_DEV", raising=False)
    name = "test-only-harness-fixture"
    # Clean up after ourselves so other tests don't see the fixture.
    if name in SCENARIOS:
        del SCENARIOS[name]

    def builder(app: object) -> None:
        app.ui_mode = UIMode.play  # type: ignore[attr-defined]
        return None

    register(Scenario(name=name, builder=builder))
    try:
        harness = PlaytestHarness(scenario_name=name, dev_mode=False)
        assert harness.scenario is not None
        assert harness.scenario.name == name
        assert harness.observe().mode["ui_mode"] == UIMode.play.value
    finally:
        SCENARIOS.pop(name, None)


def test_scenario_register_rejects_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FRAI_DEV", raising=False)
    name = "test-only-dup-fixture"
    if name in SCENARIOS:
        del SCENARIOS[name]
    register(Scenario(name=name, builder=lambda _app: None))
    try:
        with pytest.raises(ValueError, match="already registered"):
            register(Scenario(name=name, builder=lambda _app: None))
    finally:
        SCENARIOS.pop(name, None)


def test_get_scenario_lists_available_names_on_miss() -> None:
    """The KeyError raised by ``get_scenario`` should enumerate the
    registered names so a harness user spots a typo quickly."""
    with pytest.raises(KeyError, match="Registered scenarios"):
        get_scenario("never-registered-name")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _player_pos(harness: PlaytestHarness) -> tuple[int, int]:
    position = harness.app.world.positions.require(harness.app.player)
    return (position.x, position.y)


def os_env_get(name: str) -> str | None:
    """Tiny indirection so a test can stub os.environ if it ever needs
    to. Keeps the call site terse without importing ``os`` in every
    test."""
    import os

    return os.environ.get(name)
