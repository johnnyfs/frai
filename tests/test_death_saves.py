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
from src.core.modes import UIMode
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
