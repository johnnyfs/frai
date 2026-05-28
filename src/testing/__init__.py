"""Playtest harness package (M37/M38).

The :mod:`src.testing` package hosts the headless harness an agentic
playtester uses to drive the App without spinning up a real curses
screen. The public surface is :class:`~src.testing.harness.PlaytestHarness`
plus the :class:`~src.testing.scenarios.Scenario` registry that M38
populates via :mod:`src.testing.fixtures`.

Importing the package transitively imports the fixtures subpackage,
which is what populates :data:`SCENARIOS` with the M38 catalog at
process startup. Direct ``import src.testing.scenarios`` callers
(older tests) still get the same registry — the import side effect
fires regardless of which submodule the caller reaches first as long
as ``src.testing`` itself has been touched in the process.
"""

from src.testing.harness import PlaytestHarness, PredicateAssertionError
from src.testing.scenarios import SCENARIOS, Scenario

# Import for the side effect of registering the M38 catalog. Placed at
# the bottom so the typed exports above resolve cleanly even if a
# downstream fixture has an import-time error — the failure surfaces as
# the fixture's own ImportError rather than blowing up the harness.
from src.testing import fixtures as _fixtures  # noqa: F401 — registers scenarios

__all__ = [
    "PlaytestHarness",
    "PredicateAssertionError",
    "Scenario",
    "SCENARIOS",
]
