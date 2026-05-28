"""Awareness queries: who knows about whom, and who is hostile.

This is a pure-function query service over ``World`` state. It is the seam
that future milestones extend:

- M19 (vision / LOS) will narrow ``is_aware_of`` to actually-visible targets.
- M23 (stealth / perception) will add perception checks on top of LOS.
- M28 (faction relations) will replace the party-vs-rest hostility rule with
  a richer faction model (allies, neutrals, town guards, summons).

For now the rules match the legacy ``App._hostiles_in_sight`` behavior
exactly:

- A target is "hostile" if it has a faction different from the observer's
  faction *and* has combat stats (the same predicate the movement and
  turn systems already use for "should bumping into this start combat?").
- An observer is "aware of" any target that is alive and positioned. There
  is no LOS or distance constraint yet; today the engine treats the whole
  world as effectively in sight, mirroring the prior implementation.
- ``hostiles_requiring_battle`` answers "should the game be in turn-based
  combat mode right now?". It collects every alive, positioned, combat-
  statted entity whose faction is not in the party's faction set.
"""

from src.core.entity import EntityId
from src.core.world import World


def is_alive(world: World, entity: EntityId) -> bool:
    """True if ``entity`` is positioned in the world and not at 0 HP."""
    if not world.positions.has(entity):
        return False
    stats = world.combat_stats.get(entity)
    return stats is None or stats.hit_points > 0


def is_aware_of(world: World, observer: EntityId, target: EntityId) -> bool:
    """True if ``observer`` should be considered to know about ``target``.

    Today: any alive, positioned entity is "in sight" of any observer.
    This is intentionally permissive — it matches the pre-refactor behavior
    where the engine scans the entire world for hostiles. M19 will narrow
    this to actually-visible targets, and M23 will layer perception on top.
    The ``observer`` parameter is accepted now so callers and tests already
    pass it in; future LOS work will use the observer's position/facing.
    """
    if observer == target:
        return False
    if not world.positions.has(observer):
        return False
    return is_alive(world, target)


def is_hostile_to(world: World, observer: EntityId, target: EntityId) -> bool:
    """True if ``observer`` treats ``target`` as an enemy.

    Mirrors the existing rule used by movement bumps and AI: different
    factions, and target has combat stats. Either party lacking a faction
    means "not hostile" (e.g. inert scenery, doors). Self is never hostile.
    """
    if observer == target:
        return False
    observer_faction = world.factions.get(observer)
    target_faction = world.factions.get(target)
    if observer_faction is None or target_faction is None:
        return False
    if observer_faction.value == target_faction.value:
        return False
    return world.combat_stats.has(target)


def hostiles_requiring_battle(world: World, party: list[EntityId]) -> list[EntityId]:
    """Return every alive, positioned, combat-statted entity hostile to the party.

    Matches the legacy ``_hostiles_in_sight`` semantics: a non-party faction
    with combat stats and positive HP. Returns a list (callers that only
    need a boolean can truthiness-check it). Order follows the world's
    ``combat_stats`` iteration order so results are deterministic per world.
    """
    party_factions = {
        faction.value
        for entity in party
        if (faction := world.factions.get(entity)) is not None
    }
    hostiles: list[EntityId] = []
    for entity, stats in world.combat_stats.values.items():
        if stats.hit_points <= 0 or not world.positions.has(entity):
            continue
        faction = world.factions.get(entity)
        if faction is not None and faction.value not in party_factions:
            hostiles.append(entity)
    return hostiles
