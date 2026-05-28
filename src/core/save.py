"""Save/restore architecture (M16).

The runtime keeps two parallel concerns: persistent state that should
survive a save/load cycle, and transient state that re-derives from a
fresh launch (curses surfaces, dispatcher wiring, vision cache, in-
progress autowalk, current modal). Only the persistent half is
serialized; everything else is reconstructed from the standard
``create_app`` factory and then patched with the loaded ``GameState``.

Save format
-----------

The save file is JSON-only (no pickle, no eval). The top-level shape
mirrors :meth:`GameState.to_dict` plus two convenience fields:

::

    {
      "schema_version": 1,
      "format": "frai.save",
      "world": { ... },
      "party": { ... },
      "turn": { ... },
      "ui_mode": "play",
      "play_mode": "explore",
      "facing": [1, 0],
      "messages": { ... },
      "memory": { ... },
      "clock": { ... },
      "schedule": { ... },
      "character_creation_state": {...} | null,
      "player_entity_id": 1,
      "loot_rng_state": [...]    # JSON-encoded Python rng state
    }

``schema_version`` controls migration; today only version ``1`` is
defined. The :func:`migrate` helper is a no-op placeholder so future
shape changes can be slotted in without touching the load path.

What is and isn't saved
-----------------------

Persisted (re-derivable but cheap to keep around):

- ECS world (entities + components + tiles + clock + schedule)
- party state (members, active/focused index, follow order)
- turn controller bookkeeping (round, voluntary flag, per-actor
  activation map)
- UI / play mode, facing, messages, party memory, character creation
  state, the player entity id, loot RNG state.

Skipped (transient or behaviour wiring):

- ``Dispatcher`` and the systems it owns
- ``EffectApplier``
- ``VisionSystem`` (rebuilt and immediately ticked on load)
- in-progress ``autowalk`` request (M22 design choice — see help)
- the curses screen / loop ``running`` flag
- ``GodMode`` debug marker (M33 — never persisted)
- per-input modal state: ``targeting`` (M20/M21), ``dialogue`` (M13),
  ``shop_partner`` (M17). A save written mid-modal loses the in-flight
  selection. To prevent the player getting stuck with ``ui_mode``
  pointing at a modal whose state is gone, :func:`load_game` demotes
  any orphaned modal mode back to :class:`UIMode.play` (issue #88).

Default save location
---------------------

:func:`default_save_path` returns ``~/.local/share/frai/save.json``
(XDG-ish; overridable via the ``FRAI_SAVE_PATH`` environment variable).
Tests pass an explicit path so the per-test temp dir is the source of
truth.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.core.entity import EntityId
from src.core.game_state import GAME_STATE_SCHEMA_VERSION, GameState
from src.core.modes import PlayMode, UIMode, play_mode_for_state
from src.core.party_state import PartyState
from src.core.turn_controller import TurnController
from src.core.turns import ActivationState
from src.core.world import World

if TYPE_CHECKING:
    from src.app import App


SAVE_FORMAT_TAG: str = "frai.save"


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def default_save_path() -> Path:
    """Return the default save file path.

    Honours ``$FRAI_SAVE_PATH`` if set so power users (and the playtest
    harness) can redirect saves without monkeypatching. Otherwise falls
    back to ``$XDG_DATA_HOME/frai/save.json`` or ``~/.local/share/frai``.
    """
    override = os.environ.get("FRAI_SAVE_PATH")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "frai" / "save.json"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def save_game(app: "App", path: Path | None = None) -> Path:
    """Serialize ``app`` and write it to ``path`` (or the default).

    Returns the path written to. The parent directory is created if it
    doesn't already exist. The on-disk format is pretty-printed JSON
    with stable key ordering so saves diff cleanly and round-trip to
    the same bytes.
    """
    target = path if path is not None else default_save_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _app_to_payload(app)
    text = json.dumps(payload, indent=2, sort_keys=True)
    target.write_text(text, encoding="utf-8")
    return target


def load_game(path: Path | None = None) -> "App":
    """Reconstruct an :class:`App` from a save file.

    Reads ``path`` (or :func:`default_save_path`), migrates the payload
    if it carries an older ``schema_version``, and then rebuilds the
    runtime in three phases:

    1. Build a fresh ``App`` via ``create_app`` to get the dispatcher,
       vision system, loot RNG, and effect applier wired correctly.
    2. Replace the in-memory ``GameState`` with one rehydrated from the
       loaded payload, including a freshly-built ``World`` and
       ``TurnController``.
    3. Reseat the hostile-presence / can-take-turn probes on the loaded
       ``TurnController`` so they read through the *loaded* ``GameState``
       rather than the throw-away one ``create_app`` built.
    """
    # Imported lazily so the core module can be imported without
    # pulling in curses-adjacent code paths (App / dispatcher).
    from src.app import create_app

    source = path if path is not None else default_save_path()
    raw = source.read_text(encoding="utf-8")
    payload = json.loads(raw)
    payload = migrate(payload)

    # Phase 1: scaffold a real App so dispatcher / loot rng / vision
    # exist. The scaffolding world and game_state are discarded.
    scaffold = create_app()

    # Phase 2: rehydrate the persistent state.
    world = World.from_dict(payload["world"])
    party = PartyState.from_dict(payload["party"])
    turn = _turn_controller_from_dict(payload.get("turn", {}), party)
    game_state = GameState.from_dict(payload, world=world, turn=turn)
    # ``from_dict`` rebinds ``turn.party_state`` to whatever PartyState
    # the payload describes; ensure the same instance the aggregate
    # carries is what the controller sees.
    game_state.turn.party_state = game_state.party

    # Phase 3: probe rebinding. Closures must read through
    # ``game_state.world`` so a future ``restart`` (which replaces the
    # world) keeps working — mirrors create_app's wiring.
    from src.systems.awareness_system import hostiles_requiring_battle

    def _hostiles_probe() -> bool:
        return bool(
            hostiles_requiring_battle(
                game_state.world, game_state.party.members
            )
        )

    def _can_take_turn_probe(entity: EntityId) -> bool:
        stats = game_state.world.combat_stats.get(entity)
        return game_state.world.positions.has(entity) and (
            stats is None or stats.hit_points > 0
        )

    game_state.turn.hostiles_probe = _hostiles_probe
    game_state.turn.can_take_turn = _can_take_turn_probe
    # Reconcile play mode against the loaded world's hostile set so a
    # save written during combat can't drop into explore on load (and
    # vice versa). The voluntary flag survives because it's player
    # intent, not derived state.
    game_state.turn.play_mode = play_mode_for_state(
        _hostiles_probe(), game_state.turn.voluntary_turn_based
    )

    player_id_raw = payload.get("player_entity_id")
    if player_id_raw is None:
        player = world.player_entity()
    else:
        player = EntityId(int(player_id_raw))

    # Reseat the scaffold App with the loaded state. ``vision`` is the
    # default VisionSystem from the scaffold; ``refresh_vision`` ticks
    # it against the loaded world so memory & visible cells match.
    scaffold.game_state = game_state
    scaffold.player = player
    scaffold.autowalk = None
    scaffold.messages.emit("")  # clear scaffold start messages
    scaffold.messages.current = game_state.messages.current
    scaffold.messages.pending = list(game_state.messages.pending)
    rng_state = payload.get("loot_rng_state")
    if rng_state is not None:
        scaffold.loot_rng.setstate(_rng_state_from_dict(rng_state))
    # Repair stale modal ``ui_mode`` values whose backing transient state
    # was dropped by the save (issue #88). ``ui_mode`` is persisted but
    # the per-modal state (``targeting``, ``dialogue``, ``shop_partner``)
    # is not — so a save written mid-modal lands on load with
    # ``ui_mode == X`` but the state hook ``None``. Every input handler
    # gates on the state being non-None, so the player gets silently
    # stuck. Demote back to ``UIMode.play`` so input flows normally; the
    # in-flight modal selection is lost (acceptable for now — see the
    # transient-state docstring above).
    _repair_stale_modal(scaffold)
    scaffold.refresh_vision()
    return scaffold


# Modal UI modes whose interactive state lives on ``App`` rather than in
# ``GameState``. Listed once so the load-time repair and any future
# audit can share the same source of truth.
_TRANSIENT_MODAL_MODES: frozenset[UIMode] = frozenset(
    {
        UIMode.targeting,
        UIMode.dialogue,
        UIMode.shop,
        UIMode.help,
        UIMode.roster,
        UIMode.character_sheet,
    }
)


def _repair_stale_modal(app: "App") -> None:
    """Drop a stale modal ``ui_mode`` whose transient state is missing.

    Save serializes ``ui_mode`` but skips ``app.targeting``,
    ``app.dialogue``, and ``app.shop_partner`` (they're per-input
    modals, not gameplay facts). On load that combination leaves the
    player stuck because every input handler short-circuits when the
    state hook is ``None``. We unconditionally demote those orphaned
    modals back to :class:`UIMode.play`.
    """

    if app.ui_mode not in _TRANSIENT_MODAL_MODES:
        return
    if app.ui_mode is UIMode.targeting and app.targeting is not None:
        return
    if app.ui_mode is UIMode.dialogue and app.dialogue is not None:
        return
    if app.ui_mode is UIMode.shop and app.shop_partner is not None:
        return
    if app.ui_mode is UIMode.help and app.help_state is not None:
        return
    if app.ui_mode is UIMode.roster and app.roster_state is not None:
        return
    if (
        app.ui_mode is UIMode.character_sheet
        and app.character_sheet_state is not None
    ):
        return
    app.ui_mode = UIMode.play


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def migrate(payload: dict[str, Any]) -> dict[str, Any]:
    """Bring an older save payload up to the current schema version.

    Today this is a no-op (schema version 1 is the only defined
    version) but the seam exists so future shape changes can be slotted
    in without touching the load path. Migration functions are
    *non-destructive*: they return a new dict so test fixtures can
    assert against the pre-migration shape.
    """
    schema = int(payload.get("schema_version", GAME_STATE_SCHEMA_VERSION))
    # Migration ladder. Each step transforms the payload into the next
    # schema version. Order matters: pre-version-1 saves go through
    # _migrate_0_to_1 first, etc.
    if schema == 0:
        payload = _migrate_0_to_1(payload)
        schema = 1
    if schema != GAME_STATE_SCHEMA_VERSION:
        raise ValueError(
            f"Save file uses unsupported schema_version={schema}; "
            f"this build understands up to {GAME_STATE_SCHEMA_VERSION}."
        )
    return payload


def _migrate_0_to_1(payload: dict[str, Any]) -> dict[str, Any]:
    """Schema 0 -> 1 migration (placeholder).

    Schema 0 was never released — this function exists so the
    migration ladder has a real entry to dispatch to as soon as the
    next shape change lands. It returns a copy of the payload with the
    schema version bumped.
    """
    bumped = dict(payload)
    bumped["schema_version"] = 1
    return bumped


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------


def _app_to_payload(app: "App") -> dict[str, Any]:
    payload = app.game_state.to_dict()
    payload["format"] = SAVE_FORMAT_TAG
    payload["player_entity_id"] = int(app.player)
    payload["loot_rng_state"] = _rng_state_to_dict(app.loot_rng.getstate())
    return payload


def _rng_state_to_dict(state: tuple) -> list[Any]:
    """JSON-encode a ``random.Random.getstate`` tuple."""
    # state is (version: int, internal_state: tuple[int...], gauss: float|None)
    return [state[0], list(state[1]), state[2]]


def _rng_state_from_dict(payload: list[Any]) -> tuple:
    """Reverse of :func:`_rng_state_to_dict`."""
    return (int(payload[0]), tuple(payload[1]), payload[2])


def _turn_controller_from_dict(
    payload: dict[str, Any],
    party: PartyState,
) -> TurnController:
    """Rebuild a TurnController with placeholder probes.

    Probes are replaced by the loader once the GameState is in hand.
    """
    controller = TurnController(
        party_state=party,
        hostiles_probe=lambda: False,
        can_take_turn=lambda _entity: True,
        voluntary_turn_based=bool(payload.get("voluntary_turn_based", False)),
        play_mode=PlayMode(payload.get("play_mode", PlayMode.explore.value)),
        round_number=int(payload.get("round_number", 0)),
    )
    # ``active_index`` lives on PartyState; honour the controller-side
    # value if the save carries one (older payloads pre-M45 might not).
    if "active_index" in payload:
        party.active_index = int(payload["active_index"])

    for entity_key, activation_payload in payload.get("activations", {}).items():
        entity = EntityId(int(entity_key))
        controller._activations[entity] = ActivationState(
            movement_used=float(activation_payload.get("movement_used", 0.0)),
            movement_total=float(activation_payload.get(
                "movement_total", ActivationState().movement_total
            )),
            action_used=bool(activation_payload.get("action_used", False)),
            bonus_action_used=bool(
                activation_payload.get("bonus_action_used", False)
            ),
            reaction_used=bool(activation_payload.get("reaction_used", False)),
            extra_actions_used=int(
                activation_payload.get("extra_actions_used", 0)
            ),
            extra_actions_total=int(
                activation_payload.get("extra_actions_total", 0)
            ),
        )
    return controller
