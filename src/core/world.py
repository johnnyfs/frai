from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any, Generic, TypeVar

from src.core.character_creation import CharacterSheet
from src.core.components import (
    AI,
    AIBehaviorType,
    Armor,
    BlocksMovement,
    Character,
    CombatStats,
    Container,
    Corpse,
    Creature,
    Door,
    Equipment,
    Faction,
    GodMode,
    Inventory,
    InventoryStack,
    Lock,
    LootDrop,
    Name,
    PlayerControlled,
    Position,
    Presentation,
    Shop,
    Trap,
    Weapon,
)
from src.core.entity import EntityId
from src.core.loot import DropTable, GoldDrop, ItemDrop
from src.core.time import Schedule, ScheduledEvent, WorldTime
from src.map.tiles import OUTSIDE, Tile, tile_from_token, tile_token

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class TerrainBlocker:
    x: int
    y: int
    reason: str


@dataclass(frozen=True, slots=True)
class EntityBlocker:
    entity: EntityId
    reason: str


BlockerRef = TerrainBlocker | EntityBlocker


@dataclass(slots=True)
class ComponentStore(Generic[T]):
    values: dict[EntityId, T]

    def add(self, entity: EntityId, component: T) -> None:
        self.values[entity] = component

    def get(self, entity: EntityId) -> T | None:
        return self.values.get(entity)

    def require(self, entity: EntityId) -> T:
        return self.values[entity]

    def has(self, entity: EntityId) -> bool:
        return entity in self.values


@dataclass(slots=True)
class World:
    width: int
    height: int
    tiles: list[list[Tile]]
    next_entity_id: int = 1
    positions: ComponentStore[Position] = field(default_factory=lambda: ComponentStore({}))
    presentations: ComponentStore[Presentation] = field(default_factory=lambda: ComponentStore({}))
    blockers: ComponentStore[BlocksMovement] = field(default_factory=lambda: ComponentStore({}))
    player_controlled: ComponentStore[PlayerControlled] = field(
        default_factory=lambda: ComponentStore({})
    )
    names: ComponentStore[Name] = field(default_factory=lambda: ComponentStore({}))
    characters: ComponentStore[Character] = field(default_factory=lambda: ComponentStore({}))
    creatures: ComponentStore[Creature] = field(default_factory=lambda: ComponentStore({}))
    ai: ComponentStore[AI] = field(default_factory=lambda: ComponentStore({}))
    combat_stats: ComponentStore[CombatStats] = field(default_factory=lambda: ComponentStore({}))
    weapons: ComponentStore[Weapon] = field(default_factory=lambda: ComponentStore({}))
    armor: ComponentStore[Armor] = field(default_factory=lambda: ComponentStore({}))
    inventories: ComponentStore[Inventory] = field(default_factory=lambda: ComponentStore({}))
    equipment: ComponentStore[Equipment] = field(default_factory=lambda: ComponentStore({}))
    shops: ComponentStore[Shop] = field(default_factory=lambda: ComponentStore({}))
    factions: ComponentStore[Faction] = field(default_factory=lambda: ComponentStore({}))
    doors: ComponentStore[Door] = field(default_factory=lambda: ComponentStore({}))
    locks: ComponentStore[Lock] = field(default_factory=lambda: ComponentStore({}))
    traps: ComponentStore[Trap] = field(default_factory=lambda: ComponentStore({}))
    containers: ComponentStore[Container] = field(default_factory=lambda: ComponentStore({}))
    corpses: ComponentStore[Corpse] = field(default_factory=lambda: ComponentStore({}))
    loot_drops: ComponentStore[LootDrop] = field(default_factory=lambda: ComponentStore({}))
    god_modes: ComponentStore[GodMode] = field(default_factory=lambda: ComponentStore({}))
    clock: WorldTime = field(default_factory=WorldTime)
    schedule: Schedule = field(default_factory=Schedule)

    def create_entity(self) -> EntityId:
        entity = EntityId(self.next_entity_id)
        self.next_entity_id += 1
        return entity

    def tile_at(self, x: int, y: int) -> Tile:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return OUTSIDE
        return self.tiles[y][x]

    def entities_at(self, x: int, y: int) -> list[EntityId]:
        return [
            entity
            for entity, position in self.positions.values.items()
            if position.x == x and position.y == y
        ]

    def blockers_at(self, x: int, y: int) -> list[BlockerRef]:
        blockers: list[BlockerRef] = []
        tile = self.tile_at(x, y)
        if tile.blocks_movement:
            blockers.append(TerrainBlocker(x=x, y=y, reason=tile.block_reason))
        for entity in self.entities_at(x, y):
            block = self.blockers.get(entity)
            if block is not None:
                blockers.append(EntityBlocker(entity=entity, reason=block.reason))
        return blockers

    def player_entity(self) -> EntityId:
        for entity in self.player_controlled.values:
            return entity
        raise LookupError("World has no player-controlled entity.")

    def controlled_entities(self) -> list[EntityId]:
        return list(self.player_controlled.values)

    def remove_entity(self, entity: EntityId) -> None:
        for _, store in self._component_stores():
            store.values.pop(entity, None)

    def _component_stores(self) -> list[tuple[str, ComponentStore[Any]]]:
        """Return the ordered list of ``(name, store)`` pairs.

        The order is fixed so save files are deterministic across runs;
        component types are added at the *end* to keep older saves
        loadable (load drops keys the current world doesn't know about
        rather than crashing).
        """
        return [
            ("positions", self.positions),
            ("presentations", self.presentations),
            ("blockers", self.blockers),
            ("player_controlled", self.player_controlled),
            ("names", self.names),
            ("characters", self.characters),
            ("creatures", self.creatures),
            ("ai", self.ai),
            ("combat_stats", self.combat_stats),
            ("weapons", self.weapons),
            ("armor", self.armor),
            ("inventories", self.inventories),
            ("equipment", self.equipment),
            ("shops", self.shops),
            ("factions", self.factions),
            ("doors", self.doors),
            ("locks", self.locks),
            ("traps", self.traps),
            ("containers", self.containers),
            ("corpses", self.corpses),
            ("loot_drops", self.loot_drops),
            ("god_modes", self.god_modes),
        ]

    def name_for(self, entity: EntityId) -> str:
        name = self.names.get(entity)
        return name.value if name is not None else f"entity {int(entity)}"

    # ------------------------------------------------------------------
    # Serialization (M16)
    # ------------------------------------------------------------------
    #
    # Save files are JSON-only. The ``World`` dict shape is:
    #
    #   {
    #     "width": int, "height": int,
    #     "next_entity_id": int,
    #     "tiles": [[<token-or-null>, ...], ...],   # row-major
    #     "components": {
    #         "positions": {"<entity_id>": {...}, ...},
    #         ...
    #     },
    #     "clock": {...}, "schedule": {...},
    #   }
    #
    # Component dicts are produced by :func:`_component_to_dict`. Each
    # component type either gets a dedicated branch in that helper (for
    # nested data like ``Character.sheet``) or falls back to
    # ``dataclasses.asdict``. ``GodMode`` is deliberately skipped — the
    # M33 debug marker should never leak into a player save.

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe representation of the world (M16)."""
        components_payload: dict[str, dict[str, Any]] = {}
        for name, store in self._component_stores():
            if name == "god_modes":
                # Debug-only marker; never persisted (see GodMode docs).
                continue
            entries: dict[str, Any] = {}
            for entity, component in store.values.items():
                entries[str(int(entity))] = _component_to_dict(component)
            components_payload[name] = entries

        return {
            "width": self.width,
            "height": self.height,
            "next_entity_id": self.next_entity_id,
            "tiles": [
                [tile_token(tile) for tile in row]
                for row in self.tiles
            ],
            "components": components_payload,
            "clock": self.clock.to_dict(),
            "schedule": self.schedule.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "World":
        """Rehydrate a World from a save dict (M16).

        Missing optional fields fall back to defaults so partial saves
        load. Unknown component-store names are silently dropped (the
        current build doesn't know what to do with them); missing
        component-store names rehydrate as empty stores. Both behaviours
        are deliberate to keep forward and backward compatibility
        forgiving.
        """
        width = int(data.get("width", 0))
        height = int(data.get("height", 0))
        tiles_raw = data.get("tiles")
        if tiles_raw is None:
            tiles = [[OUTSIDE for _ in range(width)] for _ in range(height)]
        else:
            tiles = [
                [tile_from_token(token) if token is not None else OUTSIDE
                 for token in row]
                for row in tiles_raw
            ]
        world = cls(
            width=width,
            height=height,
            tiles=tiles,
            next_entity_id=int(data.get("next_entity_id", 1)),
        )
        # Build a name -> store map so we can fan the payload back into
        # the right component stores without another mass switch.
        store_by_name: dict[str, ComponentStore[Any]] = {
            name: store for name, store in world._component_stores()
        }
        components_payload = data.get("components", {})
        for name, entries in components_payload.items():
            store = store_by_name.get(name)
            if store is None:
                # Unknown component type — silently drop. A reviewer or
                # migration helper can spot it by diffing the payload.
                continue
            for entity_key, payload in entries.items():
                entity = EntityId(int(entity_key))
                component = _component_from_dict(name, payload)
                if component is not None:
                    store.add(entity, component)

        clock_payload = data.get("clock")
        if clock_payload is not None:
            world.clock = WorldTime.from_dict(clock_payload)
        schedule_payload = data.get("schedule")
        if schedule_payload is not None:
            world.schedule = _schedule_from_dict(schedule_payload)
        return world


# ---------------------------------------------------------------------------
# Component (de)serialization
# ---------------------------------------------------------------------------


def _component_to_dict(component: Any) -> Any:
    """Turn a component instance into JSON-safe primitives."""
    if isinstance(component, Character):
        return {"sheet": _character_sheet_to_dict(component.sheet)}
    if isinstance(component, LootDrop):
        return {"table": _drop_table_to_dict(component.table)}
    if isinstance(component, AI):
        return {
            "behavior": component.behavior.value,
            "attack_range": component.attack_range,
            "preferred_range": component.preferred_range,
        }
    if isinstance(component, Inventory):
        return {
            "gold": component.gold,
            "items": [
                {"item_id": stack.item_id, "quantity": stack.quantity}
                for stack in component.items
            ],
        }
    if is_dataclass(component):
        return asdict(component)
    # Fallback: best-effort string conversion. Should never trip in
    # practice; the test suite asserts every known component round-trips.
    return repr(component)


def _component_from_dict(name: str, payload: Any) -> Any:
    """Reverse of :func:`_component_to_dict`, keyed by store name.

    Components are rebuilt by store name (not by payload shape) so a
    future component type with the same field names as an existing one
    can't accidentally collide.
    """
    if payload is None:
        return None
    if name == "positions":
        return Position(**_filtered(Position, payload))
    if name == "presentations":
        return Presentation(**_filtered(Presentation, payload))
    if name == "blockers":
        return BlocksMovement(**_filtered(BlocksMovement, payload))
    if name == "player_controlled":
        return PlayerControlled()
    if name == "names":
        return Name(**_filtered(Name, payload))
    if name == "characters":
        sheet_payload = payload.get("sheet", {})
        return Character(sheet=_character_sheet_from_dict(sheet_payload))
    if name == "creatures":
        return Creature(**_filtered(Creature, payload))
    if name == "ai":
        return AI(
            behavior=AIBehaviorType(payload.get("behavior", AIBehaviorType.CHASE.value)),
            attack_range=int(payload.get("attack_range", 1)),
            preferred_range=int(payload.get("preferred_range", 3)),
        )
    if name == "combat_stats":
        return CombatStats(**_filtered(CombatStats, payload))
    if name == "weapons":
        return Weapon(**_filtered(Weapon, payload))
    if name == "armor":
        return Armor(**_filtered(Armor, payload))
    if name == "inventories":
        stacks = [
            InventoryStack(
                item_id=str(stack["item_id"]),
                quantity=int(stack.get("quantity", 1)),
            )
            for stack in payload.get("items", [])
        ]
        return Inventory(gold=int(payload.get("gold", 0)), items=stacks)
    if name == "equipment":
        return Equipment(**_filtered(Equipment, payload))
    if name == "shops":
        return Shop(**_filtered(Shop, payload))
    if name == "factions":
        return Faction(**_filtered(Faction, payload))
    if name == "doors":
        return Door(**_filtered(Door, payload))
    if name == "locks":
        return Lock(**_filtered(Lock, payload))
    if name == "traps":
        return Trap(**_filtered(Trap, payload))
    if name == "containers":
        return Container(**_filtered(Container, payload))
    if name == "corpses":
        return Corpse(**_filtered(Corpse, payload))
    if name == "loot_drops":
        return LootDrop(table=_drop_table_from_dict(payload.get("table", {})))
    if name == "god_modes":
        # Never rebuilt — see GodMode docs.
        return None
    return None


def _filtered(component_type: type, payload: dict[str, Any]) -> dict[str, Any]:
    """Pick keys from ``payload`` that match ``component_type``'s fields.

    Defends against forward-compatible saves that may carry extra keys
    or older saves that omit some keys — the dataclass defaults fill in
    the gaps either way.
    """
    valid = {f.name for f in fields(component_type)}
    return {key: value for key, value in payload.items() if key in valid}


def _character_sheet_to_dict(sheet: CharacterSheet) -> dict[str, Any]:
    return {
        "race": sheet.race,
        "character_class": sheet.character_class,
        "specialization": sheet.specialization,
        "base_attributes": dict(sheet.base_attributes),
        "attributes": dict(sheet.attributes),
        "cantrips": list(sheet.cantrips),
        "spells": list(sheet.spells),
        "skills": list(sheet.skills),
        "level": sheet.level,
    }


def _character_sheet_from_dict(payload: dict[str, Any]) -> CharacterSheet:
    return CharacterSheet(
        race=str(payload.get("race", "")),
        character_class=str(payload.get("character_class", "")),
        specialization=str(payload.get("specialization", "")),
        base_attributes=dict(payload.get("base_attributes", {})),
        attributes=dict(payload.get("attributes", {})),
        cantrips=tuple(payload.get("cantrips", ())),
        spells=tuple(payload.get("spells", ())),
        skills=tuple(payload.get("skills", ())),
        level=int(payload.get("level", 1)),
    )


def _drop_table_to_dict(table: DropTable) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for entry in table.entries:
        if isinstance(entry, GoldDrop):
            entries.append({
                "kind": "gold",
                "amount_min": entry.amount_min,
                "amount_max": entry.amount_max,
            })
        else:
            entries.append({
                "kind": "item",
                "item_id": entry.item_id,
                "probability": entry.probability,
                "quantity_min": entry.quantity_min,
                "quantity_max": entry.quantity_max,
            })
    return {"entries": entries}


def _drop_table_from_dict(payload: dict[str, Any]) -> DropTable:
    rebuilt: list[Any] = []
    for entry in payload.get("entries", []):
        kind = entry.get("kind")
        if kind == "gold":
            rebuilt.append(GoldDrop(
                amount_min=int(entry.get("amount_min", 0)),
                amount_max=int(entry.get("amount_max", 0)),
            ))
        elif kind == "item":
            rebuilt.append(ItemDrop(
                item_id=str(entry["item_id"]),
                probability=float(entry.get("probability", 1.0)),
                quantity_min=int(entry.get("quantity_min", 1)),
                quantity_max=int(entry.get("quantity_max", 1)),
            ))
    return DropTable(entries=tuple(rebuilt))


def _schedule_from_dict(payload: dict[str, Any]) -> Schedule:
    """Rebuild a Schedule from its dict shape.

    Today the schedule's ``to_dict`` records only the event ``kind`` —
    the per-subclass payload is dropped because no caller currently
    subclasses ``ScheduledEvent`` with non-default fields. When the
    ``ScheduledEvent`` subclass registry lands (planned alongside richer
    scheduled effects in M24), this helper grows a dispatch table keyed
    on ``kind``. Until then we round-trip ``(due_at, kind)`` pairs as
    plain ``ScheduledEvent`` instances, which is faithful for the
    no-subclass case and a TODO for richer events.
    """
    schedule = Schedule()
    for entry in payload.get("entries", []):
        schedule.schedule(
            due_at=int(entry.get("due_at", 0)),
            event=ScheduledEvent(kind=str(entry.get("kind", ""))),
        )
    return schedule
