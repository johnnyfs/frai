"""Developer/agent debug commands (M33).

The debug system is a thin command parser that turns text commands into
typed `Effect` lists. Commands route through the standard `EffectApplier`
so they never bypass invariants — `dump` is the only command that touches
the filesystem directly, and it does so for the explicit purpose of
producing a snapshot.

Gate
----
`is_dev_mode()` returns True only when the `FRAI_DEV` environment variable
is set to a truthy value (`1`, `true`, `yes`, case-insensitive). When
False, `run_debug_command` returns a refusal effect list and never mutates
state. The flag is checked per-call so tests can toggle it via
`monkeypatch.setenv`.

Spawn catalog
-------------
`DEBUG_SPAWN_CATALOG` maps a kind string (`"kobold"`, `"goblin"`,
`"chest"`, `"gold_pile"`) to a tiny spawner function. The `SpawnEntity`
effect names a kind; the applier looks it up here. Add new kinds in one
place.

Stubs
-----
Some commands (`reveal`, `quest`) depend on milestones that have not
landed yet (M19 vision/memory, quest content). Those return an
`EmitMessage` placeholder so the surface is stable now and the wiring
is one line when the dependency lands.

Hosting
-------
`run_debug_command` takes an `App`-shaped host so it can find the active
player, the world, and the message stream. The `host` parameter is
deliberately typed as `Any` to avoid an import cycle with `src.app`.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from typing import Any, Callable

from src.core.combat import weapon_for_name
from src.core.components import (
    AI,
    AIBehaviorType,
    BlocksMovement,
    CombatStats,
    Container,
    Creature,
    Faction,
    Inventory,
    Name,
    Position,
    Presentation,
)
from src.core.dump import dump_world
from src.core.effects import (
    Effect,
    EmitMessage,
    GrantGold,
    GrantItem,
    GrantXP,
    MoveEntity,
    SetGodMode,
    SpawnEntity,
)
from src.core.entity import EntityId
from src.core.factions import FactionId
from src.core.items import ITEMS
from src.core.world import World


# ---------------------------------------------------------------------------
# Dev-mode gate
# ---------------------------------------------------------------------------

_DEV_ENV_VAR = "FRAI_DEV"
_TRUTHY = {"1", "true", "yes", "on"}


def is_dev_mode() -> bool:
    """True iff the FRAI_DEV env var is set to a truthy value.

    The flag is read each call so tests using `monkeypatch.setenv` see the
    change without needing to reimport modules.
    """
    value = os.environ.get(_DEV_ENV_VAR, "")
    return value.strip().lower() in _TRUTHY


# ---------------------------------------------------------------------------
# Spawn catalog
# ---------------------------------------------------------------------------


def _spawn_kobold(world: World, x: int, y: int) -> EntityId:
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation("k"))
    world.names.add(entity, Name("kobold"))
    world.factions.add(entity, Faction(FactionId.DUNGEON.value))
    world.blockers.add(entity, BlocksMovement("occupied"))
    world.combat_stats.add(
        entity,
        CombatStats(
            armor_class=12,
            hit_points=5,
            max_hit_points=5,
            strength=8,
            dexterity=14,
            constitution=10,
        ),
    )
    world.weapons.add(entity, weapon_for_name("dagger"))
    world.creatures.add(entity, Creature(kind="kobold", attack_verb="stabs"))
    world.ai.add(entity, AI(behavior=AIBehaviorType.CHASE))
    return entity


def _spawn_goblin(world: World, x: int, y: int) -> EntityId:
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation("g"))
    world.names.add(entity, Name("goblin"))
    world.factions.add(entity, Faction(FactionId.DUNGEON.value))
    world.blockers.add(entity, BlocksMovement("occupied"))
    world.combat_stats.add(
        entity,
        CombatStats(
            armor_class=13,
            hit_points=7,
            max_hit_points=7,
            strength=10,
            dexterity=14,
            constitution=10,
        ),
    )
    world.weapons.add(entity, weapon_for_name("shortsword"))
    world.creatures.add(entity, Creature(kind="goblin", attack_verb="slashes"))
    world.ai.add(entity, AI(behavior=AIBehaviorType.CHASE))
    return entity


def _spawn_chest(world: World, x: int, y: int) -> EntityId:
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation("="))
    world.names.add(entity, Name("chest"))
    world.containers.add(entity, Container(is_open=False))
    return entity


def _spawn_gold_pile(world: World, x: int, y: int) -> EntityId:
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation("$"))
    world.names.add(entity, Name("gold pile"))
    world.inventories.add(entity, Inventory(gold=25))
    return entity


DEBUG_SPAWN_CATALOG: dict[str, Callable[[World, int, int], EntityId]] = {
    "kobold": _spawn_kobold,
    "goblin": _spawn_goblin,
    "chest": _spawn_chest,
    "gold_pile": _spawn_gold_pile,
}


# ---------------------------------------------------------------------------
# Command parsing and dispatch
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DebugCommandError(Exception):
    """Raised by individual handlers to bubble a user-facing error message."""

    message: str

    def __str__(self) -> str:  # pragma: no cover — trivial
        return self.message


def run_debug_command(command: str, host: Any) -> list[Effect]:
    """Parse and resolve a single debug command line.

    Returns a list of effects suitable to pass to ``host.apply_effects``.
    When dev mode is off, returns a single refusal `EmitMessage` and no
    state-modifying effects.

    Unknown commands and parse errors produce a single `EmitMessage` so the
    caller always gets observable feedback. Successful commands always
    append a confirmation `EmitMessage` so the playtest harness can grep
    the message stream for results.
    """
    if not is_dev_mode():
        return [EmitMessage("Debug commands disabled (set FRAI_DEV=1).")]

    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return [EmitMessage(f"Debug parse error: {exc}")]
    if not tokens:
        return [EmitMessage("Empty debug command.")]

    head, *rest = tokens
    handler = _COMMAND_HANDLERS.get(head)
    if handler is None:
        return [EmitMessage(f"Unknown debug command: {head}")]
    try:
        return handler(host, rest)
    except DebugCommandError as exc:
        return [EmitMessage(exc.message)]


def _cmd_tp(host: Any, args: list[str]) -> list[Effect]:
    if len(args) != 2:
        raise DebugCommandError("Usage: tp <x> <y>")
    try:
        x, y = int(args[0]), int(args[1])
    except ValueError as exc:
        raise DebugCommandError(f"tp: invalid coordinate ({exc})") from exc
    return [MoveEntity(host.player, x, y), EmitMessage(f"Teleported to ({x}, {y}).")]


def _cmd_reveal(host: Any, args: list[str]) -> list[Effect]:
    if args:
        raise DebugCommandError("Usage: reveal")
    # M19 vision/memory has not landed yet — this is a documented stub so the
    # command surface is stable. When `Visibility`/`Memory` lands, replace
    # this with a `RevealMap` effect.
    return [EmitMessage("reveal: vision/memory system pending (M19).")]


def _cmd_spawn(host: Any, args: list[str]) -> list[Effect]:
    if len(args) < 1:
        raise DebugCommandError("Usage: spawn <kind> [<x> <y>]")
    kind = args[0]
    if kind not in DEBUG_SPAWN_CATALOG:
        raise DebugCommandError(f"spawn: unknown kind '{kind}'.")
    if len(args) == 1:
        position = host.world.positions.require(host.player)
        x, y = _adjacent_free_tile(host.world, position.x, position.y)
    elif len(args) == 3:
        try:
            x, y = int(args[1]), int(args[2])
        except ValueError as exc:
            raise DebugCommandError(f"spawn: invalid coordinate ({exc})") from exc
    else:
        raise DebugCommandError("Usage: spawn <kind> [<x> <y>]")
    return [SpawnEntity(kind, x, y), EmitMessage(f"Spawned {kind} at ({x}, {y}).")]


def _cmd_grant(host: Any, args: list[str]) -> list[Effect]:
    if len(args) < 2:
        raise DebugCommandError("Usage: grant <xp|gold|item> <value>")
    sub, *rest = args
    if sub == "xp":
        # Grant XP to every party member (M25). We mirror quest-reward
        # semantics here rather than the kill-XP split: a debug grant
        # should be predictable and useful for testing leveling on the
        # whole party, not divided by member count. Falls back to the
        # active player when no party is attached (e.g. tiny test hosts).
        if len(rest) != 1:
            raise DebugCommandError("Usage: grant xp <n>")
        try:
            amount = int(rest[0])
        except ValueError as exc:
            raise DebugCommandError(f"grant xp: invalid amount ({exc})") from exc
        recipients = _xp_recipients(host)
        effects: list[Effect] = [GrantXP(entity, amount) for entity in recipients]
        effects.append(EmitMessage(f"Granted {amount} XP to {len(recipients)} PC(s)."))
        return effects
    if sub == "gold":
        if len(rest) != 1:
            raise DebugCommandError("Usage: grant gold <n>")
        try:
            amount = int(rest[0])
        except ValueError as exc:
            raise DebugCommandError(f"grant gold: invalid amount ({exc})") from exc
        return [
            GrantGold(host.player, amount),
            EmitMessage(f"Granted {amount} gold."),
        ]
    if sub == "item":
        if len(rest) not in (1, 2):
            raise DebugCommandError("Usage: grant item <item_id> [<quantity>]")
        item_id = rest[0]
        if item_id not in ITEMS:
            raise DebugCommandError(f"grant item: unknown item '{item_id}'.")
        quantity = 1
        if len(rest) == 2:
            try:
                quantity = int(rest[1])
            except ValueError as exc:
                raise DebugCommandError(f"grant item: invalid quantity ({exc})") from exc
            if quantity <= 0:
                raise DebugCommandError("grant item: quantity must be positive.")
        return [
            GrantItem(host.player, item_id, quantity),
            EmitMessage(f"Granted {quantity}x {item_id}."),
        ]
    raise DebugCommandError(f"grant: unknown resource '{sub}'.")


def _cmd_god(host: Any, args: list[str]) -> list[Effect]:
    if len(args) != 1 or args[0] not in ("on", "off"):
        raise DebugCommandError("Usage: god <on|off>")
    enabled = args[0] == "on"
    return [
        SetGodMode(host.player, enabled),
        EmitMessage(f"God mode {'on' if enabled else 'off'}."),
    ]


def _cmd_quest(host: Any, args: list[str]) -> list[Effect]:
    if len(args) != 1:
        raise DebugCommandError("Usage: quest <milestone>")
    # Quest content (M14) does not exist yet. Documented stub.
    return [EmitMessage(f"quest jump '{args[0]}': not implemented (M14).")]


def _cmd_dump(host: Any, args: list[str]) -> list[Effect]:
    if len(args) > 1:
        raise DebugCommandError("Usage: dump [<path>]")
    path = args[0] if args else "world_state.json"
    dump_world(host.world, path)
    return [EmitMessage(f"World state written to {path}.")]


_COMMAND_HANDLERS: dict[str, Callable[[Any, list[str]], list[Effect]]] = {
    "tp": _cmd_tp,
    "reveal": _cmd_reveal,
    "spawn": _cmd_spawn,
    "grant": _cmd_grant,
    "god": _cmd_god,
    "quest": _cmd_quest,
    "dump": _cmd_dump,
}


def debug_command_names() -> list[str]:
    """Return the list of registered debug command names, for help rendering."""
    return sorted(_COMMAND_HANDLERS.keys())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _xp_recipients(host: Any) -> list[EntityId]:
    """Return the list of PCs that should receive a debug XP grant.

    Prefers ``host.party.members`` (the full PC roster) when available
    so a debug `grant xp` keeps every member in sync. Falls back to
    ``[host.player]`` when no party is attached — that path keeps the
    tiny-host tests working without forcing them to build a PartyState.
    """
    party = getattr(host, "party", None)
    members = getattr(party, "members", None) if party is not None else None
    if members:
        return list(members)
    return [host.player]


def _adjacent_free_tile(world: World, origin_x: int, origin_y: int) -> tuple[int, int]:
    """Find an open tile within radius 1-3 of (origin_x, origin_y).

    Returns the origin itself if nothing else is reachable; the caller is
    free to spawn there anyway (debug commands prefer 'works at all' over
    'pretty placement').
    """
    for radius in range(1, 4):
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if max(abs(dx), abs(dy)) != radius:
                    continue
                x, y = origin_x + dx, origin_y + dy
                tile = world.tile_at(x, y)
                if tile.blocks_movement:
                    continue
                if world.entities_at(x, y):
                    continue
                return x, y
    return origin_x, origin_y
