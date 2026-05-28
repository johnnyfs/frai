"""ActionContext and phased ActionResolver (M46).

Generalizes the M9 :class:`~src.systems.movement_system.MovementContext`
pattern so every action flows through explicit phases:

    Intent -> AttemptContext -> propose/replace -> resolve -> apply_effects -> post_resolve

Each phase is a hook point. Phases can:

  * accumulate effects,
  * propose a replacement action (the resolver restarts on the new
    action), or
  * cancel the attempt (short-circuits later phases).

A reaction hook system runs after ``post_resolve``: each hook receives
the :class:`ResolvedAttempt` and may emit additional effects. This is
the seam M11 (spells/interruption), M24 (post-resolve condition reacts)
and M29 (HP-zero downed hook) will plug into.

Design constraints
------------------

* :class:`ActionContext` is a frozen dataclass — every phase reads the
  same snapshot. Phases that need to react to post-resolve world state
  receive a :class:`ResolvedAttempt` instead, which carries the full
  effect list.
* Reaction hooks are pure functions over the snapshot + result.
* Nothing in this module persists to save data; the context is
  transient per-attempt.

The existing :class:`~src.core.dispatcher.Dispatcher` keeps its role
as the per-action chain of systems. The resolver wires the dispatcher
into the ``resolve`` phase by default, so legacy callers
(``dispatcher.dispatch``) remain the ground truth for system handling
while the phased seam wraps around them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol, TYPE_CHECKING

from src.core.actions import Action
from src.core.effects import Effect
from src.core.entity import EntityId
from src.core.world import World

if TYPE_CHECKING:
    from src.core.dispatcher import Dispatcher
    from src.core.turn_controller import TurnController


class Phase(Enum):
    """Phases an :class:`ActionResolver` walks per attempt.

    Order is significant — the resolver iterates ``list(Phase)`` and
    relies on declaration order to drive the pipeline.
    """

    PRE_CHECK = "pre_check"
    PROPOSE = "propose"
    RESOLVE = "resolve"
    APPLY_EFFECTS = "apply_effects"
    POST_RESOLVE = "post_resolve"


@dataclass(frozen=True, slots=True)
class ActionContext:
    """Immutable snapshot of an in-flight action attempt.

    ``actor`` is the entity initiating the action, ``action`` the
    declared intent, ``world`` the world the action is dispatched
    against, and ``turn`` the active :class:`TurnController` so phase
    handlers can read action-economy state without poking back through
    the world.

    The dataclass is ``frozen`` to prevent mutation between phases.
    Phases that need to update working state produce :class:`PhaseOutcome`
    values which the resolver folds into a :class:`ResolvedAttempt`.
    """

    actor: EntityId
    action: Action
    world: World
    turn: "TurnController | None" = None


@dataclass(frozen=True, slots=True)
class PhaseOutcome:
    """Result of a single phase handler.

    A handler that doesn't apply changes returns ``PhaseOutcome()``.

    ``replacement`` swaps the in-flight action for a different one; the
    resolver restarts from :class:`Phase.PRE_CHECK` against the new
    action. ``cancel`` short-circuits the remaining phases (but lets
    ``post_resolve`` still run so observers see the final state).
    """

    effects: tuple[Effect, ...] = ()
    replacement: Action | None = None
    cancel: bool = False


class PhaseHandler(Protocol):
    """A phase hook. Receives the immutable context; returns an outcome."""

    def __call__(self, context: ActionContext) -> PhaseOutcome: ...


@dataclass(frozen=True, slots=True)
class ResolvedAttempt:
    """The fully resolved outcome of an action.

    ``original`` is the :class:`ActionContext` the resolver started
    with. ``replacement`` is set when a ``propose`` phase swapped the
    original action — the effect list is from resolving the replacement.

    ``cancelled`` indicates the attempt short-circuited (typically
    because ``pre_check`` rejected it, or the resolve phase emitted an
    in-band rejection like ``"Blocked."``). Cancellation is informational
    for reaction hooks; the resolver still returns the effects produced
    before the cancel so they apply normally.
    """

    original: ActionContext
    effects: tuple[Effect, ...] = ()
    cancelled: bool = False
    replacement: Action | None = None


# A reaction hook fires post-resolve. It receives the :class:`ResolvedAttempt`
# and returns extra effects (typically empty). The hook itself decides
# whether to react by inspecting ``attempt.effects`` (e.g. a damage hook
# looks for :class:`~src.core.effects.DamageEntity`).
ReactionHook = Callable[[ResolvedAttempt], list[Effect]]


@dataclass(slots=True)
class ActionResolver:
    """Walks the M46 phase pipeline for a single attempt.

    The resolver is stateless apart from its registered phase handlers
    and reaction hooks. Each call to :meth:`resolve` produces a fresh
    :class:`ResolvedAttempt` — there's no shared mutable state between
    attempts.

    Phase handlers
    --------------

    Each :class:`Phase` carries a list of :class:`PhaseHandler`
    callbacks. Handlers run in registration order. The first handler
    that ``cancel``\\s short-circuits the rest. A handler that returns a
    ``replacement`` restarts the pipeline from :class:`Phase.PRE_CHECK`
    against the replacement action (matching the legacy Dispatcher
    semantics).

    Default resolve phase
    ---------------------

    When constructed with a :class:`~src.core.dispatcher.Dispatcher`,
    the resolver installs a single handler on :class:`Phase.RESOLVE`
    that delegates to ``dispatcher.dispatch``. This preserves the
    existing per-system pipeline while adding the phase seams around
    it — no system needs to be rewritten to benefit from reactions or
    pre-checks.

    Reaction hooks
    --------------

    Hooks fire after ``post_resolve`` against the :class:`ResolvedAttempt`.
    A hook's emitted effects are appended to the attempt's effect list
    (the caller still applies the whole list through the normal effect
    applier). Hooks are pure — they receive the resolved snapshot, not
    the live world.

    See :func:`make_default_resolver` for the production wiring.
    """

    dispatcher: "Dispatcher | None" = None
    handlers: dict[Phase, list[PhaseHandler]] = field(default_factory=dict)
    reaction_hooks: list[ReactionHook] = field(default_factory=list)
    max_replacement_chain: int = 16

    def __post_init__(self) -> None:
        # Ensure every phase has an entry so callers can append without
        # checking key presence.
        for phase in Phase:
            self.handlers.setdefault(phase, [])
        if self.dispatcher is not None:
            # Install the dispatcher as the default ``resolve`` handler.
            # Placed at index 0 so callers can still register pre-resolve
            # handlers by appending to the same phase (they will run
            # after the dispatcher; if they need to run before the
            # dispatcher fires, use :class:`Phase.PRE_CHECK` or
            # :class:`Phase.PROPOSE`).
            self.handlers[Phase.RESOLVE].insert(0, _DispatcherHandler(self.dispatcher))

    def register(self, phase: Phase, handler: PhaseHandler) -> None:
        """Add a handler at the end of ``phase``'s handler list."""
        self.handlers[phase].append(handler)

    def add_reaction(self, hook: ReactionHook) -> None:
        """Register a post-resolve reaction hook."""
        self.reaction_hooks.append(hook)

    def resolve(self, context: ActionContext) -> ResolvedAttempt:
        """Walk every phase against ``context`` and return the result.

        Replacement actions restart the pipeline (bounded by
        ``max_replacement_chain`` to prevent runaway loops in a future
        spell-counter-spell chain). Cancellations short-circuit the
        remaining gameplay phases but still let ``post_resolve`` and
        reaction hooks run so observers see the final state.
        """
        return self._resolve(context, depth=0)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve(self, context: ActionContext, *, depth: int) -> ResolvedAttempt:
        if depth > self.max_replacement_chain:
            # Stop the runaway chain. Treat the last context as cancelled
            # so the caller doesn't end up with an unresolved attempt.
            return ResolvedAttempt(original=context, cancelled=True)

        effects: list[Effect] = []
        replacement: Action | None = None
        cancelled = False

        for phase in Phase:
            if phase is Phase.POST_RESOLVE:
                # Post-resolve runs even after a cancel so reaction hooks
                # (and other observers) see the final state — but it
                # never sees a replacement, those restart the pipeline.
                outcome = self._run_phase(phase, context)
                effects.extend(outcome.effects)
                if outcome.replacement is not None:
                    # A post-resolve replacement is interpreted as a
                    # follow-up attempt; we expose it on the attempt so
                    # the caller can decide whether to schedule it.
                    replacement = outcome.replacement
                continue

            if cancelled:
                # Skip gameplay phases after a cancel; we still want to
                # fall through to post_resolve.
                continue

            outcome = self._run_phase(phase, context)
            effects.extend(outcome.effects)
            if outcome.replacement is not None:
                # Restart the pipeline against the replacement action.
                # The previously accumulated effects (e.g. an emitted
                # message from a pre_check) are preserved by prepending
                # them to the replacement's resolved effects.
                new_context = ActionContext(
                    actor=context.actor,
                    action=outcome.replacement,
                    world=context.world,
                    turn=context.turn,
                )
                inner = self._resolve(new_context, depth=depth + 1)
                merged_effects = tuple(effects) + tuple(inner.effects)
                return ResolvedAttempt(
                    original=context,
                    effects=merged_effects,
                    cancelled=inner.cancelled,
                    replacement=outcome.replacement,
                )
            if outcome.cancel:
                cancelled = True

        attempt = ResolvedAttempt(
            original=context,
            effects=tuple(effects),
            cancelled=cancelled,
            replacement=replacement,
        )
        if self.reaction_hooks:
            extra: list[Effect] = []
            for hook in self.reaction_hooks:
                extra.extend(hook(attempt))
            if extra:
                attempt = ResolvedAttempt(
                    original=attempt.original,
                    effects=attempt.effects + tuple(extra),
                    cancelled=attempt.cancelled,
                    replacement=attempt.replacement,
                )
        return attempt

    def _run_phase(self, phase: Phase, context: ActionContext) -> PhaseOutcome:
        """Fold the registered handlers for ``phase`` into one outcome.

        Handlers run sequentially. The first handler that proposes a
        replacement short-circuits the rest (replacement triggers a full
        pipeline restart, which would run later handlers against the
        new action anyway). The first handler that cancels marks the
        outcome cancelled, but later handlers in the same phase still
        run so they can emit cleanup effects.
        """
        effects: list[Effect] = []
        cancel = False
        for handler in self.handlers[phase]:
            outcome = handler(context)
            effects.extend(outcome.effects)
            if outcome.replacement is not None:
                return PhaseOutcome(
                    effects=tuple(effects),
                    replacement=outcome.replacement,
                    cancel=cancel,
                )
            if outcome.cancel:
                cancel = True
        return PhaseOutcome(effects=tuple(effects), cancel=cancel)


@dataclass(slots=True)
class _DispatcherHandler:
    """Adapter that runs a :class:`Dispatcher` as a single phase handler.

    The dispatcher already handles per-system replacement and cancellation
    internally; for the resolver it presents as one ``resolve`` step that
    produces the final effect list.
    """

    dispatcher: "Dispatcher"

    def __call__(self, context: ActionContext) -> PhaseOutcome:
        effects = self.dispatcher.dispatch(context.action, context.world)
        return PhaseOutcome(effects=tuple(effects))


def make_default_resolver(dispatcher: "Dispatcher") -> ActionResolver:
    """Production wiring: a resolver whose ``resolve`` phase is the
    existing :class:`Dispatcher`. Reaction hooks start empty — M11/M24/M29
    register theirs by calling :meth:`ActionResolver.add_reaction`."""
    return ActionResolver(dispatcher=dispatcher)
