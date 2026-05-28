from collections.abc import Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from src.core.components import Presentation
from src.core.character_creation import (
    ABILITIES,
    can_advance,
    choices_for_step,
    class_by_name,
    key_for_choice,
    selected_for_step,
    step_title,
    total_attributes,
)
from src.core.config import MIN_TERMINAL_HEIGHT, MIN_TERMINAL_WIDTH, PLAYFIELD_WIDTH
from src.core.entity import EntityId
from src.core.items import require_item
from src.core.modes import CharacterCreationMode, GameMode, GameOverMode, InventoryMode, StartChoiceMode
from src.core.world import World
from src.systems.message_system import MORE_PROMPT, MessageState
from src.ui.layout import Layout
from src.ui.screen import Screen


@dataclass(frozen=True, slots=True)
class Glyph:
    char: str
    fg: int | None = None
    bg: int | None = None
    attrs: int = 0
    render_token: str | None = None
    color_token: str | None = None


@dataclass(frozen=True, slots=True)
class ColorProjection:
    fg: int | None = None
    bg: int | None = None
    attrs: int = 0


TERRAIN_COLOR_PROJECTIONS: Mapping[str, ColorProjection] = MappingProxyType(
    {
        "terrain.default": ColorProjection(fg=7),
        "terrain.stone.floor": ColorProjection(fg=7),
        "terrain.stone.passage": ColorProjection(fg=6),
        "terrain.stone.wall": ColorProjection(fg=8),
        "terrain.void": ColorProjection(fg=0),
        "terrain.grass": ColorProjection(fg=2),
        "terrain.road": ColorProjection(fg=3),
        "terrain.forest": ColorProjection(fg=2),
        "terrain.town.floor": ColorProjection(fg=7),
        "terrain.dungeon.floor": ColorProjection(fg=8),
        "terrain.water": ColorProjection(fg=4),
        "terrain.rubble": ColorProjection(fg=3),
    }
)


def presentation_for(observer: EntityId, world: World, x: int, y: int) -> Glyph:
    return _presentation_for(observer, world, x, y, ())


def render(
    screen: Screen,
    world: World,
    observer: EntityId,
    messages: MessageState,
    mode: GameMode,
    focus: EntityId | None = None,
    party: Sequence[EntityId] = (),
    movement_used: float = 0.0,
    movement_total: float = 30.0,
    major_mode: str = "explore",
) -> None:
    screen.clear()
    layout = Layout(width=screen.width, height=screen.height)
    if layout.is_too_small:
        warning = f"Terminal too small. Need at least {MIN_TERMINAL_WIDTH}x{MIN_TERMINAL_HEIGHT}."
        screen.print_line(0, warning)
        screen.refresh()
        return

    if isinstance(mode, StartChoiceMode):
        _render_start_choice(screen, layout)
        return

    if isinstance(mode, GameOverMode):
        _render_game_over(screen, layout)
        return

    if isinstance(mode, CharacterCreationMode):
        _render_character_creation(screen, layout, mode)
        return

    if isinstance(mode, InventoryMode):
        _render_inventory(screen, layout, world, observer)
        return

    screen.print_line(
        layout.message_y,
        _message_line(messages),
        layout.origin_x,
    )

    focus_entity = focus if focus is not None else observer
    viewport_x, viewport_y = _viewport_origin(
        world,
        layout,
        focus_entity,
    )
    for screen_y in range(layout.map_top, layout.map_bottom + 1):
        world_y = viewport_y + screen_y - layout.map_top
        for viewport_column in range(layout.playfield_width):
            world_x = viewport_x + viewport_column
            screen_x = layout.origin_x + viewport_column
            glyph = _presentation_for(observer, world, world_x, world_y, party)
            screen.draw_char(screen_x, screen_y, glyph.char)

    screen.print_line(
        layout.status_y,
        _status_line(world, observer, party, movement_used, movement_total, major_mode).ljust(
            layout.playfield_width
        ),
        layout.origin_x,
    )
    focus_position = _focus_screen_position(world, layout, focus_entity, viewport_x, viewport_y)
    if focus_position is not None:
        screen.move_cursor(*focus_position)
    screen.refresh()


def _line(screen: Screen, layout: Layout, row: int, text: str = "") -> None:
    screen.print_line(row, text[: layout.playfield_width].ljust(layout.playfield_width), layout.origin_x)


def _presentation_for(
    observer: EntityId,
    world: World,
    x: int,
    y: int,
    party: Sequence[EntityId],
) -> Glyph:
    for entity in world.entities_at(x, y):
        party_glyph = _party_glyph(entity, party)
        if party_glyph is not None:
            return Glyph(party_glyph)
        presentation: Presentation | None = world.presentations.get(entity)
        if presentation is not None:
            return Glyph(presentation.glyph)

    tile = world.tile_at(x, y)
    color = TERRAIN_COLOR_PROJECTIONS.get(
        tile.color_token,
        TERRAIN_COLOR_PROJECTIONS["terrain.default"],
    )
    return Glyph(
        tile.glyph,
        fg=color.fg,
        bg=color.bg,
        attrs=color.attrs,
        render_token=tile.render_token,
        color_token=tile.color_token,
    )


def _party_glyph(entity: EntityId, party: Sequence[EntityId]) -> str | None:
    for index, party_entity in enumerate(party):
        if entity == party_entity:
            return "@" if index == 0 else "#"
    return None


def _viewport_origin(world: World, layout: Layout, focus: EntityId) -> tuple[int, int]:
    position = world.positions.get(focus)
    if position is None:
        return 0, 0
    max_x = max(0, world.width - layout.playfield_width)
    max_y = max(0, world.height - layout.playfield_height)
    origin_x = min(max(0, position.x - layout.playfield_width // 2), max_x)
    origin_y = min(max(0, position.y - layout.playfield_height // 2), max_y)
    return origin_x, origin_y


def _focus_screen_position(
    world: World,
    layout: Layout,
    focus: EntityId,
    viewport_x: int,
    viewport_y: int,
) -> tuple[int, int] | None:
    position = world.positions.get(focus)
    if position is None:
        return None
    screen_x = layout.origin_x + position.x - viewport_x
    screen_y = layout.map_top + position.y - viewport_y
    if not (
        layout.origin_x <= screen_x < layout.origin_x + layout.playfield_width
        and layout.map_top <= screen_y <= layout.map_bottom
    ):
        return None
    return screen_x, screen_y


def _message_line(messages: MessageState) -> str:
    if messages.awaiting_more:
        suffix = f" {MORE_PROMPT}"
        return (messages.current[: max(0, PLAYFIELD_WIDTH - len(suffix))] + suffix).ljust(
            PLAYFIELD_WIDTH
        )
    return messages.current[:PLAYFIELD_WIDTH].ljust(PLAYFIELD_WIDTH)


def _status_line(
    world: World,
    observer: EntityId,
    party: Sequence[EntityId] = (),
    movement_used: float = 0.0,
    movement_total: float = 30.0,
    major_mode: str = "explore",
) -> str:
    mode_label = "Turn" if major_mode == "turn" else major_mode.capitalize()
    label = _status_label(world, observer, party)
    movement = (
        f"  Move {_format_feet(movement_used)}/{_format_feet(movement_total)}"
        if major_mode in ("battle", "turn")
        else ""
    )
    stats = world.combat_stats.get(observer)
    if stats is None:
        return f"{mode_label}  {label}  HP -/-  AC -{movement}"
    return (
        f"{mode_label}  {label}  HP {stats.hit_points}/{stats.max_hit_points}  "
        f"AC {stats.armor_class}{movement}"
    )


def _status_label(world: World, observer: EntityId, party: Sequence[EntityId]) -> str:
    character = world.characters.get(observer)
    class_name = f" {character.sheet.character_class}" if character is not None else ""
    for index, entity in enumerate(party):
        if entity == observer:
            if index == 0:
                return f"Player{class_name}"
            return f"Party Member {index}{class_name}"
    for index, entity in enumerate(world.controlled_entities()):
        if entity == observer:
            if index == 0:
                return f"Player{class_name}"
            return f"Party Member {index}{class_name}"
    return f"Entity {int(observer)}{class_name}"


def _format_feet(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _inventory_lines(world: World, entity: EntityId) -> list[str]:
    lines: list[str] = []
    inventory = world.inventories.get(entity)
    if inventory is not None:
        lines.append(f"Gold   - {inventory.gold}")
    armor = world.armor.get(entity)
    weapon = world.weapons.get(entity)
    if armor is not None and armor.name != "none":
        lines.append(f"Armor  - {armor.name} (worn)")
    if weapon is not None:
        lines.append(f"Weapon - {weapon.name} (in hand)")
    if inventory is not None and inventory.items:
        lines.append("Carried items")
        equipment = world.equipment.get(entity)
        equipped_ids = set()
        if equipment is not None:
            equipped_ids = {
                item_id
                for item_id in (equipment.weapon_item_id, equipment.armor_item_id)
                if item_id is not None
            }
        for stack in inventory.items:
            item = require_item(stack.item_id)
            suffix = " (equipped)" if stack.item_id in equipped_ids else ""
            quantity = f"{stack.quantity}x " if stack.quantity != 1 else ""
            lines.append(f"- {quantity}{item.name}{suffix}")
    return lines or ["Nothing."]


def _render_inventory(screen: Screen, layout: Layout, world: World, observer: EntityId) -> None:
    _line(screen, layout, layout.message_y, "Inventory")
    _line(screen, layout, layout.map_top + 1, "Items carried")
    _line(screen, layout, layout.map_top + 2, "-" * layout.playfield_width)
    for index, line in enumerate(_inventory_lines(world, observer)):
        _line(screen, layout, layout.map_top + 4 + index, line)
    _line(screen, layout, layout.status_y, "i/q/b close")
    screen.refresh()


def _render_start_choice(screen: Screen, layout: Layout) -> None:
    _line(screen, layout, layout.message_y, "Welcome")
    _line(screen, layout, layout.map_top + 3, "c - Create a character")
    _line(screen, layout, layout.map_top + 4, "y - YOLO")
    _line(screen, layout, layout.map_top + 5, "q - Quit")
    _line(screen, layout, layout.status_y, "single-key choice")
    screen.refresh()


def _render_game_over(screen: Screen, layout: Layout) -> None:
    _line(screen, layout, layout.message_y, "Game over")
    _line(screen, layout, layout.map_top + 3, "You die.")
    _line(screen, layout, layout.map_top + 5, "r - Restart")
    _line(screen, layout, layout.map_top + 6, "q - Quit")
    _line(screen, layout, layout.status_y, "single-key choice")
    screen.refresh()


def _render_character_creation(
    screen: Screen,
    layout: Layout,
    mode: CharacterCreationMode,
) -> None:
    state = mode.state
    choices = choices_for_step(state)
    selected = set(selected_for_step(state))
    character_class = class_by_name(state.character_class)

    _line(screen, layout, layout.message_y, "Character Creation")
    _line(screen, layout, layout.map_top, step_title(state))
    _line(screen, layout, layout.map_top + 1, "-" * layout.playfield_width)

    if state.step in ("race", "class", "specialization", "cantrips", "spells", "skills"):
        max_rows = 24
        start = 0
        if state.cursor >= max_rows:
            start = state.cursor - max_rows + 1
        visible = choices[start : start + max_rows]
        for offset, choice in enumerate(visible):
            mark = "*" if choice in selected else " "
            key = key_for_choice(state, choice)
            if state.step in ("cantrips", "spells", "skills"):
                _line(screen, layout, layout.map_top + 3 + offset, f"{key} - [{mark}] {choice}")
            else:
                _line(screen, layout, layout.map_top + 3 + offset, f"{key} - {choice}")

        if state.step in ("cantrips", "spells", "skills"):
            _line(
                screen,
                layout,
                layout.map_top + 29,
                "Press a listed key to toggle. Press y when the required picks are complete.",
            )
        else:
            _line(screen, layout, layout.map_top + 29, "Press a listed key to select. b backs up.")

    elif state.step == "attributes":
        totals = total_attributes(state)
        _line(screen, layout, layout.map_top + 3, "Ability scores are rolled as 4d6 drop lowest.")
        _line(screen, layout, layout.map_top + 4, "Press r to reroll, y to keep these scores, b to back up.")
        for index, ability in enumerate(ABILITIES):
            base = state.base_attributes[ability]
            total = totals[ability]
            delta = total - base
            bonus = f"{delta:+d}" if delta else "+0"
            _line(
                screen,
                layout,
                layout.map_top + 6 + index,
                f"{ability}: {base:2d} race {bonus:>3} => {total:2d}",
            )

    elif state.step == "confirm":
        _line(screen, layout, layout.map_top + 3, f"Race:  {state.race}")
        _line(screen, layout, layout.map_top + 4, f"Class: {state.character_class}")
        _line(screen, layout, layout.map_top + 5, f"{character_class.specialization_label if character_class else 'Specialization'}: {state.specialization}")
        _line(screen, layout, layout.map_top + 7, f"Cantrips: {', '.join(state.cantrips) if state.cantrips else '-'}")
        _line(screen, layout, layout.map_top + 8, f"Spells:   {', '.join(state.spells) if state.spells else '-'}")
        _line(screen, layout, layout.map_top + 9, f"Skills:   {', '.join(state.skills) if state.skills else '-'}")
        totals = total_attributes(state)
        _line(
            screen,
            layout,
            layout.map_top + 11,
            "Attributes: " + " ".join(f"{ability} {totals[ability]}" for ability in ABILITIES),
        )
        _line(screen, layout, layout.map_top + 13, "Press y to begin. b backs up.")

    _line(screen, layout, layout.map_top + 32, f"Selected: {state.race or '?'} / {state.character_class or '?'} / {state.specialization or '?'}")
    if state.step in ("cantrips", "spells", "skills") and not can_advance(state):
        _line(screen, layout, layout.map_top + 33, "Pick the required number before continuing.")
    if state.step in ("race", "class", "specialization"):
        status = "single-key choices  b back"
    elif state.step == "attributes":
        status = "r reroll  y keep scores  b back"
    else:
        status = "single-key choices  y confirm/continue  b back"
    _line(screen, layout, layout.status_y, status)
    screen.refresh()
