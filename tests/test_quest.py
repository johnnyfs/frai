"""Tests for M14 Quest path — The Sunken Gate.

The quest layer is intentionally small: a typed registry, a per-party
log, dialogue accept, and progress hooks on kill / pickup. These tests
exercise the layer end-to-end:

* Pure data model (registry lookup, log round-trip, accept/offer
  helpers).
* Tavern NPC opens the quest dialogue and accepting flips the log.
* Boss kill + chalice pickup completes the quest exactly when both
  conditions hold; missing either leaves it accepted.
* Save / load preserves the quest state.
* Reward effects fire on completion.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.app import create_app
from src.core.components import (
    BlocksMovement,
    BossMarker,
    CombatStats,
    Creature,
    Faction,
    Inventory,
    LootDrop,
    Name,
    NPC,
    NPCDialogue,
    NPCKind,
    Position,
    Presentation,
    Weapon,
)
from src.core.creatures import (
    combat_stats_for_creature,
    creature_component,
    creature_for_key,
    weapon_for_creature,
)
from src.core.dialogue import (
    AcceptQuestEffect,
    DialogueTree,
    quest_offer_tree,
)
from src.core.effects import KillEntity, TransferInventory
from src.core.factions import FactionId
from src.core.items import add_item
from src.core.modes import UIMode
from src.core.quest import (
    QUESTS,
    PartyQuestLog,
    QuestState,
    SUNKEN_GATE_QUEST_ID,
    accept_quest,
    offer_quest,
)
from src.core.save import load_game, save_game
from src.core.world import World
from src.world.content.skeleton import build_world_skeleton


# ---------------------------------------------------------------------------
# Pure data model
# ---------------------------------------------------------------------------


def test_quest_registry_holds_sunken_gate() -> None:
    quest = QUESTS.require(SUNKEN_GATE_QUEST_ID)

    assert quest.name == "The Sunken Gate"
    assert quest.objective.boss_marker == "sunken_gate_warlord"
    assert quest.objective.treasure_item_id == "treasure.golden_chalice"
    assert quest.reward.gold_per_member == 100
    assert quest.reward.xp_per_member == 200


def test_party_quest_log_defaults_to_not_offered() -> None:
    log = PartyQuestLog()

    assert log.state_of(SUNKEN_GATE_QUEST_ID) is QuestState.NOT_OFFERED
    assert not log.is_accepted(SUNKEN_GATE_QUEST_ID)
    assert not log.is_completed(SUNKEN_GATE_QUEST_ID)


def test_party_quest_log_round_trips_through_json() -> None:
    log = PartyQuestLog()
    log.set_state(SUNKEN_GATE_QUEST_ID, QuestState.ACCEPTED)

    rebuilt = PartyQuestLog.from_dict(json.loads(json.dumps(log.to_dict())))

    assert rebuilt.state_of(SUNKEN_GATE_QUEST_ID) is QuestState.ACCEPTED


def test_accept_quest_helper_changes_state() -> None:
    log = PartyQuestLog()

    changed = accept_quest(log, SUNKEN_GATE_QUEST_ID)

    assert changed is True
    assert log.state_of(SUNKEN_GATE_QUEST_ID) is QuestState.ACCEPTED


def test_accept_quest_helper_is_idempotent_when_already_accepted() -> None:
    log = PartyQuestLog()
    accept_quest(log, SUNKEN_GATE_QUEST_ID)

    assert accept_quest(log, SUNKEN_GATE_QUEST_ID) is False


def test_accept_quest_helper_refuses_completed_quest() -> None:
    log = PartyQuestLog()
    log.set_state(SUNKEN_GATE_QUEST_ID, QuestState.COMPLETED)

    assert accept_quest(log, SUNKEN_GATE_QUEST_ID) is False
    assert log.state_of(SUNKEN_GATE_QUEST_ID) is QuestState.COMPLETED


def test_offer_quest_helper_only_moves_unstarted_quests() -> None:
    log = PartyQuestLog()

    assert offer_quest(log, SUNKEN_GATE_QUEST_ID) is True
    assert log.state_of(SUNKEN_GATE_QUEST_ID) is QuestState.OFFERED

    # Already offered — no-op.
    assert offer_quest(log, SUNKEN_GATE_QUEST_ID) is False

    log.set_state(SUNKEN_GATE_QUEST_ID, QuestState.ACCEPTED)
    assert offer_quest(log, SUNKEN_GATE_QUEST_ID) is False


def test_quest_offer_tree_carries_accept_quest_effect() -> None:
    tree = quest_offer_tree(
        speaker_id="npc.tane",
        quest_id=SUNKEN_GATE_QUEST_ID,
        pitch="Help me?",
        accept_response="Thank you.",
        decline_response="Suit yourself.",
    )

    root = tree.node("root")
    accept_option, decline_option = root.options
    assert isinstance(accept_option.effect, AcceptQuestEffect)
    assert accept_option.effect.quest_id == SUNKEN_GATE_QUEST_ID
    assert accept_option.next_node == "accepted"
    assert decline_option.effect is None
    assert decline_option.next_node == "declined"


def test_quest_offer_tree_round_trips_accept_quest_effect() -> None:
    tree = quest_offer_tree(
        speaker_id="npc.tane",
        quest_id=SUNKEN_GATE_QUEST_ID,
        pitch="Help me?",
        accept_response="Thank you.",
        decline_response="Later.",
    )

    rebuilt = DialogueTree.from_dict(json.loads(json.dumps(tree.to_dict())))

    accept_option = rebuilt.node("root").options[0]
    assert isinstance(accept_option.effect, AcceptQuestEffect)
    assert accept_option.effect.quest_id == SUNKEN_GATE_QUEST_ID


# ---------------------------------------------------------------------------
# Tavern NPC integration
# ---------------------------------------------------------------------------


def _make_app_with_player():
    app = create_app()
    app.handle_key(ord("y"))  # finish YOLO start
    # Strip starter creatures so the play mode stays in explore.
    for entity in list(app.world.creatures.values):
        app.world.remove_entity(entity)
    app.sync_play_mode()
    app.ui_mode = UIMode.play
    return app


def _spawn_quest_giver_next_to_player(app):
    """Place a quest-giver NPC east of the player for the e-key test."""

    player_position = app.world.positions.require(app.player)
    x = player_position.x + 1
    y = player_position.y
    for occupant in list(app.world.entities_at(x, y)):
        if occupant in app.party.members:
            app.party.dismiss(occupant)
        app.world.remove_entity(occupant)
    entity = app.world.create_entity()
    app.world.positions.add(entity, Position(x=x, y=y))
    app.world.presentations.add(entity, Presentation("@"))
    app.world.names.add(entity, Name("Captain Tane"))
    app.world.factions.add(entity, Faction("town"))
    app.world.blockers.add(entity, BlocksMovement("occupied"))
    app.world.npcs.add(entity, NPC(kind=NPCKind.INFO))
    tree = quest_offer_tree(
        speaker_id="Captain Tane",
        quest_id=SUNKEN_GATE_QUEST_ID,
        pitch="Kill the warlord. Bring back the chalice.",
        accept_response="Then go, and may you return.",
        decline_response="As you wish.",
    )
    app.world.npc_dialogues.add(entity, NPCDialogue(tree=tree))
    return entity


def test_talking_to_quest_giver_offers_quest_via_dialogue() -> None:
    """Opening the dialogue surfaces accept/decline options."""

    app = _make_app_with_player()
    _spawn_quest_giver_next_to_player(app)
    app.facing = (1, 0)

    app.handle_key(ord("e"))

    assert app.ui_mode is UIMode.dialogue
    assert app.dialogue is not None
    node = app.dialogue.node()
    labels = [option.label for option in node.options]
    assert any("take it" in label.lower() for label in labels)


def test_accept_option_marks_quest_accepted_on_party_log() -> None:
    app = _make_app_with_player()
    _spawn_quest_giver_next_to_player(app)
    app.facing = (1, 0)
    app.handle_key(ord("e"))

    # Press 1 (first option = accept).
    app.handle_key(ord("1"))

    log = app.party.quests
    assert log.state_of(SUNKEN_GATE_QUEST_ID) is QuestState.ACCEPTED
    # The accept lands on the "accepted" node, then closes when the
    # player picks the goodbye option.
    assert app.ui_mode is UIMode.dialogue
    assert app.dialogue is not None
    assert app.dialogue.current_node == "accepted"


def test_decline_option_leaves_quest_unaccepted() -> None:
    app = _make_app_with_player()
    _spawn_quest_giver_next_to_player(app)
    app.facing = (1, 0)
    app.handle_key(ord("e"))

    # Press 2 (second option = decline).
    app.handle_key(ord("2"))

    log = app.party.quests
    assert log.state_of(SUNKEN_GATE_QUEST_ID) is QuestState.NOT_OFFERED


# ---------------------------------------------------------------------------
# Completion / progress
# ---------------------------------------------------------------------------


def _spawn_boss_next_to_player(app):
    spec = creature_for_key("boss_kobold_warlord")
    player_position = app.world.positions.require(app.player)
    x = player_position.x + 2
    y = player_position.y
    entity = app.world.create_entity()
    app.world.positions.add(entity, Position(x=x, y=y))
    app.world.presentations.add(entity, Presentation(spec.glyph))
    app.world.names.add(entity, Name(spec.name))
    app.world.blockers.add(entity, BlocksMovement("occupied"))
    app.world.creatures.add(entity, creature_component(spec))
    app.world.factions.add(entity, Faction(FactionId.DUNGEON.value))
    app.world.combat_stats.add(entity, combat_stats_for_creature(spec))
    app.world.weapons.add(entity, weapon_for_creature(spec))
    if spec.loot.entries:
        app.world.loot_drops.add(entity, LootDrop(table=spec.loot))
    app.world.boss_markers.add(entity, BossMarker(token="sunken_gate_warlord"))
    return entity


def test_killing_boss_and_holding_chalice_completes_quest() -> None:
    app = _make_app_with_player()
    app.party.quests.set_state(SUNKEN_GATE_QUEST_ID, QuestState.ACCEPTED)
    boss = _spawn_boss_next_to_player(app)
    # Pre-seed the chalice into the player's inventory so the kill
    # alone is the completing event.
    inventory = app.world.inventories.get(app.player)
    if inventory is None:
        inventory = Inventory()
        app.world.inventories.add(app.player, inventory)
    add_item(inventory, "treasure.golden_chalice")

    app.apply_effects([KillEntity(entity=boss)])

    assert app.party.quests.state_of(SUNKEN_GATE_QUEST_ID) is QuestState.COMPLETED


def test_killing_boss_without_chalice_does_not_complete_quest() -> None:
    app = _make_app_with_player()
    app.party.quests.set_state(SUNKEN_GATE_QUEST_ID, QuestState.ACCEPTED)
    boss = _spawn_boss_next_to_player(app)
    # Make the boss's drop table empty so no chalice spawns from the
    # kill. We model the "no chalice anywhere yet" case by stripping
    # the loot drop before the kill.
    app.world.loot_drops.values.pop(boss, None)

    app.apply_effects([KillEntity(entity=boss)])

    assert app.party.quests.state_of(SUNKEN_GATE_QUEST_ID) is QuestState.ACCEPTED


def test_picking_up_chalice_without_killing_boss_does_not_complete_quest() -> None:
    app = _make_app_with_player()
    app.party.quests.set_state(SUNKEN_GATE_QUEST_ID, QuestState.ACCEPTED)
    boss = _spawn_boss_next_to_player(app)
    # Spawn a chalice on a separate ground entity adjacent to the
    # player and transfer it. The boss is still alive (its marker is
    # still in the world) so the quest must stay accepted.
    chalice_entity = app.world.create_entity()
    position = app.world.positions.require(app.player)
    app.world.positions.add(
        chalice_entity, Position(x=position.x, y=position.y)
    )
    chalice_inventory = Inventory()
    add_item(chalice_inventory, "treasure.golden_chalice")
    app.world.inventories.add(chalice_entity, chalice_inventory)
    app.world.names.add(chalice_entity, Name("chalice pile"))
    if not app.world.inventories.has(app.player):
        app.world.inventories.add(app.player, Inventory())

    app.apply_effects(
        [TransferInventory(source=chalice_entity, destination=app.player)]
    )

    # The boss is still alive — quest must not complete.
    assert app.party.quests.state_of(SUNKEN_GATE_QUEST_ID) is QuestState.ACCEPTED
    # Sanity: the chalice did land in the player's inventory.
    player_inventory = app.world.inventories.require(app.player)
    assert any(
        stack.item_id == "treasure.golden_chalice"
        for stack in player_inventory.items
    )
    # Keep the boss reference alive so the assertion above is meaningful
    # even when the variable is otherwise unused.
    assert app.world.boss_markers.has(boss)


def test_pickup_completes_quest_when_boss_already_dead() -> None:
    """Picking up the chalice last completes the quest."""

    app = _make_app_with_player()
    app.party.quests.set_state(SUNKEN_GATE_QUEST_ID, QuestState.ACCEPTED)
    boss = _spawn_boss_next_to_player(app)
    app.world.loot_drops.values.pop(boss, None)
    app.apply_effects([KillEntity(entity=boss)])
    assert app.party.quests.state_of(SUNKEN_GATE_QUEST_ID) is QuestState.ACCEPTED

    # Drop a chalice next to the player and pick it up.
    chalice_entity = app.world.create_entity()
    position = app.world.positions.require(app.player)
    app.world.positions.add(
        chalice_entity, Position(x=position.x, y=position.y)
    )
    chalice_inventory = Inventory()
    add_item(chalice_inventory, "treasure.golden_chalice")
    app.world.inventories.add(chalice_entity, chalice_inventory)
    app.world.names.add(chalice_entity, Name("chalice pile"))
    if not app.world.inventories.has(app.player):
        app.world.inventories.add(app.player, Inventory())

    app.apply_effects(
        [TransferInventory(source=chalice_entity, destination=app.player)]
    )

    assert app.party.quests.state_of(SUNKEN_GATE_QUEST_ID) is QuestState.COMPLETED


def test_quest_completion_grants_gold_reward_to_each_member() -> None:
    app = _make_app_with_player()
    app.party.quests.set_state(SUNKEN_GATE_QUEST_ID, QuestState.ACCEPTED)
    # Ensure every party member has an inventory and a known starting
    # gold balance so the delta is easy to assert.
    starting_gold: dict = {}
    for member in app.party.members:
        inventory = app.world.inventories.get(member)
        if inventory is None:
            inventory = Inventory()
            app.world.inventories.add(member, inventory)
        starting_gold[member] = inventory.gold

    boss = _spawn_boss_next_to_player(app)
    inventory = app.world.inventories.require(app.player)
    add_item(inventory, "treasure.golden_chalice")

    app.apply_effects([KillEntity(entity=boss)])

    quest = QUESTS.require(SUNKEN_GATE_QUEST_ID)
    for member in app.party.members:
        member_inventory = app.world.inventories.require(member)
        assert member_inventory.gold == starting_gold[member] + quest.reward.gold_per_member


# ---------------------------------------------------------------------------
# Save / load
# ---------------------------------------------------------------------------


def test_save_load_preserves_quest_state(tmp_path: Path) -> None:
    app = _make_app_with_player()
    app.party.quests.set_state(SUNKEN_GATE_QUEST_ID, QuestState.ACCEPTED)

    path = tmp_path / "save.json"
    save_game(app, path)
    loaded = load_game(path)

    assert (
        loaded.party.quests.state_of(SUNKEN_GATE_QUEST_ID) is QuestState.ACCEPTED
    )


def test_world_skeleton_spawns_captain_tane_in_town() -> None:
    built = build_world_skeleton()

    tane = next(
        (
            entity
            for entity in built.world.npcs.values
            if built.world.names.get(entity)
            and built.world.names.require(entity).value == "Captain Tane"
        ),
        None,
    )
    assert tane is not None
    dialogue = built.world.npc_dialogues.require(tane)
    accept_option = dialogue.tree.node(dialogue.tree.root).options[0]
    assert isinstance(accept_option.effect, AcceptQuestEffect)
    assert accept_option.effect.quest_id == SUNKEN_GATE_QUEST_ID


def test_world_skeleton_spawns_boss_in_dungeon_level_3() -> None:
    built = build_world_skeleton()
    bosses = list(built.world.boss_markers.values.items())

    assert len(bosses) == 1
    boss_entity, marker = bosses[0]
    assert marker.token == "sunken_gate_warlord"
    creature = built.world.creatures.require(boss_entity)
    assert creature.kind == "boss_kobold_warlord"
    # The boss must be inside the dungeon_level_3 location bounds.
    level = built.locations["dungeon_level_3"]
    position = built.world.positions.require(boss_entity)
    assert level.bounds.contains(_point_at(position.x, position.y))


def _point_at(x: int, y: int):
    # Lazy import to avoid pulling Point into the public test surface.
    from src.world.content.skeleton import Point

    return Point(x=x, y=y)


# ---------------------------------------------------------------------------
# Observation surface
# ---------------------------------------------------------------------------


def test_observation_surfaces_quest_progress() -> None:
    """The agentic playtester sees quest progress in `observe()`."""

    from src.ui.observation import observe

    app = _make_app_with_player()
    snapshot = observe(app)
    # Untouched quest log: nothing surfaced.
    assert snapshot.quests == []

    app.party.quests.set_state(SUNKEN_GATE_QUEST_ID, QuestState.ACCEPTED)
    snapshot = observe(app)
    assert any(
        quest.quest_id == SUNKEN_GATE_QUEST_ID and quest.state == "accepted"
        for quest in snapshot.quests
    )
