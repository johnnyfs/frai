"""Downed actors, death saves, and recovery (M29)."""

from __future__ import annotations

import random

from src.app import create_app
from src.core.components import CombatStats, DeathSaves
from src.core.conditions import (
    Condition,
    ConditionKind,
    DurationPolicy,
    apply_condition,
)
from src.core.death_saves import (
    DEATH_SAVE_DC,
    FAILURES_TO_DIE,
    SUCCESSES_TO_STABILIZE,
    begin_downed,
    is_dying,
    is_stable,
    is_unconscious,
    party_wiped,
    record_damage_failure,
    revive_with_healing,
    roll_death_save,
    stabilize_pcs_on_rest,
)
from src.core.effects import (
    ApplyCondition,
    ApplyHealing,
    DamageEntity,
    EmitMessage,
    EndCondition,
    KillEntity,
)
from src.core.modes import PlayMode, UIMode
from src.core.save import load_game, save_game
from src.core.shelter import RestPermission, ShelterZone
from src.core.world import World
from src.systems.rest_system import attempt_long_rest, attempt_short_rest


class _DeterministicRng(random.Random):
    """A `random.Random` whose ``randint`` consumes a scripted sequence.

    The death-save resolver only ever calls ``randint(1, 20)``, so a
    single-int queue is enough. Any out-of-range value raises so a test
    that miscounts rolls fails loudly instead of advancing through stale
    state.
    """

    def __init__(self, values: list[int]) -> None:
        super().__init__(0)
        self._values = list(values)

    def randint(self, low: int, high: int) -> int:
        if not self._values:
            raise AssertionError("RNG exhausted")
        value = self._values.pop(0)
        if not low <= value <= high:
            raise AssertionError(f"value {value} outside randint({low}, {high})")
        return value


def _booted_app():
    """Yolo-create-character path to a play-screen App ready for testing."""
    app = create_app()
    app.handle_key(ord("y"))
    assert app.ui_mode is UIMode.play
    return app


# ---------------------------------------------------------------------------
# Downed transition (damage to 0 HP)
# ---------------------------------------------------------------------------


def test_pc_dropped_to_zero_hp_becomes_unconscious_not_dead() -> None:
    app = _booted_app()
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = 5

    app.apply_effects([DamageEntity(app.player, 5)])

    assert app.world.combat_stats.require(app.player).hit_points == 0
    # The player is still in the world and unconscious.
    assert app.world.positions.has(app.player)
    assert is_unconscious(app.world, app.player)
    saves = app.world.death_saves.get(app.player)
    assert saves is not None
    assert saves.successes == 0
    assert saves.failures == 0
    assert saves.stable is False


def test_npc_dropped_to_zero_hp_keeps_legacy_death_path() -> None:
    """Hostile NPCs die outright when HP hits 0 (the M28 distinction)."""
    app = _booted_app()
    enemy = app.world.create_entity()
    from src.core.components import Position, Name, Faction
    from src.core.factions import FactionId

    app.world.positions.add(enemy, Position(0, 0))
    app.world.names.add(enemy, Name("ghoul"))
    app.world.factions.add(enemy, Faction(FactionId.DUNGEON.value))
    app.world.combat_stats.add(
        enemy,
        CombatStats(
            armor_class=10,
            hit_points=3,
            max_hit_points=3,
            strength=10,
            dexterity=10,
            constitution=10,
        ),
    )

    app.apply_effects([DamageEntity(enemy, 3)])

    # NPC: no unconscious condition, no DeathSaves; HP simply clamps.
    assert app.world.combat_stats.require(enemy).hit_points == 0
    assert not is_unconscious(app.world, enemy)
    assert app.world.death_saves.get(enemy) is None


def test_massive_damage_kills_pc_outright_bypassing_downed() -> None:
    app = _booted_app()
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = stats.max_hit_points  # full HP first.

    # Damage that drives HP below -max is a real kill per SRD.
    overkill = stats.max_hit_points * 3
    app.apply_effects([DamageEntity(app.player, overkill)])

    # Player went to game-over via the massive-damage branch.
    assert app.ui_mode is UIMode.game_over


def test_combat_emitted_kill_on_pc_is_downgraded_to_downed() -> None:
    """When CombatSystem emits DamageEntity + KillEntity together on a PC."""
    app = _booted_app()
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = 3

    # Mirror CombatSystem's "lethal hit" output.
    app.apply_effects([DamageEntity(app.player, 3), KillEntity(app.player)])

    # The KillEntity should NOT have removed the player from the world.
    assert app.world.positions.has(app.player)
    assert is_unconscious(app.world, app.player)
    # Game-over is NOT triggered — the PC is still rolling saves.
    assert app.ui_mode is UIMode.play


# ---------------------------------------------------------------------------
# Death-save resolution
# ---------------------------------------------------------------------------


def test_passing_save_increments_success_count() -> None:
    app = _booted_app()
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = 0
    app.apply_effects(begin_downed(app.world, app.player))

    rng = _DeterministicRng([DEATH_SAVE_DC])  # exact DC = success.
    effects = roll_death_save(app.world, app.player, rng)
    app.apply_effects(effects)

    saves = app.world.death_saves.require(app.player)
    assert saves.successes == 1
    assert saves.failures == 0
    assert not saves.stable


def test_three_successes_stabilizes_pc() -> None:
    app = _booted_app()
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = 0
    app.apply_effects(begin_downed(app.world, app.player))

    # Three successive d20=15 rolls.
    rng = _DeterministicRng([15, 15, 15])
    for _ in range(3):
        app.apply_effects(roll_death_save(app.world, app.player, rng))

    saves = app.world.death_saves.require(app.player)
    assert saves.stable is True
    assert is_stable(app.world, app.player)
    # Still unconscious until a rest revives them to 1 HP.
    assert is_unconscious(app.world, app.player)


def test_three_failures_kills_pc() -> None:
    app = _booted_app()
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = 0
    app.apply_effects(begin_downed(app.world, app.player))

    # Three d20=2 rolls; with a 10 DC and modest CON modifier this fails.
    rng = _DeterministicRng([2, 2, 2])
    for _ in range(3):
        app.apply_effects(roll_death_save(app.world, app.player, rng))

    # KillEntity for the player triggers UIMode.game_over.
    assert app.ui_mode is UIMode.game_over


def test_three_failures_clears_downed_state_artifacts() -> None:
    """#119 — when a PC dies via the third failure the DeathSaves row
    and the unconscious condition are torn down. Without this the
    round-tick driver kept rolling a 4th/5th save on the dead actor.
    """
    app = _booted_app()
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = 0
    app.apply_effects(begin_downed(app.world, app.player))

    rng = _DeterministicRng([2, 2, 2])
    for _ in range(3):
        app.apply_effects(roll_death_save(app.world, app.player, rng))

    assert app.ui_mode is UIMode.game_over
    assert app.world.death_saves.get(app.player) is None
    assert not is_unconscious(app.world, app.player)


def test_round_tick_after_death_does_not_reroll_saves() -> None:
    """#119 — subsequent round ticks must not roll another death save
    on the dead PC. Reproduces the playtest scenario: three failures,
    then several more round ticks, and asserts no zombie save state
    materialises.
    """
    app = _booted_app()
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = 0
    app.apply_effects(begin_downed(app.world, app.player))

    # Force three failing rolls via the round-boundary tick. The third
    # tick should emit KillEntity and tear down the downed state.
    app.loot_rng = _DeterministicRng([2, 2, 2])
    for _ in range(3):
        app._tick_round_boundary()
    assert app.ui_mode is UIMode.game_over
    assert app.world.death_saves.get(app.player) is None

    # Subsequent ticks must be no-ops for the dead actor. Hand the
    # driver an empty RNG so an attempted roll would explode.
    app.loot_rng = _DeterministicRng([])
    for _ in range(5):
        app._tick_round_boundary()

    # No regression in the death-save row or the unconscious condition.
    assert app.world.death_saves.get(app.player) is None
    assert not is_unconscious(app.world, app.player)


def test_save_after_pc_death_does_not_carry_downed_artifacts(tmp_path) -> None:
    """#119 — a save written after a real PC death must not carry the
    DeathSaves row or the unconscious condition forward to the next
    load. The playtest report flagged save-friendliness as the long-
    term failure mode.
    """
    app = _booted_app()
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = 0
    app.apply_effects(begin_downed(app.world, app.player))

    rng = _DeterministicRng([2, 2, 2])
    for _ in range(3):
        app.apply_effects(roll_death_save(app.world, app.player, rng))

    target = tmp_path / "save.json"
    save_game(app, target)
    loaded = load_game(target)

    assert loaded.world.death_saves.get(loaded.player) is None
    assert not is_unconscious(loaded.world, loaded.player)


def test_natural_20_revives_pc_to_one_hp() -> None:
    app = _booted_app()
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = 0
    app.apply_effects(begin_downed(app.world, app.player))

    rng = _DeterministicRng([20])
    app.apply_effects(roll_death_save(app.world, app.player, rng))

    assert app.world.combat_stats.require(app.player).hit_points >= 1
    assert not is_unconscious(app.world, app.player)
    assert app.world.death_saves.get(app.player) is None


def test_natural_1_counts_as_two_failures() -> None:
    app = _booted_app()
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = 0
    app.apply_effects(begin_downed(app.world, app.player))

    rng = _DeterministicRng([1])
    app.apply_effects(roll_death_save(app.world, app.player, rng))

    saves = app.world.death_saves.require(app.player)
    assert saves.failures == 2
    assert saves.successes == 0
    assert not saves.stable


# ---------------------------------------------------------------------------
# Damage on downed actor
# ---------------------------------------------------------------------------


def test_damage_on_downed_pc_adds_failure() -> None:
    app = _booted_app()
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = 0
    app.apply_effects(begin_downed(app.world, app.player))

    # Single non-massive blow → one failure.
    app.apply_effects([DamageEntity(app.player, 1)])

    saves = app.world.death_saves.require(app.player)
    assert saves.failures == 1
    # HP did not drop further; clamp at 0.
    assert app.world.combat_stats.require(app.player).hit_points == 0


def test_three_damage_blows_on_downed_pc_kill() -> None:
    app = _booted_app()
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = 0
    app.apply_effects(begin_downed(app.world, app.player))

    # Three small hits convert to three failures.
    for _ in range(3):
        app.apply_effects([DamageEntity(app.player, 1)])
    # Triggered real death → game_over.
    assert app.ui_mode is UIMode.game_over


# ---------------------------------------------------------------------------
# Healing revival
# ---------------------------------------------------------------------------


def test_healing_unconscious_pc_revives_and_clears_saves() -> None:
    app = _booted_app()
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = 0
    app.apply_effects(begin_downed(app.world, app.player))

    # Even one HP of healing wakes the PC and drops the DeathSaves row.
    app.apply_effects([ApplyHealing(app.player, 5)])

    assert app.world.combat_stats.require(app.player).hit_points == 5
    assert not is_unconscious(app.world, app.player)
    assert app.world.death_saves.get(app.player) is None


# ---------------------------------------------------------------------------
# Party wipe → game-over
# ---------------------------------------------------------------------------


def test_party_wipe_when_all_pcs_unconscious_triggers_game_over() -> None:
    app = _booted_app()
    for member in app.party.members:
        stats = app.world.combat_stats.get(member)
        if stats is None:
            continue
        stats.hit_points = 1
        app.apply_effects([DamageEntity(member, 1)])

    assert app.ui_mode is UIMode.game_over


def test_one_conscious_pc_means_no_wipe_yet() -> None:
    app = _booted_app()
    members = list(app.party.members)
    assert len(members) >= 2
    leader, *rest = members
    for member in rest:
        stats = app.world.combat_stats.get(member)
        if stats is None:
            continue
        stats.hit_points = 1
        app.apply_effects([DamageEntity(member, 1)])
    # Leader still conscious.
    assert app.world.combat_stats.require(leader).hit_points > 0
    assert app.ui_mode is UIMode.play


# ---------------------------------------------------------------------------
# Rest restores stable PCs to 1 HP
# ---------------------------------------------------------------------------


def test_short_rest_restores_stable_pc_to_one_hp() -> None:
    app = _booted_app()
    # Remove every creature so we're in explore mode.
    for entity in list(app.world.creatures.values):
        app.world.remove_entity(entity)
    app.sync_play_mode()

    # Mark the player as stable.
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = 0
    apply_condition(
        app.world,
        app.player,
        Condition(
            kind=ConditionKind.UNCONSCIOUS,
            duration=DurationPolicy.until_removed(),
        ),
    )
    app.world.death_saves.add(app.player, DeathSaves(successes=3, stable=True))

    # Wrap the player in a free short-rest shelter.
    app.world.shelter_zones.add(
        ShelterZone(
            zone_id="bedroll",
            left=0,
            top=0,
            width=200,
            height=200,
            rest_permission=RestPermission.SHORT_ONLY,
        )
    )

    effects = attempt_short_rest(app)
    app.apply_effects(effects)

    assert app.world.combat_stats.require(app.player).hit_points >= 1
    assert not is_unconscious(app.world, app.player)
    assert app.world.death_saves.get(app.player) is None


# ---------------------------------------------------------------------------
# Save / load
# ---------------------------------------------------------------------------


def test_save_load_round_trips_death_saves_component() -> None:
    app = _booted_app()
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = 0
    app.apply_effects(begin_downed(app.world, app.player))
    saves = app.world.death_saves.require(app.player)
    saves.successes = 2
    saves.failures = 1

    payload = app.world.to_dict()
    rehydrated = World.from_dict(payload)

    reloaded_saves = rehydrated.death_saves.get(app.player)
    assert reloaded_saves is not None
    assert reloaded_saves.successes == 2
    assert reloaded_saves.failures == 1
    assert reloaded_saves.stable is False


# ---------------------------------------------------------------------------
# Round-tick driver wires death saves into the turn controller
# ---------------------------------------------------------------------------


def test_round_boundary_tick_rolls_death_save_for_downed_pcs() -> None:
    """`_tick_round_boundary` rolls one save per downed PC each round."""
    app = _booted_app()
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = 0
    app.apply_effects(begin_downed(app.world, app.player))

    # Force a deterministic roll: success.
    app.loot_rng = _DeterministicRng([12])
    app._tick_round_boundary()

    saves = app.world.death_saves.require(app.player)
    assert saves.successes == 1


def test_observation_surfaces_death_saves_summary() -> None:
    """An agentic playtester can read the death-save tally from the snapshot."""
    from src.ui.observation import observe

    app = _booted_app()
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = 0
    app.apply_effects(begin_downed(app.world, app.player))
    saves = app.world.death_saves.require(app.player)
    saves.successes = 1
    saves.failures = 2

    snapshot = observe(app)
    matched = next(actor for actor in snapshot.party if actor.id == int(app.player))
    assert matched.death_saves is not None
    assert matched.death_saves.successes == 1
    assert matched.death_saves.failures == 2
    assert matched.death_saves.stable is False


def _down_player_in_place(app) -> None:
    """Helper: drop the player to 0 HP and apply the downed state.

    Mirrors the playtest reproduction in #120 — the player is the
    active actor and is unconscious with a fresh DeathSaves(0,0,False)
    row. The party's other members are untouched.
    """
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = 0
    app.apply_effects(begin_downed(app.world, app.player))
    assert is_unconscious(app.world, app.player)


def test_unconscious_pc_cannot_move_attack_or_bump_attack() -> None:
    """#120 — pressing ``l`` while unconscious refuses with a clear
    message; the actor's action economy is not consumed.
    """
    app = _booted_app()
    _down_player_in_place(app)
    activation_before = app.turn.active_activation
    action_used_before = activation_before.action_used
    movement_used_before = activation_before.movement_used

    app.handle_key(ord("l"))

    assert "unconscious" in app.messages.current.lower()
    # No action or movement consumed.
    activation_after = app.turn.active_activation
    assert activation_after.action_used == action_used_before
    assert activation_after.movement_used == movement_used_before


def test_unconscious_pc_examine_key_refused() -> None:
    """#120 — pressing ``x`` while unconscious does not open the
    examine cursor.
    """
    app = _booted_app()
    _down_player_in_place(app)
    assert app.ui_mode is UIMode.play

    app.handle_key(ord("x"))

    assert app.ui_mode is UIMode.play  # still on the world view
    assert app.targeting is None
    assert "unconscious" in app.messages.current.lower()


def test_unconscious_pc_spell_menu_refused() -> None:
    """#120 — pressing ``s`` while unconscious does not open the
    spell-menu modal (broader scope than #107).
    """
    app = _booted_app()
    _down_player_in_place(app)

    app.handle_key(ord("s"))

    assert app.ui_mode is UIMode.play
    assert "unconscious" in app.messages.current.lower()


def test_unconscious_pc_can_still_open_inventory() -> None:
    """#120 — UI modals stay reachable while unconscious so the
    player can inspect items / pass control to a teammate.
    """
    app = _booted_app()
    _down_player_in_place(app)

    app.handle_key(ord("i"))

    assert app.ui_mode is UIMode.inventory


def test_unconscious_pc_can_still_open_help() -> None:
    """#120 — pressing ``?`` while unconscious still opens the help
    modal (UI, not an action).
    """
    app = _booted_app()
    _down_player_in_place(app)

    app.handle_key(ord("?"))

    assert app.ui_mode is UIMode.help


def test_unconscious_pc_can_end_turn_with_space() -> None:
    """#120 — pressing ``space`` while unconscious in turn-based mode
    must still pass the turn so a teammate can heal them.
    """
    app = _booted_app()
    _down_player_in_place(app)
    # Force turn-based mode so end-turn is meaningful.
    app.turn.voluntary_turn_based = True
    app.turn.sync_play_mode()
    assert app.turn.play_mode is PlayMode.turn_based
    starting_index = app.party.active_index

    app.handle_key(ord(" "))

    # Either the active_index advanced or the turn controller wrapped
    # the round; what matters is that the unconscious player is no
    # longer the active actor.
    assert app.active_actor() != app.player or app.party.active_index != starting_index


def test_unconscious_pc_movement_keys_refused_each_direction() -> None:
    """#120 — every cardinal movement key (h/j/k/l) is refused while
    unconscious. The actor stays put and resources are not consumed.
    """
    app = _booted_app()
    _down_player_in_place(app)
    start_position = app.world.positions.require(app.player)
    sx, sy = start_position.x, start_position.y

    for key in ("h", "j", "k"):
        app.handle_key(ord(key))
        after = app.world.positions.require(app.player)
        assert (after.x, after.y) == (sx, sy)
        assert "unconscious" in app.messages.current.lower()


def test_unconscious_pc_pickup_and_interact_refused() -> None:
    """#120 — pickup (`,`) and interact (`e`) are refused while
    unconscious — they're full-blown actions, not UI.
    """
    app = _booted_app()
    _down_player_in_place(app)

    app.handle_key(ord(","))
    assert "unconscious" in app.messages.current.lower()

    app.handle_key(ord("e"))
    assert "unconscious" in app.messages.current.lower()


def test_healing_after_downed_revives_and_clears_gate() -> None:
    """#120 — once the unconscious condition lifts (e.g. via heal),
    the same input that was refused now resolves normally. This is
    the recovery path the constraints carve out.
    """
    from src.core.effects import ApplyHealing

    app = _booted_app()
    _down_player_in_place(app)

    # Refuse first.
    app.handle_key(ord("x"))
    assert "unconscious" in app.messages.current.lower()
    assert app.ui_mode is UIMode.play

    # Heal and assert the unconscious condition + DeathSaves row are
    # cleared by the existing heal path (the constraint we promised).
    app.apply_effects([ApplyHealing(app.player, 3)])
    assert not is_unconscious(app.world, app.player)
    assert app.world.death_saves.get(app.player) is None

    # The same key now opens the examine cursor.
    app.handle_key(ord("x"))
    assert app.ui_mode is UIMode.targeting


def test_save_load_preserves_unconscious_condition_through_full_save(tmp_path) -> None:
    """Full app-level save/load round-trip preserves the downed state."""
    app = _booted_app()
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = 0
    app.apply_effects(begin_downed(app.world, app.player))
    saves = app.world.death_saves.require(app.player)
    saves.successes = 2
    saves.failures = 1

    target = tmp_path / "save.json"
    save_game(app, target)
    loaded = load_game(target)

    reloaded = loaded.world.death_saves.get(loaded.player)
    assert reloaded is not None
    assert reloaded.successes == 2
    assert reloaded.failures == 1
    assert is_unconscious(loaded.world, loaded.player)
