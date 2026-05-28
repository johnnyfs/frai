"""Stealth, noise, and perception primitives (M23).

This module defines the data types and pure functions that drive
stealth/perception. The actual rules (sneak action, perception action,
noise from attacks) live in their respective systems
(:mod:`src.systems.stealth_system`, :mod:`src.systems.combat_system`,
:mod:`src.systems.spell_system`); this module only owns the data
catalog and the noise-propagation helper.

Conceptual model
----------------

The engine distinguishes two related but distinct notions:

- **Visibility** (geometric): can a ray from observer to target reach
  the target without crossing a sight-blocker? This is M19 territory
  (`src.core.vision.compute_visible_tiles`).
- **Awareness** (cognitive): does the observer *know* about the target?
  An observer with a clear sightline to a hidden creature still isn't
  aware until perception fires; an observer that heard a noise can be
  *suspicious* without ever seeing the source.

Awareness is per-observer (not global). Two guards in the same room
might have different mental states about the same intruder.

Catalog
-------

- :class:`AwarenessState` — the three-state ramp ``unaware`` ->
  ``suspicious`` -> ``aware``. ``unaware`` is the default; downgrading
  back to ``unaware`` requires explicit action (the M23 scope only
  ramps upward).
- :class:`AwarenessTracker` — per-observer map of ``observer's mental
  state about target``. Lives as a component on hostiles (and any other
  actor that needs to "remember" who they've seen). Save-friendly.
- :class:`NoiseLevel` — tag on actions describing how loud they are.
  ``silent`` is anything the actor explicitly hides (sneak movement),
  ``quiet`` is the default for unremarkable actions (movement,
  examine), and ``loud`` covers attacks, shouting, and verbal-component
  spells.

Seams
-----

- :func:`propagate_noise` is the entry point combat / spells call to
  ramp nearby hostiles. The radius defaults to ``DEFAULT_NOISE_RADIUS``
  but callers may override (a thrown stone is louder than a footstep).
- Per-creature perception sensitivity / deafness modifiers are TODO
  follow-ups (issue 25 calls them out explicitly). For now every
  hostile receives the same ramp.
- The :class:`AwarenessTracker` store is plumbed into
  :mod:`src.core.world` so save/load and the dump tool pick it up
  automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.core.entity import EntityId


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class AwarenessState(str, Enum):
    """Three-state awareness ramp.

    The progression is one-way under M23: ``unaware`` -> ``suspicious``
    -> ``aware``. Downgrading is a follow-up (e.g. a hostile who loses
    track of an intruder after a few rounds without contact).
    """

    UNAWARE = "unaware"
    SUSPICIOUS = "suspicious"
    AWARE = "aware"

    def at_least(self, other: "AwarenessState") -> bool:
        """True when ``self`` is at or above ``other`` on the ramp."""
        return _AWARENESS_ORDER[self] >= _AWARENESS_ORDER[other]


_AWARENESS_ORDER: dict[AwarenessState, int] = {
    AwarenessState.UNAWARE: 0,
    AwarenessState.SUSPICIOUS: 1,
    AwarenessState.AWARE: 2,
}


class NoiseLevel(str, Enum):
    """Catalog of how loud an action is.

    Defaults are per-action: most movement is ``quiet``, attacks and
    verbal spells are ``loud``, sneak movement and silent spells are
    ``silent``. Systems read this when calling
    :func:`propagate_noise`.
    """

    SILENT = "silent"
    QUIET = "quiet"
    LOUD = "loud"


# Default propagation radius (Chebyshev tiles) for a ``loud`` action.
# Tuned so a combat in one room ramps everyone in the same room/corridor
# but doesn't immediately alert a dungeon two levels away. Adjacent
# levels of awareness step the radius proportionally.
DEFAULT_NOISE_RADIUS: int = 8
QUIET_NOISE_RADIUS: int = 2


# ---------------------------------------------------------------------------
# AwarenessTracker component
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AwarenessTracker:
    """Per-observer awareness state about other entities.

    The observer is whoever owns the tracker (a hostile NPC, typically).
    ``per_observer`` maps a target ``EntityId`` to the observer's
    current :class:`AwarenessState` about that target. Missing keys
    default to :attr:`AwarenessState.UNAWARE`.

    Trackers are per-entity (not global) so two guards can disagree
    about an intruder. The store is plain data so save/load and the
    dump tool round-trip it automatically.
    """

    per_observer: dict[EntityId, AwarenessState] = field(default_factory=dict)

    def state_of(self, target: EntityId) -> AwarenessState:
        """Return the observer's current state about ``target``.

        Defaults to :attr:`AwarenessState.UNAWARE`; the M23 scope
        doesn't pre-populate trackers, so every contact starts cold.
        """

        return self.per_observer.get(target, AwarenessState.UNAWARE)

    def set_state(self, target: EntityId, state: AwarenessState) -> None:
        """Force ``target`` to ``state``.

        Used by perception (sets ``aware`` directly) and by content
        scripts that want to seed a hostile as already alerted.
        """

        self.per_observer[target] = state

    def ramp_to_at_least(self, target: EntityId, state: AwarenessState) -> bool:
        """Raise the observer's state about ``target`` to at least ``state``.

        Returns ``True`` when the call actually changed the recorded
        state. The one-way ramp is enforced here so callers never
        accidentally downgrade by passing a lower state.
        """

        current = self.state_of(target)
        if _AWARENESS_ORDER[state] <= _AWARENESS_ORDER[current]:
            return False
        self.per_observer[target] = state
        return True

    def clear(self, target: EntityId) -> None:
        """Forget everything about ``target`` (back to ``UNAWARE``)."""

        self.per_observer.pop(target, None)

    # -- serialization --------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "per_observer": {
                str(int(target)): state.value
                for target, state in self.per_observer.items()
            }
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AwarenessTracker":
        rebuilt: dict[EntityId, AwarenessState] = {}
        for key, value in payload.get("per_observer", {}).items():
            try:
                state = AwarenessState(value)
            except ValueError:
                state = AwarenessState.UNAWARE
            rebuilt[EntityId(int(key))] = state
        return cls(per_observer=rebuilt)


# ---------------------------------------------------------------------------
# Noise propagation
# ---------------------------------------------------------------------------


def propagate_noise(
    world: Any,
    source: EntityId,
    level: NoiseLevel,
    *,
    radius: int | None = None,
) -> list[EntityId]:
    """Ramp the awareness of every hostile within ``radius`` of ``source``.

    Silent actions are a no-op. ``quiet`` actions ramp nearby hostiles
    from ``UNAWARE`` to ``SUSPICIOUS`` (within :data:`QUIET_NOISE_RADIUS`
    by default). ``loud`` actions ramp to ``AWARE`` directly (within
    :data:`DEFAULT_NOISE_RADIUS`).

    Returns the list of entities whose tracker was actually updated, so
    the caller (a system, typically) can decide whether to emit a
    "creature heard you!" message. The function is otherwise a
    side-effecting projection — it mutates the awareness trackers in
    place but emits no effects of its own.

    Save-friendly: only mutates the trackers' ``per_observer`` dicts,
    which round-trip through ``to_dict`` / ``from_dict``.
    """

    if level is NoiseLevel.SILENT:
        return []

    source_position = world.positions.get(source)
    if source_position is None:
        return []

    if radius is None:
        radius = (
            DEFAULT_NOISE_RADIUS if level is NoiseLevel.LOUD else QUIET_NOISE_RADIUS
        )

    target_state = (
        AwarenessState.AWARE if level is NoiseLevel.LOUD else AwarenessState.SUSPICIOUS
    )

    # Late import to avoid cyclic dependency at module load: the
    # awareness module imports stealth indirectly through world setup.
    from src.systems.awareness_system import is_hostile_to

    sx, sy = source_position.x, source_position.y
    updated: list[EntityId] = []
    for observer, tracker in list(world.awareness_trackers.values.items()):
        if observer == source:
            continue
        position = world.positions.get(observer)
        if position is None:
            continue
        if max(abs(position.x - sx), abs(position.y - sy)) > radius:
            continue
        # Only hostiles react to noise. Friends and neutrals ignore the
        # ramp so a town full of NPCs doesn't all aggro on a sword swing.
        if not is_hostile_to(world, observer, source):
            continue
        if tracker.ramp_to_at_least(source, target_state):
            updated.append(observer)
    return updated


def get_or_create_tracker(world: Any, observer: EntityId) -> AwarenessTracker:
    """Return ``observer``'s tracker, creating an empty one if missing.

    Used by perception (which needs to set state on the active actor's
    own tracker) and by tests that want to seed awareness state without
    a full hostile setup.
    """

    tracker = world.awareness_trackers.get(observer)
    if tracker is None:
        tracker = AwarenessTracker()
        world.awareness_trackers.add(observer, tracker)
    return tracker


__all__ = [
    "AwarenessState",
    "AwarenessTracker",
    "DEFAULT_NOISE_RADIUS",
    "NoiseLevel",
    "QUIET_NOISE_RADIUS",
    "get_or_create_tracker",
    "propagate_noise",
]
