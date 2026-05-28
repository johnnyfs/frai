"""Conditions, statuses, and durations (M24).

A ``Condition`` is a typed, durable status attached to an actor. The
canonical examples are SRD-style statuses (``poisoned``, ``prone``,
``frightened``, ``unconscious``) and timed effects (``burning``,
``blessed``). They differ from one-shot ``Effect`` dataclasses in two
ways:

1. They persist on the world (in an actor's :class:`ConditionStore`)
   until something explicitly removes them or their duration runs out.
2. They can be ticked. Each round and each in-world turn, every active
   condition is offered a chance to expire and (optionally) emit a
   tick effect (e.g. ``burning`` deals 1 fire damage per round).

Design constraints
------------------

- Pure data. ``Condition`` is a frozen dataclass that round-trips to
  JSON cleanly. Tick handlers are lookup-by-kind so the data itself
  doesn't store callables (saves stay structural).
- Save-friendly. ``ConditionStore.to_dict`` / ``from_dict`` produce
  primitive lists/dicts. The store is just another
  :class:`ComponentStore` on :class:`World`.
- Schedule-friendly. ``DurationPolicy.Minutes`` and ``UntilRest`` are
  driven by the world clock; ``Rounds`` and ``Turns`` use the actor's
  round counter (via ``TurnController``). Ticks are explicit calls into
  ``tick_conditions`` from the turn controller / app, so this module
  has no knowledge of ``App`` or ``World`` internals.
- Concentration is special-cased: applying a new ``concentrating``
  condition automatically ends any prior one on the same actor. This is
  the foundation M11 (spells) will build on.

Seams for M29 (downed/unconscious)
----------------------------------

``unconscious`` is just another :class:`ConditionKind` here. M29 will
add the death-save state machine that produces and clears it, plus the
combat rules that treat unconscious actors as prone/incapacitated. This
module keeps the condition tag itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from src.core.entity import EntityId
from src.core.time import (
    SECONDS_PER_LONG_REST,
    SECONDS_PER_MINUTE,
    SECONDS_PER_ROUND,
    SECONDS_PER_TURN,
    WorldTime,
)


# ---------------------------------------------------------------------------
# Kinds & duration policies
# ---------------------------------------------------------------------------


class ConditionKind(str, Enum):
    """Catalog of conditions the engine knows how to surface.

    Adding a new condition is two steps: declare it here and (if it has
    runtime behaviour) register a tick handler with
    :func:`register_tick_handler`. The catalog is intentionally narrow;
    M11 (spells) and M29 (downed) will grow it.
    """

    POISONED = "poisoned"
    PRONE = "prone"
    RESTRAINED = "restrained"
    FRIGHTENED = "frightened"
    HIDDEN = "hidden"
    BLESSED = "blessed"
    BURNING = "burning"
    UNCONSCIOUS = "unconscious"
    BLINDED = "blinded"
    DEAFENED = "deafened"
    CONCENTRATING = "concentrating"


class DurationKind(str, Enum):
    """The five duration policies supported today.

    - ``ROUNDS``: ticks down at every combat round boundary.
    - ``TURNS``: ticks down at every explore-mode "turn" (one minute).
    - ``MINUTES``: clock-based; expires once ``WorldTime`` reaches
      ``expires_at``.
    - ``UNTIL_REST``: cleared by a long rest. (Short-rest clearing is
      not a thing in this catalog today.)
    - ``UNTIL_REMOVED``: persists until something explicitly emits
      ``EndCondition`` (e.g. a save-or-suck the player passes).
    """

    ROUNDS = "rounds"
    TURNS = "turns"
    MINUTES = "minutes"
    UNTIL_REST = "until_rest"
    UNTIL_REMOVED = "until_removed"


@dataclass(frozen=True, slots=True)
class DurationPolicy:
    """How long a condition lasts.

    The natural constructors are :meth:`rounds`, :meth:`turns`,
    :meth:`minutes`, :meth:`until_rest`, :meth:`until_removed`. The
    raw fields are exposed so save/load can round-trip them, but
    gameplay code should always use the helpers.
    """

    kind: DurationKind
    amount: int = 0

    # -- constructors ---------------------------------------------------

    @classmethod
    def rounds(cls, n: int) -> "DurationPolicy":
        if n < 0:
            raise ValueError("Round count must be non-negative.")
        return cls(kind=DurationKind.ROUNDS, amount=int(n))

    @classmethod
    def turns(cls, n: int) -> "DurationPolicy":
        if n < 0:
            raise ValueError("Turn count must be non-negative.")
        return cls(kind=DurationKind.TURNS, amount=int(n))

    @classmethod
    def minutes(cls, n: int) -> "DurationPolicy":
        if n < 0:
            raise ValueError("Minute count must be non-negative.")
        return cls(kind=DurationKind.MINUTES, amount=int(n))

    @classmethod
    def until_rest(cls) -> "DurationPolicy":
        return cls(kind=DurationKind.UNTIL_REST, amount=0)

    @classmethod
    def until_removed(cls) -> "DurationPolicy":
        return cls(kind=DurationKind.UNTIL_REMOVED, amount=0)

    # -- expiry computation --------------------------------------------

    def expires_at_for(self, clock: WorldTime) -> int | None:
        """Return the absolute ``elapsed_seconds`` value at which this
        condition expires under clock-driven policies.

        Returns ``None`` for round/turn/until-rest/until-removed
        policies because those aren't pure clock readings — they are
        driven by tick calls or rest semantics.
        """

        if self.kind is DurationKind.MINUTES:
            return clock.elapsed_seconds + self.amount * SECONDS_PER_MINUTE
        return None

    # -- serialization --------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "amount": self.amount}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DurationPolicy":
        return cls(
            kind=DurationKind(payload["kind"]),
            amount=int(payload.get("amount", 0)),
        )


# ---------------------------------------------------------------------------
# Condition
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Condition:
    """A single condition applied to an actor.

    ``source`` is the (optional) origin entity id — the caster of a
    spell, the trap that lit you on fire, etc. ``payload`` is a free-
    form, JSON-serializable dict for condition-specific data (e.g.
    ``burning`` carries ``{"damage": 1}``). ``expires_at`` is the
    absolute world-clock second at which a clock-driven condition
    expires (``None`` for everything else). ``rounds_remaining`` /
    ``turns_remaining`` track the countdown for tick-driven policies.
    """

    kind: ConditionKind
    duration: DurationPolicy
    source: EntityId | None = None
    expires_at: int | None = None
    rounds_remaining: int = 0
    turns_remaining: int = 0
    payload: dict[str, Any] = field(default_factory=dict)

    # -- serialization --------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "duration": self.duration.to_dict(),
            "source": int(self.source) if self.source is not None else None,
            "expires_at": self.expires_at,
            "rounds_remaining": self.rounds_remaining,
            "turns_remaining": self.turns_remaining,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Condition":
        source = payload.get("source")
        return cls(
            kind=ConditionKind(payload["kind"]),
            duration=DurationPolicy.from_dict(payload["duration"]),
            source=EntityId(int(source)) if source is not None else None,
            expires_at=payload.get("expires_at"),
            rounds_remaining=int(payload.get("rounds_remaining", 0)),
            turns_remaining=int(payload.get("turns_remaining", 0)),
            payload=dict(payload.get("payload", {})),
        )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ConditionStore:
    """All conditions currently attached to a single actor.

    The store is intentionally a list (not a set) so duplicate
    instances of the same kind are allowed where it makes sense
    (e.g. multiple ``blessed`` stacks from different casters). The
    helper :meth:`has` answers "is the actor under any X?" without
    caring how many sources contributed.
    """

    conditions: list[Condition] = field(default_factory=list)

    def has(self, kind: ConditionKind) -> bool:
        return any(c.kind is kind for c in self.conditions)

    def of_kind(self, kind: ConditionKind) -> list[Condition]:
        return [c for c in self.conditions if c.kind is kind]

    def add(self, condition: Condition) -> None:
        self.conditions.append(condition)

    def remove_kind(self, kind: ConditionKind) -> int:
        """Remove every condition of ``kind``; return how many were removed."""
        before = len(self.conditions)
        self.conditions = [c for c in self.conditions if c.kind is not kind]
        return before - len(self.conditions)

    # -- serialization --------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {"conditions": [c.to_dict() for c in self.conditions]}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ConditionStore":
        return cls(
            conditions=[
                Condition.from_dict(item)
                for item in payload.get("conditions", [])
            ]
        )


# ---------------------------------------------------------------------------
# Tick handlers
# ---------------------------------------------------------------------------
#
# A tick handler is a small pure function that, given a (kind, condition)
# pair plus the actor it lives on, returns a list of Effect-shaped
# instructions. Handlers run when the relevant tick fires
# (round-boundary for burning, etc.) and the world clock has already
# advanced — so they see "after the round" state. The actual Effect
# types are imported lazily to avoid a circular import; handlers return
# bare dicts/Effect instances and the caller is responsible for
# dispatching them.

TickContext = dict  # forward-compat: a typed context dataclass can grow later


# The handler returns a list of Effect; we type as ``Any`` here to avoid
# importing src.core.effects at module load (the effects module already
# pulls in modes / character_creation which import from core.* — keeping
# this seam loose prevents future cycles).
TickHandler = Callable[[EntityId, Condition], list[Any]]


_TICK_HANDLERS: dict[ConditionKind, TickHandler] = {}


def register_tick_handler(kind: ConditionKind, handler: TickHandler) -> None:
    """Register the function that fires when ``kind`` ticks.

    Idempotent registration: re-registering the same kind replaces the
    prior handler. This keeps test setup simple.
    """

    _TICK_HANDLERS[kind] = handler


def tick_handler_for(kind: ConditionKind) -> TickHandler | None:
    return _TICK_HANDLERS.get(kind)


def _burning_tick(actor: EntityId, condition: Condition) -> list[Any]:
    """Default ``burning`` tick: deal ``payload['damage']`` (or 1) fire damage."""
    from src.core.effects import DamageEntity, EmitMessage

    damage = int(condition.payload.get("damage", 1))
    return [
        DamageEntity(actor, damage),
        EmitMessage(f"Burning deals {damage} damage."),
    ]


register_tick_handler(ConditionKind.BURNING, _burning_tick)


# ---------------------------------------------------------------------------
# apply / end primitives
# ---------------------------------------------------------------------------


def apply_condition(
    world: Any,
    actor: EntityId,
    condition: Condition,
) -> Condition:
    """Attach ``condition`` to ``actor`` and return the stored instance.

    The returned instance may differ from the supplied one when the
    duration policy needs to be resolved against the current clock
    (``Minutes`` needs an absolute ``expires_at``; ``Rounds`` /
    ``Turns`` need the countdown seeded). Mutates the world's
    ``conditions`` store on ``actor``.

    Concentration is special-cased here: applying a new
    ``concentrating`` condition to an actor that already concentrates
    on something ends the prior one before adding the new one. This is
    the only place the engine encodes "you can only concentrate on one
    thing at a time".
    """

    store = world.conditions.get(actor)
    if store is None:
        store = ConditionStore()
        world.conditions.add(actor, store)

    # Concentration handoff happens before we resolve the new instance
    # so we don't run the tick handler for the old condition.
    if condition.kind is ConditionKind.CONCENTRATING:
        store.remove_kind(ConditionKind.CONCENTRATING)

    resolved = _resolve_for_clock(condition, world.clock)
    store.add(resolved)
    return resolved


def end_condition(
    world: Any,
    actor: EntityId,
    kind: ConditionKind,
) -> int:
    """Remove every condition of ``kind`` from ``actor``.

    Returns how many were removed (0 if none). Safe to call when the
    actor has no condition store yet.
    """

    store = world.conditions.get(actor)
    if store is None:
        return 0
    return store.remove_kind(kind)


def _resolve_for_clock(condition: Condition, clock: WorldTime) -> Condition:
    """Compute the stored shape of ``condition`` against ``clock``.

    For clock-driven policies this populates ``expires_at``. For
    tick-driven policies it seeds ``rounds_remaining`` /
    ``turns_remaining`` from the duration amount.
    """

    duration = condition.duration
    if duration.kind is DurationKind.MINUTES:
        expires = duration.expires_at_for(clock)
        return _replace(condition, expires_at=expires)
    if duration.kind is DurationKind.ROUNDS:
        return _replace(condition, rounds_remaining=duration.amount)
    if duration.kind is DurationKind.TURNS:
        return _replace(condition, turns_remaining=duration.amount)
    return condition


def _replace(condition: Condition, **kwargs: Any) -> Condition:
    """Frozen-dataclass-friendly ``replace`` for :class:`Condition`."""
    from dataclasses import replace

    return replace(condition, **kwargs)


# ---------------------------------------------------------------------------
# Tick driver
# ---------------------------------------------------------------------------


def tick_conditions(
    world: Any,
    actors: list[EntityId],
    *,
    boundary: str,
) -> list[Any]:
    """Tick every condition on every actor against ``boundary``.

    ``boundary`` is one of:

    - ``"round"``: decrement ``rounds_remaining`` on ROUNDS-policy
      conditions and run round-boundary tick handlers (e.g. burning
      damage). Called from the turn controller's round wrap.
    - ``"turn"``: decrement ``turns_remaining`` on TURNS-policy
      conditions. Called from the explore-mode tick (one minute per
      action).
    - ``"clock"``: expire MINUTES-policy conditions whose
      ``expires_at`` has passed. Called whenever the world clock
      advances.
    - ``"long_rest"``: clear UNTIL_REST conditions. Called from the
      rest system (M34); today we expose the entry point so tests can
      exercise it without M34 landing.

    Returns the flat list of tick-handler effects produced this call,
    in actor / condition order. The caller dispatches them through
    ``EffectApplier`` so message ordering matches the rest of the
    engine.
    """

    effects: list[Any] = []
    for actor in actors:
        store = world.conditions.get(actor)
        if store is None:
            continue
        survivors: list[Condition] = []
        for condition in store.conditions:
            ticked, expired = _tick_one(condition, world.clock, boundary)
            if not expired and boundary == "round" and ticked.kind is ConditionKind.BURNING:
                handler = tick_handler_for(ticked.kind)
                if handler is not None:
                    effects.extend(handler(actor, ticked))
            if not expired:
                survivors.append(ticked)
        store.conditions = survivors
    return effects


def _tick_one(
    condition: Condition,
    clock: WorldTime,
    boundary: str,
) -> tuple[Condition, bool]:
    """Apply ``boundary`` to ``condition``; return (new_state, expired)."""

    duration = condition.duration

    if boundary == "round" and duration.kind is DurationKind.ROUNDS:
        remaining = condition.rounds_remaining - 1
        if remaining <= 0:
            return condition, True
        return _replace(condition, rounds_remaining=remaining), False

    if boundary == "turn" and duration.kind is DurationKind.TURNS:
        remaining = condition.turns_remaining - 1
        if remaining <= 0:
            return condition, True
        return _replace(condition, turns_remaining=remaining), False

    if boundary == "clock" and duration.kind is DurationKind.MINUTES:
        if condition.expires_at is not None and clock.elapsed_seconds >= condition.expires_at:
            return condition, True

    if boundary == "long_rest" and duration.kind is DurationKind.UNTIL_REST:
        return condition, True

    return condition, False


__all__ = [
    "Condition",
    "ConditionKind",
    "ConditionStore",
    "DurationKind",
    "DurationPolicy",
    "SECONDS_PER_LONG_REST",
    "SECONDS_PER_ROUND",
    "SECONDS_PER_TURN",
    "apply_condition",
    "end_condition",
    "register_tick_handler",
    "tick_conditions",
    "tick_handler_for",
]
