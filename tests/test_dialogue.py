"""Tests for M13 NPC and dialogue.

The dialogue module is split between:

* Pure data types (`DialogueTree`, `DialogueNode`, `DialogueOption`,
  ...) — covered by unit tests below that exercise the constructors,
  navigation, and JSON round-trip.
* App-side wiring (open dialogue via the ``e`` key, option selection,
  recruit + shop effects) — covered by integration tests against
  ``create_app`` with NPCs spliced in next to the player so the
  ``e``-on-adjacent-NPC path runs through the same code the live game
  uses.
"""

from __future__ import annotations

import json

from src.app import create_app
from src.core.components import (
    BlocksMovement,
    Faction,
    Inventory,
    NPC,
    NPCDialogue,
    NPCKind,
    Name,
    Position,
    Presentation,
    Shop,
)
from src.core.dialogue import (
    CloseDialogueEffect,
    DialogueLine,
    DialogueNode,
    DialogueOption,
    DialogueState,
    DialogueTree,
    OpenShopEffect,
    RecruitEffect,
    info_tree,
    recruit_tree,
    shopkeeper_tree,
)
from src.core.modes import UIMode
from src.world.content.skeleton import build_world_skeleton


# ---------------------------------------------------------------------
# Pure data tests
# ---------------------------------------------------------------------


def test_info_tree_has_single_node_and_one_close_option() -> None:
    tree = info_tree("npc.gerda", "The dungeon lies east of town.")

    assert tree.root == "root"
    root = tree.node("root")
    assert root.line.speaker_id == "npc.gerda"
    assert root.line.text == "The dungeon lies east of town."
    assert len(root.options) == 1
    assert root.options[0].next_node is None
    assert root.options[0].effect is None


def test_recruit_tree_offers_accept_and_decline() -> None:
    tree = recruit_tree(
        "npc.karn",
        "Join us?",
        "Glad to.",
    )

    root = tree.node("root")
    assert len(root.options) == 2
    accept, decline = root.options
    assert isinstance(accept.effect, RecruitEffect)
    assert accept.next_node == "joined"
    assert decline.effect is None
    assert decline.next_node is None
    joined = tree.node("joined")
    assert joined.line.text == "Glad to."


def test_shopkeeper_tree_has_open_shop_option() -> None:
    tree = shopkeeper_tree("npc.hadrin", "Welcome.")

    root = tree.node("root")
    assert any(isinstance(option.effect, OpenShopEffect) for option in root.options)


def test_dialogue_state_begin_starts_at_root() -> None:
    tree = info_tree("npc.gerda", "Hi.")
    state = DialogueState.begin(speaker=42, tree=tree)

    assert state.current_node == tree.root
    assert state.node().line.text == "Hi."


def test_dialogue_state_advance_to_unknown_node_raises() -> None:
    tree = info_tree("npc.gerda", "Hi.")
    state = DialogueState.begin(speaker=42, tree=tree)

    try:
        state.advance_to("nope")
    except KeyError:
        return
    raise AssertionError("Expected KeyError for unknown node key")


def test_dialogue_tree_round_trips_through_json() -> None:
    tree = DialogueTree(
        root="root",
        nodes={
            "root": DialogueNode(
                line=DialogueLine(speaker_id="npc.x", text="Greetings."),
                options=(
                    DialogueOption(
                        label="Recruit", next_node="thanks", effect=RecruitEffect()
                    ),
                    DialogueOption(
                        label="Shop",
                        next_node=None,
                        effect=OpenShopEffect(),
                    ),
                    DialogueOption(
                        label="Bye", next_node=None, effect=CloseDialogueEffect()
                    ),
                ),
            ),
            "thanks": DialogueNode(
                line=DialogueLine(speaker_id="npc.x", text="Glad to."),
                options=(),
            ),
        },
    )

    payload = json.dumps(tree.to_dict())
    rebuilt = DialogueTree.from_dict(json.loads(payload))

    assert rebuilt.root == tree.root
    assert set(rebuilt.nodes) == set(tree.nodes)
    root = rebuilt.node("root")
    assert root.line == tree.node("root").line
    assert [option.label for option in root.options] == [
        "Recruit",
        "Shop",
        "Bye",
    ]
    assert isinstance(root.options[0].effect, RecruitEffect)
    assert isinstance(root.options[1].effect, OpenShopEffect)
    assert isinstance(root.options[2].effect, CloseDialogueEffect)
    assert rebuilt.node("thanks").options == ()


# ---------------------------------------------------------------------
# Integration tests (App wiring)
# ---------------------------------------------------------------------


def _make_app_with_player():
    app = create_app()
    app.handle_key(ord("y"))  # finish YOLO start
    # Strip the starter frogs so the play mode stays in explore.
    for entity in list(app.world.creatures.values):
        app.world.remove_entity(entity)
    app.sync_play_mode()
    app.ui_mode = UIMode.play
    return app


def _spawn_npc(
    app,
    kind: NPCKind,
    tree: DialogueTree,
    *,
    name: str,
    dx: int = 1,
    dy: int = 0,
    shop: bool = False,
):
    """Drop an NPC one tile away from the player, with the given tree.

    The starter room comes with companions placed around the player,
    so we first clear any blocker on the target tile (remove a
    companion that happens to be there) so the NPC really is at the
    intended cell and the player's ``e`` finds it.
    """

    player_position = app.world.positions.require(app.player)
    x = player_position.x + dx
    y = player_position.y + dy
    # Sweep any pre-existing entities off the target tile so the
    # adjacency check has a clean target. Companions placed by the
    # YOLO party fill the surrounding tiles, so this is the cleanest
    # way to make room for the test NPC.
    for occupant in list(app.world.entities_at(x, y)):
        if occupant in app.party.members:
            app.party.dismiss(occupant)
        app.world.remove_entity(occupant)
    entity = app.world.create_entity()
    app.world.positions.add(entity, Position(x=x, y=y))
    app.world.presentations.add(entity, Presentation("@"))
    app.world.names.add(entity, Name(name))
    app.world.factions.add(entity, Faction("town"))
    app.world.blockers.add(entity, BlocksMovement("occupied"))
    app.world.npcs.add(entity, NPC(kind=kind))
    app.world.npc_dialogues.add(entity, NPCDialogue(tree=tree))
    if shop:
        app.world.shops.add(entity, Shop(name=name))
        app.world.inventories.add(entity, Inventory(gold=100))
    return entity


def test_e_on_adjacent_info_npc_opens_dialogue_and_renders_line() -> None:
    app = _make_app_with_player()
    tree = info_tree("npc.gerda", "The dungeon lies east.")
    _spawn_npc(app, NPCKind.INFO, tree, name="Old Gerda")
    app.facing = (1, 0)

    app.handle_key(ord("e"))

    assert app.ui_mode is UIMode.dialogue
    assert app.dialogue is not None
    assert app.dialogue.node().line.text == "The dungeon lies east."


def test_e_on_npc_does_not_consume_action_in_turn_based_play() -> None:
    app = _make_app_with_player()
    # Force turn-based via the voluntary flag so we don't need a
    # hostile to flip the mode.
    app.voluntary_turn_based = True
    app.sync_play_mode()
    tree = info_tree("npc.gerda", "Hi.")
    _spawn_npc(app, NPCKind.INFO, tree, name="Old Gerda")
    app.facing = (1, 0)
    before_action_used = app.activation.action_used

    app.handle_key(ord("e"))

    assert app.ui_mode is UIMode.dialogue
    assert app.activation.action_used is before_action_used


def test_dialogue_close_via_esc_returns_to_play_mode() -> None:
    app = _make_app_with_player()
    tree = info_tree("npc.gerda", "Hi.")
    _spawn_npc(app, NPCKind.INFO, tree, name="Old Gerda")
    app.facing = (1, 0)
    app.handle_key(ord("e"))
    assert app.ui_mode is UIMode.dialogue

    app.handle_key(27)  # Esc

    assert app.ui_mode is UIMode.play
    assert app.dialogue is None


def test_dialogue_close_via_enter_on_single_option_node() -> None:
    app = _make_app_with_player()
    tree = info_tree("npc.gerda", "Hi.")
    _spawn_npc(app, NPCKind.INFO, tree, name="Old Gerda")
    app.facing = (1, 0)
    app.handle_key(ord("e"))

    # Enter selects option 1 which closes for an info tree.
    app.handle_key(10)

    assert app.ui_mode is UIMode.play
    assert app.dialogue is None


def test_recruit_option_adds_npc_to_party_and_removes_npc_marker() -> None:
    app = _make_app_with_player()
    tree = recruit_tree("npc.karn", "Join?", "Welcome.")
    npc = _spawn_npc(app, NPCKind.RECRUIT, tree, name="Karn")
    app.facing = (1, 0)
    app.handle_key(ord("e"))
    assert app.ui_mode is UIMode.dialogue

    # Press 1 (first option = accept).
    app.handle_key(ord("1"))

    assert npc in app.party.members
    # NPC marker / dialogue payload stripped.
    assert not app.world.npcs.has(npc)
    assert not app.world.npc_dialogues.has(npc)
    # The entity is still in the world (it's now a party member with
    # its position kept).
    assert app.world.positions.has(npc)
    # Recruit closes the modal.
    assert app.ui_mode is UIMode.play


def test_recruit_decline_does_not_join_party() -> None:
    app = _make_app_with_player()
    tree = recruit_tree("npc.karn", "Join?", "Welcome.")
    npc = _spawn_npc(app, NPCKind.RECRUIT, tree, name="Karn")
    app.facing = (1, 0)
    app.handle_key(ord("e"))

    # Press 2 (second option = decline).
    app.handle_key(ord("2"))

    assert npc not in app.party.members
    assert app.world.npcs.has(npc)
    assert app.ui_mode is UIMode.play


def test_shopkeeper_option_opens_shop_ui() -> None:
    app = _make_app_with_player()
    tree = shopkeeper_tree("npc.hadrin", "Welcome.")
    shopkeeper = _spawn_npc(
        app, NPCKind.SHOPKEEPER, tree, name="Hadrin", shop=True
    )
    app.facing = (1, 0)
    app.handle_key(ord("e"))
    assert app.ui_mode is UIMode.dialogue

    # Press 1 (first option = open shop).
    app.handle_key(ord("1"))

    assert app.ui_mode is UIMode.shop
    assert app.shop_partner == shopkeeper


def test_shopkeeper_close_option_returns_to_play() -> None:
    app = _make_app_with_player()
    tree = shopkeeper_tree("npc.hadrin", "Welcome.")
    _spawn_npc(app, NPCKind.SHOPKEEPER, tree, name="Hadrin", shop=True)
    app.facing = (1, 0)
    app.handle_key(ord("e"))

    # Press 2 (second option = close).
    app.handle_key(ord("2"))

    assert app.ui_mode is UIMode.play
    assert app.shop_partner is None


def test_dialogue_modal_ignores_world_keys_while_open() -> None:
    app = _make_app_with_player()
    tree = info_tree("npc.gerda", "Hi.")
    _spawn_npc(app, NPCKind.INFO, tree, name="Old Gerda")
    app.facing = (1, 0)
    player_x = app.world.positions.require(app.player).x

    app.handle_key(ord("e"))
    assert app.ui_mode is UIMode.dialogue

    # Movement key while the modal is open is ignored.
    app.handle_key(ord("l"))

    assert app.ui_mode is UIMode.dialogue
    assert app.world.positions.require(app.player).x == player_x


def test_world_skeleton_spawns_three_town_npcs() -> None:
    built = build_world_skeleton()
    npcs = list(built.world.npcs.values.items())

    assert len(npcs) == 3
    kinds = {component.kind for _, component in npcs}
    assert kinds == {NPCKind.INFO, NPCKind.RECRUIT, NPCKind.SHOPKEEPER}


def test_world_skeleton_shopkeeper_has_shop_component_and_dialogue() -> None:
    built = build_world_skeleton()
    shopkeeper = next(
        entity
        for entity, npc in built.world.npcs.values.items()
        if npc.kind is NPCKind.SHOPKEEPER
    )
    assert built.world.shops.has(shopkeeper)
    assert built.world.npc_dialogues.has(shopkeeper)
    assert built.world.inventories.has(shopkeeper)


def test_npc_dialogue_components_round_trip_through_world_save() -> None:
    """Save/load preserves NPC kind + dialogue tree structure."""

    built = build_world_skeleton()
    payload = json.loads(json.dumps(built.world.to_dict()))
    from src.core.world import World

    rebuilt = World.from_dict(payload)

    original_npcs = {
        int(entity): component.kind
        for entity, component in built.world.npcs.values.items()
    }
    rebuilt_npcs = {
        int(entity): component.kind
        for entity, component in rebuilt.npcs.values.items()
    }
    assert original_npcs == rebuilt_npcs
    # At least one dialogue tree survived with its root + first
    # option label intact.
    for entity, dialogue in built.world.npc_dialogues.values.items():
        rebuilt_dialogue = rebuilt.npc_dialogues.get(entity)
        assert rebuilt_dialogue is not None
        original_options = dialogue.tree.node(dialogue.tree.root).options
        rebuilt_options = rebuilt_dialogue.tree.node(
            rebuilt_dialogue.tree.root
        ).options
        assert [option.label for option in rebuilt_options] == [
            option.label for option in original_options
        ]
