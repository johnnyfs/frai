"""Agent-readable game-state snapshot.

`observe(app)` builds a compact, JSON-serializable view of the runtime
state suitable for an agentic playtester. Agents should consume this
snapshot rather than the curses framebuffer.

Design constraints (see issue #13):

- No ANSI/terminal control sequences.
- JSON-serializable (`to_dict` / `from_dict` round-trip).
- Reproducible across runs with the same seed (no time- or address-
  dependent fields).
- Read-only: `observe()` must not mutate `app` or `app.world`.

This module is deliberately a thin projection. It does NOT register
into the input/run loop — the playtest harness (M37) is responsible for
calling `observe(app)` after each command and computing deltas.

Forward-compatibility notes:

- Visibility filtering: `_visible_filter` consults `app.memory.visible`
  (a frozenset of `(x, y)`) when present — this is the M19 vision/LOS
  surface. If for some reason an `App` instance has no `memory`
  attribute (e.g. a custom harness fixture), the helper falls back to
  a Chebyshev radius from the active actor.
- Combat detail: when M44 (`TurnController`) extracts initiative/order
  from `App.activation`, the `combat` block can expose the full
  initiative roster. Today we surface only what `ActivationState`
  already tracks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.core.entity import EntityId
from src.core.modes import UIMode, is_turn_based_play


# Fallback Chebyshev radius used when `app.memory.visible` is missing
# (e.g. a custom harness fixture that bypasses `App`). The normal path
# uses M19 party memory; this constant exists so tests and tools can
# replicate the fallback geometry.
DEFAULT_VISIBILITY_RADIUS: int = 10

# Recent-messages cap. The current `MessageState` only retains the
# current and pending pages (no history), so today this list is at most
# `1 + len(pending)`. Capped here for forward-compat with a future log.
RECENT_MESSAGES_LIMIT: int = 10

# Eight-direction exit probe. (0, 0) is omitted on purpose; "stay" is
# not an exit.
_EXIT_DIRECTIONS: tuple[tuple[int, int, str], ...] = (
    (0, -1, "north"),
    (1, -1, "northeast"),
    (1, 0, "east"),
    (1, 1, "southeast"),
    (0, 1, "south"),
    (-1, 1, "southwest"),
    (-1, 0, "west"),
    (-1, -1, "northwest"),
)


@dataclass(frozen=True, slots=True)
class ConditionSummary:
    """A single active condition, surfaced to the agentic playtester.

    ``expires_at`` is the absolute ``WorldTime.elapsed_seconds`` at which
    a clock-driven condition expires; ``None`` for tick-driven or
    indefinite policies. ``rounds_remaining`` / ``turns_remaining`` are
    populated for ROUNDS / TURNS policies respectively so a harness can
    plan around the countdown without inspecting the duration payload.
    """

    kind: str
    duration: str
    expires_at: int | None = None
    rounds_remaining: int = 0
    turns_remaining: int = 0


@dataclass(frozen=True, slots=True)
class ActorSummary:
    """Summary of a single party member or visible actor."""

    id: int
    name: str
    hp: int
    max_hp: int
    position: tuple[int, int]
    faction: str | None = None
    glyph: str | None = None
    conditions: tuple[ConditionSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class VisibleEntity:
    """Anything visible to the active actor that isn't the party."""

    id: int
    name: str
    glyph: str | None
    faction: str | None
    position: tuple[int, int]
    hp: int | None = None
    max_hp: int | None = None
    kind: str | None = None  # creature.kind if present, else a tag like "door"
    distance: int = 0  # Chebyshev distance from the active actor


@dataclass(frozen=True, slots=True)
class CombatSnapshot:
    """Per-activation resource state, surfaced only in turn-based play."""

    round: int
    active_index: int
    action_remaining: bool
    movement_remaining: float
    movement_total: float
    bonus_remaining: bool
    reaction_remaining: bool


@dataclass(frozen=True, slots=True)
class ModalSnapshot:
    """Currently-active modal screen and the choices it offers."""

    kind: str
    options: list[str] = field(default_factory=list)
    cursor: int | None = None


@dataclass(frozen=True, slots=True)
class WorldTimeSnapshot:
    seconds: int
    rounds: int
    minutes: int
    hours: int


@dataclass(frozen=True, slots=True)
class Observation:
    """Snapshot of the agent-visible state at a single point in time."""

    mode: dict[str, str | None]
    active_actor: ActorSummary | None
    party: list[ActorSummary]
    visible_entities: list[VisibleEntity]
    exits: list[str]
    recent_messages: list[str]
    combat: CombatSnapshot | None
    available_actions: list[str]
    modal: ModalSnapshot | None
    world_time: WorldTimeSnapshot

    def to_dict(self) -> dict[str, Any]:
        """Return a fully JSON-serializable dict representation."""

        return {
            "mode": dict(self.mode),
            "active_actor": _actor_to_dict(self.active_actor),
            "party": [_actor_to_dict(actor) for actor in self.party],
            "visible_entities": [_visible_to_dict(entity) for entity in self.visible_entities],
            "exits": list(self.exits),
            "recent_messages": list(self.recent_messages),
            "combat": asdict(self.combat) if self.combat is not None else None,
            "available_actions": list(self.available_actions),
            "modal": (
                {
                    "kind": self.modal.kind,
                    "options": list(self.modal.options),
                    "cursor": self.modal.cursor,
                }
                if self.modal is not None
                else None
            ),
            "world_time": asdict(self.world_time),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Observation":
        return cls(
            mode=dict(payload["mode"]),
            active_actor=_actor_from_dict(payload.get("active_actor")),
            party=[_actor_from_dict(item) for item in payload.get("party", []) if item is not None],
            visible_entities=[
                VisibleEntity(
                    id=int(item["id"]),
                    name=str(item["name"]),
                    glyph=item.get("glyph"),
                    faction=item.get("faction"),
                    position=tuple(item["position"]),  # type: ignore[arg-type]
                    hp=item.get("hp"),
                    max_hp=item.get("max_hp"),
                    kind=item.get("kind"),
                    distance=int(item.get("distance", 0)),
                )
                for item in payload.get("visible_entities", [])
            ],
            exits=list(payload.get("exits", [])),
            recent_messages=list(payload.get("recent_messages", [])),
            combat=(
                CombatSnapshot(**payload["combat"])
                if payload.get("combat") is not None
                else None
            ),
            available_actions=list(payload.get("available_actions", [])),
            modal=(
                ModalSnapshot(
                    kind=payload["modal"]["kind"],
                    options=list(payload["modal"].get("options", [])),
                    cursor=payload["modal"].get("cursor"),
                )
                if payload.get("modal") is not None
                else None
            ),
            world_time=WorldTimeSnapshot(**payload["world_time"]),
        )


def _visible_to_dict(entity: VisibleEntity) -> dict[str, Any]:
    payload = asdict(entity)
    payload["position"] = list(entity.position)
    return payload


def _actor_to_dict(actor: ActorSummary | None) -> dict[str, Any] | None:
    if actor is None:
        return None
    return {
        "id": actor.id,
        "name": actor.name,
        "hp": actor.hp,
        "max_hp": actor.max_hp,
        "position": list(actor.position),
        "faction": actor.faction,
        "glyph": actor.glyph,
        "conditions": [_condition_to_dict(c) for c in actor.conditions],
    }


def _actor_from_dict(payload: dict[str, Any] | None) -> ActorSummary | None:
    if payload is None:
        return None
    return ActorSummary(
        id=int(payload["id"]),
        name=str(payload["name"]),
        hp=int(payload["hp"]),
        max_hp=int(payload["max_hp"]),
        position=tuple(payload["position"]),  # type: ignore[arg-type]
        faction=payload.get("faction"),
        glyph=payload.get("glyph"),
        conditions=tuple(
            _condition_from_dict(item) for item in payload.get("conditions", [])
        ),
    )


def _condition_to_dict(summary: ConditionSummary) -> dict[str, Any]:
    return {
        "kind": summary.kind,
        "duration": summary.duration,
        "expires_at": summary.expires_at,
        "rounds_remaining": summary.rounds_remaining,
        "turns_remaining": summary.turns_remaining,
    }


def _condition_from_dict(payload: dict[str, Any]) -> ConditionSummary:
    return ConditionSummary(
        kind=str(payload["kind"]),
        duration=str(payload["duration"]),
        expires_at=payload.get("expires_at"),
        rounds_remaining=int(payload.get("rounds_remaining", 0)),
        turns_remaining=int(payload.get("turns_remaining", 0)),
    )


def observe(app: Any) -> Observation:
    """Build a structured snapshot of the current game state.

    Pure: this function does not mutate `app`, `app.world`, or any
    component store. Calling `observe()` twice in a row with no
    intervening input must produce equal observations.

    Reads from `app.game_state` when available (M49 container); falls
    back to the legacy flat attributes on `app` so harness fixtures
    that construct mock apps without a GameState still work.
    """

    game_state = getattr(app, "game_state", None)
    if game_state is not None:
        world = game_state.world
        ui_mode = game_state.ui_mode
        play_mode_value = (
            game_state.play_mode.value if ui_mode is UIMode.play else None
        )
    else:
        world = app.world
        ui_mode = app.ui_mode
        play_mode_value = app.play_mode.value if ui_mode is UIMode.play else None

    active_entity = _safe_active_actor(app)
    active_actor = _build_actor_summary(app, active_entity) if active_entity is not None else None

    party_summaries = [
        summary
        for entity in app.party
        if (summary := _build_actor_summary(app, entity)) is not None
    ]

    visible = _visible_entities(app, active_actor)
    exits = _exits_from(app, active_actor)
    recent = _recent_messages(app)
    combat = _combat_snapshot(app)
    actions = _available_actions(app, active_actor, combat)
    modal = _modal_snapshot(app)
    world_time = _world_time_snapshot(world.clock)

    return Observation(
        mode={"ui_mode": ui_mode.value, "play_mode": play_mode_value},
        active_actor=active_actor,
        party=party_summaries,
        visible_entities=visible,
        exits=exits,
        recent_messages=recent,
        combat=combat,
        available_actions=actions,
        modal=modal,
        world_time=world_time,
    )


# -- internal helpers ----------------------------------------------------


def _safe_active_actor(app: Any) -> EntityId | None:
    """Return the active actor entity id, or None if unavailable.

    `App.active_actor()` requires `app.party` to be populated. On the
    start/game-over screens that's still true (the party survives
    UIMode transitions), but we guard anyway so `observe()` never
    raises from a partially-initialized harness fixture.
    """
    try:
        entity = app.active_actor()
    except (IndexError, AttributeError):
        return None
    if not app.world.positions.has(entity):
        return None
    return entity


def _build_actor_summary(app: Any, entity: EntityId) -> ActorSummary | None:
    world = app.world
    position = world.positions.get(entity)
    if position is None:
        return None
    stats = world.combat_stats.get(entity)
    if stats is not None:
        hp = stats.hit_points
        max_hp = stats.max_hit_points
    else:
        hp = 0
        max_hp = 0
    faction = world.factions.get(entity)
    presentation = world.presentations.get(entity)
    return ActorSummary(
        id=int(entity),
        name=world.name_for(entity),
        hp=hp,
        max_hp=max_hp,
        position=(position.x, position.y),
        faction=faction.value if faction is not None else None,
        glyph=presentation.glyph if presentation is not None else None,
        conditions=_conditions_for_actor(world, entity),
    )


def _conditions_for_actor(world: Any, entity: EntityId) -> tuple[ConditionSummary, ...]:
    """Project the entity's :class:`ConditionStore` into the snapshot.

    Returns an empty tuple when the actor has no store or no
    conditions. Order matches storage order so a harness can rely on
    the same condition appearing at the same index when polling
    repeatedly without intervening changes.
    """
    store_holder = getattr(world, "conditions", None)
    if store_holder is None:
        return ()
    store = store_holder.get(entity)
    if store is None or not store.conditions:
        return ()
    return tuple(
        ConditionSummary(
            kind=condition.kind.value,
            duration=condition.duration.kind.value,
            expires_at=condition.expires_at,
            rounds_remaining=condition.rounds_remaining,
            turns_remaining=condition.turns_remaining,
        )
        for condition in store.conditions
    )


def _visible_filter(app: Any, origin_x: int, origin_y: int) -> "callable":
    """Return a predicate deciding whether `(x, y)` is visible.

    Prefers `app.memory.visible` (M19 party memory: a frozenset of
    `(x, y)` cells). If memory is absent or exposes a callable
    `visible(x, y)` predicate, that is used directly. Falls back to a
    Chebyshev radius from the active actor when neither is available
    (e.g. a custom harness fixture).
    """

    memory = getattr(app, "memory", None)
    if memory is not None:
        visible_attr = getattr(memory, "visible", None)
        if callable(visible_attr):
            return visible_attr
        if visible_attr is not None:
            visible_set = visible_attr
            return lambda x, y: (x, y) in visible_set
    radius = DEFAULT_VISIBILITY_RADIUS

    def _within(x: int, y: int) -> bool:
        return max(abs(x - origin_x), abs(y - origin_y)) <= radius

    return _within


def _visible_entities(app: Any, active: ActorSummary | None) -> list[VisibleEntity]:
    if active is None:
        return []
    world = app.world
    party_ids = {int(entity) for entity in app.party}
    origin_x, origin_y = active.position
    in_view = _visible_filter(app, origin_x, origin_y)

    visible: list[VisibleEntity] = []
    for entity, position in world.positions.values.items():
        if int(entity) in party_ids:
            continue
        if not in_view(position.x, position.y):
            continue
        stats = world.combat_stats.get(entity)
        creature = world.creatures.get(entity)
        faction = world.factions.get(entity)
        presentation = world.presentations.get(entity)
        kind = creature.kind if creature is not None else _non_creature_kind(world, entity)
        visible.append(
            VisibleEntity(
                id=int(entity),
                name=world.name_for(entity),
                glyph=presentation.glyph if presentation is not None else None,
                faction=faction.value if faction is not None else None,
                position=(position.x, position.y),
                hp=stats.hit_points if stats is not None else None,
                max_hp=stats.max_hit_points if stats is not None else None,
                kind=kind,
                distance=max(abs(position.x - origin_x), abs(position.y - origin_y)),
            )
        )
    visible.sort(key=lambda item: (item.distance, item.id))
    return visible


def _non_creature_kind(world: Any, entity: EntityId) -> str | None:
    """Best-effort tag for non-creature entities (doors, containers, etc.)."""
    if world.doors.has(entity):
        return "door"
    if world.containers.has(entity):
        return "container"
    if world.shops.has(entity):
        return "shop"
    if world.traps.has(entity):
        return "trap"
    if world.corpses.has(entity):
        return "corpse"
    if world.inventories.has(entity):
        # Loose ground-drop entities (M30): Inventory + Position with
        # no creature/container/corpse marker. Tagging them so an
        # agentic playtester can spot a pickup target.
        return "ground_items"
    return None


def _exits_from(app: Any, active: ActorSummary | None) -> list[str]:
    if active is None:
        return []
    world = app.world
    origin_x, origin_y = active.position
    exits: list[str] = []
    for dx, dy, name in _EXIT_DIRECTIONS:
        target_x, target_y = origin_x + dx, origin_y + dy
        tile = world.tile_at(target_x, target_y)
        if tile.blocks_movement:
            continue
        if world.blockers_at(target_x, target_y):
            # Any blocker (entity-occupied tile, terrain) closes the exit.
            continue
        exits.append(name)
    return exits


def _recent_messages(app: Any) -> list[str]:
    state = getattr(app, "messages", None)
    if state is None:
        return []
    # MessageState today exposes `current` + `pending`. No log retention
    # exists yet; we surface what's still on screen so the harness sees
    # the same text the player would.
    collected: list[str] = []
    current = getattr(state, "current", "")
    if current:
        collected.append(current)
    pending = getattr(state, "pending", []) or []
    collected.extend(pending)
    return collected[:RECENT_MESSAGES_LIMIT]


def _combat_snapshot(app: Any) -> CombatSnapshot | None:
    if app.ui_mode is not UIMode.play:
        return None
    if not is_turn_based_play(app.play_mode):
        return None
    activation = app.activation
    return CombatSnapshot(
        round=app.world.clock.rounds,
        active_index=app.active_party_index,
        action_remaining=not activation.action_used,
        movement_remaining=activation.movement_remaining(),
        movement_total=activation.movement_total,
        bonus_remaining=not activation.bonus_action_used,
        reaction_remaining=not activation.reaction_used,
    )


def _available_actions(
    app: Any,
    active: ActorSummary | None,
    combat: CombatSnapshot | None,
) -> list[str]:
    """Best-effort list of action names the active actor can take.

    Once M44 lands, this should consult `app.turn` (the TurnController)
    so the list is authoritative. For now we infer from `UIMode`,
    `PlayMode`, and `ActivationState` directly.
    """

    ui_mode = app.ui_mode
    if ui_mode is UIMode.start:
        return ["start.new_character", "start.yolo", "start.quit"]
    if ui_mode is UIMode.character_creation:
        return ["creation.choose", "creation.back", "creation.confirm"]
    if ui_mode is UIMode.inventory:
        return ["inventory.close", "inventory.drop"]
    if ui_mode is UIMode.quit_confirm:
        return ["quit.confirm", "quit.cancel"]
    if ui_mode is UIMode.game_over:
        return ["game_over.restart", "game_over.quit"]
    if ui_mode is UIMode.message_pager:
        return ["message.advance"]
    if ui_mode is UIMode.targeting:
        # M20: only cursor motion + confirm/cancel are legal while
        # targeting. World-changing actions are explicitly NOT in this
        # list so an agentic playtester knows it has to commit first.
        return [
            "targeting.move_cursor",
            "targeting.confirm",
            "targeting.cancel",
        ]
    if ui_mode is not UIMode.play or active is None:
        return []

    actions: list[str] = ["move", "interact", "inventory", "pickup", "quit"]
    if combat is None:
        # Explore mode: voluntary turn toggle is always available; no
        # action budget gating.
        actions.append("toggle_turn_mode")
        return actions

    # Turn-based: action availability depends on remaining resources.
    if combat.movement_remaining > 0:
        actions.append("move")
    if combat.action_remaining:
        actions.append("attack")
        actions.append("interact")
        actions.append("pickup")
    actions.append("end_turn")
    # Voluntary turn mode can be exited only when no hostiles are present;
    # the toggle is still surfaced so the agent can attempt it. The app
    # itself will refuse with a message if forced turn-based.
    actions.append("toggle_turn_mode")
    # Deduplicate while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for name in actions:
        if name in seen:
            continue
        seen.add(name)
        deduped.append(name)
    return deduped


def _modal_snapshot(app: Any) -> ModalSnapshot | None:
    ui_mode = app.ui_mode
    if ui_mode is UIMode.play:
        return None
    if ui_mode is UIMode.start:
        return ModalSnapshot(
            kind="start",
            options=["new_character", "yolo", "quit"],
        )
    if ui_mode is UIMode.character_creation:
        state = app.character_creation_state
        if state is None:
            return ModalSnapshot(kind="character_creation", options=[])
        # Import here to keep the top-level import surface small and
        # avoid pulling character-creation specifics into `observe()` on
        # paths that never hit the modal.
        from src.core.character_creation import choices_for_step

        return ModalSnapshot(
            kind=f"character_creation.{state.step}",
            options=list(choices_for_step(state)),
            cursor=state.cursor,
        )
    if ui_mode is UIMode.inventory:
        inventory = app.world.inventories.get(app.active_actor())
        items = (
            [stack.item_id for stack in inventory.items] if inventory is not None else []
        )
        return ModalSnapshot(kind="inventory", options=items)
    if ui_mode is UIMode.quit_confirm:
        return ModalSnapshot(kind="quit_confirm", options=["yes", "no"])
    if ui_mode is UIMode.game_over:
        return ModalSnapshot(kind="game_over", options=["restart", "quit"])
    if ui_mode is UIMode.message_pager:
        return ModalSnapshot(kind="message_pager", options=["advance"])
    if ui_mode is UIMode.targeting:
        # M20: surface the cursor position + range so an agentic
        # playtester can plan its next confirm. ``options`` lists the
        # input verbs the modal accepts; cursor is the world-space
        # ``x*100+y`` index isn't useful here, so we leave ``cursor``
        # unset and put the live position into a "target" option token
        # the harness can parse out.
        targeting = getattr(app, "targeting", None)
        options = ["move_cursor", "confirm", "cancel"]
        if targeting is not None:
            options.append(f"cursor={targeting.cursor[0]},{targeting.cursor[1]}")
            options.append(f"origin={targeting.origin[0]},{targeting.origin[1]}")
            options.append(f"range={targeting.range}")
            # M21: surface a one-line examine summary at the cursor so
            # an agentic playtester can read "what's at the cursor"
            # without separately confirming. We use the same composer
            # the examine modal does, so the agent text mirrors what
            # the player would see on confirm.
            memory = getattr(app, "memory", None)
            if memory is not None:
                from src.core.descriptions import examine_tile as _examine

                cx, cy = targeting.cursor
                examine_lines = _examine(app.world, memory, cx, cy)
                if examine_lines:
                    options.append(f"examine={' | '.join(examine_lines)}")
        return ModalSnapshot(kind="targeting", options=options)
    # Future modal kinds (dialogue, shop, examine, help) just report
    # the kind; option content will be filled in as those land.
    return ModalSnapshot(kind=ui_mode.value, options=[])


def _world_time_snapshot(clock: Any) -> WorldTimeSnapshot:
    return WorldTimeSnapshot(
        seconds=int(clock.elapsed_seconds),
        rounds=int(clock.rounds),
        minutes=int(clock.minutes),
        hours=int(clock.hours),
    )
