"""GameState — persistent runtime aggregate (M49).

`GameState` is the explicit container for the parts of the runtime
state that should survive a save/load cycle and that an agentic
playtester (M35) needs as a structured view. Before this milestone,
``App`` held everything as a flat field list — making it hard to tell
which fields belonged in a save file, which were view-only, and which
were curses-loop scaffolding.

What lives here
---------------

- ``world``: the ECS-style world (entities + component stores +
  ``world.clock`` and ``world.schedule``).
- ``party``: the player party (members, active actor, formation order).
- ``turn``: the turn controller (action economy, round counter,
  voluntary turn-based flag, per-actor activation map).
- ``ui_mode`` / ``play_mode``: the screen modal state and the play
  mode within ``UIMode.play``. ``play_mode`` is mirrored from
  ``turn.play_mode`` so the canonical store is the controller; the
  field on this dataclass exists so save dicts can carry the value
  without reaching through the controller, and so the back-compat
  ``app.play_mode`` property has a stable source.
- ``character_creation_state``: in-flight new-character wizard state,
  ``None`` outside the character creation modal.
- ``messages``: on-screen message state (current page + pending pages).
  No log retention yet — M16 may grow this into a true log.
- ``memory``: party shared-vision memory (M19). Visible/seen sets are
  derived from the world but persisted across UI mode flips.
- ``facing``: the player's last movement direction. Persists across
  interactions for "interact with the tile I'm facing" semantics.

What does NOT live here
-----------------------

- ``dispatcher`` / ``effect_applier`` / ``vision``: behaviour wiring
  (they hold callables, system objects, and re-derivable caches).
- ``running``: the curses loop flag.
- ``player`` entity id: re-derivable from
  ``world.player_entity()``; ``App`` caches it for convenience.

These remain on ``App`` itself.

Serialization (M16 precondition)
--------------------------------

``to_dict()`` is JSON-safe. It does not include entity references,
callables, or curses surfaces. The shape is intentionally version-
gated by ``schema_version``: future migrations bump the version and
``from_dict`` switches on it.

Today, ``world.to_dict()`` does not exist yet (M16 will add it). For
now, ``to_dict()`` carries the parts that already have a serialized
shape (``party``, ``turn``, ``messages``, ``ui_mode``, ``play_mode``,
``facing``, ``character_creation_state``) and stores the world clock
and schedule explicitly so a partial save/load can already round-trip
time. The ``world`` field is omitted from the dict shape until M16
extends the ECS world with its own serializer.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from src.core.character_creation import CharacterCreationState
from src.core.modes import PlayMode, UIMode
from src.core.party_state import PartyState
from src.core.time import Schedule, WorldTime
from src.core.turn_controller import TurnController
from src.core.vision import PartyMemory
from src.core.world import World
from src.systems.message_system import MessageState


GAME_STATE_SCHEMA_VERSION: int = 1


@dataclass(slots=True)
class GameState:
    """Persistent runtime aggregate. The save/load target.

    Required positional fields are the structural ones (world, party,
    turn). Everything else has a sensible default so a "fresh" save
    dict missing optional keys can still be loaded.
    """

    world: World
    party: PartyState
    turn: TurnController
    schema_version: int = GAME_STATE_SCHEMA_VERSION
    ui_mode: UIMode = UIMode.start
    character_creation_state: CharacterCreationState | None = None
    messages: MessageState = field(default_factory=MessageState)
    memory: PartyMemory = field(default_factory=PartyMemory)
    facing: tuple[int, int] = (1, 0)

    def __post_init__(self) -> None:
        # The turn controller borrows the same PartyState reference so
        # active_index / focused_index stay consistent across the
        # camera, formation, and rotation. ``from_dict`` may have built
        # a controller against a different PartyState instance (e.g.
        # tests constructing fresh objects) — rebind it here so the
        # aggregate is the single source of truth.
        if self.turn.party_state is not self.party:
            self.turn.party_state = self.party

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def play_mode(self) -> PlayMode:
        """Current PlayMode (mirrored from the turn controller)."""
        return self.turn.play_mode

    @play_mode.setter
    def play_mode(self, value: PlayMode) -> None:
        self.turn.play_mode = value

    @property
    def clock(self) -> WorldTime:
        """The world clock. Lives on ``world`` so movement systems can
        access it without threading ``GameState`` everywhere."""
        return self.world.clock

    @property
    def schedule(self) -> Schedule:
        """The world schedule (M27)."""
        return self.world.schedule

    # ------------------------------------------------------------------
    # Serialization (M16 precondition)
    # ------------------------------------------------------------------

    def to_dict(self, *, include_world: bool = True) -> dict[str, Any]:
        """Return a JSON-safe representation.

        With ``include_world=True`` (the default at and after M16) the
        full ECS world serialization is folded in under the ``"world"``
        key. The legacy partial shape used by M49 tests is preserved by
        passing ``include_world=False`` — useful for observation
        snapshots and any caller that only wants the aggregate fields.
        ``play_mode`` is sourced from the turn controller in either
        mode.
        """

        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "ui_mode": self.ui_mode.value,
            "play_mode": self.turn.play_mode.value,
            "facing": list(self.facing),
            "party": self.party.to_dict(),
            "turn": self.turn.to_dict(),
            "messages": _messages_to_dict(self.messages),
            "memory": _memory_to_dict(self.memory),
            "clock": self.world.clock.to_dict(),
            "schedule": self.world.schedule.to_dict(),
            "character_creation_state": _character_creation_to_dict(
                self.character_creation_state
            ),
        }
        if include_world:
            payload["world"] = self.world.to_dict()
        return payload

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        world: World,
        turn: TurnController,
    ) -> "GameState":
        """Rehydrate a GameState from a save dict.

        ``world`` and ``turn`` are supplied by the caller because the
        world serializer (M16) and the controller construction
        (callable seams for hostile-probe etc.) need information that
        does not live in the save file alone. The caller is expected
        to construct a fresh world (or hand one in from M16's loader)
        and a TurnController bound to the same PartyState we build
        here.

        Missing optional keys fall back to their default values so a
        minimal dict (just ``schema_version``) round-trips.
        """

        schema = int(data.get("schema_version", GAME_STATE_SCHEMA_VERSION))
        if schema != GAME_STATE_SCHEMA_VERSION:
            # Future migrations will dispatch here. For now we accept
            # the current version only.
            raise ValueError(
                f"Unsupported GameState schema version: {schema} "
                f"(expected {GAME_STATE_SCHEMA_VERSION})"
            )

        party_payload = data.get("party")
        if party_payload is not None:
            party = PartyState.from_dict(party_payload)
            # Rebind so the supplied controller observes the loaded
            # PartyState rather than whatever it was built against.
            turn.party_state = party
        else:
            party = turn.party_state

        ui_mode_raw = data.get("ui_mode")
        ui_mode = UIMode(ui_mode_raw) if ui_mode_raw is not None else UIMode.start

        play_mode_raw = data.get("play_mode")
        if play_mode_raw is not None:
            turn.play_mode = PlayMode(play_mode_raw)

        facing_raw = data.get("facing")
        if facing_raw is not None:
            facing = (int(facing_raw[0]), int(facing_raw[1]))
        else:
            facing = (1, 0)

        messages = _messages_from_dict(data.get("messages"))
        memory = _memory_from_dict(data.get("memory"))
        character_state = _character_creation_from_dict(
            data.get("character_creation_state")
        )

        clock_payload = data.get("clock")
        if clock_payload is not None:
            world.clock = WorldTime.from_dict(clock_payload)
        # Schedule is not yet reconstructible from its dict (events lose
        # their concrete subclass info). M16 will close that gap by
        # giving ``ScheduledEvent`` a registry. Today, loading a save
        # built before that registry simply preserves the clock.

        return cls(
            world=world,
            party=party,
            turn=turn,
            schema_version=GAME_STATE_SCHEMA_VERSION,
            ui_mode=ui_mode,
            character_creation_state=character_state,
            messages=messages,
            memory=memory,
            facing=facing,
        )


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _messages_to_dict(state: MessageState) -> dict[str, Any]:
    return {"current": state.current, "pending": list(state.pending)}


def _messages_from_dict(payload: dict[str, Any] | None) -> MessageState:
    if payload is None:
        return MessageState()
    state = MessageState()
    state.current = str(payload.get("current", ""))
    state.pending = [str(item) for item in payload.get("pending", [])]
    return state


def _memory_to_dict(memory: PartyMemory) -> dict[str, Any]:
    """Serialize PartyMemory (M16).

    The remembered tile snapshots (glyph + static feature list) are
    fully captured: each ``(x, y) -> RememberedTile`` entry becomes a
    ``[x, y, {glyph, features}]`` triple. Visible cells are the M19
    party-vision set and round-trip as a plain list of coordinates.

    Older saves that only carried a ``seen`` list of coordinates still
    load via :func:`_memory_from_dict` — the snapshot reconstructs as a
    placeholder ``RememberedTile`` with an empty glyph.
    """
    return {
        "visible": [list(cell) for cell in sorted(memory.visible)],
        "tiles": [
            [
                cell[0],
                cell[1],
                {
                    "glyph": tile.glyph,
                    "features": [
                        {
                            "kind": feature.kind,
                            "glyph": feature.glyph,
                            "is_open": feature.is_open,
                        }
                        for feature in tile.features
                    ],
                },
            ]
            for cell, tile in sorted(memory.tiles.items())
        ],
    }


def _memory_from_dict(payload: dict[str, Any] | None) -> PartyMemory:
    from src.core.vision import RememberedFeature, RememberedTile

    memory = PartyMemory()
    if payload is None:
        return memory
    visible_raw = payload.get("visible", [])
    memory.set_visible({(int(cell[0]), int(cell[1])) for cell in visible_raw})

    # M16 shape: tiles is a list of [x, y, {glyph, features}] triples.
    tiles_raw = payload.get("tiles")
    if tiles_raw is not None:
        for cell in tiles_raw:
            x, y, snapshot = int(cell[0]), int(cell[1]), cell[2]
            features = tuple(
                RememberedFeature(
                    kind=str(item.get("kind", "")),
                    glyph=str(item.get("glyph", " ")),
                    is_open=bool(item.get("is_open", False)),
                )
                for item in snapshot.get("features", [])
            )
            memory.remember(
                x, y,
                RememberedTile(
                    glyph=str(snapshot.get("glyph", " ")),
                    features=features,
                ),
            )
        return memory

    # Back-compat: pre-M16 saves only carried a ``seen`` coordinate list
    # with no per-tile snapshot. Rebuild as placeholders so the renderer
    # still treats those cells as remembered.
    for cell in payload.get("seen", []):
        memory.remember(int(cell[0]), int(cell[1]), RememberedTile(glyph=" "))
    return memory


def _character_creation_to_dict(
    state: CharacterCreationState | None,
) -> dict[str, Any] | None:
    if state is None:
        return None
    return {
        "step": state.step,
        "cursor": state.cursor,
        "race": state.race,
        "character_class": state.character_class,
        "specialization": state.specialization,
        "cantrips": list(state.cantrips),
        "spells": list(state.spells),
        "skills": list(state.skills),
        "base_attributes": dict(state.base_attributes),
    }


def _character_creation_from_dict(
    payload: dict[str, Any] | None,
) -> CharacterCreationState | None:
    if payload is None:
        return None
    return replace(
        CharacterCreationState(),
        step=payload.get("step", "race"),
        cursor=int(payload.get("cursor", 0)),
        race=payload.get("race"),
        character_class=payload.get("character_class"),
        specialization=payload.get("specialization"),
        cantrips=tuple(payload.get("cantrips", ())),
        spells=tuple(payload.get("spells", ())),
        skills=tuple(payload.get("skills", ())),
        base_attributes=dict(payload.get("base_attributes", {})),
    )
