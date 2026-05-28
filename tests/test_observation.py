"""Tests for the agent-readable observation snapshot (M35)."""

import copy

from src.app import create_app
from src.core.components import (
    Faction,
    Name,
    Position,
    Presentation,
)
from src.core.modes import PlayMode, UIMode
from src.ui.observation import (
    DEFAULT_VISIBILITY_RADIUS,
    Observation,
    observe,
)


def _clear_creatures(app) -> None:
    for entity in list(app.world.creatures.values):
        app.world.remove_entity(entity)


def _enter_play(app) -> None:
    """Drive past the start screen into UIMode.play."""
    app.handle_key(ord("y"))


def test_observe_on_fresh_app_reports_start_ui_mode() -> None:
    app = create_app()

    obs = observe(app)

    assert obs.mode == {"ui_mode": "start", "play_mode": None}
    # Pre-play: no combat snapshot even though hostiles exist.
    assert obs.combat is None
    # Start screen surfaces its options as a modal.
    assert obs.modal is not None
    assert obs.modal.kind == "start"
    assert "yolo" in obs.modal.options
    # World clock has not advanced.
    assert obs.world_time.seconds == 0


def test_observe_after_yolo_reports_play_mode() -> None:
    app = create_app()
    _enter_play(app)

    obs = observe(app)

    assert obs.mode["ui_mode"] == "play"
    # Hostiles spawned by the world builder force turn_based.
    assert obs.mode["play_mode"] == PlayMode.turn_based.value
    assert obs.modal is None
    assert obs.combat is not None
    assert obs.combat.movement_total > 0
    assert obs.combat.action_remaining is True


def test_observe_is_pure_does_not_mutate_app() -> None:
    app = create_app()
    _enter_play(app)

    before_position = copy.copy(app.world.positions.require(app.player))
    before_clock = app.world.clock.elapsed_seconds
    before_activation = copy.copy(app.activation)

    observe(app)
    observe(app)  # twice — observation must be idempotent.

    after_position = app.world.positions.require(app.player)
    assert (before_position.x, before_position.y) == (after_position.x, after_position.y)
    assert app.world.clock.elapsed_seconds == before_clock
    assert app.activation == before_activation


def test_position_field_updates_after_move() -> None:
    app = create_app()
    _enter_play(app)
    _clear_creatures(app)
    app.sync_play_mode()

    before = observe(app)
    assert before.active_actor is not None
    before_x, before_y = before.active_actor.position

    # 'l' is a vi-style east move.
    app.handle_key(ord("l"))

    after = observe(app)
    assert after.active_actor is not None
    after_x, after_y = after.active_actor.position

    assert (after_x, after_y) != (before_x, before_y)
    # Also reflected in the party listing.
    assert any(
        member.position == (after_x, after_y) and member.id == after.active_actor.id
        for member in after.party
    )


def test_inventory_modal_observation() -> None:
    app = create_app()
    _enter_play(app)
    _clear_creatures(app)
    app.sync_play_mode()

    app.handle_key(ord("i"))

    obs = observe(app)
    assert obs.mode["ui_mode"] == UIMode.inventory.value
    assert obs.modal is not None
    assert obs.modal.kind == "inventory"
    # Starter loadout puts at least a weapon item id into the inventory.
    assert obs.modal.options, "Expected starter inventory to have at least one stack."


def test_visible_entities_filters_by_distance() -> None:
    """Distant entities are excluded — both M19 vision and the radius
    fallback agree about this for the default vision/observation
    radius of 10."""
    app = create_app()
    _enter_play(app)
    _clear_creatures(app)
    app.sync_play_mode()

    player_position = app.world.positions.require(app.player)

    # Plant a non-hostile entity on a tile we'll mark visible.
    near = app.world.create_entity()
    app.world.positions.add(
        near,
        Position(player_position.x + 1, player_position.y),
    )
    app.world.names.add(near, Name("near"))
    app.world.presentations.add(near, Presentation("?"))
    app.world.factions.add(near, Faction("neutral"))

    # And one far away.
    far = app.world.create_entity()
    app.world.positions.add(
        far,
        Position(player_position.x + DEFAULT_VISIBILITY_RADIUS + 5, player_position.y),
    )
    app.world.names.add(far, Name("far"))
    app.world.presentations.add(far, Presentation("?"))
    app.world.factions.add(far, Faction("neutral"))

    # Refresh vision so the new tiles enter the memory mask.
    app.refresh_vision()

    obs = observe(app)
    ids = {entity.id for entity in obs.visible_entities}
    assert int(near) in ids
    assert int(far) not in ids


def test_visible_entities_excludes_party_members() -> None:
    app = create_app()
    _enter_play(app)

    obs = observe(app)
    party_ids = {member.id for member in obs.party}
    visible_ids = {entity.id for entity in obs.visible_entities}

    assert party_ids.isdisjoint(visible_ids)


def test_exits_omits_blocked_directions() -> None:
    app = create_app()
    _enter_play(app)

    obs = observe(app)
    assert obs.active_actor is not None
    origin_x, origin_y = obs.active_actor.position
    world = app.world

    # Every reported exit must correspond to a non-blocking tile with no
    # blockers stacked on top.
    name_to_delta = {
        "north": (0, -1),
        "northeast": (1, -1),
        "east": (1, 0),
        "southeast": (1, 1),
        "south": (0, 1),
        "southwest": (-1, 1),
        "west": (-1, 0),
        "northwest": (-1, -1),
    }
    for direction in obs.exits:
        dx, dy = name_to_delta[direction]
        tile = world.tile_at(origin_x + dx, origin_y + dy)
        assert tile.blocks_movement is False
        assert not world.blockers_at(origin_x + dx, origin_y + dy)


def test_combat_snapshot_present_only_in_turn_based_play() -> None:
    app = create_app()
    _enter_play(app)

    # Hostiles are present in the freshly created world.
    obs = observe(app)
    assert obs.combat is not None
    assert obs.combat.action_remaining is True
    assert obs.combat.movement_remaining == obs.combat.movement_total

    # Clearing hostiles drops us back to explore mode → no combat snapshot.
    _clear_creatures(app)
    app.sync_play_mode()

    obs = observe(app)
    assert obs.combat is None
    assert obs.mode["play_mode"] == PlayMode.explore.value


def test_available_actions_include_movement_when_movement_remains() -> None:
    app = create_app()
    _enter_play(app)

    obs = observe(app)
    assert "move" in obs.available_actions
    assert "end_turn" in obs.available_actions
    # Inventory is reachable even in combat (it's a UI modal switch).
    assert "inventory" in obs.available_actions


def test_quit_confirm_modal_observation() -> None:
    app = create_app()
    _enter_play(app)
    _clear_creatures(app)
    app.sync_play_mode()

    app.handle_key(ord("q"))

    obs = observe(app)
    assert obs.mode["ui_mode"] == UIMode.quit_confirm.value
    assert obs.modal is not None
    assert obs.modal.kind == "quit_confirm"
    assert set(obs.modal.options) == {"yes", "no"}


def test_to_dict_and_from_dict_round_trip() -> None:
    app = create_app()
    _enter_play(app)

    original = observe(app)
    payload = original.to_dict()
    restored = Observation.from_dict(payload)

    # Round-trip via JSON-compatible payloads must preserve every field.
    assert restored.mode == original.mode
    assert restored.active_actor == original.active_actor
    assert restored.party == original.party
    assert restored.visible_entities == original.visible_entities
    assert restored.exits == original.exits
    assert restored.recent_messages == original.recent_messages
    assert restored.combat == original.combat
    assert restored.available_actions == original.available_actions
    assert restored.modal == original.modal
    assert restored.world_time == original.world_time


def test_to_dict_is_json_serializable() -> None:
    """Observation must contain no objects that defeat `json.dumps`."""
    import json

    app = create_app()
    _enter_play(app)

    payload = observe(app).to_dict()
    encoded = json.dumps(payload)
    decoded = json.loads(encoded)

    assert decoded["mode"]["ui_mode"] == "play"


def test_observation_uses_party_memory_visible_set_when_available() -> None:
    """M19 wired `app.memory.visible` — observation defers to it.

    Constraining the visible set to just the player's tile must exclude
    entities sitting on adjacent tiles, even though they are well inside
    `DEFAULT_VISIBILITY_RADIUS`. This proves the radius fallback is
    bypassed when `app.memory.visible` is populated.
    """
    app = create_app()
    _enter_play(app)
    _clear_creatures(app)
    app.sync_play_mode()

    player_position = app.world.positions.require(app.player)

    # Place an entity right next to the player — inside Chebyshev range,
    # but the only memory-visible cell will be the player's tile.
    hidden = app.world.create_entity()
    app.world.positions.add(hidden, Position(player_position.x + 1, player_position.y))
    app.world.names.add(hidden, Name("hidden"))
    app.world.presentations.add(hidden, Presentation("?"))
    app.world.factions.add(hidden, Faction("neutral"))

    app.memory.set_visible({(player_position.x, player_position.y)})

    obs = observe(app)
    visible_ids = {entity.id for entity in obs.visible_entities}
    assert int(hidden) not in visible_ids


def test_observation_visible_set_includes_entities_inside_memory() -> None:
    """Sanity: entities on cells the memory marks visible are reported."""
    app = create_app()
    _enter_play(app)
    _clear_creatures(app)
    app.sync_play_mode()

    player_position = app.world.positions.require(app.player)
    near_cell = (player_position.x + 1, player_position.y)

    near = app.world.create_entity()
    app.world.positions.add(near, Position(*near_cell))
    app.world.names.add(near, Name("near"))
    app.world.presentations.add(near, Presentation("?"))
    app.world.factions.add(near, Faction("neutral"))

    app.memory.set_visible({(player_position.x, player_position.y), near_cell})

    obs = observe(app)
    visible_ids = {entity.id for entity in obs.visible_entities}
    assert int(near) in visible_ids


def test_observation_handles_party_member_without_position() -> None:
    """A party member that lost its Position must not crash observe()."""
    app = create_app()
    _enter_play(app)
    # Drop a companion's Position component to simulate a removed entity.
    companion = app.party[1]
    app.world.positions.values.pop(companion, None)

    obs = observe(app)
    # The party listing skips the placeless companion but still includes
    # the others.
    ids = {member.id for member in obs.party}
    assert int(companion) not in ids
    assert int(app.player) in ids


def test_observation_surfaces_spell_list_and_slots_for_caster() -> None:
    """M11: a caster's spell list and slot ledger are surfaced in the
    actor summary so the playtest agent can plan spell choices."""

    from src.core.spells import SpellList, SpellSlots

    app = create_app()
    _enter_play(app)
    player = app.player
    app.world.spell_lists.add(player, SpellList(known=("magic_missile", "firebolt")))
    app.world.spell_slots.add(player, SpellSlots.from_pairs({1: 2}))

    obs = observe(app)
    assert obs.active_actor is not None
    assert "magic_missile" in obs.active_actor.spells
    assert any(
        s.level == 1 and s.remaining == 2 and s.maximum == 2
        for s in obs.active_actor.spell_slots
    )

    # Round-trip through dict to confirm JSON-friendliness.
    restored = Observation.from_dict(obs.to_dict())
    assert restored.active_actor is not None
    assert restored.active_actor.spells == obs.active_actor.spells
    assert restored.active_actor.spell_slots == obs.active_actor.spell_slots


def test_spell_menu_modal_observation_lists_known_spells() -> None:
    """M11: the spell menu modal surfaces the active actor's spell list."""
    from src.core.spells import SpellList, SpellSlots

    app = create_app()
    _enter_play(app)
    player = app.player
    app.world.spell_lists.add(player, SpellList(known=("magic_missile", "firebolt")))
    app.world.spell_slots.add(player, SpellSlots.from_pairs({1: 2}))
    app.handle_key(ord("s"))

    obs = observe(app)
    assert obs.modal is not None
    assert obs.modal.kind == "spell_menu"
    assert "magic_missile" in obs.modal.options
    assert "firebolt" in obs.modal.options


def test_world_time_snapshot_advances_with_explore_moves() -> None:
    app = create_app()
    _enter_play(app)
    _clear_creatures(app)
    app.sync_play_mode()

    before = observe(app).world_time.seconds

    # 'l' moves east one tile in explore mode → ticks the world clock.
    app.handle_key(ord("l"))

    after = observe(app).world_time.seconds
    assert after > before
