"""Best-effort world serialization for the M33 `dump` debug command.

This is intentionally a separate module: it is not the future save/load
schema (#M16). It writes whatever is currently in the world to a JSON file
so a human dev or an agent playtester can inspect runtime state. Dev-only
state (GodMode component) is included so dev sessions are debuggable, but
this file is not a save format — fields and shape may change freely.

The serializer walks every ``ComponentStore`` on ``World``, treats each
entry as ``{int(entity_id): <component-as-dict>}``, and falls back to
``repr()`` for any value it can't directly serialize. This guarantees the
dump never crashes the running game on novel component types.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

from src.core.world import World


def world_to_dict(world: World) -> dict[str, Any]:
    """Return a plain-dict snapshot of ``world``.

    Tiles are serialized by glyph + kind only. Component stores are
    serialized as ``{entity_id: component_dict}`` maps keyed by store name.
    """
    component_stores: dict[str, dict[int, Any]] = {}
    for name, store in _iter_component_stores(world):
        component_stores[name] = {
            int(entity): _to_jsonable(component)
            for entity, component in store.values.items()
        }
    return {
        "width": world.width,
        "height": world.height,
        "next_entity_id": world.next_entity_id,
        "tiles": [
            [{"glyph": tile.glyph, "kind": tile.kind.value} for tile in row]
            for row in world.tiles
        ],
        "components": component_stores,
    }


def dump_world(world: World, path: str) -> None:
    """Write a JSON snapshot of ``world`` to ``path``."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(world_to_dict(world), handle, indent=2, default=_to_jsonable)


def _iter_component_stores(world: World):
    for name in (
        "positions",
        "presentations",
        "blockers",
        "player_controlled",
        "names",
        "characters",
        "creatures",
        "ai",
        "combat_stats",
        "weapons",
        "armor",
        "inventories",
        "equipment",
        "shops",
        "factions",
        "doors",
        "locks",
        "traps",
        "containers",
        "corpses",
        "loot_drops",
        "god_modes",
        "conditions",
    ):
        store = getattr(world, name, None)
        if store is not None:
            yield name, store


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            key: _to_jsonable(sub_value)
            for key, sub_value in asdict(value).items()
        }
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # Enums and other unknown types -> their value or repr.
    enum_value = getattr(value, "value", None)
    if enum_value is not None and isinstance(enum_value, (str, int, float, bool)):
        return enum_value
    return repr(value)
