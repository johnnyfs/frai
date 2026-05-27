from src.app import create_app
from src.core.components import Position
from src.core.config import PLAYFIELD_HEIGHT, PLAYFIELD_WIDTH
from src.core.entity import EntityId
from src.core.world import World
from src.map.tiles import OUTSIDE
from src.systems.render_system import _inventory_lines, _status_line, _viewport_origin
from src.ui.layout import Layout


def test_status_line_shows_hp_and_ac() -> None:
    app = create_app()
    app.handle_key(ord("y"))
    stats = app.world.combat_stats.require(app.player)
    stats.hit_points = stats.max_hit_points - 1

    assert _status_line(app.world, app.player) == f"HP {stats.hit_points}/{stats.max_hit_points}  AC {stats.armor_class}"


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
