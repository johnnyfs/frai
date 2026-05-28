from src.app import create_app
from src.core.components import Position
from src.core.config import PLAYFIELD_HEIGHT, PLAYFIELD_WIDTH
from src.core.entity import EntityId
from src.core.world import World
from src.map.tiles import OUTSIDE
from src.systems.render_system import (
    _focus_screen_position,
    _inventory_lines,
    _presentation_for,
    _status_line,
    _viewport_origin,
)
from src.ui.layout import Layout


def test_status_line_shows_hp_and_ac() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = stats.max_hit_points - 1
    character_class = app.world.characters.require(app.player).sheet.character_class

    assert _status_line(app.world, app.player, app.party) == (
        f"Explore  Player {character_class}  HP {stats.hit_points}/{stats.max_hit_points}  "
        f"AC {stats.armor_class}"
    )


def test_status_line_labels_party_members_by_roster_order() -> None:
    app = create_app()
    companion = app.party[1]
    stats = app.world.combat_stats.require(companion)
    character_class = app.world.characters.require(companion).sheet.character_class

    assert _status_line(
        app.world,
        companion,
        app.party,
        movement_used=4.25,
        major_mode="battle",
    ) == (
        f"Battle  Party Member 1 {character_class}  HP {stats.hit_points}/{stats.max_hit_points}  "
        f"AC {stats.armor_class}  Move 4.25/30"
    )


def test_status_line_shows_voluntary_turn_mode_movement() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    companion = app.party[1]
    stats = app.world.combat_stats.require(companion)
    character_class = app.world.characters.require(companion).sheet.character_class

    assert _status_line(
        app.world,
        companion,
        app.party,
        movement_used=3,
        major_mode="turn",
    ) == (
        f"Turn  Party Member 1 {character_class}  HP {stats.hit_points}/{stats.max_hit_points}  "
        f"AC {stats.armor_class}  Move 3/30"
    )


def test_inventory_lines_list_worn_armor_and_weapon_in_hand() -> None:
    app = create_app()
    app.handle_key(ord("c"))
    app.handle_key(ord("u"))  # Human
    app.handle_key(ord("f"))  # Fighter
    app.handle_key(ord("c"))  # Champion
    app.handle_key(ord("a"))
    app.handle_key(ord("t"))
    app.handle_key(ord("y"))
    app.handle_key(ord("y"))
    app.handle_key(ord("y"))

    assert _inventory_lines(app.world, app.player) == [
        "Armor  - chain mail (worn)",
        "Weapon - longsword (in hand)",
    ]


def test_viewport_origin_centers_on_focus_entity() -> None:
    world = World(
        width=PLAYFIELD_WIDTH * 2,
        height=PLAYFIELD_HEIGHT * 2,
        tiles=[
            [OUTSIDE for _ in range(PLAYFIELD_WIDTH * 2)]
            for _ in range(PLAYFIELD_HEIGHT * 2)
        ],
    )
    focus = EntityId(1)
    world.positions.add(focus, Position(x=120, y=35))

    assert _viewport_origin(world, Layout(width=PLAYFIELD_WIDTH, height=PLAYFIELD_HEIGHT + 2), focus) == (
        40,
        15,
    )


def test_focus_screen_position_tracks_focused_entity_in_viewport() -> None:
    world = World(width=20, height=20, tiles=[[OUTSIDE for _ in range(20)] for _ in range(20)])
    focus = EntityId(1)
    world.positions.add(focus, Position(x=8, y=9))
    layout = Layout(width=PLAYFIELD_WIDTH, height=PLAYFIELD_HEIGHT + 2)

    assert _focus_screen_position(world, layout, focus, viewport_x=3, viewport_y=4) == (
        layout.origin_x + 5,
        layout.map_top + 5,
    )


def test_party_glyphs_follow_roster_order() -> None:
    world = World(width=5, height=5, tiles=[[OUTSIDE for _ in range(5)] for _ in range(5)])
    player = EntityId(1)
    companion = EntityId(2)
    world.positions.add(player, Position(x=1, y=1))
    world.positions.add(companion, Position(x=2, y=1))

    assert _presentation_for(player, world, 1, 1, [player, companion]).char == "@"
    assert _presentation_for(player, world, 2, 1, [player, companion]).char == "1"
