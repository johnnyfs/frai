"""Targeting mode primitives (M20).

Targeting lets the player pick a tile (and, by extension, the entity on
that tile) before committing an action that needs a target outside the
"step into them" melee model. Ranged attacks (M11), spells, the examine
command (M21), throw-item, and any future interact-at-distance action
funnel through this seam.

Design constraints
------------------

* **Transient.** A :class:`TargetingState` is held on :class:`App` while
  ``UIMode.targeting`` is active and dropped on save. GameState (M16)
  never carries it.
* **Render-only highlight.** The cursor is a projection over world
  state, not a world mutation. The renderer reads the targeting state
  to draw a highlight; the world has no idea targeting is up.
* **Confirm goes through the resolver.** On confirm we ask the state's
  ``on_confirm`` callback to build an :class:`Action`, then hand it to
  ``app.resolve_action`` exactly like any other action. Cancel emits no
  action and consumes no resource.
* **Predicates are pure.** A :class:`TargetPredicate` reads
  ``(world, x, y, origin)`` and returns ``True``/``False``. The small
  library at the bottom of this module exposes the common ones — any
  caller that wants stricter rules (e.g. "must be a creature with HP")
  composes its own.

The predicate is consulted *only on confirm*. Cursor movement is free
within the configured range; an out-of-range or predicate-failing
confirm is rejected with a message and the modal stays up. This keeps
cursor motion responsive (no per-cell filtering) while still preventing
illegal commits.

Range is measured as Chebyshev distance from the origin tile. The
:func:`clamp_cursor` helper makes sure cursor movement never escapes
the range — out-of-range steps are silently no-ops. A ``range`` of 0
locks the cursor on the origin (useful for "examine self" flows).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from src.core.actions import Action
from src.core.entity import EntityId
from src.core.modes import UIMode
from src.core.vision import compute_visible_tiles
from src.core.world import World


# A predicate decides whether ``(x, y)`` is a legal confirm target. It
# receives the world, the candidate cell, and the origin (the actor's
# tile when targeting started) so a predicate can be range-aware or
# require LOS without re-walking app state.
class TargetPredicate(Protocol):
    """Function-shaped check: is ``(x, y)`` a legal confirm target?"""

    def __call__(self, world: World, x: int, y: int, origin: tuple[int, int]) -> bool: ...


# A confirm builder takes the cursor's final position and produces the
# action to dispatch (or ``None`` to cancel silently). Most callers
# return an action unconditionally; the optional return covers spell
# flows that may decide a target is illegal mid-build and want to leave
# the modal up with no action emitted.
ConfirmAction = Callable[[tuple[int, int]], Optional[Action]]


@dataclass(slots=True)
class TargetingState:
    """The in-flight targeting modal.

    ``origin`` is the tile the cursor opened on (usually the active
    actor's position). ``cursor`` is the live cursor position. ``range``
    is the maximum Chebyshev distance the cursor may move from
    ``origin``; a 0 range means the cursor is locked on the origin.

    ``predicate`` is consulted on confirm; if it rejects the cell, the
    modal stays open and the App emits a refusal message.

    ``on_confirm`` produces the action to dispatch once the predicate
    passes.

    ``previous_mode`` is the :class:`UIMode` that was active when
    targeting opened, so cancel restores it. Stored as a value (not a
    reference) so save/load can never accidentally entangle the
    transient state with anything else.

    ``label`` is a short human-readable string the renderer can drop in
    the message line (e.g. ``"Target a tile (range 6)"``). Optional —
    callers that don't care leave it empty.

    ``cancel_message`` is the text emitted into the log when the modal
    is closed without dispatching an action (Esc/q, or a confirm where
    the on_confirm callback returns ``None``). Defaults to the
    targeting banner; the M21 examine flow overrides it to an empty
    string so confirm-as-look doesn't clobber the description text the
    callback just emitted.
    """

    origin: tuple[int, int]
    cursor: tuple[int, int]
    range: int
    on_confirm: ConfirmAction
    predicate: TargetPredicate = field(default=lambda world, x, y, origin: True)
    previous_mode: UIMode | None = None
    label: str = ""
    cancel_message: str = "Targeting cancelled."

    def in_range(self, x: int, y: int) -> bool:
        """True when ``(x, y)`` is within ``range`` of the origin."""

        return chebyshev(self.origin, (x, y)) <= self.range

    def move_cursor(self, dx: int, dy: int) -> bool:
        """Move the cursor by ``(dx, dy)`` if it stays in range.

        Out-of-range moves are silently rejected (the cursor stays
        where it was). Returns ``True`` when the cursor actually moved.
        Movement is unconstrained by world geometry — we deliberately
        let the cursor pass over walls so the player can target a tile
        behind a known wall (the predicate decides whether confirm is
        legal). Out-of-bounds moves *are* clamped because there's no
        such thing as a tile outside the world.
        """

        new_x = self.cursor[0] + dx
        new_y = self.cursor[1] + dy
        if not self.in_range(new_x, new_y):
            return False
        self.cursor = (new_x, new_y)
        return True

    def set_cursor(self, x: int, y: int) -> bool:
        """Snap the cursor to ``(x, y)`` if in range."""

        if not self.in_range(x, y):
            return False
        self.cursor = (x, y)
        return True

    def confirm(self, world: World) -> tuple[Optional[Action], Optional[str]]:
        """Resolve the cursor into an action.

        Returns ``(action, refusal)``: at most one is non-None.

        * ``(action, None)`` — predicate passed and the on_confirm
          callback returned an action. The caller (App) should exit the
          modal and dispatch the action via ``resolve_action``.
        * ``(None, refusal)`` — predicate rejected the cell, or the
          on_confirm callback returned None. The caller keeps the modal
          open and emits ``refusal`` into the message log.
        * ``(None, None)`` — only happens if a predicate-passing
          on_confirm returned None deliberately (e.g. a spell flow that
          wants to "swallow" the confirm without dispatching). The
          caller should treat this as a silent no-op.
        """

        x, y = self.cursor
        if not self.in_range(x, y):
            return None, "Target out of range."
        if not self.predicate(world, x, y, self.origin):
            return None, "Invalid target."
        action = self.on_confirm((x, y))
        if action is None:
            return None, None
        return action, None


# ---------------------------------------------------------------------
# Predicate library
# ---------------------------------------------------------------------
#
# These are small, composable predicates. A caller picks one (or wraps
# its own) and passes it as :class:`TargetingState.predicate`. They are
# pure functions over the world snapshot — no app state, no RNG.


def any_tile(world: World, x: int, y: int, origin: tuple[int, int]) -> bool:
    """Permissive predicate: any in-bounds cell is fair game.

    Useful for examine-style flows that want to inspect arbitrary
    tiles. Out-of-bounds tiles are rejected so callers never have to
    special-case world edges.
    """

    return 0 <= x < world.width and 0 <= y < world.height


def any_visible_tile(
    world: World, x: int, y: int, origin: tuple[int, int]
) -> bool:
    """Tile must be reachable by an LOS ray from the origin.

    Reuses :func:`compute_visible_tiles` semantics by walking the ray
    directly — the caller is the targeter, not a stored observer
    entity, so we use a one-shot raycast against the world. The origin
    itself is always allowed.
    """

    if not any_tile(world, x, y, origin):
        return False
    if (x, y) == origin:
        return True
    return _line_of_sight(world, origin, (x, y))


def any_visible_entity(
    world: World, x: int, y: int, origin: tuple[int, int]
) -> bool:
    """At least one entity must be on the cell, and the cell must be visible."""

    if not any_visible_tile(world, x, y, origin):
        return False
    return bool(world.entities_at(x, y))


def hostile_entity(
    world: World, x: int, y: int, origin: tuple[int, int]
) -> bool:
    """A visible entity with a *different* faction than the origin tile's actor.

    "Hostile" here is faction-relative: the predicate looks at the
    actor standing on the origin tile (if any) and demands that the
    target cell holds an entity with a different faction tag *and* live
    combat stats. This matches the M10 hostility model without
    bringing in the AwarenessSystem (which works on whole party lists,
    not single tiles).
    """

    if not any_visible_entity(world, x, y, origin):
        return False
    origin_faction = _faction_at(world, origin)
    for entity in world.entities_at(x, y):
        target_faction = world.factions.get(entity)
        if target_faction is None or target_faction.value == origin_faction:
            continue
        stats = world.combat_stats.get(entity)
        if stats is None or stats.hit_points <= 0:
            continue
        return True
    return False


def make_visible_predicate(observer: EntityId, *, radius: int) -> TargetPredicate:
    """Build a predicate that consults the *observer's* live LOS set.

    Use this when the targeting actor is an entity in the world (the
    common case): the predicate evaluates :func:`compute_visible_tiles`
    against ``observer`` so closed doors and walls limit valid targets
    exactly as they do for the player's vision. This is the right
    choice for spells/ranged attacks; ``any_visible_tile`` is fine for
    "abstract cursor" flows like examine.
    """

    def _predicate(world: World, x: int, y: int, origin: tuple[int, int]) -> bool:
        if not any_tile(world, x, y, origin):
            return False
        visible = compute_visible_tiles(world, observer, radius=radius)
        return (x, y) in visible

    return _predicate


def make_spell_target_predicate(
    caster: EntityId,
    *,
    radius: int,
    require_hostile: bool,
    allow_self_target: bool,
) -> TargetPredicate:
    """Build a predicate for single-entity spell targeting.

    Layers three rules on top of :func:`make_visible_predicate`:

    1. The cell must hold an entity with combat stats (something to
       cast on).
    2. If ``allow_self_target`` is False, the caster's own tile is
       rejected. Fixes bug #100 — Enter-Enter on the spell menu used
       to confirm the cursor at the caster's tile and damage the
       caster.
    3. If ``require_hostile`` is True (damage spells), the entity must
       be hostile to the caster per
       :func:`src.systems.awareness_system.is_hostile_to`. Fixes bug
       #101 — Magic Missile / Fire Bolt would happily strike a party
       member. Healing / buff spells flip this off so Cure Wounds can
       still target friendlies.
    """

    visible = make_visible_predicate(caster, radius=radius)

    def _predicate(world: World, x: int, y: int, origin: tuple[int, int]) -> bool:
        from src.systems.awareness_system import is_hostile_to

        if not visible(world, x, y, origin):
            return False
        candidates = [
            entity
            for entity in world.entities_at(x, y)
            if world.combat_stats.has(entity)
        ]
        if not candidates:
            return False
        for entity in candidates:
            if entity == caster:
                if not allow_self_target:
                    continue
                return True
            if require_hostile:
                if is_hostile_to(world, caster, entity):
                    return True
            else:
                # Friendly-target spells: anyone not hostile to the
                # caster is fair game (party members, town NPCs).
                if not is_hostile_to(world, caster, entity):
                    return True
        return False

    return _predicate


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def chebyshev(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Standard Chebyshev distance (king-move count)."""

    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _faction_at(world: World, position: tuple[int, int]) -> str:
    """Return the faction tag of the first faction-bearing entity on the tile.

    Falls back to ``"player"`` when nothing on the tile has a faction;
    this is the conservative answer for an origin tile (the targeter
    is almost always a party member, so default hostility flips work).
    """

    for entity in world.entities_at(position[0], position[1]):
        faction = world.factions.get(entity)
        if faction is not None:
            return faction.value
    return "player"


def _line_of_sight(
    world: World, start: tuple[int, int], end: tuple[int, int]
) -> bool:
    """Pure Bresenham LOS check between two cells.

    Mirrors :func:`src.core.vision._ray_clear` (the start/end cells are
    *not* blocker-tested; intermediate cells are). Centralised here
    because the targeting predicate library does not own an observer
    entity, so the vision module's observer-keyed helper doesn't fit.
    """

    from src.core.vision import blocks_sight  # local to avoid cycle

    x0, y0 = start
    x1, y1 = end
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x1 > x0 else -1
    sy = 1 if y1 > y0 else -1
    err = dx - dy
    x, y = x0, y0
    while True:
        if (x, y) != (x0, y0) and (x, y) != (x1, y1):
            if blocks_sight(world, x, y):
                return False
        if x == x1 and y == y1:
            return True
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x += sx
        if e2 < dx:
            err += dx
            y += sy
