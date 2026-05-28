"""M38 playtest scenario fixtures.

Importing this package registers a curated set of scenarios in the
M37 :data:`src.testing.scenarios.SCENARIOS` registry. Each fixture is a
small, deterministic builder that exercises one major subsystem
(combat, AI behaviors, doors+locks, traps, containers, shops, vision,
autowalk). The agentic playtester (``/playtest``) picks targets from
this catalog; CI integration tests instantiate them via
``PlaytestHarness(scenario_name=...)``.

The fixtures are *thin*: they reuse the standard ``create_app`` party
construction and bolt scenario-specific entities onto a small purpose-
built room. Builders return a new ``App`` (replacing the default
``create_app`` world) so the harness's seed → world contract stays
clean — the wider overworld is never built for a fixture run.

Scenarios registered here
-------------------------

- ``combat_simple``    — two kobolds adjacent; forces turn-based.
- ``combat_archer``    — one ranged kobold archer at distance.
- ``door_locked``      — locked door blocking the only exit.
- ``trap_armed``       — armed trap on the tile in front of the party.
- ``container_loot``   — closed chest with weapon + gold.
- ``shop_basic``       — shopkeeper with a small inventory + gold.
- ``vision_corridor``  — long corridor; LOS limits and autowalk gating.
- ``hostile_far``      — hostile parked outside LOS; autowalk-reveals.
- ``open_terrain``     — empty room large enough to test autowalk bound.

Determinism note
----------------

The builders themselves are pure functions of the App they receive
(which the harness builds with ``random.Random(seed)``). Loot/AI rolls
remain deterministic because the same seed is propagated through
``create_app`` before the builder runs.
"""

from src.testing.fixtures import scenarios as _scenarios

# Re-export the catalog so callers can introspect what's registered
# without importing the private module.
CATALOG: tuple[str, ...] = _scenarios.CATALOG

__all__ = ["CATALOG"]
