"""Shared builders for the M38 playtest scenario fixtures.

A fixture builds a tiny purpose-made world (typically a single
walled-in room) and a party that already exists on it. The default
``create_app`` overworld is replaced wholesale — the fixture's
``builder`` returns a fresh :class:`~src.app.App` and the harness
swaps to it.

The helpers in this module are deliberately small and explicit. They
exist to keep the per-scenario builders short and focused on the
specific feature under exercise; no attempt is made to recreate the
full ``create_app`` machinery (start screen, character creation, etc.).

What you get
------------

- :func:`build_fixture_room` — make a walled room of a given size and
  return the world, the floor coordinates of the interior, and the
  centre point used to anchor the party.
- :func:`spawn_party` — drop the player + companion party into the
  room and return their entity ids. Sheets are picked deterministically
  from the seed so save/load and observation snapshots round-trip.
- :func:`spawn_kobold`, :func:`spawn_kobold_archer`, :func:`spawn_chest`,
  :func:`spawn_door`, :func:`spawn_trap`, :func:`spawn_shopkeeper` —
  one-line helpers that add the right components for each scenario
  category. They mirror the M33 debug spawn catalog (and are kept
  intentionally close to those signatures) but live here so the
  fixture builders never need to monkey with the dev-mode flag.
- :func:`make_fixture_app` — orchestrate the boilerplate: build the
  room, populate the party, run the caller's content callback, and
  produce a fully wired ``App`` ready for ``PlaytestHarness`` use.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable

from src.app import (
    App,
    _add_companions_for_player_sheet,
    _assign_character_sheet,
    _make_turn_controller,
)
from src.core.action_context import make_default_resolver
from src.core.character_creation import (
    CharacterSheet,
    CLASSES,
    RACES,
    initial_character_creation_state,
    next_step,
    to_character_sheet,
    with_selection,
)
from src.core.combat import weapon_for_name
from src.core.components import (
    AI,
    AIBehaviorType,
    BlocksMovement,
    CombatStats,
    Container,
    Creature,
    Door,
    Faction,
    Inventory,
    Lock,
    Name,
    PlayerControlled,
    Position,
    Presentation,
    Shop,
    Trap,
)
from src.core.dispatcher import Dispatcher
from src.core.entity import EntityId
from src.core.game_state import GameState
from src.core.items import add_item
from src.core.modes import UIMode, play_mode_for_state
from src.core.party_state import PartyState
from src.core.world import World
from src.map.tiles import FLOOR, HORIZONTAL_WALL, VERTICAL_WALL
from src.systems.ai_system import EnemyAISystem  # noqa: F401 — re-exported for builders
from src.systems.awareness_system import hostiles_requiring_battle
from src.systems.character_creation_system import CharacterCreationSystem
from src.systems.combat_system import CombatSystem
from src.systems.game_over_system import GameOverSystem
from src.systems.interaction_system import InteractionSystem
from src.systems.inventory_system import InventorySystem
from src.systems.loot_system import LootSystem
from src.systems.movement_system import (
    MovementContextResolver,
    MovementSystem,
)
from src.systems.obstruction_system import ObstructionSystem
from src.systems.quit_system import QuitSystem
from src.systems.spell_system import SpellSystem
from src.systems.start_system import StartSystem
from src.systems.stealth_system import StealthSystem


# Default fixture-room dimensions. Big enough for a small encounter +
# the four-member party + a couple of spare tiles around the edges,
# small enough that LOS / autowalk tests don't get lost in empty space.
DEFAULT_ROOM_WIDTH = 21
DEFAULT_ROOM_HEIGHT = 11


@dataclass(frozen=True, slots=True)
class FixtureRoom:
    """Geometry of a freshly-built fixture room.

    The room is a walled rectangle; ``floor_bounds`` is the inclusive
    ``(left, top, right, bottom)`` of the walkable interior. ``centre``
    is the tile we drop the player on; everything else (companions,
    enemies, doors) is positioned relative to it.
    """

    world: World
    floor_bounds: tuple[int, int, int, int]
    centre: tuple[int, int]


def build_fixture_room(
    width: int = DEFAULT_ROOM_WIDTH,
    height: int = DEFAULT_ROOM_HEIGHT,
) -> FixtureRoom:
    """Build a walled-in floor room and return its geometry.

    The world is sized exactly to the room (no overworld outside).
    Walls live at the borders; the interior tiles are catalog
    :data:`FLOOR`. Coordinates returned in ``floor_bounds`` are the
    *interior* edge — ``(1, 1)`` to ``(width - 2, height - 2)`` for a
    standard room.
    """
    if width < 5 or height < 5:
        raise ValueError("Fixture rooms need room for walls and a centre tile.")
    tiles = [[FLOOR for _ in range(width)] for _ in range(height)]
    for x in range(width):
        tiles[0][x] = HORIZONTAL_WALL
        tiles[height - 1][x] = HORIZONTAL_WALL
    for y in range(height):
        tiles[y][0] = VERTICAL_WALL
        tiles[y][width - 1] = VERTICAL_WALL
    world = World(width=width, height=height, tiles=tiles)
    floor_bounds = (1, 1, width - 2, height - 2)
    centre = (width // 2, height // 2)
    return FixtureRoom(world=world, floor_bounds=floor_bounds, centre=centre)


def deterministic_player_sheet(rng: random.Random) -> CharacterSheet:
    """Roll a class/race/skills bundle, deterministic for ``rng``.

    Equivalent to :func:`src.systems.start_system.yolo_sheet` but local
    so fixtures don't depend on the start-system import path. Picks
    Rogue when the random class would otherwise lack the Sleight-of-
    Hand skill required for the lock/trap scenarios — we override that
    for fixtures that need it via :func:`force_rogue_sheet` below.
    """
    state = initial_character_creation_state(rng=rng)
    state = with_selection(state, rng.choice(RACES).name)
    character_class = rng.choice(CLASSES)
    state = with_selection(state, character_class.name)
    state = with_selection(state, rng.choice(character_class.specializations))
    for choices, count in (
        (character_class.cantrip_choices, character_class.cantrip_count),
        (character_class.spell_choices, character_class.spell_count),
        (character_class.skill_choices, character_class.skill_count),
    ):
        for choice in rng.sample(list(choices), count):
            state = with_selection(state, choice)
        if count:
            state = next_step(state)
    while state.step != "confirm":
        state = next_step(state)
    return to_character_sheet(state)


def force_wizard_sheet(rng: random.Random) -> CharacterSheet:
    """Roll a Wizard sheet for the M11 spell-encounter fixture.

    Picks Wizard explicitly so the resulting party leader has a
    :class:`SpellList` and slot ledger after
    :func:`_assign_character_sheet`. Race and specialization are
    chosen deterministically over the same seed.
    """
    wizard = next(option for option in CLASSES if option.name == "Wizard")
    state = initial_character_creation_state(rng=rng)
    state = with_selection(state, rng.choice(RACES).name)
    state = with_selection(state, wizard.name)
    state = with_selection(state, rng.choice(wizard.specializations))
    for choices, count in (
        (wizard.cantrip_choices, wizard.cantrip_count),
        (wizard.spell_choices, wizard.spell_count),
        (wizard.skill_choices, wizard.skill_count),
    ):
        for choice in rng.sample(list(choices), count):
            state = with_selection(state, choice)
        if count:
            state = next_step(state)
    while state.step != "confirm":
        state = next_step(state)
    return to_character_sheet(state)


def force_rogue_sheet(rng: random.Random) -> CharacterSheet:
    """Roll a Rogue sheet that includes Sleight of Hand for lock/trap tests.

    Picks the Rogue class explicitly so :data:`_LOCK_SKILL` /
    :data:`_TRAP_SKILL` checks have a proficient actor at the wheel.
    Race is deterministic over the same seed so save/load still
    round-trips.
    """
    rogue = next(option for option in CLASSES if option.name == "Rogue")
    state = initial_character_creation_state(rng=rng)
    state = with_selection(state, rng.choice(RACES).name)
    state = with_selection(state, rogue.name)
    state = with_selection(state, rng.choice(rogue.specializations))
    # Rogue has no cantrips/spells, so we go straight to skills.
    state = next_step(state)  # advance past spec
    # Pick a deterministic skill bundle: Sleight of Hand first, then
    # fill the rest from the remaining options.
    must_have = "Sleight of Hand"
    remaining = [skill for skill in rogue.skill_choices if skill != must_have]
    chosen = [must_have, *rng.sample(remaining, rogue.skill_count - 1)]
    for choice in chosen:
        state = with_selection(state, choice)
    state = next_step(state)
    while state.step != "confirm":
        state = next_step(state)
    return to_character_sheet(state)


def spawn_party(
    world: World,
    position: tuple[int, int],
    *,
    rng: random.Random,
    sheet: CharacterSheet | None = None,
) -> tuple[EntityId, list[EntityId]]:
    """Spawn the player at ``position`` plus the standard companion
    party, returning ``(player_id, party_list)``.

    ``sheet`` defaults to :func:`deterministic_player_sheet` so callers
    that don't care about class composition get a reproducible-enough
    roll for free. Pass :func:`force_rogue_sheet` (or any other sheet)
    when a fixture needs a specific skill set.
    """
    if sheet is None:
        sheet = deterministic_player_sheet(rng)
    player = world.create_entity()
    world.positions.add(player, Position(x=position[0], y=position[1]))
    world.presentations.add(player, Presentation("@"))
    world.blockers.add(player, BlocksMovement("occupied"))
    world.player_controlled.add(player, PlayerControlled())
    world.names.add(player, Name("you"))
    from src.core.factions import FactionId
    world.factions.add(player, Faction(FactionId.PLAYER_PARTY.value))
    _assign_character_sheet(world, player, sheet)
    party = _add_companions_for_player_sheet(world, player, sheet)
    return player, party


_ADJACENT_OFFSETS: tuple[tuple[int, int], ...] = (
    # Cardinal directions first (east, west, south, north), then diagonals.
    # This matches the convention scenarios use ("one tile east of the
    # player") so the first free cardinal is picked when possible.
    (1, 0),
    (-1, 0),
    (0, 1),
    (0, -1),
    (1, 1),
    (-1, 1),
    (1, -1),
    (-1, -1),
)


def find_open_adjacent(
    world: World,
    x: int,
    y: int,
    *,
    bounds: tuple[int, int, int, int] | None = None,
) -> tuple[int, int]:
    """Return the first walkable, unoccupied tile adjacent to ``(x, y)``.

    Iterates in a deterministic order — cardinals first (E, W, S, N),
    then diagonals — so scenario builders that prefer "one tile east"
    get that placement when the tile is free, and otherwise fall back
    predictably. ``bounds`` is an optional ``(left, top, right, bottom)``
    clamp (typically the fixture room's ``floor_bounds``) so candidates
    don't escape the walled interior.

    Raises :class:`RuntimeError` if no adjacent tile is open — fixture
    rooms are deliberately sized so this should never trigger; the
    exception exists to surface a misconfigured scenario loudly rather
    than dump a silent collision into the world.
    """
    for dx, dy in _ADJACENT_OFFSETS:
        cx, cy = x + dx, y + dy
        if bounds is not None:
            left, top, right, bottom = bounds
            if not (left <= cx <= right and top <= cy <= bottom):
                continue
        if world.tile_at(cx, cy).blocks_movement:
            continue
        if world.entities_at(cx, cy):
            continue
        return cx, cy
    raise RuntimeError(
        f"No open adjacent tile found around ({x}, {y}) — fixture room is too crowded."
    )


def clear_tiles_for_spawn(
    world: World,
    tiles: tuple[tuple[int, int], ...],
    movable: tuple[EntityId, ...],
    *,
    bounds: tuple[int, int, int, int],
    avoid: tuple[tuple[int, int], ...] = (),
) -> None:
    """Relocate any of ``movable`` that sit on a tile in ``tiles``.

    Used by fixture builders that want to place scenario entities on
    *specific* cardinal tiles (e.g. "the door is exactly one east of
    the player") and need to evict whichever companion the
    deterministic party-placement helper left there. The relocated
    entity is moved to the first walkable, unoccupied tile reachable
    from its old position via :func:`find_open_adjacent`; positions
    in ``avoid`` (typically the spawn tiles themselves plus the
    player's tile) are skipped so the eviction can't cascade back into
    the same conflict.

    Mutates ``world.positions`` in place. Idempotent — passing tiles
    that are already free is a no-op.
    """
    blocked = set(tiles) | set(avoid)
    targeted = set(tiles)
    for entity in movable:
        position = world.positions.get(entity)
        if position is None:
            continue
        current = (position.x, position.y)
        if current not in targeted:
            continue
        # Find a new tile that isn't claimed and isn't in the avoid set.
        new_position: tuple[int, int] | None = None
        for dx, dy in _ADJACENT_OFFSETS:
            cx, cy = position.x + dx, position.y + dy
            left, top, right, bottom = bounds
            if not (left <= cx <= right and top <= cy <= bottom):
                continue
            if (cx, cy) in blocked:
                continue
            if world.tile_at(cx, cy).blocks_movement:
                continue
            if world.entities_at(cx, cy):
                continue
            new_position = (cx, cy)
            break
        if new_position is None:
            raise RuntimeError(
                f"Could not relocate entity {entity} off {current} — no open tile."
            )
        position.x, position.y = new_position
        blocked.add(new_position)


def _require_open_spawn_tile(world: World, x: int, y: int, kind: str) -> None:
    """Guard against spawning a fixture entity on top of an existing one.

    Used by every ``spawn_*`` helper. Raises a clear error so collisions
    surface during fixture construction rather than mid-playtest as a
    confusing "You displaced X." message at t=0.
    """
    if world.tile_at(x, y).blocks_movement:
        raise RuntimeError(
            f"Cannot spawn {kind} at ({x}, {y}): tile blocks movement."
        )
    occupants = world.entities_at(x, y)
    if occupants:
        names = ", ".join(
            (world.names.get(e).value if world.names.has(e) else f"entity_{e}")
            for e in occupants
        )
        raise RuntimeError(
            f"Cannot spawn {kind} at ({x}, {y}): tile already occupied by {names}."
        )


def spawn_kobold(
    world: World,
    x: int,
    y: int,
    *,
    ai: AI | None = None,
) -> EntityId:
    """Add a melee kobold at ``(x, y)``. Mirrors the M33 debug catalog.

    Carries the canonical M28 ``dungeon`` faction so the M28 relation
    table treats it as hostile to the player party without falling
    through the legacy ``"enemy" → DUNGEON`` alias.

    Raises :class:`RuntimeError` if ``(x, y)`` is already occupied — use
    :func:`find_open_adjacent` to discover a free tile near the party.
    """
    _require_open_spawn_tile(world, x, y, "kobold")
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation("k"))
    world.names.add(entity, Name("kobold"))
    world.factions.add(entity, Faction("dungeon"))
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
    world.ai.add(entity, ai or AI(behavior=AIBehaviorType.CHASE))
    return entity


def spawn_kobold_archer(world: World, x: int, y: int) -> EntityId:
    """Ranged kobold archer with the RANGED AI behavior.

    ``preferred_range`` is set to 3 so the AI tries to keep distance,
    and ``attack_range`` to 6 so an arrow flies from the spawn tile.
    The weapon damage die stays modest so the encounter is winnable in
    a few rounds.
    """
    entity = spawn_kobold(
        world,
        x,
        y,
        ai=AI(behavior=AIBehaviorType.RANGED, attack_range=6, preferred_range=4),
    )
    world.names.add(entity, Name("kobold archer"))
    # Replace the dagger with a shortbow-equivalent for flavour. We
    # keep the existing weapon catalog mechanic (longsword die size)
    # rather than adding a new item type just for the archer; ranged
    # attacks are mechanically identical at this milestone.
    world.weapons.add(entity, weapon_for_name("shortsword"))
    world.creatures.add(entity, Creature(kind="kobold_archer", attack_verb="shoots"))
    return entity


def spawn_door(
    world: World,
    x: int,
    y: int,
    *,
    locked: bool = False,
    pick_dc: int = 10,
) -> EntityId:
    """Add a door (optionally locked) at ``(x, y)``.

    Doors block movement until opened; locked doors additionally
    require a Sleight-of-Hand check (the M9 default) to bypass.

    Raises :class:`RuntimeError` if ``(x, y)`` is already occupied.
    """
    _require_open_spawn_tile(world, x, y, "door")
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation("+"))
    world.names.add(entity, Name("locked door" if locked else "door"))
    world.doors.add(entity, Door(is_open=False))
    world.blockers.add(entity, BlocksMovement("door"))
    if locked:
        world.locks.add(entity, Lock(is_locked=True, pick_dc=pick_dc))
    return entity


def spawn_trap(
    world: World,
    x: int,
    y: int,
    *,
    disarm_dc: int = 10,
    damage: int = 2,
) -> EntityId:
    """Add an armed trap at ``(x, y)``.

    The trap is *not* a movement blocker — the player walks onto the
    tile (or pre-empts it by interacting toward it for the disarm
    check). Damage is small so a fixture run doesn't accidentally KO
    the actor mid-test.

    Raises :class:`RuntimeError` if ``(x, y)`` is already occupied.
    """
    _require_open_spawn_tile(world, x, y, "trap")
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation("^"))
    world.names.add(entity, Name("trap"))
    world.traps.add(entity, Trap(is_armed=True, disarm_dc=disarm_dc, damage=damage))
    return entity


def spawn_chest(
    world: World,
    x: int,
    y: int,
    *,
    items: tuple[str, ...] = (),
    gold: int = 0,
) -> EntityId:
    """Add a closed container at ``(x, y)`` with seeded contents.

    The chest blocks movement (you have to open it from an adjacent
    tile via ``e``). Contents land in its :class:`Inventory`; opening
    via :data:`OpenEntity` flips the ``is_open`` flag, and pickup uses
    the standard M30 inventory transfer.

    Raises :class:`RuntimeError` if ``(x, y)`` is already occupied.
    """
    _require_open_spawn_tile(world, x, y, "chest")
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation("="))
    world.names.add(entity, Name("chest"))
    world.containers.add(entity, Container(is_open=False))
    world.blockers.add(entity, BlocksMovement("container"))
    inventory = Inventory(gold=gold)
    for item_id in items:
        add_item(inventory, item_id)
    world.inventories.add(entity, inventory)
    return entity


def spawn_info_npc(
    world: World,
    x: int,
    y: int,
    *,
    name: str,
    line: str,
) -> EntityId:
    """Add an :class:`NPCKind.INFO` NPC that speaks one line on ``e``.

    The NPC carries the canonical M28 ``town`` faction so it stays
    neutral. The :class:`DialogueTree` is the one-node tree built by
    :func:`info_tree`. Useful for playtest fixtures that want to
    exercise the dialogue modal end-to-end.
    """

    from src.core.components import NPC, NPCDialogue, NPCKind
    from src.core.dialogue import info_tree

    _require_open_spawn_tile(world, x, y, "info NPC")
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation("@"))
    world.names.add(entity, Name(name))
    world.factions.add(entity, Faction("town"))
    world.blockers.add(entity, BlocksMovement("occupied"))
    world.npcs.add(entity, NPC(kind=NPCKind.INFO))
    world.npc_dialogues.add(
        entity, NPCDialogue(tree=info_tree(speaker_id=name, text=line))
    )
    return entity


def spawn_recruit_npc(
    world: World,
    x: int,
    y: int,
    *,
    name: str,
    ask_text: str = "Looking for sword work. Join up?",
    accept_text: str = "Then you have my blade.",
) -> EntityId:
    """Add an :class:`NPCKind.RECRUIT` NPC offering to join the party.

    A minimal Fighter sheet, starter armor + weapon, and a small
    inventory are attached so accepting the recruit gives a usable
    party member from the next tick.
    """

    from src.core.character_creation import CharacterSheet
    from src.core.combat import (
        combat_stats_for_sheet,
        starter_armor_for_class,
        starter_weapon_for_class,
    )
    from src.core.components import Character, Equipment, NPC, NPCDialogue, NPCKind
    from src.core.dialogue import recruit_tree
    from src.core.items import (
        add_item as items_add_item,
        armor_item_id_for_name,
        weapon_item_id_for_name,
    )

    sheet = CharacterSheet(
        race="Human",
        character_class="Fighter",
        specialization="Champion",
        base_attributes={
            "STR": 14, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10,
        },
        attributes={
            "STR": 14, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10,
        },
        skills=("Athletics",),
    )

    _require_open_spawn_tile(world, x, y, "recruit NPC")
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation("@"))
    world.names.add(entity, Name(name))
    world.factions.add(entity, Faction("town"))
    world.blockers.add(entity, BlocksMovement("occupied"))
    world.npcs.add(entity, NPC(kind=NPCKind.RECRUIT))
    world.npc_dialogues.add(
        entity,
        NPCDialogue(
            tree=recruit_tree(
                speaker_id=name, ask_text=ask_text, accept_text=accept_text
            )
        ),
    )
    world.characters.add(entity, Character(sheet))
    armor = starter_armor_for_class(sheet.character_class)
    world.armor.add(entity, armor)
    world.combat_stats.add(entity, combat_stats_for_sheet(sheet, armor))
    weapon = starter_weapon_for_class(sheet.character_class)
    world.weapons.add(entity, weapon)
    inventory = Inventory(gold=10)
    weapon_item_id = weapon_item_id_for_name(weapon.name)
    armor_item_id = armor_item_id_for_name(armor.name)
    items_add_item(inventory, weapon_item_id)
    if armor_item_id is not None:
        items_add_item(inventory, armor_item_id)
    world.inventories.add(entity, inventory)
    world.equipment.add(
        entity,
        Equipment(weapon_item_id=weapon_item_id, armor_item_id=armor_item_id),
    )
    return entity


def spawn_quest_giver(
    world: World,
    x: int,
    y: int,
    *,
    name: str,
    quest_id: str,
    pitch: str,
    accept_response: str = "Then go, and may you return.",
    decline_response: str = "Suit yourself.",
) -> EntityId:
    """Add an NPC carrying a :func:`quest_offer_tree` (M14).

    Mirrors :func:`spawn_info_npc` but the dialogue carries an
    ``AcceptQuestEffect`` keyed on ``quest_id``. Used by the
    ``quest_path`` playtest fixture.

    Raises :class:`RuntimeError` if ``(x, y)`` is already occupied.
    """
    from src.core.components import NPC, NPCDialogue, NPCKind
    from src.core.dialogue import quest_offer_tree

    _require_open_spawn_tile(world, x, y, "quest giver")
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation("@"))
    world.names.add(entity, Name(name))
    world.factions.add(entity, Faction("town"))
    world.blockers.add(entity, BlocksMovement("occupied"))
    world.npcs.add(entity, NPC(kind=NPCKind.INFO))
    world.npc_dialogues.add(
        entity,
        NPCDialogue(
            tree=quest_offer_tree(
                speaker_id=name,
                quest_id=quest_id,
                pitch=pitch,
                accept_response=accept_response,
                decline_response=decline_response,
            )
        ),
    )
    return entity


def spawn_quest_boss(
    world: World,
    x: int,
    y: int,
    *,
    boss_marker_token: str = "sunken_gate_warlord",
    creature_key: str = "boss_kobold_warlord",
) -> EntityId:
    """Add the M14 quest boss with marker + loot table.

    Uses the catalogue :class:`CreatureSpec` for ``creature_key`` so
    the stats / weapon / drop table stay aligned with the live world
    skeleton. A :class:`BossMarker` with ``boss_marker_token`` is
    attached so the kill hook can credit the quest.
    """
    from src.core.components import BossMarker, LootDrop
    from src.core.creatures import (
        combat_stats_for_creature,
        creature_component,
        creature_for_key,
        weapon_for_creature,
    )

    _require_open_spawn_tile(world, x, y, "quest boss")
    spec = creature_for_key(creature_key)
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation(spec.glyph))
    world.blockers.add(entity, BlocksMovement("occupied"))
    world.names.add(entity, Name(spec.name))
    world.creatures.add(entity, creature_component(spec))
    world.factions.add(entity, Faction("dungeon"))
    world.combat_stats.add(entity, combat_stats_for_creature(spec))
    world.weapons.add(entity, weapon_for_creature(spec))
    if spec.loot.entries:
        world.loot_drops.add(entity, LootDrop(table=spec.loot))
    world.boss_markers.add(entity, BossMarker(token=boss_marker_token))
    return entity


def spawn_shopkeeper(
    world: World,
    x: int,
    y: int,
    *,
    name: str = "Quartermaster",
    gold: int = 200,
    stock: tuple[str, ...] = (),
    with_dialogue: bool = True,
) -> EntityId:
    """Add a stationary shopkeeper with a stocked inventory.

    The shopkeeper carries the canonical M28 ``town`` faction so the
    relation table treats them as neutral to the player party
    (no forced turn-based mode, no autowalk interrupt). The shop
    machinery in :mod:`src.core.shop` reads from the :class:`Inventory`
    on the same entity.

    M13: when ``with_dialogue`` is true (the default), the shopkeeper
    also gets an :class:`NPC` marker + a :class:`shopkeeper_tree`
    so pressing ``e`` opens the dialogue modal instead of going
    straight through the interaction dispatcher. Pre-M13 fixtures
    that want the bare M12 shopkeeper (no modal) pass
    ``with_dialogue=False``.

    Raises :class:`RuntimeError` if ``(x, y)`` is already occupied.
    """
    from src.core.components import NPC, NPCDialogue, NPCKind
    from src.core.dialogue import shopkeeper_tree

    _require_open_spawn_tile(world, x, y, "shopkeeper")
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(
        entity, Presentation("@" if with_dialogue else "S")
    )
    world.names.add(entity, Name(name))
    world.factions.add(entity, Faction("town"))
    world.blockers.add(entity, BlocksMovement("occupied"))
    world.shops.add(entity, Shop(name=name))
    inventory = Inventory(gold=gold)
    for item_id in stock:
        add_item(inventory, item_id)
    world.inventories.add(entity, inventory)
    if with_dialogue:
        world.npcs.add(entity, NPC(kind=NPCKind.SHOPKEEPER))
        world.npc_dialogues.add(
            entity,
            NPCDialogue(
                tree=shopkeeper_tree(
                    speaker_id=name,
                    greeting=(
                        f"Welcome to {name}'s shop. "
                        "Looking to trade?"
                    ),
                )
            ),
        )
    return entity


def make_fixture_app(
    *,
    width: int = DEFAULT_ROOM_WIDTH,
    height: int = DEFAULT_ROOM_HEIGHT,
    rng: random.Random,
    populate: Callable[[FixtureRoom, EntityId, list[EntityId]], None],
    sheet: CharacterSheet | None = None,
    party_position: tuple[int, int] | None = None,
    ui_mode: UIMode = UIMode.play,
) -> App:
    """Build a fully-wired :class:`App` around a fresh fixture room.

    ``populate(room, player, party)`` is called after the party is
    placed so the caller only writes the per-scenario content
    (enemies, doors, chests, etc.). The function returns an App in
    ``ui_mode`` (defaults to :data:`UIMode.play` because nearly every
    fixture wants to start in the play screen).

    The dispatcher, movement/interaction RNG, and turn controller are
    rebuilt from scratch — replacing the default ``create_app`` world
    wholesale rather than monkey-patching it keeps the fixture's seed
    contract clean (the same seed produces the same world).
    """
    room = build_fixture_room(width=width, height=height)
    if party_position is None:
        party_position = room.centre
    player, party = spawn_party(room.world, party_position, rng=rng, sheet=sheet)

    populate(room, player, party)

    movement = MovementSystem(
        obstruction=ObstructionSystem(),
        context_resolver=MovementContextResolver(),
    )
    combat = CombatSystem(rng=rng)
    # SpellSystem must be registered for any fixture that exercises
    # spellcasting (M11). Omitting it causes CastSpellAttempt actions
    # to consume a slot in PRE_CHECK but never produce damage / heal /
    # condition effects -- bug #99. Mirrors the registration order in
    # ``src.app.create_app``.
    dispatcher = Dispatcher(
        systems=[
            StartSystem(),
            GameOverSystem(),
            InventorySystem(),
            CharacterCreationSystem(),
            QuitSystem(),
            InteractionSystem(rng=rng),
            LootSystem(),
            SpellSystem(rng=rng),
            StealthSystem(rng=rng),
            movement,
            combat,
        ]
    )
    party_state = PartyState.from_members(party)
    game_state = GameState(
        world=room.world,
        party=party_state,
        turn=_make_turn_controller(party_state),
        ui_mode=ui_mode,
    )
    game_state.turn.hostiles_probe = lambda: bool(
        hostiles_requiring_battle(game_state.world, party_state.members)
    )
    game_state.turn.can_take_turn = lambda entity: _can_take_turn(
        game_state.world, entity
    )
    game_state.turn.play_mode = play_mode_for_state(
        bool(hostiles_requiring_battle(game_state.world, party_state.members))
    )
    app = App(
        game_state=game_state,
        player=player,
        dispatcher=dispatcher,
        loot_rng=rng,
        action_resolver=None,
    )
    # ``__post_init__`` builds the resolver and refreshes vision.
    return app


def _can_take_turn(world: World, entity: EntityId) -> bool:
    """Local copy of the standard turn-eligibility predicate.

    Mirrors :func:`src.app._can_take_turn`. Inlined so the fixture
    helpers don't import a private name from the App module.
    """
    stats = world.combat_stats.get(entity)
    return world.positions.has(entity) and (stats is None or stats.hit_points > 0)
