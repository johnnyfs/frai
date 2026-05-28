import subprocess
import sys

from src.core.actions import MoveAttempt
from src.core.effects import DamageEntity, KillEntity, MoveEntity, SetMode
from src.core.modes import NormalMode
from src.map.tiles import TileKind
from tests.support.tiny_world import (
    SequenceRng,
    add_actor,
    add_enemy,
    add_party_member,
    apply_world_effects,
    build_tiny_encounter,
    build_tiny_map,
    build_tiny_party_world,
    resolve_action,
)


def test_tiny_map_builder_creates_deterministic_bordered_floor() -> None:
    first = build_tiny_map()
    second = build_tiny_map()

    assert first.width == second.width == 7
    assert first.height == second.height == 5
    assert first.tile_at(2, 2).kind is TileKind.FLOOR
    assert first.tile_at(0, 2).kind is TileKind.WALL
    assert first.tile_at(6, 2).kind is TileKind.WALL
    assert first.tile_at(2, 0).kind is TileKind.WALL
    assert first.tile_at(2, 4).kind is TileKind.WALL
    assert [[tile.glyph for tile in row] for row in first.tiles] == [
        [tile.glyph for tile in row]
        for row in second.tiles
    ]


def test_actor_party_and_enemy_builders_attach_minimal_rule_components() -> None:
    world = build_tiny_map()
    player = add_actor(world, 2, 2)
    companion = add_party_member(world, 3, 2, name="hireling")
    enemy = add_enemy(world, 4, 2)

    assert world.controlled_entities() == [player, companion]
    assert world.factions.require(player).value == "player"
    assert world.factions.require(companion).value == "player"
    assert world.factions.require(enemy).value == "enemy"
    assert world.creatures.require(enemy).kind == "frog"
    assert world.blockers.has(player)
    assert world.blockers.has(companion)
    assert world.blockers.has(enemy)
    assert world.combat_stats.require(enemy).hit_points == 3
    assert world.weapons.require(player).name == "longsword"


def test_party_fixture_returns_roster_order_and_positions() -> None:
    fixture = build_tiny_party_world()

    assert fixture.party == [fixture.player, fixture.companion]
    assert fixture.world.player_entity() == fixture.player
    assert fixture.world.positions.require(fixture.player).x == 2
    assert fixture.world.positions.require(fixture.companion).x == 3


def test_encounter_fixture_places_hostile_next_to_party_member() -> None:
    fixture = build_tiny_encounter()

    player_position = fixture.world.positions.require(fixture.player)
    companion_position = fixture.world.positions.require(fixture.companion)
    enemy_position = fixture.world.positions.require(fixture.enemy)

    assert companion_position.x == player_position.x + 1
    assert enemy_position.x == companion_position.x + 1
    assert enemy_position.y == companion_position.y
    assert fixture.world.factions.require(fixture.enemy).value != fixture.world.factions.require(
        fixture.player
    ).value


def test_action_resolution_helper_moves_and_applies_effects_without_app() -> None:
    fixture = build_tiny_party_world()
    effects = resolve_action(MoveAttempt(fixture.player, 0, 1), fixture.world)

    assert effects == [MoveEntity(fixture.player, 2, 3)]

    apply_world_effects(fixture.world, effects)

    assert fixture.world.positions.require(fixture.player).y == 3


def test_action_resolution_helper_resolves_deterministic_attack() -> None:
    fixture = build_tiny_encounter(enemy_hit_points=1)
    effects = resolve_action(
        MoveAttempt(fixture.companion, 1, 0),
        fixture.world,
        rng=SequenceRng([20, 1]),
    )

    assert any(
        isinstance(effect, DamageEntity) and effect.entity == fixture.enemy
        for effect in effects
    )
    assert KillEntity(fixture.enemy) in effects


def test_action_resolution_helper_reports_blocked_movement() -> None:
    fixture = build_tiny_party_world()
    effects = resolve_action(MoveAttempt(fixture.player, -2, 0), fixture.world)

    assert not any(isinstance(effect, MoveEntity) for effect in effects)
    assert fixture.world.positions.require(fixture.player).x == 2


def test_apply_world_effects_rejects_unsupported_effects() -> None:
    fixture = build_tiny_party_world()

    try:
        apply_world_effects(fixture.world, [SetMode(NormalMode())])
    except AssertionError as exc:
        assert "Unsupported test effect" in str(exc)
    else:
        raise AssertionError("Expected unsupported effects to fail loudly.")


def test_sequence_rng_copies_input_values() -> None:
    values = [7]
    rng = SequenceRng(values)

    assert rng.randint(1, 20) == 7
    assert values == [7]


def test_tiny_world_support_import_does_not_load_terminal_rendering() -> None:
    code = """
import sys
import tests.support.tiny_world

forbidden = {
    "curses",
    "src.app",
    "src.systems.render_system",
    "src.ui.screen",
}
loaded = sorted(forbidden.intersection(sys.modules))
if loaded:
    raise SystemExit("loaded forbidden terminal modules: " + ", ".join(loaded))
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
