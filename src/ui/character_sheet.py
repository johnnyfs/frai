"""Character sheet modal projection.

Pure projection over ``world`` for a single entity. The renderer reads
:class:`CharacterSheetView` to draw the modal; observation.py reads the
same view to surface the selected fields to the agentic playtester.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.entity import EntityId
from src.core.items import require_item
from src.core.spells import SPELL_CATALOG
from src.core.world import World


@dataclass(frozen=True, slots=True)
class SpellSlotLine:
    level: int
    remaining: int
    maximum: int


@dataclass(frozen=True, slots=True)
class InventoryLine:
    item_id: str
    name: str
    quantity: int
    equipped: bool


@dataclass(frozen=True, slots=True)
class CharacterSheetView:
    """All fields the character-sheet screen needs.

    Built once when the modal opens; the renderer reads from this rather
    than re-querying world stores so the projection logic lives in one
    place. ``None`` fields mean the entity has nothing to show for that
    section (e.g. a non-caster has no spell list or slots).
    """

    entity: EntityId
    name: str
    race: str
    character_class: str
    specialization: str
    level: int
    xp: int
    xp_to_next: int | None
    hp: int
    max_hp: int
    armor_class: int
    abilities: dict[str, int]
    conditions: tuple[str, ...]
    weapon: str | None
    armor: str | None
    inventory: tuple[InventoryLine, ...]
    spells: tuple[str, ...]
    spell_slots: tuple[SpellSlotLine, ...]
    faction: str | None
    position: tuple[int, int]


@dataclass(slots=True)
class CharacterSheetState:
    """Transient state for the open character sheet modal."""

    view: CharacterSheetView
    previous_mode: str | None = None


def build_view(world: World, entity: EntityId) -> CharacterSheetView | None:
    """Project ``entity``'s sheet from world component stores.

    Returns ``None`` when the entity has no position (e.g. was removed
    from the world); callers should refuse to open the modal in that
    case.
    """

    position = world.positions.get(entity)
    if position is None:
        return None

    name = world.name_for(entity)
    character = world.characters.get(entity)
    if character is not None:
        sheet = character.sheet
        race = sheet.race
        character_class = sheet.character_class
        specialization = sheet.specialization
        level = sheet.level
        abilities = dict(sheet.attributes)
    else:
        race = "?"
        character_class = "?"
        specialization = "?"
        level = 1
        abilities = {}

    stats = world.combat_stats.get(entity)
    if stats is not None:
        hp = stats.hit_points
        max_hp = stats.max_hit_points
        armor_class = stats.armor_class
    else:
        hp = 0
        max_hp = 0
        armor_class = 0

    xp_component = world.experience_points.get(entity)
    if xp_component is not None:
        xp = xp_component.value
    else:
        xp = 0
    xp_to_next = _xp_to_next(level)

    conditions = _condition_kinds(world, entity)
    weapon_name, armor_name, equipped_ids = _equipment_summary(world, entity)
    inventory = _inventory_lines(world, entity, equipped_ids)
    spells = _spell_names(world, entity)
    spell_slots = _spell_slot_lines(world, entity)
    faction_component = world.factions.get(entity)
    faction = faction_component.value if faction_component is not None else None

    return CharacterSheetView(
        entity=entity,
        name=name,
        race=race,
        character_class=character_class,
        specialization=specialization,
        level=level,
        xp=xp,
        xp_to_next=xp_to_next,
        hp=hp,
        max_hp=max_hp,
        armor_class=armor_class,
        abilities=abilities,
        conditions=conditions,
        weapon=weapon_name,
        armor=armor_name,
        inventory=inventory,
        spells=spells,
        spell_slots=spell_slots,
        faction=faction,
        position=(position.x, position.y),
    )


def _xp_to_next(level: int) -> int | None:
    from src.core.leveling import next_threshold

    return next_threshold(level)


def _condition_kinds(world: World, entity: EntityId) -> tuple[str, ...]:
    store = world.conditions.get(entity)
    if store is None:
        return ()
    return tuple(condition.kind.value for condition in store.conditions)


def _equipment_summary(
    world: World, entity: EntityId
) -> tuple[str | None, str | None, set[str]]:
    weapon = world.weapons.get(entity)
    armor = world.armor.get(entity)
    equipment = world.equipment.get(entity)
    equipped: set[str] = set()
    if equipment is not None:
        for item_id in (equipment.weapon_item_id, equipment.armor_item_id):
            if item_id is not None:
                equipped.add(item_id)
    weapon_name = weapon.name if weapon is not None else None
    armor_name = None
    if armor is not None and armor.name != "none":
        armor_name = armor.name
    return weapon_name, armor_name, equipped


def _inventory_lines(
    world: World, entity: EntityId, equipped_ids: set[str]
) -> tuple[InventoryLine, ...]:
    inventory = world.inventories.get(entity)
    if inventory is None or not inventory.items:
        return ()
    out: list[InventoryLine] = []
    for stack in inventory.items:
        try:
            item = require_item(stack.item_id)
            name = item.name
        except Exception:
            name = stack.item_id
        out.append(
            InventoryLine(
                item_id=stack.item_id,
                name=name,
                quantity=stack.quantity,
                equipped=stack.item_id in equipped_ids,
            )
        )
    return tuple(out)


def _spell_names(world: World, entity: EntityId) -> tuple[str, ...]:
    spell_list = world.spell_lists.get(entity)
    if spell_list is None:
        return ()
    out: list[str] = []
    for spell_id in spell_list.known:
        spell = SPELL_CATALOG.get(spell_id)
        out.append(spell.name if spell is not None else spell_id)
    return tuple(out)


def _spell_slot_lines(world: World, entity: EntityId) -> tuple[SpellSlotLine, ...]:
    slots = world.spell_slots.get(entity)
    if slots is None:
        return ()
    levels = sorted(set(slots.slots_by_level.keys()) | set(slots.max_by_level.keys()))
    return tuple(
        SpellSlotLine(
            level=level,
            remaining=int(slots.slots_by_level.get(level, 0)),
            maximum=int(slots.max_by_level.get(level, 0)),
        )
        for level in levels
    )


def render_lines(view: CharacterSheetView) -> list[str]:
    """Format the view as a list of lines for the renderer / observation.

    Kept as a single helper so the same projection drives both the
    curses renderer and the snapshot a playtester reads — that means a
    test against the lines doubles as a smoke test for the on-screen
    rendering.
    """

    lines: list[str] = []
    lines.append(f"{view.name} - {view.race} {view.character_class} ({view.specialization})")
    lines.append(f"Level {view.level}  XP {view.xp}" + (f" / {view.xp_to_next}" if view.xp_to_next is not None else ""))
    lines.append(f"HP {view.hp}/{view.max_hp}  AC {view.armor_class}")
    if view.abilities:
        abilities_line = "  ".join(f"{k} {v}" for k, v in view.abilities.items())
        lines.append(f"Abilities: {abilities_line}")
    if view.conditions:
        lines.append("Conditions: " + ", ".join(view.conditions))
    else:
        lines.append("Conditions: (none)")
    lines.append(f"Weapon: {view.weapon or '(none)'}")
    lines.append(f"Armor: {view.armor or '(none)'}")
    if view.inventory:
        lines.append("Inventory:")
        for entry in view.inventory:
            quantity = f"{entry.quantity}x " if entry.quantity != 1 else ""
            suffix = " (equipped)" if entry.equipped else ""
            lines.append(f"  - {quantity}{entry.name}{suffix}")
    else:
        lines.append("Inventory: (empty)")
    if view.spells:
        lines.append("Spells: " + ", ".join(view.spells))
    else:
        lines.append("Spells: (none)")
    if view.spell_slots:
        slot_summary = ", ".join(
            f"L{line.level}: {line.remaining}/{line.maximum}" for line in view.spell_slots
        )
        lines.append(f"Slots: {slot_summary}")
    lines.append(f"Faction: {view.faction or '(none)'}")
    lines.append(f"Position: ({view.position[0]}, {view.position[1]})")
    return lines
