"""Playtest scenario registry (M37 stub, populated by M38).

A :class:`Scenario` is a tiny declarative description of a playable
starting position used by the :class:`PlaytestHarness`. The registry
``SCENARIOS`` is empty in this milestone — M38 wires the first
fixtures into place. The seam lives here today so the harness API can
stabilise (``PlaytestHarness(scenario_name=...)``) without waiting for
fixture content.

A scenario is *purely* a starting condition:

- a ``builder`` callable that mutates a freshly-created
  :class:`~src.app.App` (e.g. teleports the party, spawns NPCs, sets
  the UI mode) — or returns a brand-new ``App`` if the default world
  is too restrictive.
- ``expected_entities`` / ``expected_exits`` are documentation /
  predicate scaffolding — fixture authors record what an agent should
  observe at ``t=0`` so harness tests can detect drift.

The harness consults this registry through
:func:`get_scenario`; passing an unknown name raises
:class:`KeyError`. Passing ``None`` (the default) means "no fixture —
use the standard ``create_app`` starting world".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from src.app import App


# Builders receive the App produced by ``create_app`` and either mutate
# it in place or return a replacement. Returning ``None`` (the common
# case) leaves the harness using the same App instance; returning a
# fresh ``App`` lets a fixture replace the world wholesale.
ScenarioBuilder = Callable[..., "App | None"]
"""Scenario builders accept ``(app)`` or ``(app, rng)``; the harness
passes the seeded ``random.Random`` as a second positional argument
when the builder accepts it. Use ``rng`` to seed any fixture-local
randomness so harness ``seed`` reaches builders."""


@dataclass(frozen=True, slots=True)
class Scenario:
    """A named starting condition for the playtest harness.

    ``builder`` is the only field the harness reads at runtime. The
    other fields exist so fixture authors can document what a
    well-behaved agent should observe at t=0 — harness tests in M38
    will pivot on them.
    """

    name: str
    builder: ScenarioBuilder
    description: str = ""
    expected_entities: tuple[str, ...] = ()
    expected_exits: tuple[str, ...] = ()
    seed: int = 0
    # Extra metadata stays open-ended on purpose: M38 will iterate on
    # the shape and we'd rather not lock in fields prematurely.
    extras: dict[str, object] = field(default_factory=dict)


# Registry. Empty in M37 — M38 will populate it. The harness reads
# through :func:`get_scenario` so callers can swap in a custom registry
# in tests without monkey-patching this module-level dict.
SCENARIOS: dict[str, Scenario] = {}


def register(scenario: Scenario) -> Scenario:
    """Add a scenario to the global registry.

    Returns the scenario so call sites can use ``register(...)`` as a
    one-liner. Duplicate names raise :class:`ValueError` — a fixture
    file should never register the same name twice.
    """
    if scenario.name in SCENARIOS:
        raise ValueError(f"Scenario '{scenario.name}' already registered.")
    SCENARIOS[scenario.name] = scenario
    return scenario


def get_scenario(name: str) -> Scenario:
    """Return the :class:`Scenario` registered under ``name``.

    Raises :class:`KeyError` with a helpful message listing available
    scenarios when the name is unknown — better than the generic dict
    lookup error so harness users see what's actually wired up.
    """
    try:
        return SCENARIOS[name]
    except KeyError as exc:
        available = ", ".join(sorted(SCENARIOS)) or "(none)"
        raise KeyError(
            f"Unknown scenario '{name}'. Registered scenarios: {available}."
        ) from exc
