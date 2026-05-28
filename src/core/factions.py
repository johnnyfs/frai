"""Faction identifiers and relation matrix (M28).

Before M28 the engine used a single ``Faction(value: str)`` component and
the hostility predicate boiled down to "different string == hostile if
the target has combat stats". That worked while the only factions in
play were ``"player"`` and ``"enemy"``, but it can't express:

- A town NPC who is neutral to the party (e.g. a baker or a guard who
  hasn't been provoked). With the old rule, the moment they got a
  faction string different from ``player`` the engine flipped into
  turn-based mode.
- A wildlife creature that ignores the party until it's aggro'd (e.g.
  a deer that flees but never attacks).
- A shopkeeper who turns hostile *only after a specific event* (theft,
  assault) — and only to the party, not to other town NPCs.
- A companion or summon whose faction is "follow the owner's faction"
  even if the owner is the player.

This module owns the data model for those scenarios:

- ``FactionId``: the small catalog of canonical faction identities.
  Stored as strings on the ``Faction`` component for save-compat with
  pre-M28 saves; resolved into the enum at query time. Unknown strings
  resolve to ``FactionId.UNKNOWN`` and fall back to the legacy
  "different string == hostile" rule, so older saves keep working.
- ``Relation``: the three relation kinds — ``hostile``, ``neutral``,
  ``friendly``. The hostility predicate is "Relation == hostile".
- ``RelationTable``: the symmetric ``(FactionId, FactionId) -> Relation``
  mapping. Built from a small list of declarations rather than a 2D
  array so the default table reads naturally and additions stay terse.
- ``AggroOverride`` (component): per-entity overrides that *only* affect
  the holder's relation to a given target faction. Used for the
  "shopkeeper hates the party after they stole" case: the shopkeeper
  carries ``AggroOverride(target=PLAYER_PARTY, relation=HOSTILE)`` —
  other town NPCs remain neutral.

The awareness system (``src/systems/awareness_system.py``) is the only
caller that should resolve relations directly. Other systems go through
``is_hostile_to`` / ``hostiles_requiring_battle`` so the rule stays in
one place.

Seams
-----

- M23 (stealth/perception) will gate awareness on visibility + perception
  checks before consulting the relation table.
- M13 (recruitment) hands a recruited NPC ``Faction(PLAYER_PARTY)``.
- Summoned creatures use ``Faction(summoner=<owner-entity>)``; the
  awareness system resolves their relation through the summoner so a
  summon never accidentally turns hostile to its caster.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FactionId(str, Enum):
    """The canonical faction identities the engine knows about.

    Stored as ``str`` so the existing ``Faction(value=str)`` component
    keeps round-tripping through save files unchanged; new code should
    construct factions via these members rather than raw strings.
    """

    PLAYER_PARTY = "player_party"
    TOWN = "town"
    DUNGEON = "dungeon"
    WILDLIFE = "wildlife"
    UNKNOWN = "unknown"

    @classmethod
    def from_value(cls, value: str | None) -> "FactionId":
        """Resolve a string to a known ``FactionId``.

        Pre-M28 saves used ``"player"`` and ``"enemy"``; those are
        aliased to the closest matching member so legacy fixtures and
        save files keep working without a migration step. Unknown
        strings resolve to ``UNKNOWN`` and the relation table falls
        back to the legacy "different string == hostile" rule.
        """
        if value is None:
            return cls.UNKNOWN
        alias = _LEGACY_ALIASES.get(value)
        if alias is not None:
            return alias
        try:
            return cls(value)
        except ValueError:
            return cls.UNKNOWN


_LEGACY_ALIASES: dict[str, FactionId] = {
    "player": FactionId.PLAYER_PARTY,
    "enemy": FactionId.DUNGEON,
}


class Relation(str, Enum):
    HOSTILE = "hostile"
    NEUTRAL = "neutral"
    FRIENDLY = "friendly"


@dataclass(slots=True)
class AggroOverride:
    """Per-entity relation override against a target faction.

    Lives in ``World.aggro_overrides`` as a list per entity. The
    awareness system consults overrides *before* the global relation
    table, so a shopkeeper carrying
    ``AggroOverride(target=PLAYER_PARTY, relation=HOSTILE)`` will
    attack the party even though the global ``town ↔ player_party``
    relation is neutral.

    Overrides are directional: an aggro'd shopkeeper sees the party as
    hostile, but the party's relation back to the shopkeeper is still
    decided by the shopkeeper's *faction* (TOWN). That's enough to drive
    turn-based mode (any hostile-to-party actor triggers combat) without
    flipping the entire town against the party. M13 / M14 quest scripts
    will mass-apply overrides to whole town crowds if a story beat
    demands it.
    """

    target: FactionId
    relation: Relation


@dataclass(slots=True)
class AggroOverrideList:
    """A bag of overrides attached to a single entity.

    Wrapped in a dataclass (rather than a bare ``list``) so the
    ``ComponentStore`` shape on ``World`` matches every other component
    type and serialization stays straightforward.
    """

    overrides: list[AggroOverride] = field(default_factory=list)

    def relation_to(self, target: FactionId) -> Relation | None:
        """Return the override relation for ``target`` if one exists."""
        for entry in self.overrides:
            if entry.target == target:
                return entry.relation
        return None

    def set(self, target: FactionId, relation: Relation) -> None:
        """Replace or add the override for ``target``."""
        for entry in self.overrides:
            if entry.target == target:
                entry.relation = relation
                return
        self.overrides.append(AggroOverride(target=target, relation=relation))

    def clear(self, target: FactionId) -> None:
        self.overrides = [entry for entry in self.overrides if entry.target != target]


# ---------------------------------------------------------------------------
# RelationTable
# ---------------------------------------------------------------------------


_DEFAULT_RELATION = Relation.NEUTRAL


@dataclass(slots=True)
class RelationTable:
    """Symmetric ``(FactionId, FactionId) -> Relation`` mapping.

    Stored as a single dict keyed on a canonicalized tuple so adding a
    declaration via ``set`` automatically applies in both directions.
    Lookups fall back to a configured default (``Relation.NEUTRAL`` by
    default) when no entry exists; ``UNKNOWN`` is handled separately by
    the awareness system, which preserves the legacy "different string
    == hostile" rule for unrecognized factions.
    """

    _entries: dict[tuple[FactionId, FactionId], Relation] = field(default_factory=dict)
    default: Relation = _DEFAULT_RELATION

    @staticmethod
    def _key(a: FactionId, b: FactionId) -> tuple[FactionId, FactionId]:
        return (a, b) if a.value <= b.value else (b, a)

    def set(self, a: FactionId, b: FactionId, relation: Relation) -> None:
        self._entries[self._key(a, b)] = relation

    def relation(self, a: FactionId, b: FactionId) -> Relation:
        if a == b:
            # A faction is always friendly to itself; explicit entries
            # can override (e.g. infighting wildlife) by calling
            # ``set(faction, faction, NEUTRAL)``.
            entry = self._entries.get(self._key(a, b))
            if entry is not None:
                return entry
            return Relation.FRIENDLY
        return self._entries.get(self._key(a, b), self.default)


def default_relation_table() -> RelationTable:
    """The M28 default faction relations.

    | Pair                                | Relation  |
    | ----                                | --------- |
    | ``player_party`` ↔ ``player_party`` | friendly  |
    | ``player_party`` ↔ ``town``         | neutral   |
    | ``player_party`` ↔ ``dungeon``      | hostile   |
    | ``player_party`` ↔ ``wildlife``     | neutral   |
    | ``town`` ↔ ``dungeon``              | hostile   |
    | ``town`` ↔ ``wildlife``             | neutral   |
    | ``dungeon`` ↔ ``wildlife``          | neutral   |
    | ``dungeon`` ↔ ``dungeon``           | friendly  |
    | ``town`` ↔ ``town``                 | friendly  |
    | ``wildlife`` ↔ ``wildlife``         | neutral   |

    Wildlife is intentionally neutral with itself — most wild creatures
    don't run in unified packs the way a faction implies. Aggro between
    individual wild creatures is driven by per-entity ``AggroOverride``s
    rather than the global table.
    """
    table = RelationTable(default=Relation.NEUTRAL)
    table.set(FactionId.PLAYER_PARTY, FactionId.PLAYER_PARTY, Relation.FRIENDLY)
    table.set(FactionId.PLAYER_PARTY, FactionId.TOWN, Relation.NEUTRAL)
    table.set(FactionId.PLAYER_PARTY, FactionId.DUNGEON, Relation.HOSTILE)
    table.set(FactionId.PLAYER_PARTY, FactionId.WILDLIFE, Relation.NEUTRAL)
    table.set(FactionId.TOWN, FactionId.TOWN, Relation.FRIENDLY)
    table.set(FactionId.TOWN, FactionId.DUNGEON, Relation.HOSTILE)
    table.set(FactionId.TOWN, FactionId.WILDLIFE, Relation.NEUTRAL)
    table.set(FactionId.DUNGEON, FactionId.DUNGEON, Relation.FRIENDLY)
    table.set(FactionId.DUNGEON, FactionId.WILDLIFE, Relation.NEUTRAL)
    table.set(FactionId.WILDLIFE, FactionId.WILDLIFE, Relation.NEUTRAL)
    return table


# Module-level singleton — relation rules are pure data and not per-world
# today. M29+ may move this onto ``World`` once content can mutate it; for
# now keeping it a constant keeps the predicate cheap and side-effect-free.
DEFAULT_RELATION_TABLE = default_relation_table()
