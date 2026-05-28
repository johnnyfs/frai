from collections.abc import Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from src.core.components import Presentation
from src.core.dialogue import DialogueState
from src.ui.character_sheet import CharacterSheetState, render_lines as render_sheet_lines
from src.ui.help import HelpState, wrap_body
from src.ui.roster import RosterState, roster_line
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
from src.core.character_creation import CharacterCreationState
from src.core.config import MIN_TERMINAL_HEIGHT, MIN_TERMINAL_WIDTH, PLAYFIELD_WIDTH
from src.core.entity import EntityId
from src.core.items import require_item
from src.core.modes import PlayMode, UIMode
from src.core.vision import PartyMemory, VisibilityState
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
    ui_mode: UIMode,
    focus: EntityId | None = None,
    party: Sequence[EntityId] = (),
    movement_used: float = 0.0,
    movement_total: float = 30.0,
    play_mode: PlayMode = PlayMode.explore,
    character_creation_state: CharacterCreationState | None = None,
    memory: PartyMemory | None = None,
    targeting_cursor: tuple[int, int] | None = None,
    targeting_origin: tuple[int, int] | None = None,
    targeting_range: int = 0,
    dialogue: DialogueState | None = None,
    help_state: HelpState | None = None,
    roster_state: RosterState | None = None,
    character_sheet_state: CharacterSheetState | None = None,
) -> None:
    screen.clear()
    layout = Layout(width=screen.width, height=screen.height)
    if layout.is_too_small:
        warning = f"Terminal too small. Need at least {MIN_TERMINAL_WIDTH}x{MIN_TERMINAL_HEIGHT}."
        screen.print_line(0, warning)
        screen.refresh()
        return

    if ui_mode is UIMode.start:
        _render_start_choice(screen, layout)
        return

    if ui_mode is UIMode.game_over:
        _render_game_over(screen, layout)
        return

    if ui_mode is UIMode.character_creation:
        if character_creation_state is None:
            screen.refresh()
            return
        _render_character_creation(screen, layout, character_creation_state)
        return

    if ui_mode is UIMode.inventory:
        _render_inventory(screen, layout, world, observer)
        return

    if ui_mode is UIMode.dialogue and dialogue is not None:
        _render_dialogue(screen, layout, world, dialogue)
        return

    if ui_mode is UIMode.level_up:
        _render_level_up(screen, layout, world, party)
        return

    if ui_mode is UIMode.help and help_state is not None:
        _render_help(screen, layout, help_state)
        return

    if ui_mode is UIMode.roster and roster_state is not None:
        _render_roster(screen, layout, roster_state)
        return

    if (
        ui_mode is UIMode.character_sheet
        and character_sheet_state is not None
    ):
        _render_character_sheet(screen, layout, character_sheet_state)
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
            glyph = _projected_presentation(observer, world, world_x, world_y, party, memory)
            screen.draw_char(screen_x, screen_y, glyph.char)

    screen.print_line(
        layout.status_y,
        _status_line(world, observer, party, movement_used, movement_total, play_mode).ljust(
            layout.playfield_width
        ),
        layout.origin_x,
    )
    # M20 targeting overlay: when the play screen is hosting a targeting
    # modal, draw a highlight on the cursor cell and park the terminal
    # cursor there so the player sees their selection. The cursor is a
    # projection — the world has no idea targeting is up.
    if ui_mode is UIMode.targeting and targeting_cursor is not None:
        cursor_screen = _world_to_screen(
            layout, targeting_cursor, viewport_x, viewport_y
        )
        if cursor_screen is not None:
            screen.draw_char(cursor_screen[0], cursor_screen[1], "X")
            screen.move_cursor(*cursor_screen)
            screen.refresh()
            return

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


def _projected_presentation(
    observer: EntityId,
    world: World,
    x: int,
    y: int,
    party: Sequence[EntityId],
    memory: PartyMemory | None,
) -> Glyph:
    """Project a tile through party memory + visible set.

    With ``memory=None`` the renderer falls back to the omniscient
    behaviour (used by tests/UI paths that have not yet wired vision in).
    With a memory provided, unknown tiles render as a blank space,
    remembered tiles render their last-seen snapshot (no live entities),
    and visible tiles render the live world.
    """
    if memory is None:
        return _presentation_for(observer, world, x, y, party)
    state = memory.state_at(x, y)
    if state is VisibilityState.VISIBLE:
        return _presentation_for(observer, world, x, y, party)
    if state is VisibilityState.REMEMBERED:
        remembered = memory.tiles.get((x, y))
        if remembered is None:
            return Glyph(" ")
        glyph_char = remembered.features[0].glyph if remembered.features else remembered.glyph
        return Glyph(glyph_char)
    return Glyph(" ")


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


def _world_to_screen(
    layout: Layout,
    position: tuple[int, int],
    viewport_x: int,
    viewport_y: int,
) -> tuple[int, int] | None:
    """Translate a world ``(x, y)`` to ``(screen_x, screen_y)`` if on-screen."""

    screen_x = layout.origin_x + position[0] - viewport_x
    screen_y = layout.map_top + position[1] - viewport_y
    if not (
        layout.origin_x <= screen_x < layout.origin_x + layout.playfield_width
        and layout.map_top <= screen_y <= layout.map_bottom
    ):
        return None
    return screen_x, screen_y


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
    play_mode: PlayMode = PlayMode.explore,
) -> str:
    mode_label = _play_mode_label(play_mode)
    label = _status_label(world, observer, party)
    movement = (
        f"  Move {_format_feet(movement_used)}/{_format_feet(movement_total)}"
        if play_mode in (PlayMode.turn_based, PlayMode.voluntary_turn)
        else ""
    )
    stats = world.combat_stats.get(observer)
    if stats is None:
        return f"{mode_label}  {label}  HP -/-  AC -{movement}"
    return (
        f"{mode_label}  {label}  HP {stats.hit_points}/{stats.max_hit_points}  "
        f"AC {stats.armor_class}{movement}"
    )


def _play_mode_label(play_mode: PlayMode) -> str:
    if play_mode is PlayMode.explore:
        return "Explore"
    if play_mode is PlayMode.turn_based:
        return "Battle"
    return "Turn"


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


def _render_dialogue(
    screen: Screen,
    layout: Layout,
    world: World,
    dialogue: DialogueState,
) -> None:
    """Draw the dialogue modal: speaker name, line, numbered options.

    The speaker name comes from the world (the entity's :class:`Name`
    component) so a single dialogue tree can be reused across NPCs
    without baking the name into the tree. The line itself is the
    raw text from the current :class:`DialogueNode`.
    """

    node = dialogue.node()
    speaker_name = world.name_for(dialogue.speaker)
    _line(screen, layout, layout.message_y, "Dialogue")
    _line(screen, layout, layout.map_top, speaker_name)
    _line(screen, layout, layout.map_top + 1, "-" * layout.playfield_width)
    _line(screen, layout, layout.map_top + 3, node.line.text)

    if node.options:
        for index, option in enumerate(node.options):
            _line(
                screen,
                layout,
                layout.map_top + 5 + index,
                f"{index + 1} - {option.label}",
            )
        _line(
            screen,
            layout,
            layout.status_y,
            "1-9 select, Esc/q close",
        )
    else:
        _line(
            screen,
            layout,
            layout.map_top + 5,
            "[Press Enter or Esc to close]",
        )
        _line(screen, layout, layout.status_y, "Enter/Esc close")
    screen.refresh()


def _render_level_up(
    screen: Screen,
    layout: Layout,
    world: World,
    party: Sequence[EntityId],
) -> None:
    """Render the M25 level-up modal.

    Shows the first party member with a pending :class:`LevelUpAvailable`
    plus the previews the player needs to make an informed confirm:
    current → projected HP, projected proficiency, and any new spell
    slots their class unlocks at the target level. The actual
    application happens in the :class:`LevelUp` effect handler — this
    is pure projection, no world mutation.
    """

    from src.core.character_creation import class_by_name
    from src.core.combat import proficiency_bonus_for_level
    from src.core.leveling import hp_gain_for_level_up, slot_progression_for

    target_member: EntityId | None = None
    target_level = 1
    for member in party:
        pending = world.level_up_pending.get(member)
        if pending is not None:
            target_member = member
            target_level = pending.target_level
            break

    _line(screen, layout, layout.message_y, "Level Up")
    if target_member is None:
        _line(
            screen,
            layout,
            layout.map_top + 3,
            "(No pending level-up; press q to close.)",
        )
        _line(screen, layout, layout.status_y, "y confirm, q/Esc dismiss")
        screen.refresh()
        return

    name = world.name_for(target_member)
    character = world.characters.get(target_member)
    character_class = (
        character.sheet.character_class if character is not None else "?"
    )
    class_option = class_by_name(character_class)
    stats = world.combat_stats.get(target_member)
    constitution = stats.constitution if stats is not None else 10
    hit_die = class_option.hit_die if class_option is not None else 8
    hp_gain = hp_gain_for_level_up(hit_die, constitution)
    new_max_hp = (stats.max_hit_points if stats is not None else 0) + hp_gain
    new_proficiency = proficiency_bonus_for_level(target_level)
    progression = slot_progression_for(character_class, target_level)

    _line(
        screen,
        layout,
        layout.map_top,
        f"{name} ({character_class}) reaches level {target_level}!",
    )
    _line(screen, layout, layout.map_top + 1, "-" * layout.playfield_width)
    if stats is not None:
        _line(
            screen,
            layout,
            layout.map_top + 3,
            f"HP: {stats.max_hit_points} -> {new_max_hp} (+{hp_gain})",
        )
        _line(
            screen,
            layout,
            layout.map_top + 4,
            f"Proficiency: +{stats.proficiency_bonus} -> +{new_proficiency}",
        )
    if progression:
        slot_summary = ", ".join(
            f"L{level}: {maximum}" for level, maximum in sorted(progression.items())
        )
        _line(
            screen,
            layout,
            layout.map_top + 6,
            f"Spell slots: {slot_summary}",
        )
    else:
        _line(
            screen,
            layout,
            layout.map_top + 6,
            "Spell slots: (no change)",
        )
    _line(
        screen,
        layout,
        layout.map_top + 8,
        "Press y / Enter to confirm. q / Esc dismisses.",
    )
    _line(screen, layout, layout.status_y, "y confirm, q/Esc dismiss")
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
    state: CharacterCreationState,
) -> None:
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


def _render_help(screen: Screen, layout: Layout, state: HelpState) -> None:
    """Draw the help index (when no topic is selected) or the body view.

    The index shows a single-column list of topics; the cursor row
    carries a ``>`` marker. The body view wraps the topic's markdown to
    the playfield width, scrolling by ``state.scroll`` rows.
    """

    _line(screen, layout, layout.message_y, "Help")
    if state.viewing is None:
        _line(screen, layout, layout.map_top, "Topics")
        _line(screen, layout, layout.map_top + 1, "-" * layout.playfield_width)
        # Render the topic list. ``map_top + 3`` keeps the layout
        # consistent with the inventory + dialogue screens.
        topics = state.topics
        row_offset = 3
        max_rows = max(0, layout.map_bottom - (layout.map_top + row_offset))
        # Scroll the index so the cursor stays on-screen for long lists.
        first = 0
        if state.cursor >= max_rows:
            first = state.cursor - max_rows + 1
        visible = topics[first : first + max_rows]
        for offset, topic in enumerate(visible):
            marker = ">" if (first + offset) == state.cursor else " "
            _line(
                screen,
                layout,
                layout.map_top + row_offset + offset,
                f"{marker} {topic.title}",
            )
        _line(
            screen,
            layout,
            layout.status_y,
            "j/k or arrows select  Enter view  Esc/q close",
        )
        screen.refresh()
        return

    topic = state.viewing
    _line(screen, layout, layout.map_top, topic.title)
    _line(screen, layout, layout.map_top + 1, "-" * layout.playfield_width)
    body_lines = wrap_body(topic.body, layout.playfield_width)
    row_offset = 3
    max_rows = max(0, layout.map_bottom - (layout.map_top + row_offset))
    first = min(state.scroll, max(0, len(body_lines) - max_rows))
    visible_body = body_lines[first : first + max_rows]
    for offset, body_line in enumerate(visible_body):
        _line(screen, layout, layout.map_top + row_offset + offset, body_line)
    _line(
        screen,
        layout,
        layout.status_y,
        "j/k scroll  Space page  Esc/q back",
    )
    screen.refresh()


def _render_roster(screen: Screen, layout: Layout, state: RosterState) -> None:
    _line(screen, layout, layout.message_y, "Party Roster")
    _line(screen, layout, layout.map_top, "Members")
    _line(screen, layout, layout.map_top + 1, "-" * layout.playfield_width)
    if not state.entries:
        _line(screen, layout, layout.map_top + 3, "(no party members)")
    else:
        for offset, entry in enumerate(state.entries):
            _line(
                screen,
                layout,
                layout.map_top + 3 + offset,
                roster_line(entry, selected=offset == state.cursor),
            )
    _line(
        screen,
        layout,
        layout.status_y,
        "j/k or arrows select  Enter view sheet  Esc/q close",
    )
    screen.refresh()


def _render_character_sheet(
    screen: Screen,
    layout: Layout,
    state: CharacterSheetState,
) -> None:
    view = state.view
    _line(screen, layout, layout.message_y, "Character Sheet")
    lines = render_sheet_lines(view)
    for offset, body_line in enumerate(lines[: layout.playfield_height - 1]):
        _line(screen, layout, layout.map_top + offset, body_line)
    _line(screen, layout, layout.status_y, "Esc/q back")
    screen.refresh()
