from random import Random

from src.core.actions import MoveAttempt, QuitConfirm, QuitRequest
from src.core.config import PLAYFIELD_WIDTH, WORLD_HEIGHT, WORLD_WIDTH
from src.core.effects import EmitMessage, MoveEntity, QuitGame, SetMode
from src.core.modes import UIMode
from src.map.room_builder import build_room_world
from src.map.tiles import TileKind
from src.systems.movement_system import MovementContextResolver, MovementSystem
from src.systems.obstruction_system import ObstructionSystem
from src.systems.quit_system import QuitSystem


def test_movement_emits_move_effect_for_open_tile() -> None:
    built = build_room_world(20, 20)
    player = built.player
    position = built.world.positions.require(player)
    system = MovementSystem(ObstructionSystem(), MovementContextResolver())

    result = system.handle(MoveAttempt(player, 1, 0), built.world)

    assert result.effects == [MoveEntity(player, position.x + 1, position.y)]
    assert result.cancel is True


def test_movement_emits_blocked_message_for_wall() -> None:
    built = build_room_world(20, 20)
    player = built.player
    built.world.positions.require(player).x = 5
    built.world.positions.require(player).y = 5
    system = MovementSystem(ObstructionSystem(), MovementContextResolver())

    result = system.handle(MoveAttempt(player, -1, 0), built.world)

    assert result.effects == [EmitMessage("Blocked.")]
    assert result.cancel is True


def test_quit_system_mode_transitions() -> None:
    system = QuitSystem()
    built = build_room_world(20, 20)

    assert system.handle(QuitRequest(), built.world).effects == [
        EmitMessage("Quit? y/n"),
        SetMode(UIMode.quit_confirm),
    ]
    assert system.handle(QuitConfirm(False), built.world).effects == [
        SetMode(UIMode.play),
        EmitMessage(""),
    ]
    assert system.handle(QuitConfirm(True), built.world).effects == [QuitGame()]


def test_room_uses_nethack_wall_glyphs() -> None:
    built = build_room_world(80, 40)
    wall_cells = [
        (x, y, tile)
        for y, row in enumerate(built.world.tiles)
        for x, tile in enumerate(row)
        if tile.kind is TileKind.WALL
    ]

    top_y = min(y for _, y, _ in wall_cells)
    bottom_y = max(y for _, y, _ in wall_cells)
    left_x = min(x for x, _, _ in wall_cells)
    right_x = max(x for x, _, _ in wall_cells)

    assert built.world.tile_at((left_x + right_x) // 2, top_y).glyph == "-"
    assert built.world.tile_at((left_x + right_x) // 2, bottom_y).glyph == "-"
    assert built.world.tile_at(left_x, (top_y + bottom_y) // 2).glyph == "|"
    assert built.world.tile_at(right_x, (top_y + bottom_y) // 2).glyph == "|"


def test_default_world_adds_long_side_passages_and_rooms_beyond_initial_view() -> None:
    built = build_room_world(WORLD_WIDTH, WORLD_HEIGHT, rng=Random(0))
    player_position = built.world.positions.require(built.player)
    viewport_left = player_position.x - PLAYFIELD_WIDTH // 2
    viewport_right = viewport_left + PLAYFIELD_WIDTH - 1
    passage_rows = {
        y: [x for x, tile in enumerate(row) if tile.kind is TileKind.PASSAGE]
        for y, row in enumerate(built.world.tiles)
        if any(tile.kind is TileKind.PASSAGE for tile in row)
    }

    assert len(passage_rows) == 2
    assert all(len(xs) > 40 for xs in passage_rows.values())
    assert any(
        x < viewport_left and tile.kind is not TileKind.OUTSIDE
        for row in built.world.tiles
        for x, tile in enumerate(row)
    )
    assert any(
        x > viewport_right and tile.kind is not TileKind.OUTSIDE
        for row in built.world.tiles
        for x, tile in enumerate(row)
    )
