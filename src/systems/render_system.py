from dataclasses import dataclass

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
from src.core.config import MIN_TERMINAL_HEIGHT, MIN_TERMINAL_WIDTH
from src.core.entity import EntityId
from src.core.modes import CharacterCreationMode, GameMode
from src.core.world import World
from src.map.tiles import TileKind
from src.systems.message_system import MessageState
from src.ui.layout import Layout
from src.ui.screen import Screen


@dataclass(frozen=True, slots=True)
class Glyph:
    char: str
    fg: int | None = None
    bg: int | None = None
    attrs: int = 0


def presentation_for(observer: EntityId, world: World, x: int, y: int) -> Glyph:
    for entity in world.entities_at(x, y):
        presentation: Presentation | None = world.presentations.get(entity)
        if presentation is not None:
            return Glyph(presentation.glyph)

    tile = world.tile_at(x, y)
    if tile.kind in (TileKind.WALL, TileKind.FLOOR):
        return Glyph(tile.glyph)
    return Glyph(tile.glyph)


def render(
    screen: Screen,
    world: World,
    observer: EntityId,
    messages: MessageState,
    mode: GameMode,
) -> None:
    screen.clear()
    layout = Layout(width=screen.width, height=screen.height)
    if layout.is_too_small:
        warning = f"Terminal too small. Need at least {MIN_TERMINAL_WIDTH}x{MIN_TERMINAL_HEIGHT}."
        screen.print_line(0, warning)
        screen.refresh()
        return

    if isinstance(mode, CharacterCreationMode):
        _render_character_creation(screen, layout, mode)
        return

    screen.print_line(
        layout.message_y,
        messages.current[: layout.playfield_width].ljust(layout.playfield_width),
        layout.origin_x,
    )

    for screen_y in range(layout.map_top, layout.map_bottom + 1):
        world_y = screen_y - layout.map_top
        for world_x in range(layout.playfield_width):
            screen_x = layout.origin_x + world_x
            glyph = presentation_for(observer, world, world_x, world_y)
            screen.draw_char(screen_x, screen_y, glyph.char)

    screen.print_line(layout.status_y, "Status".ljust(layout.playfield_width), layout.origin_x)
    screen.refresh()


def _line(screen: Screen, layout: Layout, row: int, text: str = "") -> None:
    screen.print_line(row, text[: layout.playfield_width].ljust(layout.playfield_width), layout.origin_x)


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
