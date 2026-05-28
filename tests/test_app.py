from src.app import create_app
from src.core.config import PLAYFIELD_WIDTH
from src.core.effects import KillEntity
from src.core.modes import CharacterCreationMode, ConfirmQuitMode, GameOverMode, InventoryMode, NormalMode, StartChoiceMode


def test_quit_prompt_and_cancel_flow_uses_effects() -> None:
    app = create_app()
    app.mode = NormalMode()

    app.handle_key(ord("q"))

    assert isinstance(app.mode, ConfirmQuitMode)
    assert app.messages.current == "Quit? y/n"
    assert app.running is True

    app.handle_key(ord("n"))

    assert isinstance(app.mode, NormalMode)
    assert app.messages.current == ""
    assert app.running is True


def test_quit_confirmation_stops_app() -> None:
    app = create_app()
    app.mode = NormalMode()

    app.handle_key(ord("q"))
    app.handle_key(ord("y"))

    assert app.running is False


def test_app_starts_in_start_choice() -> None:
    app = create_app()

    assert isinstance(app.mode, StartChoiceMode)


def test_create_choice_enters_character_creation() -> None:
    app = create_app()

    app.handle_key(ord("c"))

    assert isinstance(app.mode, CharacterCreationMode)


def test_start_choice_can_request_quit_confirmation() -> None:
    app = create_app()

    app.handle_key(ord("q"))

    assert isinstance(app.mode, ConfirmQuitMode)
    assert app.messages.current == "Quit? y/n"


def test_character_creation_flow_assigns_sheet_and_starts_game() -> None:
    app = create_app()

    app.handle_key(ord("c"))  # create a character
    app.handle_key(ord("d"))  # Dragonborn
    app.handle_key(ord("a"))  # Barbarian
    app.handle_key(ord("e"))  # Berserker
    app.handle_key(ord("a"))  # Animal Handling
    app.handle_key(ord("t"))  # Athletics
    app.handle_key(ord("y"))  # continue skills
    app.handle_key(ord("y"))  # keep attributes
    app.handle_key(ord("y"))  # confirm

    assert isinstance(app.mode, NormalMode)
    assert app.world.characters.has(app.player)
    assert app.messages.current.startswith("Welcome,")


def test_yolo_choice_assigns_sheet_and_starts_game() -> None:
    app = create_app()

    app.handle_key(ord("y"))

    assert isinstance(app.mode, NormalMode)
    assert app.world.characters.has(app.player)
    assert app.world.combat_stats.has(app.player)
    assert app.world.weapons.has(app.player)
    assert app.messages.current.startswith("YOLO:")


def test_app_starts_with_yolo_party_member_nearby() -> None:
    app = create_app()
    companion = app.party[1]
    player_position = app.world.positions.require(app.player)
    companion_position = app.world.positions.require(companion)

    assert app.party == app.world.controlled_entities()
    assert companion != app.player
    assert app.world.characters.has(companion)
    assert app.world.combat_stats.has(companion)
    assert app.world.weapons.has(companion)
    assert max(
        abs(companion_position.x - player_position.x),
        abs(companion_position.y - player_position.y),
    ) <= 7


def test_battle_mode_uses_space_to_rotate_active_party_focus() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    player, companion = app.party
    frog = next(iter(app.world.creatures.values))
    for entity in list(app.world.creatures.values):
        if entity != frog:
            app.world.remove_entity(entity)
    app.world.positions.require(player).x = 160
    app.world.positions.require(player).y = 40
    app.world.positions.require(companion).x = 162
    app.world.positions.require(companion).y = 40
    app.world.positions.require(frog).x = 150
    app.world.positions.require(frog).y = 40

    app.handle_key(ord("h"))

    assert app.world.positions.require(player).x == 159
    assert app.active_actor() == player

    app.handle_key(ord(" "))

    assert app.active_actor() == companion
    assert app.focus == companion

    app.handle_key(ord("h"))

    assert app.world.positions.require(companion).x == 161

    app.handle_key(ord(" "))

    assert app.active_actor() == player
    assert app.focus == player


def test_movement_spends_budget_and_blocks_when_exhausted() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    player = app.active_actor()
    frog = next(iter(app.world.creatures.values))
    for entity in list(app.world.creatures.values):
        if entity != frog:
            app.world.remove_entity(entity)
    app.world.positions.require(player).x = 160
    app.world.positions.require(player).y = 40
    app.world.positions.require(frog).x = 150
    app.world.positions.require(frog).y = 40
    app.activation.movement_used = app.activation.movement_total

    app.handle_key(ord("h"))

    assert app.world.positions.require(player).x == 160
    assert app.messages.current == "No movement remaining."


def test_diagonal_movement_spends_four_and_quarter_feet() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    player = app.active_actor()
    companion = app.party[1]
    frog = next(iter(app.world.creatures.values))
    for entity in list(app.world.creatures.values):
        if entity != frog:
            app.world.remove_entity(entity)
    app.world.positions.require(player).x = 160
    app.world.positions.require(player).y = 40
    app.world.positions.require(companion).x = 170
    app.world.positions.require(companion).y = 40
    app.world.positions.require(frog).x = 150
    app.world.positions.require(frog).y = 40

    app.handle_key(ord("y"))

    assert app.world.positions.require(player).x == 159
    assert app.world.positions.require(player).y == 39
    assert app.activation.movement_used == 4.25


def test_explore_mode_moves_freely_and_party_follows() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    player, companion = app.party
    for entity in list(app.world.creatures.values):
        app.world.remove_entity(entity)
    app.world.positions.require(player).x = 160
    app.world.positions.require(player).y = 40
    app.world.positions.require(companion).x = 162
    app.world.positions.require(companion).y = 40
    app.activation.movement_used = app.activation.movement_total

    app.handle_key(ord("h"))

    assert app.major_mode == "explore"
    assert app.world.positions.require(player).x == 159
    assert app.world.positions.require(companion).x == 160
    assert app.activation.movement_used == 0


def test_explore_mode_displaces_party_member() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    player, companion = app.party
    for entity in list(app.world.creatures.values):
        app.world.remove_entity(entity)
    app.world.positions.require(player).x = 160
    app.world.positions.require(player).y = 40
    app.world.positions.require(companion).x = 159
    app.world.positions.require(companion).y = 40

    app.handle_key(ord("h"))

    assert app.world.positions.require(player).x == 159
    assert app.world.positions.require(companion).x == 160
    assert app.messages.current == "You displaced companion."


def test_killing_last_hostile_switches_to_explore_mode() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    frog = next(iter(app.world.creatures.values))
    for entity in list(app.world.creatures.values):
        if entity != frog:
            app.world.remove_entity(entity)
    app.sync_major_mode()

    assert app.major_mode == "battle"

    app.apply_effects([KillEntity(frog)])
    app.sync_major_mode()

    assert app.major_mode == "explore"
    assert app.active_actor() == app.player


def test_battle_mode_displaces_party_member_and_spends_movement() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    player, companion = app.party
    frog = next(iter(app.world.creatures.values))
    for entity in list(app.world.creatures.values):
        if entity != frog:
            app.world.remove_entity(entity)
    app.world.positions.require(player).x = 160
    app.world.positions.require(player).y = 40
    app.world.positions.require(companion).x = 159
    app.world.positions.require(companion).y = 40
    app.world.positions.require(frog).x = 150
    app.world.positions.require(frog).y = 40

    app.handle_key(ord("h"))

    assert app.world.positions.require(player).x == 159
    assert app.world.positions.require(companion).x == 160
    assert app.activation.movement_used == 3
    assert app.messages.current == "You displaced companion."


def test_attack_spends_action_but_not_movement_and_repeat_attack_is_blocked() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    player = app.active_actor()
    companion = app.party[1]
    frog = next(iter(app.world.creatures.values))
    for entity in list(app.world.creatures.values):
        if entity != frog:
            app.world.remove_entity(entity)
    app.world.positions.require(player).x = 160
    app.world.positions.require(player).y = 40
    app.world.positions.require(companion).x = 170
    app.world.positions.require(companion).y = 40
    app.world.positions.require(frog).x = 159
    app.world.positions.require(frog).y = 40
    app.world.combat_stats.require(frog).hit_points = 99
    app.world.combat_stats.require(frog).max_hit_points = 99

    app.handle_key(ord("h"))

    assert app.activation.action_used is True
    assert app.activation.movement_used == 0

    app.handle_key(ord("h"))

    assert app.messages.current == "Action already used."


def test_actor_can_still_move_after_attacking() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    player = app.active_actor()
    companion = app.party[1]
    frog = next(iter(app.world.creatures.values))
    for entity in list(app.world.creatures.values):
        if entity != frog:
            app.world.remove_entity(entity)
    app.world.positions.require(player).x = 160
    app.world.positions.require(player).y = 40
    app.world.positions.require(companion).x = 170
    app.world.positions.require(companion).y = 40
    app.world.positions.require(frog).x = 159
    app.world.positions.require(frog).y = 40
    app.world.combat_stats.require(frog).hit_points = 99

    app.handle_key(ord("h"))
    app.handle_key(ord("l"))

    assert app.world.positions.require(player).x == 161
    assert app.activation.action_used is True
    assert app.activation.movement_used == 3


def test_enemy_activation_uses_full_turn_after_party_round() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    player, companion = app.party
    frog = next(iter(app.world.creatures.values))
    for entity in list(app.world.creatures.values):
        if entity != frog:
            app.world.remove_entity(entity)
    app.world.positions.require(player).x = 160
    app.world.positions.require(player).y = 40
    app.world.positions.require(companion).x = 161
    app.world.positions.require(companion).y = 40
    app.world.positions.require(frog).x = 149
    app.world.positions.require(frog).y = 40
    app.active_party_index = 1

    app.handle_key(ord(" "))

    assert app.active_actor() == player
    assert app.world.positions.require(frog).x == 159
    assert "frog" in app.messages.current


def test_created_character_gets_class_starter_weapon() -> None:
    app = create_app()

    app.handle_key(ord("c"))
    app.handle_key(ord("d"))  # Dragonborn
    app.handle_key(ord("a"))  # Barbarian
    app.handle_key(ord("e"))  # Berserker
    app.handle_key(ord("a"))
    app.handle_key(ord("t"))
    app.handle_key(ord("y"))
    app.handle_key(ord("y"))
    app.handle_key(ord("y"))

    assert app.world.weapons.require(app.player).name == "greataxe"


def test_created_character_gets_class_starter_armor() -> None:
    app = create_app()

    app.handle_key(ord("c"))
    app.handle_key(ord("u"))  # Human
    app.handle_key(ord("f"))  # Fighter
    app.handle_key(ord("c"))  # Champion
    app.handle_key(ord("a"))
    app.handle_key(ord("t"))
    app.handle_key(ord("y"))
    app.handle_key(ord("y"))
    app.handle_key(ord("y"))

    assert app.world.armor.require(app.player).name == "chain mail"
    assert app.world.combat_stats.require(app.player).armor_class == 16


def test_inventory_command_opens_and_closes_inventory() -> None:
    app = create_app()
    app.handle_key(ord("y"))

    app.handle_key(ord("i"))

    assert isinstance(app.mode, InventoryMode)

    app.handle_key(ord("q"))

    assert isinstance(app.mode, NormalMode)


def test_long_messages_require_key_to_continue_before_actions() -> None:
    app = create_app()
    app.messages.emit("x" * (PLAYFIELD_WIDTH + 20))

    app.handle_key(ord("y"))

    assert app.messages.current
    assert isinstance(app.mode, StartChoiceMode)


def test_player_death_can_restart_to_opening_choice() -> None:
    app = create_app()
    old_player = app.player

    app.apply_effects([KillEntity(app.player)])

    assert isinstance(app.mode, GameOverMode)
    assert app.running is True

    app.handle_key(ord("r"))

    assert isinstance(app.mode, StartChoiceMode)
    assert app.player != old_player or app.world.player_entity() == app.player


def test_game_over_restart_is_not_blocked_by_pending_messages() -> None:
    app = create_app()
    app.messages.emit("x" * 100)
    app.apply_effects([KillEntity(app.player)])

    app.handle_key(ord("r"))

    assert isinstance(app.mode, StartChoiceMode)
