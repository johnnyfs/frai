"""Shelter zones and rest configuration (M34).

A :class:`ShelterZone` is a rectangular area on the map that explicitly
permits rest. The game NEVER infers "safe to rest" from terrain; only
zones flagged here unlock short / long rests. This is a content-side
decision: a wilderness glade is rest-friendly because the map author
placed a zone there, not because :data:`~src.map.tiles.FOREST` happens
to be "outdoors".

Design constraints
------------------

- **Pure data.** :class:`ShelterZone` is a small dataclass with
  primitives. ``contains(x, y)`` is the only behaviour; everything else
  is read by :mod:`src.systems.rest_system` and
  :mod:`src.systems.zone_system`.
- **Save-friendly.** :meth:`to_dict` / :meth:`from_dict` round-trip
  cleanly through JSON. ``uses_remaining`` is preserved across saves so
  a consumable shelter (e.g. a one-night-only inn key) is honored.
- **Separation of concerns.** This module defines the data shape;
  :mod:`src.systems.rest_system` enforces rest permission/cost/risk;
  :mod:`src.systems.zone_system` emits entry/exit messages.
- **Stable ids.** Each zone has a ``zone_id`` string so the zone-
  occupancy tracker (M34) and save files reference zones by id rather
  than by index into a list. New zones should pick a unique slug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RestPermission(str, Enum):
    """Which rest kinds a zone allows.

    - ``NONE``: no rest at all (e.g. a forbidden temple anteroom that
      still wants to emit entry text).
    - ``SHORT_ONLY``: short rest permitted; long rest refused.
    - ``LONG_ONLY``: long rest permitted; short rest refused (rare).
    - ``BOTH``: both rest kinds permitted (typical inn / tavern room).
    """

    NONE = "none"
    SHORT_ONLY = "short_only"
    LONG_ONLY = "long_only"
    BOTH = "both"

    def allows_short(self) -> bool:
        return self in (RestPermission.SHORT_ONLY, RestPermission.BOTH)

    def allows_long(self) -> bool:
        return self in (RestPermission.LONG_ONLY, RestPermission.BOTH)


class RestRisk(str, Enum):
    """How risky resting in a zone is.

    - ``NONE``: rest is uninterrupted.
    - ``ENCOUNTER_CHECK``: the rest system rolls a single random
      encounter check; on a failure (low roll) the rest is interrupted
      and a generic interruption message is emitted. M34 keeps the roll
      simple; M14/M15 will replace it with proper encounter decks.
    - ``FORBIDDEN``: rest is refused outright even if the permission
      flag would otherwise allow it. Use for narrative chokepoints
      that should still emit entry text (e.g. "you sense unseen
      eyes — sleep would be folly here").
    """

    NONE = "none"
    ENCOUNTER_CHECK = "encounter_check"
    FORBIDDEN = "forbidden"


class RestKind(str, Enum):
    """What the player is attempting."""

    SHORT = "short"
    LONG = "long"


@dataclass(slots=True)
class ShelterZone:
    """A rectangular rest-permitting area on the map.

    The zone is identified by ``zone_id`` (unique slug) and lives on the
    world as a member of :class:`~src.core.world.World.shelter_zones`.
    The rectangle is half-open in neither axis — :meth:`contains`
    treats both bounds as inclusive, matching how
    :class:`~src.world.content.skeleton.Rect` treats its own bounds.

    ``cost`` is in gold pieces deducted from the active actor's
    inventory on a successful rest. ``requirements`` is a free-form list
    of item ids that must be in any party member's inventory (today the
    rest system only checks the active actor, but the field is reserved
    for "party has a tent" style content). ``uses_remaining`` is
    ``None`` for unlimited shelters and an integer counter otherwise;
    each successful rest decrements it by 1 and a shelter at 0 refuses
    further attempts.
    """

    zone_id: str
    left: int
    top: int
    width: int
    height: int
    rest_permission: RestPermission = RestPermission.BOTH
    rest_risk: RestRisk = RestRisk.NONE
    entry_message: str = ""
    exit_message: str = ""
    cost: int = 0
    requirements: tuple[str, ...] = ()
    uses_remaining: int | None = None
    label: str = ""

    @property
    def right(self) -> int:
        return self.left + self.width - 1

    @property
    def bottom(self) -> int:
        return self.top + self.height - 1

    def contains(self, x: int, y: int) -> bool:
        """True iff (x, y) is inside the zone (inclusive on both bounds)."""

        return self.left <= x <= self.right and self.top <= y <= self.bottom

    def consume_use(self) -> bool:
        """Decrement ``uses_remaining`` by one.

        Returns ``True`` when the decrement succeeded (or the zone has
        no use cap), ``False`` when the zone was already exhausted. The
        rest system calls this only after every other check has passed,
        so a refusal here is purely "out of uses".
        """

        if self.uses_remaining is None:
            return True
        if self.uses_remaining <= 0:
            return False
        self.uses_remaining -= 1
        return True

    # -- serialization --------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "zone_id": self.zone_id,
            "left": int(self.left),
            "top": int(self.top),
            "width": int(self.width),
            "height": int(self.height),
            "rest_permission": self.rest_permission.value,
            "rest_risk": self.rest_risk.value,
            "entry_message": self.entry_message,
            "exit_message": self.exit_message,
            "cost": int(self.cost),
            "requirements": list(self.requirements),
            "uses_remaining": self.uses_remaining,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ShelterZone":
        permission_raw = payload.get("rest_permission", RestPermission.BOTH.value)
        try:
            permission = RestPermission(permission_raw)
        except ValueError:
            permission = RestPermission.BOTH
        risk_raw = payload.get("rest_risk", RestRisk.NONE.value)
        try:
            risk = RestRisk(risk_raw)
        except ValueError:
            risk = RestRisk.NONE
        uses_raw = payload.get("uses_remaining")
        uses = int(uses_raw) if uses_raw is not None else None
        return cls(
            zone_id=str(payload.get("zone_id", "")),
            left=int(payload.get("left", 0)),
            top=int(payload.get("top", 0)),
            width=int(payload.get("width", 0)),
            height=int(payload.get("height", 0)),
            rest_permission=permission,
            rest_risk=risk,
            entry_message=str(payload.get("entry_message", "")),
            exit_message=str(payload.get("exit_message", "")),
            cost=int(payload.get("cost", 0)),
            requirements=tuple(str(item) for item in payload.get("requirements", ())),
            uses_remaining=uses,
            label=str(payload.get("label", "")),
        )


@dataclass(slots=True)
class ShelterZoneRegistry:
    """All shelter zones in a world, addressable by zone id.

    A separate list (rather than a ``ComponentStore`` keyed by entity)
    because zones are map data, not entities — they have no position,
    presentation, or other entity-flavoured components. The registry
    survives save/load via :meth:`to_dict` / :meth:`from_dict`.
    """

    zones: list[ShelterZone] = field(default_factory=list)

    def add(self, zone: ShelterZone) -> None:
        if any(existing.zone_id == zone.zone_id for existing in self.zones):
            raise ValueError(f"Duplicate shelter zone id: {zone.zone_id!r}")
        self.zones.append(zone)

    def by_id(self, zone_id: str) -> ShelterZone | None:
        for zone in self.zones:
            if zone.zone_id == zone_id:
                return zone
        return None

    def at(self, x: int, y: int) -> ShelterZone | None:
        """First zone containing (x, y), or ``None``.

        Zones are not expected to overlap; if they do, registration
        order wins. The map author should keep them disjoint.
        """

        for zone in self.zones:
            if zone.contains(x, y):
                return zone
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"zones": [zone.to_dict() for zone in self.zones]}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ShelterZoneRegistry":
        registry = cls()
        for entry in payload.get("zones", []):
            registry.zones.append(ShelterZone.from_dict(entry))
        return registry


@dataclass(slots=True)
class ZoneOccupancyState:
    """Tracks which shelter zone the party leader currently occupies.

    The :class:`~src.systems.zone_system.ZoneSystem` updates this on
    every tick so entry/exit messages fire exactly once per transition.
    Stored on the world so save/load preserves the "we already greeted
    the party" state — without it, loading a save inside a zone would
    re-emit the entry text.
    """

    current_zone_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"current_zone_id": self.current_zone_id}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ZoneOccupancyState":
        zone_id = payload.get("current_zone_id")
        return cls(current_zone_id=str(zone_id) if zone_id is not None else None)


__all__ = [
    "RestKind",
    "RestPermission",
    "RestRisk",
    "ShelterZone",
    "ShelterZoneRegistry",
    "ZoneOccupancyState",
]
