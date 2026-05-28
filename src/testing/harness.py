"""Headless playtest harness (M37).

The harness wraps an :class:`~src.app.App` constructed via the standard
:func:`~src.app.create_app` factory and exposes a small, stable surface
the agentic playtester (and CI integration tests) drive instead of the
real curses loop. The harness has three jobs:

1. **Stand up an App without curses.** ``create_app`` is already
   curses-free; the harness merely ensures no screen-touching code path
   runs during construction. The :mod:`src.ui.screen` module is *not*
   imported transitively here — see the comment block below.
2. **Bind the supporting modules** an agent expects to be ergonomic to
   call: :func:`~src.ui.observation.observe`,
   :func:`~src.ui.script_runner.run_script`, the M33 debug command
   path, and :mod:`src.core.save`. Each is exposed as a one-line method
   so a playtest session reads top-to-bottom.
3. **Be deterministic.** ``seed`` flows into ``random.Random(seed)``
   and is the only source of stochasticity ``create_app`` accepts. Two
   harnesses with the same ``seed`` and same script must produce equal
   :class:`~src.ui.observation.Observation` sequences. Tests assert
   this end-to-end.

Why a class (and not a free function) ?
---------------------------------------

A playtest session is inherently stateful — the agent calls
``run(...)``, ``observe()``, ``save()``, ``load()`` many times and
each call mutates the underlying App. A class lets us own that App and
hand back the M35 ``Observation`` after every command without leaking
the App around the call site.

Curses isolation
----------------

``create_app`` does not touch curses (see
:func:`src.app.create_app`). The :mod:`src.app` module *does* import
``curses`` at the top of the file — but importing the module is fine
on any platform Python supports. The harness never instantiates a
``Screen`` or calls into ``curses.wrapper``. Tests assert this by
constructing a harness inside the standard pytest process (no TTY
attached) and confirming no exception is raised.

Save/load
---------

:func:`~src.core.save.save_game` and :func:`~src.core.save.load_game`
already understand the App container. The harness wraps them so the
on-disk path is consistent (``$tmp/<scenario_or_default>.json``) and
so ``load(path)`` swaps ``self.app`` in place — the agent's other
methods keep working without re-binding.
"""

from __future__ import annotations

import os
import random
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from src.core.save import load_game, save_game
from src.testing.scenarios import Scenario, get_scenario
from src.ui.observation import Observation, observe
from src.ui.script_runner import CommandOutcome, run_script

if TYPE_CHECKING:
    from src.app import App


_DEV_ENV_VAR = "FRAI_DEV"


class PredicateAssertionError(AssertionError):
    """Raised by :meth:`PlaytestHarness.assert_predicate` on failure.

    Subclasses :class:`AssertionError` so pytest treats it as a normal
    assertion failure (full traceback, useful message) while still
    being catchable by callers that want to distinguish harness
    predicate failures from generic ``assert`` failures elsewhere in a
    test.
    """


class PlaytestHarness:
    """Headless wrapper around an :class:`~src.app.App` for agent
    playthroughs and integration tests.

    Parameters
    ----------
    scenario_name:
        Name of a fixture registered in
        :data:`src.testing.scenarios.SCENARIOS`. The M37 registry is
        empty; M38 will populate it. Passing ``None`` (the default)
        uses the standard ``create_app`` starting world.
    seed:
        Seeds the loot / interaction RNG. The default ``0`` keeps
        tests deterministic without callers needing to think about it.
        Exposed as :attr:`seed` so a test that hits a bug can reproduce
        with the same value.
    dev_mode:
        When True, ``FRAI_DEV`` is forced on for the lifetime of the
        process (no automatic teardown — pytest's ``monkeypatch.setenv``
        is the right tool when you need scoped behaviour). Required
        for :meth:`debug` to actually run commands; otherwise the
        debug system refuses with a message.
    """

    __slots__ = ("app", "seed", "scenario", "_dev_mode_set_here")

    def __init__(
        self,
        scenario_name: str | None = None,
        seed: int = 0,
        dev_mode: bool = True,
    ) -> None:
        # Lazy-import the App factory so the harness module itself is
        # importable on systems whose ``src.app`` chain hasn't been
        # touched yet. ``create_app`` itself is curses-free.
        from src.app import create_app

        self.seed: int = seed

        # Flip the dev-mode env flag *before* we touch the App so any
        # initialization that wants to consult it sees the right value.
        # We deliberately only set it when ``dev_mode`` is True so a
        # harness configured with ``dev_mode=False`` can sanity-check
        # the disabled path without leaking state from elsewhere.
        self._dev_mode_set_here = False
        if dev_mode and os.environ.get(_DEV_ENV_VAR) is None:
            os.environ[_DEV_ENV_VAR] = "1"
            self._dev_mode_set_here = True

        # Determinism contract: every RNG that touches App construction
        # must be reproducible from ``seed``. ``create_app`` threads
        # ``rng`` into loot, interaction, and the YOLO sheet roll — the
        # three stochastic surfaces an agentic playtester cares about.
        rng = random.Random(seed)
        app = create_app(rng=rng)

        scenario: Scenario | None = None
        if scenario_name is not None:
            scenario = get_scenario(scenario_name)
            replacement = scenario.builder(app)
            if replacement is not None:
                app = replacement
        self.scenario: Scenario | None = scenario
        self.app: "App" = app

    # ------------------------------------------------------------------
    # Core agent surface
    # ------------------------------------------------------------------

    def run(self, script: str) -> list[CommandOutcome]:
        """Execute an M36 command script against the wrapped App.

        Equivalent to ``run_script(harness.app, script)``. Returns the
        per-command outcomes the agent should consume (each carries a
        fresh :class:`~src.ui.observation.Observation` snapshot, so a
        harness user doesn't need to call :meth:`observe` again unless
        they want a no-input read).
        """
        return run_script(self.app, script)

    def observe(self) -> Observation:
        """Return a fresh :class:`~src.ui.observation.Observation`.

        Calling :meth:`observe` is pure — it never mutates the App.
        Useful as the first read at session start (before any
        commands have run) and after a debug/load/save call that the
        run loop doesn't otherwise snapshot.
        """
        return observe(self.app)

    def debug(self, command: str) -> str:
        """Run a single M33 debug command and return its message.

        The debug system always emits at least one ``EmitMessage``
        (confirmation, refusal, or parse error) so the return value is
        always populated. When dev mode is off the harness still
        returns the refusal text — no exception is raised so a test
        can assert the disabled path.

        The message returned is whatever ``app.messages.current`` holds
        after the command settles. For multi-page output (rare in
        debug) the first page is returned; the rest sits in
        ``app.messages.pending`` and will surface on the next
        :meth:`observe`.
        """
        before = self.app.messages.current
        self.app.run_debug_command(command)
        after = self.app.messages.current
        # If the debug command didn't change the current message (a
        # silent debug effect like ``dump`` followed by an EmitMessage
        # that wraps to the same text) prefer the existing text rather
        # than returning an empty string.
        return after if after else before

    def save(self, path: Path | None = None) -> Path:
        """Serialise the App to ``path`` and return the path written.

        When ``path`` is omitted, a per-harness temporary file is used
        so tests can call ``harness.save()`` without managing scratch
        directories. The on-disk format is whatever
        :func:`~src.core.save.save_game` writes (JSON, schema v1
        today).
        """
        if path is None:
            tmpdir = Path(tempfile.gettempdir()) / "frai-playtest-harness"
            tmpdir.mkdir(parents=True, exist_ok=True)
            path = tmpdir / f"harness-seed{self.seed}.json"
        return save_game(self.app, path)

    def load(self, path: Path) -> None:
        """Replace the wrapped App with one loaded from ``path``.

        The seed is preserved for traceability — note that loading a
        save written under a *different* seed silently overrides the
        in-memory ``self.seed`` to reflect what the on-disk RNG state
        actually represents. This avoids the trap of "harness says
        seed=0 but the RNG is half-way through a different stream".
        """
        self.app = load_game(path)

    # ------------------------------------------------------------------
    # Inspection helpers
    # ------------------------------------------------------------------

    def assert_predicate(
        self,
        fn: Callable[["App"], bool],
        msg: str = "",
    ) -> None:
        """Evaluate ``fn(self.app)`` and raise on a falsey result.

        The assertion is deliberately framed in terms of the App rather
        than the Observation because predicates often want raw world
        access (component stores, faction tables) that doesn't survive
        the snapshot projection.

        When ``fn`` returns falsey the harness raises
        :class:`PredicateAssertionError` with ``msg`` (or a fallback
        message that names the predicate's qualname) so pytest's
        assertion rewriting still produces a useful trace.
        """
        try:
            result = fn(self.app)
        except Exception as exc:  # pragma: no cover - bubbled with context
            raise PredicateAssertionError(
                f"Predicate {_callable_label(fn)} raised: {exc!r}. {msg}".rstrip()
            ) from exc
        if not result:
            label = _callable_label(fn)
            detail = msg or f"Predicate {label} returned a falsey value."
            raise PredicateAssertionError(detail)

    def messages(self) -> list[str]:
        """Return the current + pending message-log lines.

        Mirrors the projection that :func:`~src.ui.observation.observe`
        does for ``recent_messages``, but accessible without a full
        Observation. Useful in tests asserting that a debug command
        emitted a specific banner.
        """
        state = self.app.messages
        collected: list[str] = []
        if state.current:
            collected.append(state.current)
        collected.extend(state.pending)
        return collected

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def load_scenario(self, name: str) -> None:
        """Re-build the wrapped App against the scenario named ``name``.

        Equivalent to ``self.__init__(scenario_name=name, seed=self.seed)``
        but keeps ``self`` identity stable — useful in tests that hold
        a reference to the harness and want to swap fixtures without
        re-creating it.
        """
        from src.app import create_app

        scenario = get_scenario(name)
        rng = random.Random(self.seed)
        app = create_app(rng=rng)
        replacement = scenario.builder(app)
        if replacement is not None:
            app = replacement
        self.app = app
        self.scenario = scenario


def _callable_label(fn: Callable[..., Any]) -> str:
    """Best-effort name for ``fn`` used in failure messages.

    ``functools.partial`` instances and lambdas hide useful metadata
    behind nondescript ``__qualname__`` values, so we fall back to
    ``repr`` if the standard attributes don't resolve to a string.
    """
    qualname = getattr(fn, "__qualname__", None)
    if qualname:
        return qualname
    name = getattr(fn, "__name__", None)
    if name:
        return name
    return repr(fn)
