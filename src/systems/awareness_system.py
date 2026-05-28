"""Awareness queries: who knows about whom, and who is hostile.

This is a pure-function query service over ``World`` state. It is the
seam that future milestones extend:

- M19 (vision / LOS) narrowed ``is_aware_of`` to actually-visible
  targets via the party memory frontier (deferred from this module).
- M23 (stealth / perception) will add perception checks on top of LOS;
  hooking in here keeps the rule centralized.
- M28 (this milestone) replaced the party-vs-rest hostility rule with
  a proper faction relation table plus per-entity aggro overrides
  (see ``src/core/factions.py``).

The hostility predicate now reads:

1. If observer == target, not hostile.
2. If the target has no combat stats, not hostile (doors, signs).
3. Resolve each side's effective ``FactionId``, walking the
   ``Faction.summoner`` chain so a summon inherits its caster's
   faction.
4. Consult any ``AggroOverride`` on the observer first — overrides
   take precedence over the global table so "shopkeeper turned
   hostile" works without flipping the whole town.
5. Otherwise consult the global ``RelationTable``:
   - Known × known: table lookup.
   - At least one ``UNKNOWN``: fall back to the legacy "different
     string == hostile" rule. This keeps pre-M28 fixtures and saves
     using ad-hoc faction strings working unchanged.

``hostiles_requiring_battle`` is the entry point used by the App turn
controller to decide "are we in turn-based mode right now?". It scans
every alive, positioned, combat-statted entity and asks the same
predicate from the party's perspective.
"""

from src.core.components import Faction
from src.core.conditions import ConditionKind
from src.core.entity import EntityId
from src.core.factions import (
    DEFAULT_RELATION_TABLE,
    FactionId,
    Relation,
    RelationTable,
)
from src.core.stealth import AwarenessState
from src.core.world import World


def is_alive(world: World, entity: EntityId) -> bool:
    """True if ``entity`` is positioned in the world and not at 0 HP."""
    if not world.positions.has(entity):
        return False
    stats = world.combat_stats.get(entity)
    return stats is None or stats.hit_points > 0


def is_aware_of(world: World, observer: EntityId, target: EntityId) -> bool:
    """True if ``observer`` should be considered to know about ``target``.

    Resolution order (M23):

    1. Self / dead / unpositioned targets are never "aware".
    2. If the observer carries an :class:`AwarenessTracker` and its
       recorded state about ``target`` is ``AWARE`` -> True (the
       observer has been alerted, perception fired, etc.). This wins
       over visibility — an alerted guard remembers the intruder even
       behind a corner.
    3. If the target carries the ``hidden`` condition AND the observer
       is not already aware -> False (stealth wins).
    4. Otherwise, fall back to "alive and positioned" (the pre-M23
       semantics). This keeps the predicate permissive for hostiles
       that don't yet carry trackers — the M23 scope wires trackers
       on a per-content basis rather than retrofitting every existing
       enemy.

    The :class:`AwarenessTracker` lookup is per-observer, so two
    guards in the same room can disagree about an intruder. The
    ``hidden`` check is global today (a hidden actor is hidden from
    everyone who isn't already aware); a per-observer hidden bit is a
    follow-up.
    """
    if observer == target:
        return False
    if not world.positions.has(observer):
        return False
    if not is_alive(world, target):
        return False

    tracker = world.awareness_trackers.get(observer)
    if tracker is not None and tracker.state_of(target) is AwarenessState.AWARE:
        return True

    target_conditions = world.conditions.get(target)
    if target_conditions is not None and target_conditions.has(ConditionKind.HIDDEN):
        return False

    return True


def _effective_faction(world: World, entity: EntityId) -> Faction | None:
    """Walk the summoner chain to find the entity's effective faction.

    A summoned creature with ``Faction(summoner=caster)`` inherits the
    relation graph of its caster — including any ``AggroOverride``s on
    the caster — so a player-summoned elemental is never accidentally
    treated as hostile to the rest of the party. Cycles are guarded by
    a visited set; the chain bottoms out at the first ``Faction``
    without a ``summoner``.
    """
    visited: set[EntityId] = set()
    current = entity
    while True:
        if current in visited:
            return None
        visited.add(current)
        faction = world.factions.get(current)
        if faction is None:
            return None
        if faction.summoner is None:
            return faction
        owner = faction.summoner
        if not world.factions.has(owner):
            # Owner is gone (dismissed companion, dead summoner) — fall
            # back to the summon's own declared faction so the engine
            # still has a definite answer.
            return faction
        current = owner


def _effective_faction_id(world: World, entity: EntityId) -> FactionId:
    faction = _effective_faction(world, entity)
    if faction is None:
        return FactionId.UNKNOWN
    return FactionId.from_value(faction.value)


def _override_for(
    world: World, observer: EntityId, target_id: FactionId
) -> Relation | None:
    """Walk the observer's summoner chain looking for a matching override.

    Overrides apply to the entity that owns them *and* to anyone who
    inherits faction through it — that's what lets a player-summoned
    elemental treat a town that the player has aggro'd as hostile.
    Cycles are guarded the same way :func:`_effective_faction` does.
    """
    visited: set[EntityId] = set()
    current = observer
    while True:
        if current in visited:
            return None
        visited.add(current)
        overrides = world.aggro_overrides.get(current)
        if overrides is not None:
            relation = overrides.relation_to(target_id)
            if relation is not None:
                return relation
        faction = world.factions.get(current)
        if faction is None or faction.summoner is None:
            return None
        current = faction.summoner


def _resolve_relation(
    world: World,
    observer: EntityId,
    target: EntityId,
    table: RelationTable,
) -> Relation:
    """Resolve the relation the *observer* sees toward the *target*.

    Aggro overrides on the observer (and any summoner upstream of it)
    are consulted first. Then the global table. If either side is
    ``UNKNOWN``, fall back to the legacy "different raw string ==
    hostile" rule so pre-M28 fixtures keep working — this is the
    smallest change that lets ad-hoc faction strings coexist with the
    typed catalog.
    """
    observer_faction = _effective_faction(world, observer)
    target_faction = _effective_faction(world, target)
    if observer_faction is None or target_faction is None:
        return Relation.NEUTRAL
    target_id = FactionId.from_value(target_faction.value)
    override = _override_for(world, observer, target_id)
    if override is not None:
        return override
    observer_id = FactionId.from_value(observer_faction.value)
    if observer_id is FactionId.UNKNOWN or target_id is FactionId.UNKNOWN:
        # Legacy fallback: different raw faction strings are hostile,
        # same string is friendly. Matches pre-M28 semantics.
        if observer_faction.value == target_faction.value:
            return Relation.FRIENDLY
        return Relation.HOSTILE
    return table.relation(observer_id, target_id)


def is_hostile_to(
    world: World,
    observer: EntityId,
    target: EntityId,
    *,
    table: RelationTable = DEFAULT_RELATION_TABLE,
) -> bool:
    """True if ``observer`` treats ``target`` as an enemy.

    The predicate gates on combat stats so non-combatant entities
    (doors, signs, dropped items) never trigger combat even if they
    somehow ended up with a hostile faction. Self is never hostile.

    The optional ``table`` parameter is provided so tests can plug in a
    custom relation table without monkeypatching the module default.
    """
    if observer == target:
        return False
    if not world.combat_stats.has(target):
        return False
    return _resolve_relation(world, observer, target, table) is Relation.HOSTILE


def hostiles_requiring_battle(
    world: World,
    party: list[EntityId],
    *,
    table: RelationTable = DEFAULT_RELATION_TABLE,
) -> list[EntityId]:
    """Return every alive, positioned, combat-statted entity hostile to the party.

    "Hostile to the party" means *any* party member treats the entity
    as hostile — that's the rule that decides whether the app enters
    turn-based mode. A neutral town NPC therefore stays neutral until
    something flips them with an ``AggroOverride``; a dungeon monster
    triggers combat immediately. Order follows the world's
    ``combat_stats`` iteration order so results are deterministic per
    world.
    """
    hostiles: list[EntityId] = []
    for entity, stats in world.combat_stats.values.items():
        if stats.hit_points <= 0 or not world.positions.has(entity):
            continue
        if entity in party:
            continue
        for member in party:
            if is_hostile_to(world, member, entity, table=table):
                hostiles.append(entity)
                break
            # Also consider whether the candidate is hostile *toward*
            # any party member — an aggro'd shopkeeper sees the party
            # as hostile (via AggroOverride) even though the party's
            # base relation to the shopkeeper (town) is neutral.
            if is_hostile_to(world, entity, member, table=table):
                hostiles.append(entity)
                break
    return hostiles
