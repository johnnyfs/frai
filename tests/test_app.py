from src.app import create_app
from src.core.components import CombatStats, Faction, Name, Position
from src.core.config import PLAYFIELD_WIDTH
from src.core.effects import KillEntity
from src.core.modes import (
    CharacterCreationMode,
    ConfirmQuitMode,
    GameOverMode,
    InventoryMode,
    NormalMode,
    StartChoiceMode,
)
from src.map.tiles import RUBBLE


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


def test_battle_move_onto_rubble_spends_terrain_adjusted_cost() -> None:
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
    app.world.tiles[40][159] = RUBBLE

    app.handle_key(ord("h"))

    assert app.world.positions.require(player).x == 159
    assert app.activation.movement_used == 6.0


def test_battle_move_is_denied_when_terrain_adjusted_cost_exceeds_remaining() -> None:
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
    app.world.tiles[40][159] = RUBBLE
    app.activation.movement_used = app.activation.movement_total - 3.0

    app.handle_key(ord("h"))

    assert app.world.positions.require(player).x == 160
    assert app.activation.movement_used == app.activation.movement_total - 3.0
    assert app.messages.current == "No movement remaining."


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


def test_explore_mode_rubble_movement_is_intentionally_free() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    player, companion = app.party
    for entity in list(app.world.creatures.values):
        app.world.remove_entity(entity)
    app.world.positions.require(player).x = 160
    app.world.positions.require(player).y = 40
    app.world.positions.require(companion).x = 162
    app.world.positions.require(companion).y = 40
    app.world.tiles[40][159] = RUBBLE
    app.activation.movement_used = app.activation.movement_total

    app.handle_key(ord("h"))

    assert app.major_mode == "explore"
    assert app.world.positions.require(player).x == 159
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


def test_player_can_enter_and_exit_voluntary_turn_mode_without_hostiles() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    player, companion = app.party
    for entity in list(app.world.creatures.values):
        app.world.remove_entity(entity)
    app.sync_major_mode()

    app.handle_key(ord("t"))

    assert app.major_mode == "turn"
    assert app.voluntary_turn_based is True
    assert app.active_actor() == player
    assert app.messages.current == "Entered turn-based mode."

    app.handle_key(ord(" "))

    assert app.active_actor() == companion

    app.handle_key(ord("t"))

    assert app.major_mode == "explore"
    assert app.voluntary_turn_based is False
    assert app.active_actor() == player
    assert app.messages.current == "Exited turn-based mode."


def test_voluntary_turn_mode_uses_battle_movement_budget_without_enemies() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    player, companion = app.party
    for entity in list(app.world.creatures.values):
        app.world.remove_entity(entity)
    app.world.positions.require(player).x = 160
    app.world.positions.require(player).y = 40
    app.world.positions.require(companion).x = 170
    app.world.positions.require(companion).y = 40
    app.sync_major_mode()

    app.handle_key(ord("t"))
    app.handle_key(ord("h"))

    assert app.major_mode == "turn"
    assert app.world.positions.require(player).x == 159
    assert app.activation.movement_used == 3


def test_enemy_presence_forces_battle_and_exit_waits_until_hostiles_are_gone() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    frog = next(iter(app.world.creatures.values))
    for entity in list(app.world.creatures.values):
        if entity != frog:
            app.world.remove_entity(entity)
    app.sync_major_mode()

    app.handle_key(ord("t"))

    assert app.major_mode == "battle"
    assert app.voluntary_turn_based is False
    assert app.messages.current == "Cannot exit turn-based mode while hostiles are present."

    app.apply_effects([KillEntity(frog)])
    app.sync_major_mode()

    assert app.major_mode == "explore"


def test_hostile_interrupting_voluntary_turn_mode_returns_to_explore_after_combat() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    frog = next(iter(app.world.creatures.values))
    for entity in list(app.world.creatures.values):
        if entity != frog:
            app.world.remove_entity(entity)
    app.apply_effects([KillEntity(frog)])
    app.sync_major_mode()

    app.handle_key(ord("t"))

    assert app.major_mode == "turn"
    assert app.voluntary_turn_based is True

    hostile = app.world.create_entity()
    player_position = app.world.positions.require(app.player)
    app.world.positions.add(hostile, Position(player_position.x + 2, player_position.y))
    app.world.names.add(hostile, Name("ambusher"))
    app.world.factions.add(hostile, Faction("enemy"))
    app.world.combat_stats.add(
        hostile,
        CombatStats(
            armor_class=10,
            hit_points=1,
            max_hit_points=1,
            strength=10,
            dexterity=10,
            constitution=10,
        ),
    )
    app.sync_major_mode()

    assert app.major_mode == "battle"
    assert app.voluntary_turn_based is False

    app.apply_effects([KillEntity(hostile)])
    app.sync_major_mode()

    assert app.major_mode == "explore"


def test_voluntary_turn_party_round_does_not_run_enemy_activations() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    for entity in list(app.world.creatures.values):
        app.world.remove_entity(entity)
    app.sync_major_mode()
    app.handle_key(ord("t"))
    app.active_party_index = len(app.party) - 1
    enemy_activations = 0

    def count_enemy_activations() -> None:
        nonlocal enemy_activations
        enemy_activations += 1

    original = type(app).run_enemy_activations
    type(app).run_enemy_activations = lambda _: count_enemy_activations()

    try:
        app.handle_key(ord(" "))
    finally:
        type(app).run_enemy_activations = original

    assert app.major_mode == "turn"
    assert app.active_actor() == app.player
    assert app.messages.current == "Entered turn-based mode."
    assert enemy_activations == 0


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


def test_battle_party_displacement_onto_rubble_spends_adjusted_cost() -> None:
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
    app.world.tiles[40][159] = RUBBLE

    app.handle_key(ord("h"))

    assert app.world.positions.require(player).x == 159
    assert app.world.positions.require(companion).x == 160
    assert app.activation.movement_used == 6.0


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


def test_enemy_activation_uses_default_budget_not_active_party_budget() -> None:
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
    app.activation.movement_total = 3

    app.handle_key(ord(" "))

    assert app.active_actor() == player
    assert app.world.positions.require(frog).x == 159


def test_enemy_step_feasibility_uses_terrain_adjusted_remaining_budget() -> None:
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
    app.world.tiles[40][159] = RUBBLE
    app.active_party_index = 1

    app.handle_key(ord(" "))

    assert app.active_actor() == player
    assert app.world.positions.require(frog).x == 158


def test_enemy_movement_spending_uses_terrain_adjusted_cost() -> None:
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
    for x in range(150, 160):
        app.world.tiles[40][x] = RUBBLE
    app.active_party_index = 1

    app.handle_key(ord(" "))

    assert app.active_actor() == player
    assert app.world.positions.require(frog).x == 154


def test_sync_major_mode_resets_activation_on_mode_transition() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    app.activation.movement_used = 12
    app.activation.action_used = True

    for entity in list(app.world.creatures.values):
        app.world.remove_entity(entity)
    app.sync_major_mode()

    assert app.major_mode == "explore"
    assert app.activation.movement_used == 0
    assert app.activation.action_used is False


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
