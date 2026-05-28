"""Tests for M16 save/restore.

The save module owns three concerns:

1. Producing a JSON-only payload that captures everything an ``App``
   needs to resume play.
2. Reading that payload back into an ``App`` that behaves the same as
   the one we saved.
3. Migrating older payloads forward without crashing.

These tests exercise all three. The file-system interactions go
through ``tmp_path`` so the user's real save file is never touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.app import create_app
from src.core.actions import DropItemAttempt, PickupAttempt
from src.core.components import (
    AI,
    AIBehaviorType,
    BlocksMovement,
    Faction,
    Name,
    Position,
    Presentation,
    Weapon,
)
from src.core.creatures import combat_stats_for_creature, creature_for_key
from src.core.game_state import GAME_STATE_SCHEMA_VERSION
from src.core.items import add_item
from src.core.modes import PlayMode, UIMode
from src.core.save import (
    SAVE_FORMAT_TAG,
    default_save_path,
    load_game,
    migrate,
    save_game,
)
from src.core.vision import RememberedFeature, RememberedTile
from src.core.world import World
from src.map.tiles import FLOOR, HORIZONTAL_WALL, OUTSIDE


# ---------------------------------------------------------------------------
# Save path
# ---------------------------------------------------------------------------


def test_default_save_path_honors_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRAI_SAVE_PATH", "/tmp/frai-custom/save.json")
    assert default_save_path() == Path("/tmp/frai-custom/save.json")


def test_default_save_path_falls_back_to_xdg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("FRAI_SAVE_PATH", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_save_path() == tmp_path / "frai" / "save.json"


# ---------------------------------------------------------------------------
# save_game
# ---------------------------------------------------------------------------


def test_save_game_writes_a_json_file(tmp_path: Path) -> None:
    app = create_app()
    path = tmp_path / "save.json"
    written = save_game(app, path)
    assert written == path
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["format"] == SAVE_FORMAT_TAG
    assert payload["schema_version"] == GAME_STATE_SCHEMA_VERSION
    assert "world" in payload
    assert isinstance(payload["world"]["tiles"], list)


def test_save_game_creates_parent_directory(tmp_path: Path) -> None:
    app = create_app()
    path = tmp_path / "nested" / "dir" / "save.json"
    save_game(app, path)
    assert path.exists()


def test_save_game_payload_round_trips_through_json(tmp_path: Path) -> None:
    """No callables / entities / dataclass instances leak into the file."""
    app = create_app()
    path = tmp_path / "save.json"
    save_game(app, path)
    # Re-parse and re-serialize: identical bytes confirm full JSON purity.
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert json.dumps(payload, indent=2, sort_keys=True) == path.read_text(
        encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_round_trip_produces_identical_bytes(tmp_path: Path) -> None:
    """save -> load -> save yields the same on-disk text."""
    app = create_app()
    first = tmp_path / "first.json"
    save_game(app, first)

    app2 = load_game(first)
    second = tmp_path / "second.json"
    save_game(app2, second)

    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")


def test_round_trip_preserves_world_topology(tmp_path: Path) -> None:
    app = create_app()
    width = app.world.width
    height = app.world.height
    saved_tile = app.world.tile_at(width // 2, height // 2)

    path = tmp_path / "save.json"
    save_game(app, path)
    loaded = load_game(path)

    assert loaded.world.width == width
    assert loaded.world.height == height
    assert loaded.world.tile_at(width // 2, height // 2) is saved_tile


def test_round_trip_preserves_player_entity(tmp_path: Path) -> None:
    app = create_app()
    player_position = app.world.positions.require(app.player)

    path = tmp_path / "save.json"
    save_game(app, path)
    loaded = load_game(path)

    assert int(loaded.player) == int(app.player)
    new_position = loaded.world.positions.require(loaded.player)
    assert (new_position.x, new_position.y) == (player_position.x, player_position.y)


def test_round_trip_preserves_party_and_active_actor(tmp_path: Path) -> None:
    app = create_app()
    members_before = list(app.party.members)

    path = tmp_path / "save.json"
    save_game(app, path)
    loaded = load_game(path)

    assert list(loaded.party.members) == members_before
    assert loaded.party.active_index == app.party.active_index
    assert loaded.party.size == app.party.size


def test_round_trip_preserves_inventory(tmp_path: Path) -> None:
    app = create_app()
    add_item(app.world.inventories.require(app.player), "consumable.healing_potion", 3)

    path = tmp_path / "save.json"
    save_game(app, path)
    loaded = load_game(path)

    loaded_inv = loaded.world.inventories.require(loaded.player)
    quantities = {stack.item_id: stack.quantity for stack in loaded_inv.items}
    assert quantities.get("consumable.healing_potion") == 3


def test_round_trip_preserves_world_clock(tmp_path: Path) -> None:
    app = create_app()
    app.world.clock.advance_seconds(360)

    path = tmp_path / "save.json"
    save_game(app, path)
    loaded = load_game(path)

    assert loaded.world.clock.elapsed_seconds == 360


def test_round_trip_preserves_party_memory_glyphs_and_features(
    tmp_path: Path,
) -> None:
    """Remembered tile snapshots (glyph + features) survive a round trip.

    The visible set is transient — :meth:`App.refresh_vision` recomputes
    it on load — so we deliberately seed memory at a cell far from the
    starting position and assert the snapshot survives unchanged.
    """
    app = create_app()
    # A cell far from the player so the post-load vision tick can't
    # overwrite our seeded snapshot.
    far_cell = (0, 0)
    app.memory.remember(*far_cell, RememberedTile(
        glyph="+",
        features=(RememberedFeature(kind="door", glyph="+", is_open=False),),
    ))

    path = tmp_path / "save.json"
    save_game(app, path)
    loaded = load_game(path)

    assert far_cell in loaded.memory.tiles
    remembered = loaded.memory.tiles[far_cell]
    assert remembered.glyph == "+"
    assert remembered.features == (
        RememberedFeature(kind="door", glyph="+", is_open=False),
    )


def test_memory_to_dict_round_trips_directly() -> None:
    """The memory serializer (independent of refresh_vision)."""
    from src.core.game_state import _memory_from_dict, _memory_to_dict
    from src.core.vision import PartyMemory

    memory = PartyMemory()
    memory.set_visible({(1, 1), (2, 2)})
    memory.remember(3, 3, RememberedTile(
        glyph="=",
        features=(RememberedFeature(kind="container", glyph="=", is_open=True),),
    ))

    rebuilt = _memory_from_dict(_memory_to_dict(memory))
    assert rebuilt.visible == frozenset({(1, 1), (2, 2)})
    assert rebuilt.tiles[(3, 3)].glyph == "="
    assert rebuilt.tiles[(3, 3)].features == (
        RememberedFeature(kind="container", glyph="=", is_open=True),
    )


def test_round_trip_preserves_ui_and_play_mode(tmp_path: Path) -> None:
    app = create_app()
    app.ui_mode = UIMode.play
    app.facing = (-1, 1)

    path = tmp_path / "save.json"
    save_game(app, path)
    loaded = load_game(path)

    assert loaded.ui_mode is UIMode.play
    assert loaded.facing == (-1, 1)


# ---------------------------------------------------------------------------
# Loaded game continues to behave
# ---------------------------------------------------------------------------


def test_loaded_game_can_continue_movement(tmp_path: Path) -> None:
    app = create_app()
    app.ui_mode = UIMode.play

    path = tmp_path / "save.json"
    save_game(app, path)
    loaded = load_game(path)
    loaded.ui_mode = UIMode.play

    direction_key, dx, dy = _pick_open_direction(loaded)
    before_position = loaded.world.positions.require(loaded.player)
    # Position is a mutable dataclass; copy the values before move
    # because the move effect mutates the same instance in place.
    before = (before_position.x, before_position.y)
    loaded.handle_key(direction_key)
    after_position = loaded.world.positions.require(loaded.player)
    after = (after_position.x, after_position.y)
    assert after == (before[0] + dx, before[1] + dy)


def test_loaded_game_can_pickup_and_drop(tmp_path: Path) -> None:
    app = create_app()
    app.ui_mode = UIMode.play
    # Add a potion to the player's tile by way of inventory + drop.
    add_item(app.world.inventories.require(app.player), "consumable.healing_potion")

    path = tmp_path / "save.json"
    save_game(app, path)
    loaded = load_game(path)
    loaded.ui_mode = UIMode.play

    loaded.apply_effects(
        loaded.dispatcher.dispatch(
            DropItemAttempt(
                actor=loaded.player,
                item_id="consumable.healing_potion",
                quantity=1,
            ),
            loaded.world,
        )
    )
    # Drop placed an item on the ground; pickup should retrieve it.
    loaded.apply_effects(
        loaded.dispatcher.dispatch(
            PickupAttempt(actor=loaded.player),
            loaded.world,
        )
    )
    loaded_inv = loaded.world.inventories.require(loaded.player)
    quantities = {stack.item_id: stack.quantity for stack in loaded_inv.items}
    assert quantities.get("consumable.healing_potion", 0) >= 1


def test_loaded_game_can_enter_combat(tmp_path: Path) -> None:
    """An enemy adjacent to the player on load should force turn-based."""
    app = _build_minimal_combat_app()

    path = tmp_path / "save.json"
    save_game(app, path)
    loaded = load_game(path)

    assert loaded.turn.play_mode is PlayMode.turn_based


def test_loaded_game_has_working_autowalk(tmp_path: Path) -> None:
    """Autowalk is transient state; after load the player can still
    initiate a fresh walk via the capital direction key."""
    app = create_app()
    app.ui_mode = UIMode.play
    # Park the party in explore mode so the autowalk action isn't gated
    # by an action-economy budget. Hostile-vs-explore reconciliation
    # happens through the hostile probe — drop the frogs so the loaded
    # game lands in explore.
    _strip_hostiles(app.world)
    app.sync_play_mode()

    path = tmp_path / "save.json"
    save_game(app, path)
    loaded = load_game(path)
    loaded.ui_mode = UIMode.play

    assert loaded.autowalk is None
    direction_key, dx, dy = _pick_open_direction(loaded, upper=True)
    before_position = loaded.world.positions.require(loaded.player)
    before = (before_position.x, before_position.y)
    loaded.handle_key(direction_key)
    after_position = loaded.world.positions.require(loaded.player)
    after = (after_position.x, after_position.y)
    # Autowalk moved at least one step in the requested direction.
    assert after != before
    # And the walk completed (autowalk cleared).
    assert loaded.autowalk is None


# ---------------------------------------------------------------------------
# Old / minimal saves
# ---------------------------------------------------------------------------


def test_load_minimal_payload_falls_back_to_defaults(tmp_path: Path) -> None:
    """A save that omits optional fields still loads."""
    # Build the minimum a loader needs: schema_version + world + party.
    app = create_app()
    full_payload = app.game_state.to_dict()
    minimal = {
        "schema_version": GAME_STATE_SCHEMA_VERSION,
        "world": full_payload["world"],
        "party": full_payload["party"],
        "turn": full_payload["turn"],
        "player_entity_id": int(app.player),
    }
    path = tmp_path / "save.json"
    path.write_text(json.dumps(minimal), encoding="utf-8")

    loaded = load_game(path)
    assert loaded.ui_mode is UIMode.start  # default
    assert loaded.facing == (1, 0)  # default
    assert loaded.messages.current == ""


def test_load_missing_world_falls_back_to_empty(tmp_path: Path) -> None:
    """A save with only schema_version + empty world rehydrates without
    crashing. Useful for partial fixtures and forward-compat."""
    minimal = {
        "schema_version": GAME_STATE_SCHEMA_VERSION,
        "world": {
            "width": 4,
            "height": 4,
            "next_entity_id": 1,
            "tiles": [[None] * 4 for _ in range(4)],
            "components": {},
        },
        "party": {
            "members": [1],
            "active_index": 0,
            "focused_index": None,
            "follow_order": [1],
        },
        "turn": {},
        "player_entity_id": 1,
    }
    path = tmp_path / "save.json"
    path.write_text(json.dumps(minimal), encoding="utf-8")
    # Even though the world has no player_controlled entity, the loader
    # falls back to ``player_entity_id`` so it doesn't crash.
    loaded = load_game(path)
    assert int(loaded.player) == 1


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_migrate_is_noop_for_current_version() -> None:
    payload = {"schema_version": GAME_STATE_SCHEMA_VERSION, "marker": True}
    migrated = migrate(payload)
    assert migrated["schema_version"] == GAME_STATE_SCHEMA_VERSION
    assert migrated["marker"] is True


def test_migrate_schema_zero_to_one_is_placeholder_noop() -> None:
    payload = {"schema_version": 0, "marker": True}
    migrated = migrate(payload)
    assert migrated["schema_version"] == GAME_STATE_SCHEMA_VERSION
    assert migrated["marker"] is True


def test_migrate_rejects_future_schema_version() -> None:
    with pytest.raises(ValueError):
        migrate({"schema_version": 999})


# ---------------------------------------------------------------------------
# World serialization edges
# ---------------------------------------------------------------------------


def test_world_to_dict_round_trip_preserves_tile_singletons() -> None:
    world = World(
        width=3,
        height=2,
        tiles=[
            [FLOOR, HORIZONTAL_WALL, OUTSIDE],
            [FLOOR, FLOOR, FLOOR],
        ],
    )
    rebuilt = World.from_dict(world.to_dict())
    for y in range(2):
        for x in range(3):
            assert rebuilt.tile_at(x, y) is world.tile_at(x, y)


def test_world_to_dict_preserves_component_data() -> None:
    world = World(width=3, height=3, tiles=[[FLOOR] * 3 for _ in range(3)])
    e = world.create_entity()
    world.positions.add(e, Position(x=1, y=1))
    world.presentations.add(e, Presentation(":"))
    world.blockers.add(e, BlocksMovement("occupied"))
    world.names.add(e, Name("frog"))
    world.factions.add(e, Faction("enemy"))
    world.weapons.add(e, Weapon("bite", 2, "piercing"))
    world.ai.add(e, AI(behavior=AIBehaviorType.WANDER, attack_range=2, preferred_range=4))
    world.combat_stats.add(e, combat_stats_for_creature(creature_for_key("frog")))

    rebuilt = World.from_dict(world.to_dict())
    assert rebuilt.positions.require(e) == Position(x=1, y=1)
    assert rebuilt.presentations.require(e).glyph == ":"
    assert rebuilt.names.require(e).value == "frog"
    rebuilt_ai = rebuilt.ai.require(e)
    assert rebuilt_ai.behavior is AIBehaviorType.WANDER
    assert rebuilt_ai.attack_range == 2
    assert rebuilt_ai.preferred_range == 4
    assert rebuilt.combat_stats.require(e).armor_class == 11


def test_world_to_dict_drops_god_mode() -> None:
    """Debug god marker never persists."""
    from src.core.components import GodMode

    world = World(width=2, height=2, tiles=[[FLOOR] * 2 for _ in range(2)])
    e = world.create_entity()
    world.positions.add(e, Position(x=0, y=0))
    world.god_modes.add(e, GodMode())

    payload = world.to_dict()
    assert "god_modes" not in payload["components"]


# ---------------------------------------------------------------------------
# Issue #88 — stale modal ui_mode repair
#
# The save serializes ``ui_mode`` but skips per-input modal state
# (``targeting``, ``dialogue``, ``shop_partner``). Loading a save written
# mid-modal used to leave ``ui_mode == X`` with the backing state
# ``None``, and every input handler short-circuits in that case, so the
# player was permanently stuck. The loader now demotes any orphaned
# modal mode back to :class:`UIMode.play`.
# ---------------------------------------------------------------------------


def test_load_clears_stale_targeting_mode(tmp_path: Path) -> None:
    """Save written mid-examine loads as play (issue #88)."""
    app = create_app()
    app.ui_mode = UIMode.play
    # Open the examine cursor through the player input path so we
    # get a realistic ``UIMode.targeting`` + ``app.targeting`` state.
    app.handle_key(ord("x"))
    assert app.ui_mode is UIMode.targeting
    assert app.targeting is not None

    path = tmp_path / "save.json"
    save_game(app, path)
    # Sanity check: the on-disk payload really does carry the
    # ``targeting`` ui_mode (so the repair has something to fix).
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["ui_mode"] == "targeting"

    loaded = load_game(path)
    assert loaded.ui_mode is UIMode.play
    assert loaded.targeting is None


def test_load_after_stale_targeting_lets_player_move(tmp_path: Path) -> None:
    """The repaired loaded App accepts a movement key (issue #88)."""
    app = create_app()
    app.ui_mode = UIMode.play
    app.handle_key(ord("x"))
    assert app.ui_mode is UIMode.targeting

    path = tmp_path / "save.json"
    save_game(app, path)
    loaded = load_game(path)

    direction_key, dx, dy = _pick_open_direction(loaded)
    before = loaded.world.positions.require(loaded.player)
    before_xy = (before.x, before.y)
    loaded.handle_key(direction_key)
    after = loaded.world.positions.require(loaded.player)
    assert (after.x, after.y) == (before_xy[0] + dx, before_xy[1] + dy)


def test_load_clears_stale_dialogue_mode(tmp_path: Path) -> None:
    """A save with ``ui_mode == dialogue`` but no dialogue state loads as play."""
    app = create_app()
    # Forge a stale dialogue mode directly — we don't need a real
    # NPC interaction to exercise the load-time repair. The save
    # path drops ``app.dialogue`` either way, so simulating the
    # broken-on-disk shape is exactly the orphan we want.
    app.ui_mode = UIMode.dialogue

    path = tmp_path / "save.json"
    save_game(app, path)
    loaded = load_game(path)

    assert loaded.ui_mode is UIMode.play
    assert loaded.dialogue is None


def test_load_clears_stale_shop_mode(tmp_path: Path) -> None:
    """A save with ``ui_mode == shop`` but no shop partner loads as play."""
    app = create_app()
    app.ui_mode = UIMode.shop

    path = tmp_path / "save.json"
    save_game(app, path)
    loaded = load_game(path)

    assert loaded.ui_mode is UIMode.play
    assert loaded.shop_partner is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_DIRECTION_KEYS: list[tuple[int, int, int, int]] = [
    # (lowercase key, uppercase key, dx, dy)
    (ord("h"), ord("H"), -1, 0),
    (ord("j"), ord("J"), 0, 1),
    (ord("k"), ord("K"), 0, -1),
    (ord("l"), ord("L"), 1, 0),
    (ord("y"), ord("Y"), -1, -1),
    (ord("u"), ord("U"), 1, -1),
    (ord("b"), ord("B"), -1, 1),
    (ord("n"), ord("N"), 1, 1),
]


def _pick_open_direction(app, *, upper: bool = False) -> tuple[int, int, int]:
    """Return a ``(key, dx, dy)`` that's clear from the player's tile.

    Search starts with the cardinal directions so callers get the most
    "natural" move when one is available; diagonals are tried only when
    every cardinal is blocked. Tests use this to avoid hard-coding a
    direction that the random fixture happens to block today.
    """
    position = app.world.positions.require(app.player)
    for low, high, dx, dy in _DIRECTION_KEYS:
        tx, ty = position.x + dx, position.y + dy
        if app.world.tile_at(tx, ty).blocks_movement:
            continue
        if app.world.entities_at(tx, ty):
            continue
        return high if upper else low, dx, dy
    raise RuntimeError("No open direction available from player position.")


def _strip_hostiles(world) -> None:
    """Remove every non-party entity with combat stats.

    Used by tests that need to drop into explore mode without rebuilding
    the world from scratch. Recognizes both the canonical M28
    ``player_party`` faction id and the legacy ``"player"`` alias so the
    helper works against either fixture flavor.
    """
    from src.core.factions import FactionId

    party_values = {FactionId.PLAYER_PARTY.value, "player"}
    to_remove = []
    for entity in list(world.factions.values.keys()):
        faction = world.factions.require(entity)
        if faction.value not in party_values and world.combat_stats.has(entity):
            to_remove.append(entity)
    for entity in to_remove:
        world.remove_entity(entity)


def _build_minimal_combat_app():
    """Construct an app and plant a goblin next to the player.

    Used by tests that need a save written mid-combat. We mutate the
    standard ``create_app`` world rather than building a fixture from
    scratch so the loaded App's other systems (vision, dispatcher) are
    still wired correctly.
    """
    from src.core.components import Creature

    app = create_app()
    app.ui_mode = UIMode.play
    player_pos = app.world.positions.require(app.player)
    goblin = app.world.create_entity()
    spec = creature_for_key("goblin")
    app.world.positions.add(goblin, Position(x=player_pos.x + 1, y=player_pos.y))
    app.world.presentations.add(goblin, Presentation(spec.glyph))
    app.world.blockers.add(goblin, BlocksMovement("occupied"))
    app.world.names.add(goblin, Name(spec.name))
    app.world.creatures.add(
        goblin, Creature(kind=spec.key, attack_verb=spec.attack_verb)
    )
    app.world.factions.add(goblin, Faction("enemy"))
    app.world.combat_stats.add(goblin, combat_stats_for_creature(spec))
    app.world.weapons.add(goblin, Weapon(
        name=spec.weapon.name,
        damage_die=spec.weapon.damage_die,
        damage_type=spec.weapon.damage_type,
        ability=spec.weapon.ability,
        finesse=spec.weapon.finesse,
    ))
    app.sync_play_mode()
    return app
