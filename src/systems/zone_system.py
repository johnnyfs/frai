"""Zone system (M34).

Watches the party leader's tile and emits the entry / exit message of
the :class:`~src.core.shelter.ShelterZone` they cross into or out of.
The system is intentionally separate from
:mod:`src.systems.rest_system` so a player can know "I'm in a shelter
zone" without also pressing the rest key, and so a future map author
can drop messaging-only zones (cutscenes, atmosphere) without coupling
to rest semantics.

Wiring
------

:func:`tick_zone_transitions` is called every time the party leader's
position MAY have changed:

- After an explore-mode move (single step or autowalk iteration).
- After an active-mode move (turn-based move that doesn't bump
  hostiles).
- After an effect batch that included a :class:`~src.core.effects.MoveEntity`
  on the active actor (because party displacement teleports
  companions / leader directly).

The function reads the current zone, compares against
:class:`~src.core.shelter.ZoneOccupancyState.current_zone_id`, and
emits at most one entry message and at most one exit message per tick.
On a save/load round-trip the recorded ``current_zone_id`` keeps the
party from re-greeting the entry text — important because
``observe()`` may otherwise see two identical entries during a session
replay.

Save-friendliness
-----------------

State lives on :class:`~src.core.world.World.zone_occupancy` so a save
captures it. The zone system itself is stateless — every call reads
the current position and the stored zone id, decides what messages to
emit, and writes the new zone id back to the world.
"""

from __future__ import annotations

from typing import Any

from src.core.effects import Effect, EmitMessage


def tick_zone_transitions(app: Any) -> list[Effect]:
    """Detect a zone transition for the party leader; return messages.

    Returns the (possibly empty) list of :class:`EmitMessage` effects
    the caller should apply. The list always has at most two entries:
    one exit (when leaving the previous zone) followed by one entry
    (when arriving in the new zone). Either may be absent — a move
    inside the same zone returns ``[]``.

    The :class:`ZoneOccupancyState` on the world is updated in-place to
    reflect the new zone id. Subsequent calls with no further movement
    return ``[]`` because the state already matches the position.
    """

    world = app.world
    registry = world.shelter_zones
    if not registry.zones:
        # Fast path: no zones in this world. Clear the occupancy state
        # so a previously-set value (e.g. from a save with zones that
        # have since been removed) doesn't leak.
        if world.zone_occupancy.current_zone_id is not None:
            world.zone_occupancy.current_zone_id = None
        return []

    leader = _leader_entity(app)
    if leader is None:
        return []
    position = world.positions.get(leader)
    if position is None:
        return []

    current_zone = registry.at(position.x, position.y)
    current_id = current_zone.zone_id if current_zone is not None else None
    previous_id = world.zone_occupancy.current_zone_id
    if current_id == previous_id:
        return []

    effects: list[Effect] = []
    if previous_id is not None:
        previous_zone = registry.by_id(previous_id)
        if previous_zone is not None and previous_zone.exit_message:
            effects.append(EmitMessage(previous_zone.exit_message))
    if current_zone is not None and current_zone.entry_message:
        effects.append(EmitMessage(current_zone.entry_message))
    world.zone_occupancy.current_zone_id = current_id
    return effects


def _leader_entity(app: Any):
    """Return the party leader entity used for zone occupancy.

    The leader is the first party member (``app.party.members[0]``).
    The active actor changes round-by-round in turn-based play; using
    the leader keeps zone occupancy stable across companion rotations
    so a shelter never re-greets the party when control passes from
    the player to a companion.
    """

    party = getattr(app, "party", None)
    if party is None:
        return None
    members = getattr(party, "members", [])
    if not members:
        return None
    return members[0]


__all__ = ["tick_zone_transitions"]
