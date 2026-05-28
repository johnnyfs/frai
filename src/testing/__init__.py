"""Playtest harness package (M37).

The :mod:`src.testing` package hosts the headless harness an agentic
playtester uses to drive the App without spinning up a real curses
screen. The public surface is :class:`~src.testing.harness.PlaytestHarness`
plus the small :class:`~src.testing.scenarios.Scenario` registry that
M38 will populate.
"""

from src.testing.harness import PlaytestHarness, PredicateAssertionError
from src.testing.scenarios import SCENARIOS, Scenario

__all__ = [
    "PlaytestHarness",
    "PredicateAssertionError",
    "Scenario",
    "SCENARIOS",
]
