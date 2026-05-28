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
class DeathSavesSummary:
    """The M29 death-save tally for a downed actor.

    ``stable`` is ``True`` once the actor has banked three successes —
    they are no longer rolling but still unconscious until a rest
    restores 1 HP. Surfaced separately from ``conditions`` so an
    agentic playtester can plan around the failure count without
    parsing condition payloads.
    """

    successes: int
    failures: int
    stable: bool


@dataclass(frozen=True, slots=True)
class SpellSlotSummary:
    """Per-level slot ledger surfaced to the agentic playtester (M11).

    ``remaining`` and ``maximum`` cover the same set of levels (a
    level without an entry is implicitly zero on both sides). The
    snapshot is sorted by level for stability across runs.
    """

    level: int
    remaining: int
    maximum: int


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
    spells: tuple[str, ...] = ()
    spell_slots: tuple[SpellSlotSummary, ...] = ()
    level: int = 1
    xp: int = 0
    xp_to_next: int | None = None
    level_up_pending: int | None = None
    death_saves: DeathSavesSummary | None = None


@dataclass(frozen=True, slots=True)
class VisibleEntity:
    """Anything visible to the active actor that isn't the party.

    ``awareness`` is the M23 awareness state the visible entity holds
    toward the active actor — ``unaware`` / ``suspicious`` / ``aware``
    — or ``None`` when the entity carries no :class:`AwarenessTracker`.
    Agents use this to decide whether stealth is worth attempting.
    """

    id: int
    name: str
    glyph: str | None
    faction: str | None
    position: tuple[int, int]
    hp: int | None = None
    max_hp: int | None = None
    kind: str | None = None  # creature.kind if present, else a tag like "door"
    distance: int = 0  # Chebyshev distance from the active actor
    awareness: str | None = None


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
class QuestProgressSummary:
    """A single quest's current state, surfaced to the agentic playtester (M14).

    Only quests the party has interacted with appear here; the implicit
    ``not_offered`` default is omitted to keep the snapshot terse.
    """

    quest_id: str
    state: str


@dataclass(frozen=True, slots=True)
class ShelterSnapshot:
    """The shelter zone the party leader currently stands on (M34).

    ``None`` outside any zone. Surfaces the zone's permission, risk,
    cost, and remaining uses so an agentic playtester can decide
    whether to rest here without inspecting world component stores
    directly. ``label`` is the human-readable zone name (defaults to
    empty string if the map author didn't set one).
    """

    zone_id: str
    label: str
    rest_permission: str
    rest_risk: str
    cost: int
    uses_remaining: int | None
    requirements: tuple[str, ...]


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
    quests: list[QuestProgressSummary] = field(default_factory=list)
    shelter: ShelterSnapshot | None = None

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
            "quests": [
                {"quest_id": quest.quest_id, "state": quest.state}
                for quest in self.quests
            ],
            "shelter": _shelter_to_dict(self.shelter),
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
                    awareness=item.get("awareness"),
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
            quests=[
                QuestProgressSummary(
                    quest_id=str(item["quest_id"]),
                    state=str(item["state"]),
                )
                for item in payload.get("quests", [])
            ],
            shelter=_shelter_from_dict(payload.get("shelter")),
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
        "spells": list(actor.spells),
        "spell_slots": [
            {"level": s.level, "remaining": s.remaining, "maximum": s.maximum}
            for s in actor.spell_slots
        ],
        "level": actor.level,
        "xp": actor.xp,
        "xp_to_next": actor.xp_to_next,
        "level_up_pending": actor.level_up_pending,
        "death_saves": (
            {
                "successes": actor.death_saves.successes,
                "failures": actor.death_saves.failures,
                "stable": actor.death_saves.stable,
            }
            if actor.death_saves is not None
            else None
        ),
    }


def _actor_from_dict(payload: dict[str, Any] | None) -> ActorSummary | None:
    if payload is None:
        return None
    death_saves_payload = payload.get("death_saves")
    death_saves = (
        DeathSavesSummary(
            successes=int(death_saves_payload.get("successes", 0)),
            failures=int(death_saves_payload.get("failures", 0)),
            stable=bool(death_saves_payload.get("stable", False)),
        )
        if isinstance(death_saves_payload, dict)
        else None
    )
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
        spells=tuple(str(item) for item in payload.get("spells", [])),
        spell_slots=tuple(
            SpellSlotSummary(
                level=int(item["level"]),
                remaining=int(item["remaining"]),
                maximum=int(item["maximum"]),
            )
            for item in payload.get("spell_slots", [])
        ),
        level=int(payload.get("level", 1)),
        xp=int(payload.get("xp", 0)),
        xp_to_next=(
            int(payload["xp_to_next"]) if payload.get("xp_to_next") is not None else None
        ),
        level_up_pending=(
            int(payload["level_up_pending"])
            if payload.get("level_up_pending") is not None
            else None
        ),
        death_saves=death_saves,
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


def _shelter_to_dict(snapshot: "ShelterSnapshot | None") -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {
        "zone_id": snapshot.zone_id,
        "label": snapshot.label,
        "rest_permission": snapshot.rest_permission,
        "rest_risk": snapshot.rest_risk,
        "cost": snapshot.cost,
        "uses_remaining": snapshot.uses_remaining,
        "requirements": list(snapshot.requirements),
    }


def _shelter_from_dict(payload: dict[str, Any] | None) -> "ShelterSnapshot | None":
    if payload is None:
        return None
    uses_raw = payload.get("uses_remaining")
    return ShelterSnapshot(
        zone_id=str(payload["zone_id"]),
        label=str(payload.get("label", "")),
        rest_permission=str(payload.get("rest_permission", "")),
        rest_risk=str(payload.get("rest_risk", "")),
        cost=int(payload.get("cost", 0)),
        uses_remaining=int(uses_raw) if uses_raw is not None else None,
        requirements=tuple(str(item) for item in payload.get("requirements", ())),
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
    quests = _quest_progress(app)
    shelter = _shelter_snapshot(app)

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
        quests=quests,
        shelter=shelter,
    )


def _quest_progress(app: Any) -> list[QuestProgressSummary]:
    """Project the party's quest log into the snapshot (M14).

    Returns an empty list when the party has not touched any quest yet
    (the implicit ``not_offered`` default is omitted). Order matches
    the log's stored order so a harness can rely on stable indices
    across repeated polls without intervening progress.
    """

    party = getattr(app, "party", None)
    if party is None:
        return []
    log = getattr(party, "quests", None)
    if log is None:
        return []
    return [
        QuestProgressSummary(quest_id=quest_id, state=state.value)
        for quest_id, state in log.states.items()
    ]


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
    level, xp, xp_to_next = _level_xp_for_actor(world, entity)
    pending = _pending_level_for_actor(world, entity)
    return ActorSummary(
        id=int(entity),
        name=world.name_for(entity),
        hp=hp,
        max_hp=max_hp,
        position=(position.x, position.y),
        faction=faction.value if faction is not None else None,
        glyph=presentation.glyph if presentation is not None else None,
        conditions=_conditions_for_actor(world, entity),
        spells=_spells_for_actor(world, entity),
        spell_slots=_spell_slots_for_actor(world, entity),
        level=level,
        xp=xp,
        xp_to_next=xp_to_next,
        level_up_pending=pending,
        death_saves=_death_saves_for_actor(world, entity),
    )


def _death_saves_for_actor(world: Any, entity: EntityId) -> DeathSavesSummary | None:
    """Project the M29 DeathSaves row into the snapshot."""
    store = getattr(world, "death_saves", None)
    if store is None:
        return None
    saves = store.get(entity)
    if saves is None:
        return None
    return DeathSavesSummary(
        successes=int(saves.successes),
        failures=int(saves.failures),
        stable=bool(saves.stable),
    )


def _level_xp_for_actor(world: Any, entity: EntityId) -> tuple[int, int, int | None]:
    """Return ``(level, xp, xp_to_next)`` for the actor (M25).

    ``level`` mirrors the character sheet's level; ``xp`` is the current
    XP total (zero for non-PCs); ``xp_to_next`` is the XP needed to
    reach the next known level threshold (``None`` once the actor is at
    the engine's max level).
    """

    from src.core.leveling import next_threshold

    character = getattr(world, "characters", None)
    sheet_component = character.get(entity) if character is not None else None
    level = sheet_component.sheet.level if sheet_component is not None else 1
    xp_store = getattr(world, "experience_points", None)
    xp_component = xp_store.get(entity) if xp_store is not None else None
    xp = xp_component.value if xp_component is not None else 0
    threshold = next_threshold(level)
    return level, xp, threshold


def _pending_level_for_actor(world: Any, entity: EntityId) -> int | None:
    """Return the pending ``target_level`` for the actor, or ``None``.

    Surfaces the M25 :class:`LevelUpAvailable` marker so an agentic
    playtester can detect "the level-up modal will open" without
    inspecting component stores directly.
    """

    pending_store = getattr(world, "level_up_pending", None)
    if pending_store is None:
        return None
    pending = pending_store.get(entity)
    if pending is None:
        return None
    return pending.target_level


def _spells_for_actor(world: Any, entity: EntityId) -> tuple[str, ...]:
    """Project the actor's :class:`SpellList` into a tuple of ids.

    Returns an empty tuple when the actor has no list. Order matches
    storage order so a harness can map letters to spells the same way
    the in-game menu does.
    """
    store = getattr(world, "spell_lists", None)
    if store is None:
        return ()
    spell_list = store.get(entity)
    if spell_list is None:
        return ()
    return tuple(spell_list.known)


def _spell_slots_for_actor(world: Any, entity: EntityId) -> tuple[Any, ...]:
    """Project the actor's :class:`SpellSlots` into stable per-level summaries.

    Levels are sorted ascending so the snapshot order is independent
    of the underlying dict iteration order (Python 3.7+ preserves
    insertion order, but a harness shouldn't have to rely on that).
    """
    store = getattr(world, "spell_slots", None)
    if store is None:
        return ()
    slots = store.get(entity)
    if slots is None:
        return ()
    summaries: list[SpellSlotSummary] = []
    levels = sorted(
        set(slots.slots_by_level.keys()) | set(slots.max_by_level.keys())
    )
    for level in levels:
        summaries.append(
            SpellSlotSummary(
                level=int(level),
                remaining=int(slots.slots_by_level.get(level, 0)),
                maximum=int(slots.max_by_level.get(level, 0)),
            )
        )
    return tuple(summaries)


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
    awareness_store = getattr(world, "awareness_trackers", None)

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
        awareness_value: str | None = None
        if awareness_store is not None:
            tracker = awareness_store.get(entity)
            if tracker is not None:
                awareness_value = tracker.state_of(active.id).value
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
                awareness=awareness_value,
            )
        )
    visible.sort(key=lambda item: (item.distance, item.id))
    return visible


def _actor_has_spells(app: Any, entity_id: int) -> bool:
    """True when the entity has any known spell in its :class:`SpellList`.

    Read through the projection so we don't import SpellList at this
    layer; the harness sees the same data as the in-game menu.
    """
    world = app.world
    store = getattr(world, "spell_lists", None)
    if store is None:
        return False
    spell_list = store.get(EntityId(entity_id))
    return spell_list is not None and bool(spell_list.known)


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
    if world.npcs.has(entity):
        return "npc"
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
    if ui_mode is UIMode.dialogue:
        # M13: only option selection / close are legal while the
        # dialogue modal is up.
        return ["dialogue.select_option", "dialogue.close"]
    if ui_mode is UIMode.shop:
        # M13/M17: full buy/sell UI is pending; the close verb is the
        # only one that fully resolves today. ``buy`` / ``sell`` are
        # surfaced so a playtester can see the reserved keys (they
        # currently emit a placeholder message).
        return ["shop.close", "shop.buy", "shop.sell"]
    if ui_mode is UIMode.spell_menu:
        # M11: spell menu only accepts a letter pick or cancel.
        return ["spell_menu.pick", "spell_menu.cancel"]
    if ui_mode is UIMode.rest_menu:
        # M34: rest menu accepts a kind pick (short / long) or cancel.
        return ["rest_menu.short", "rest_menu.long", "rest_menu.cancel"]
    if ui_mode is UIMode.level_up:
        # M25: level-up modal accepts confirm or dismiss only; the
        # play-screen action set is hidden while the modal is up.
        return ["level_up.confirm", "level_up.dismiss"]
    if ui_mode is not UIMode.play or active is None:
        return []

    actions: list[str] = [
        "move",
        "interact",
        "inventory",
        "pickup",
        "sneak",
        "perceive",
        "rest",
        "quit",
    ]
    # The cast action is only meaningful when the active actor has a
    # spell list. We surface it as the same "cast" token regardless of
    # the catalog content so the harness sees a uniform name.
    has_spells = active is not None and _actor_has_spells(app, active.id)
    if has_spells:
        actions.append("cast")
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
        if has_spells:
            actions.append("cast")
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
    if ui_mode is UIMode.dialogue:
        # M13: surface the speaker, the current line, and the option
        # labels so an agentic playtester can plan its selection.
        dialogue = getattr(app, "dialogue", None)
        options: list[str] = []
        if dialogue is not None:
            node = dialogue.node()
            speaker_name = app.world.name_for(dialogue.speaker)
            options.append(f"speaker={speaker_name}")
            options.append(f"node={dialogue.current_node}")
            options.append(f"line={node.line.text}")
            for index, option in enumerate(node.options):
                options.append(f"option_{index + 1}={option.label}")
            options.append("close")
        return ModalSnapshot(kind="dialogue", options=options)
    if ui_mode is UIMode.shop:
        partner = getattr(app, "shop_partner", None)
        # Reserved keys for the M17 follow-up; ``close`` is the only
        # one that fully resolves today.
        options = ["close", "buy", "sell"]
        if partner is not None:
            shop = app.world.shops.get(partner)
            if shop is not None:
                options.append(f"shopkeeper={shop.name}")
        return ModalSnapshot(kind="shop", options=options)
    if ui_mode is UIMode.spell_menu:
        # M11: surface the active actor's known spells as the modal's
        # options so the harness can pick one without consulting the
        # actor's spell_lists store directly.
        active = _safe_active_actor(app)
        options = []
        if active is not None:
            spell_list = app.world.spell_lists.get(active)
            if spell_list is not None:
                options = list(spell_list.known)
        return ModalSnapshot(kind="spell_menu", options=options)
    if ui_mode is UIMode.rest_menu:
        # M34: surface the rest kinds. The active actor's tile
        # determines which kinds are *actually* permitted, but the
        # modal itself always offers both keys — the rest system
        # produces the refusal banner on an unsupported pick so the
        # agent learns the constraint by trying it.
        return ModalSnapshot(kind="rest_menu", options=["short", "long", "cancel"])
    if ui_mode is UIMode.level_up:
        # M25: surface the pending member, target level, and confirm
        # verbs. An agentic playtester reads ``target=<id>:level=<n>``
        # from the options list to plan its next confirm.
        options = ["confirm", "dismiss"]
        world = getattr(app, "world", None)
        party = getattr(app, "party", None)
        if world is not None and party is not None:
            members = getattr(party, "members", [])
            for member in members:
                pending = world.level_up_pending.get(member)
                if pending is None:
                    continue
                name = world.name_for(member)
                options.append(f"target={int(member)}")
                options.append(f"name={name}")
                options.append(f"level={pending.target_level}")
                break
        return ModalSnapshot(kind="level_up", options=options)
    # Future modal kinds (examine, help) just report the kind;
    # option content will be filled in as those land.
    return ModalSnapshot(kind=ui_mode.value, options=[])


def _world_time_snapshot(clock: Any) -> WorldTimeSnapshot:
    return WorldTimeSnapshot(
        seconds=int(clock.elapsed_seconds),
        rounds=int(clock.rounds),
        minutes=int(clock.minutes),
        hours=int(clock.hours),
    )


def _shelter_snapshot(app: Any) -> ShelterSnapshot | None:
    """Return a :class:`ShelterSnapshot` for the party leader's tile, or ``None``.

    Reads the party leader's position and projects the
    :class:`~src.core.shelter.ShelterZone` (if any) covering that tile.
    The leader's tile (not the active actor's) is used so the answer
    stays stable across companion rotations in turn-based play —
    consistent with how :func:`tick_zone_transitions` chooses which
    entity drives entry / exit messages.
    """

    world = getattr(app, "world", None)
    if world is None:
        return None
    registry = getattr(world, "shelter_zones", None)
    if registry is None or not registry.zones:
        return None
    party = getattr(app, "party", None)
    if party is None:
        return None
    members = getattr(party, "members", [])
    if not members:
        return None
    leader = members[0]
    position = world.positions.get(leader)
    if position is None:
        return None
    zone = registry.at(position.x, position.y)
    if zone is None:
        return None
    return ShelterSnapshot(
        zone_id=zone.zone_id,
        label=zone.label,
        rest_permission=zone.rest_permission.value,
        rest_risk=zone.rest_risk.value,
        cost=int(zone.cost),
        uses_remaining=zone.uses_remaining,
        requirements=tuple(zone.requirements),
    )
