import random

from src.app import create_app
from src.core.actions import InteractAttempt, MoveAttempt
from src.core.components import BlocksMovement, Container, Door, Lock, Position, Trap
from src.core.modes import UIMode


def _clear_hostiles(app) -> None:
    for entity in list(app.world.creatures.values):
        app.world.remove_entity(entity)
    app.sync_play_mode()


def _add_feature(app, x: int, y: int):
    entity = app.world.create_entity()
    app.world.positions.add(entity, Position(x, y))
    return entity


def test_app_applies_locked_door_interaction_and_allows_movement_afterward() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    _clear_hostiles(app)
    app.ui_mode = UIMode.play
    player_position = app.world.positions.require(app.player)
    start_x = player_position.x
    door = _add_feature(app, start_x + 1, player_position.y)
    app.world.doors.add(door, Door())
    app.world.locks.add(door, Lock(is_locked=True, pick_dc=12))
    app.world.blockers.add(door, BlocksMovement("locked door"))

    app.apply_effects(app._handle_interaction(InteractAttempt(app.player, 1, 0, check_result=12)))

    assert app.world.locks.require(door).is_locked is False
    assert app.world.doors.require(door).is_open is True
    assert not app.world.blockers.has(door)

    app.apply_effects(app._handle_explore_move(MoveAttempt(app.player, 1, 0)))

    assert app.world.positions.require(app.player).x == start_x + 1


def test_app_failed_lock_pick_leaves_blocker() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    _clear_hostiles(app)
    player_position = app.world.positions.require(app.player)
    door = _add_feature(app, player_position.x + 1, player_position.y)
    app.world.doors.add(door, Door())
    app.world.locks.add(door, Lock(is_locked=True, pick_dc=12))
    app.world.blockers.add(door, BlocksMovement("locked door"))

    app.apply_effects(app._handle_interaction(InteractAttempt(app.player, 1, 0, check_result=8)))

    assert app.world.locks.require(door).is_locked is True
    assert app.world.blockers.has(door)


def test_app_trap_trigger_damages_and_disarms_non_reusable_trap() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    _clear_hostiles(app)
    player_position = app.world.positions.require(app.player)
    trap = _add_feature(app, player_position.x + 1, player_position.y)
    app.world.traps.add(trap, Trap(disarm_dc=12, damage=3))
    before_hp = app.world.combat_stats.require(app.player).hit_points

    app.apply_effects(app._handle_interaction(InteractAttempt(app.player, 1, 0, check_result=5)))

    assert app.world.combat_stats.require(app.player).hit_points == before_hp - 3
    assert app.world.traps.require(trap).is_armed is False


def test_app_container_interaction_marks_container_open() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    _clear_hostiles(app)
    player_position = app.world.positions.require(app.player)
    container = _add_feature(app, player_position.x + 1, player_position.y)
    app.world.containers.add(container, Container())

    app.apply_effects(app._handle_interaction(InteractAttempt(app.player, 1, 0)))

    assert app.world.containers.require(container).is_open is True
    # Unified Container/Inventory model: opening a container must expose its
    # contents via an Inventory component on the same entity (M42).
    assert app.world.inventories.has(container)


def test_app_opened_container_removes_blocker_and_allows_walk_in() -> None:
    """Regression for #80: opening a chest must clear its
    ``BlocksMovement`` so the actor can step onto the tile and pick up
    its contents via the M30 ground-pickup path. Before the fix the
    chest's blocker persisted, walking east returned ``Blocked.``, and
    the seeded loot was stranded forever."""
    from src.core.actions import PickupAttempt
    from src.core.components import Inventory
    from src.core.items import add_item, has_item, item_count

    app = create_app()
    app.handle_key(ord("y"))
    _clear_hostiles(app)
    app.ui_mode = UIMode.play
    player_position = app.world.positions.require(app.player)
    start_x = player_position.x
    container = _add_feature(app, start_x + 1, player_position.y)
    app.world.containers.add(container, Container())
    app.world.blockers.add(container, BlocksMovement("container"))
    chest_inventory = Inventory(gold=25)
    add_item(chest_inventory, "weapon.dagger", 1)
    app.world.inventories.add(container, chest_inventory)

    # Open the chest from the adjacent tile.
    app.apply_effects(app._handle_interaction(InteractAttempt(app.player, 1, 0)))

    assert app.world.containers.require(container).is_open is True
    assert not app.world.blockers.has(container)

    # Step onto the now-open chest tile.
    app.apply_effects(app._handle_explore_move(MoveAttempt(app.player, 1, 0)))
    assert app.world.positions.require(app.player).x == start_x + 1

    # Pick up the contents from the chest tile.
    party_inventory = app.world.inventories.require(app.player)
    before_gold = party_inventory.gold
    before_daggers = item_count(party_inventory, "weapon.dagger")

    app.apply_effects(
        app.dispatcher.dispatch(PickupAttempt(actor=app.player), app.world)
    )

    assert party_inventory.gold == before_gold + 25
    assert has_item(party_inventory, "weapon.dagger", before_daggers + 1)
    # The chest persists on the map but is now empty.
    assert app.world.inventories.require(container).gold == 0
    assert item_count(app.world.inventories.require(container), "weapon.dagger") == 0


def test_handle_key_interacts_with_faced_feature() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    _clear_hostiles(app)
    player_position = app.world.positions.require(app.player)
    container = _add_feature(app, player_position.x + 1, player_position.y)
    app.world.containers.add(container, Container())

    app.handle_key(ord("e"))

    assert app.world.containers.require(container).is_open is True


def test_handle_key_uses_last_movement_direction_for_interaction() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    _clear_hostiles(app)
    player_position = app.world.positions.require(app.player)
    container = _add_feature(app, player_position.x, player_position.y + 2)
    app.world.containers.add(container, Container())

    app.handle_key(ord("j"))
    app.handle_key(ord("e"))

    assert app.world.containers.require(container).is_open is True


def test_handle_key_locked_door_via_public_path_resolves_through_skill_check() -> None:
    # With a character sheet on the player, the public `e` path no longer
    # refuses with "You need a way to pick it" - it rolls an implicit check
    # (M26) and resolves to either an unlock or a "Lock pick failed." message.
    app = create_app(rng=random.Random(0))
    app.handle_key(ord("y"))
    _clear_hostiles(app)
    app.ui_mode = UIMode.play
    player_position = app.world.positions.require(app.player)
    door = _add_feature(app, player_position.x + 1, player_position.y)
    app.world.doors.add(door, Door())
    app.world.locks.add(door, Lock(is_locked=True, pick_dc=12))
    app.world.blockers.add(door, BlocksMovement("locked door"))

    app.handle_key(ord("e"))

    assert app.messages.current != "It's locked. You need a way to pick it."
    assert app.messages.current in {"Unlocked and opened.", "Lock pick failed."}
    if app.messages.current == "Unlocked and opened.":
        assert app.world.locks.require(door).is_locked is False
        assert app.world.doors.require(door).is_open is True
        assert not app.world.blockers.has(door)
    else:
        assert app.world.locks.require(door).is_locked is True
        assert app.world.blockers.has(door)


def test_handle_key_locked_door_eventually_succeeds_across_seeds() -> None:
    # Across enough seeds at least one roll passes DC 10, proving the
    # success path is actually reachable via the public `e` key for a
    # character-sheet-equipped party member.
    for seed in range(50):
        app = create_app(rng=random.Random(seed))
        app.handle_key(ord("y"))
        _clear_hostiles(app)
        app.ui_mode = UIMode.play
        player_position = app.world.positions.require(app.player)
        door = _add_feature(app, player_position.x + 1, player_position.y)
        app.world.doors.add(door, Door())
        app.world.locks.add(door, Lock(is_locked=True, pick_dc=10))
        app.world.blockers.add(door, BlocksMovement("locked door"))

        app.handle_key(ord("e"))

        if app.world.locks.require(door).is_locked is False:
            assert app.world.doors.require(door).is_open is True
            return
    raise AssertionError("No seed in 0..49 unlocked DC-10 lock via public e path")


def test_handle_key_armed_trap_via_public_path_resolves_through_skill_check() -> None:
    # M9 used to emit "You sense danger..." for any armed trap touched via
    # public `e`. With M26 the disarm is now actually attempted using the
    # actor's DEX modifier; outcome is either disarm or trigger+damage.
    app = create_app(rng=random.Random(0))
    app.handle_key(ord("y"))
    _clear_hostiles(app)
    app.ui_mode = UIMode.play
    player_position = app.world.positions.require(app.player)
    trap = _add_feature(app, player_position.x + 1, player_position.y)
    app.world.traps.add(trap, Trap(disarm_dc=12, damage=3))
    before_hp = app.world.combat_stats.require(app.player).hit_points

    app.handle_key(ord("e"))

    assert app.messages.current != "You sense danger - you need a way to disarm it."
    trap_component = app.world.traps.require(trap)
    after_hp = app.world.combat_stats.require(app.player).hit_points
    if trap_component.is_armed is False and after_hp == before_hp:
        assert "disarmed" in app.messages.current.lower()
    else:
        # Failure path: damage taken and trap triggered.
        assert after_hp == before_hp - 3


def test_handle_key_opens_unlocked_door_via_public_path() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    _clear_hostiles(app)
    app.ui_mode = UIMode.play
    player_position = app.world.positions.require(app.player)
    door = _add_feature(app, player_position.x + 1, player_position.y)
    app.world.doors.add(door, Door())
    app.world.blockers.add(door, BlocksMovement("door"))

    app.handle_key(ord("e"))

    assert app.world.doors.require(door).is_open is True
    assert not app.world.blockers.has(door)


def test_handle_key_reports_nothing_to_interact_with_when_no_target() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    _clear_hostiles(app)
    app.ui_mode = UIMode.play

    app.handle_key(ord("e"))

    assert app.messages.current == "Nothing to interact with."


def test_turn_based_interaction_spends_action_and_blocks_second_interaction() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    frog = next(iter(app.world.creatures.values))
    for entity in list(app.world.creatures.values):
        if entity != frog:
            app.world.remove_entity(entity)
    player_position = app.world.positions.require(app.player)
    container = _add_feature(app, player_position.x + 1, player_position.y)
    app.world.containers.add(container, Container())
    app.sync_play_mode()

    app.apply_effects(app._handle_interaction(InteractAttempt(app.player, 1, 0)))

    assert app.activation.action_used is True
    assert app.world.containers.require(container).is_open is True

    app.world.containers.require(container).is_open = False
    app.apply_effects(app._handle_interaction(InteractAttempt(app.player, 1, 0)))

    assert app.world.containers.require(container).is_open is False
    assert app.messages.current == "Action already used."
