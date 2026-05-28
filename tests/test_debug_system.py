"""Tests for the M33 debug/dev tools.

The debug system is gated by the `FRAI_DEV` env var; every test that wants
debug commands to actually run uses `monkeypatch.setenv("FRAI_DEV", "1")`.

Tests prefer direct `run_debug_command` calls over `App.run_debug_command`
where they exercise pure parsing/effect-emission logic; the few integration
tests that touch App state (god mode, dump, tp end-to-end) go through the
App entry point.
"""

from __future__ import annotations

import json
import os

import pytest

from src.app import create_app
from src.core.components import GodMode
from src.core.effects import (
    EmitMessage,
    GrantGold,
    GrantItem,
    MoveEntity,
    SetGodMode,
    SpawnEntity,
)
from src.core.modes import UIMode
from src.systems.debug_system import (
    DEBUG_SPAWN_CATALOG,
    debug_command_names,
    is_dev_mode,
    run_debug_command,
)
from tests.support.tiny_world import build_tiny_party_world


@pytest.fixture(autouse=True)
def _clear_dev_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test to dev mode OFF; tests opt in explicitly."""
    monkeypatch.delenv("FRAI_DEV", raising=False)


class _FakeHost:
    """Minimal host shaped like `App` for direct-call tests."""

    def __init__(self, world, player) -> None:
        self.world = world
        self.player = player


def test_is_dev_mode_respects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert is_dev_mode() is False
    monkeypatch.setenv("FRAI_DEV", "1")
    assert is_dev_mode() is True
    monkeypatch.setenv("FRAI_DEV", "yes")
    assert is_dev_mode() is True
    monkeypatch.setenv("FRAI_DEV", "0")
    assert is_dev_mode() is False
    monkeypatch.setenv("FRAI_DEV", "")
    assert is_dev_mode() is False


def test_dev_off_rejects_all_commands() -> None:
    fixture = build_tiny_party_world()
    host = _FakeHost(fixture.world, fixture.player)
    for command in ("tp 1 1", "spawn kobold", "god on", "grant gold 5", "dump"):
        effects = run_debug_command(command, host)
        assert effects == [EmitMessage("Debug commands disabled (set FRAI_DEV=1).")]


def test_tp_emits_move_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRAI_DEV", "1")
    fixture = build_tiny_party_world()
    host = _FakeHost(fixture.world, fixture.player)
    effects = run_debug_command("tp 4 3", host)
    assert any(
        isinstance(effect, MoveEntity)
        and effect.entity == fixture.player
        and effect.x == 4
        and effect.y == 3
        for effect in effects
    )


def test_tp_via_app_moves_player(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRAI_DEV", "1")
    app = create_app()
    app.ui_mode = UIMode.play
    app.run_debug_command("tp 10 12")
    position = app.world.positions.require(app.player)
    assert (position.x, position.y) == (10, 12)


def test_spawn_kobold_creates_hostile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRAI_DEV", "1")
    app = create_app()
    app.ui_mode = UIMode.play
    # Spawned dungeon monsters carry FactionId.DUNGEON (M28); count those
    # rather than the legacy "enemy" string.
    from src.core.factions import FactionId

    dungeon_value = FactionId.DUNGEON.value
    enemy_count_before = sum(
        1 for faction in app.world.factions.values.values() if faction.value == dungeon_value
    )
    app.run_debug_command("spawn kobold")
    enemy_count_after = sum(
        1 for faction in app.world.factions.values.values() if faction.value == dungeon_value
    )
    assert enemy_count_after == enemy_count_before + 1
    new_kobolds = [
        entity
        for entity, name in app.world.names.values.items()
        if name.value == "kobold"
    ]
    assert new_kobolds, "spawn kobold should create a named kobold entity"


def test_spawn_emits_typed_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRAI_DEV", "1")
    fixture = build_tiny_party_world()
    host = _FakeHost(fixture.world, fixture.player)
    effects = run_debug_command("spawn kobold 4 3", host)
    assert any(
        isinstance(effect, SpawnEntity) and effect.kind == "kobold"
        and effect.x == 4 and effect.y == 3
        for effect in effects
    )


def test_spawn_unknown_kind_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRAI_DEV", "1")
    fixture = build_tiny_party_world()
    host = _FakeHost(fixture.world, fixture.player)
    effects = run_debug_command("spawn dragon 1 1", host)
    assert any(
        isinstance(effect, EmitMessage) and "unknown kind" in effect.text
        for effect in effects
    )


def test_god_on_makes_player_immune_to_damage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRAI_DEV", "1")
    app = create_app()
    app.ui_mode = UIMode.play
    app.run_debug_command("god on")
    assert isinstance(app.world.god_modes.get(app.player), GodMode)
    stats = app.world.combat_stats.require(app.player)
    starting_hp = stats.hit_points
    # Apply a damage effect directly through the standard pipeline.
    from src.core.effects import DamageEntity
    app.apply_effects([DamageEntity(app.player, 9999)])
    assert app.world.combat_stats.require(app.player).hit_points == starting_hp

    app.run_debug_command("god off")
    assert app.world.god_modes.get(app.player) is None
    app.apply_effects([DamageEntity(app.player, 1)])
    assert app.world.combat_stats.require(app.player).hit_points == starting_hp - 1


def test_god_emits_set_god_mode_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRAI_DEV", "1")
    fixture = build_tiny_party_world()
    host = _FakeHost(fixture.world, fixture.player)
    effects_on = run_debug_command("god on", host)
    effects_off = run_debug_command("god off", host)
    assert any(
        isinstance(effect, SetGodMode) and effect.enabled is True
        for effect in effects_on
    )
    assert any(
        isinstance(effect, SetGodMode) and effect.enabled is False
        for effect in effects_off
    )


def test_grant_gold_adds_to_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRAI_DEV", "1")
    app = create_app()
    app.ui_mode = UIMode.play
    starting_gold = app.world.inventories.require(app.player).gold
    app.run_debug_command("grant gold 100")
    assert app.world.inventories.require(app.player).gold == starting_gold + 100


def test_grant_item_adds_to_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRAI_DEV", "1")
    app = create_app()
    app.ui_mode = UIMode.play
    from src.core.items import item_count

    starting = item_count(
        app.world.inventories.require(app.player), "consumable.healing_potion"
    )
    app.run_debug_command("grant item consumable.healing_potion 3")
    assert (
        item_count(
            app.world.inventories.require(app.player), "consumable.healing_potion"
        )
        == starting + 3
    )


def test_grant_xp_is_a_documented_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRAI_DEV", "1")
    fixture = build_tiny_party_world()
    host = _FakeHost(fixture.world, fixture.player)
    effects = run_debug_command("grant xp 500", host)
    assert any(
        isinstance(effect, EmitMessage) and "M25" in effect.text for effect in effects
    )
    # No GrantGold/GrantItem leakage from the xp stub.
    assert not any(isinstance(effect, (GrantGold, GrantItem)) for effect in effects)


def test_reveal_is_a_documented_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRAI_DEV", "1")
    fixture = build_tiny_party_world()
    host = _FakeHost(fixture.world, fixture.player)
    effects = run_debug_command("reveal", host)
    assert any(
        isinstance(effect, EmitMessage) and "M19" in effect.text for effect in effects
    )


def test_quest_is_a_documented_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRAI_DEV", "1")
    fixture = build_tiny_party_world()
    host = _FakeHost(fixture.world, fixture.player)
    effects = run_debug_command("quest tavern", host)
    assert any(
        isinstance(effect, EmitMessage) and "tavern" in effect.text
        for effect in effects
    )


def test_dump_writes_json_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    monkeypatch.setenv("FRAI_DEV", "1")
    app = create_app()
    app.ui_mode = UIMode.play
    target = os.path.join(str(tmp_path), "snapshot.json")
    app.run_debug_command(f"dump {target}")
    assert os.path.exists(target)
    with open(target, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    # The snapshot must contain the expected top-level structure.
    assert "width" in data and "height" in data and "components" in data
    assert "positions" in data["components"]
    # The player has a Position and that should appear in the snapshot.
    assert str(int(app.player)) in data["components"]["positions"]


def test_unknown_command_emits_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRAI_DEV", "1")
    fixture = build_tiny_party_world()
    host = _FakeHost(fixture.world, fixture.player)
    effects = run_debug_command("frobnicate", host)
    assert any(
        isinstance(effect, EmitMessage) and "Unknown" in effect.text
        for effect in effects
    )


def test_debug_command_names_lists_all_registered() -> None:
    names = debug_command_names()
    assert set(names) >= {"tp", "reveal", "spawn", "grant", "god", "quest", "dump"}


def test_spawn_catalog_kinds_present() -> None:
    assert set(DEBUG_SPAWN_CATALOG.keys()) >= {
        "kobold",
        "goblin",
        "chest",
        "gold_pile",
    }
