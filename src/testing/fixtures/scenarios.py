"""Concrete scenario builders registered into the M37 registry.

This module is imported for its side-effects: at import time each
``register(...)`` call adds a :class:`~src.testing.scenarios.Scenario`
to the global :data:`SCENARIOS` dict. The fixtures package
``__init__.py`` imports this module so callers only have to ``import
src.testing.fixtures`` (or the harness, which transitively imports
the package) to make the catalog available.

Why fixtures *replace* the App rather than mutate it
----------------------------------------------------

``PlaytestHarness`` builds an App via ``create_app`` (the 320x80
overworld) and then calls ``scenario.builder(app)``. We could mutate
that App in place — teleport the party, spawn hostiles next to them —
but the overworld then leaks the unused content into save files,
observation snapshots, and vision memory. Returning a *new* App from
the builder is the cleaner contract: the harness drops the original
and tests see exactly what the fixture set up.

The cost is that each builder rebuilds the dispatcher / turn
controller / GameState, but that's all done by
:func:`make_fixture_app` and totals a few hundred microseconds in
practice. The win is determinism.
"""

from __future__ import annotations

import random
from typing import Callable

from src.app import App
from src.core.entity import EntityId
from src.core.modes import UIMode
from src.core.party_state import PartyState
from src.testing.fixtures._helpers import (
    DEFAULT_ROOM_WIDTH,
    DEFAULT_ROOM_HEIGHT,
    FixtureRoom,
    clear_tiles_for_spawn,
    force_rogue_sheet,
    force_wizard_sheet,
    make_fixture_app,
    spawn_chest,
    spawn_door,
    spawn_info_npc,
    spawn_kobold,
    spawn_kobold_archer,
    spawn_recruit_npc,
    spawn_shopkeeper,
    spawn_trap,
)
from src.testing.scenarios import Scenario, SCENARIOS, register


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------
#
# Each ``_build_<name>`` function returns the App that the scenario's
# ``builder`` hands back. The pattern is the same everywhere: build a
# fixture room via :func:`make_fixture_app`, plug a ``populate``
# callback that adds the per-scenario entities, and let the helper do
# the wiring. The builders pull the App-replacing seed from the
# default-seeded harness ``rng`` so each fixture loads deterministically
# regardless of the host App's RNG state.
#
# Each builder pushes the party to a corner of the room so there's
# space for the scenario content on the opposite side. The party
# follows a deterministic placement (head-of-party at ``corner``, the
# companion-placement helper drops the rest nearby with a fixed
# random.Random(0) — see :func:`src.app._add_companions_for_player_sheet`).


def _fixture_rng(seed: int = 0) -> random.Random:
    """Return a fresh RNG for fixture construction.

    The same seed is threaded through every helper, so two builds with
    the same seed produce equal worlds (entity ids, loot rolls, party
    sheets). The default 0 keeps things deterministic across tests
    without callers needing to pass a value.
    """
    return random.Random(seed)


def _build_combat_simple(_app: App, rng: random.Random | None = None) -> App:
    def populate(room: FixtureRoom, player: EntityId, party: list[EntityId]) -> None:
        # Two kobolds, both adjacent to the player. Adjacency forces
        # the play mode to ``turn_based`` from t=0 (no exploration
        # window). Each is on a cardinal so vision can confirm them
        # without LOS edge cases.
        #
        # The deterministic companion-placement helper (random.Random(0))
        # lands one companion on ``(px+1, py)``; we evict that
        # companion before placing the kobolds so the scenario entity
        # owns the tile instead of being silently displaced (issue #77).
        px = room.world.positions.require(player).x
        py = room.world.positions.require(player).y
        kobold_tiles = ((px + 1, py), (px, py + 1))
        clear_tiles_for_spawn(
            room.world,
            tiles=kobold_tiles,
            movable=tuple(party[1:]),
            bounds=room.floor_bounds,
            avoid=((px, py),),
        )
        spawn_kobold(room.world, *kobold_tiles[0])
        spawn_kobold(room.world, *kobold_tiles[1])

    return make_fixture_app(rng=rng if rng is not None else _fixture_rng(), populate=populate)


def _build_combat_archer(_app: App, rng: random.Random | None = None) -> App:
    def populate(room: FixtureRoom, player: EntityId, _party: list[EntityId]) -> None:
        # Single ranged kobold at the opposite end of the room. The
        # RANGED AI keeps distance and shoots — at distance 6 the
        # archer is well outside the M3 melee threshold but inside
        # the AI's ``attack_range``.
        px, py = room.world.positions.require(player).x, room.world.positions.require(player).y
        archer_x = min(px + 6, room.floor_bounds[2])
        spawn_kobold_archer(room.world, archer_x, py)

    return make_fixture_app(rng=rng if rng is not None else _fixture_rng(), populate=populate)


def _build_door_locked(_app: App, rng: random.Random | None = None) -> App:
    def populate(room: FixtureRoom, player: EntityId, party: list[EntityId]) -> None:
        # Locked door one tile east of the player. The party rolls
        # Rogue (Sleight-of-Hand) so the M9 lockpick path is exercised
        # rather than the refusal banner — a tester wanting the refusal
        # path can override the player's skill set via the harness.
        # Companion-placement may have parked someone at ``(px+1, py)``
        # so we evict them before placing the door (issue #77).
        px = room.world.positions.require(player).x
        py = room.world.positions.require(player).y
        door_tile = (px + 1, py)
        clear_tiles_for_spawn(
            room.world,
            tiles=(door_tile,),
            movable=tuple(party[1:]),
            bounds=room.floor_bounds,
            avoid=((px, py),),
        )
        spawn_door(room.world, door_tile[0], door_tile[1], locked=True, pick_dc=10)

    rng = rng if rng is not None else _fixture_rng()
    return make_fixture_app(
        rng=rng,
        populate=populate,
        sheet=force_rogue_sheet(rng),
    )


def _build_trap_armed(_app: App, rng: random.Random | None = None) -> App:
    def populate(room: FixtureRoom, player: EntityId, _party: list[EntityId]) -> None:
        # Trap two tiles east — close enough to interact with but not
        # on the spawn tile (so the test can decide whether to step
        # onto it or disarm it from one tile away).
        px, py = room.world.positions.require(player).x, room.world.positions.require(player).y
        spawn_trap(room.world, px + 2, py, disarm_dc=10, damage=1)

    rng = rng if rng is not None else _fixture_rng()
    return make_fixture_app(
        rng=rng,
        populate=populate,
        sheet=force_rogue_sheet(rng),
    )


def _build_container_loot(_app: App, rng: random.Random | None = None) -> App:
    def populate(room: FixtureRoom, player: EntityId, _party: list[EntityId]) -> None:
        # Chest two tiles east with a weapon + 25gp. The combination
        # exercises both M9 OpenEntity (toggles ``Container.is_open``)
        # and the M30 ground-pickup pathway that follows.
        px, py = room.world.positions.require(player).x, room.world.positions.require(player).y
        spawn_chest(
            room.world,
            px + 2,
            py,
            items=("weapon.dagger", "consumable.healing_potion"),
            gold=25,
        )

    return make_fixture_app(rng=rng if rng is not None else _fixture_rng(), populate=populate)


def _build_npc_dialogue(_app: App, rng: random.Random | None = None) -> App:
    """Three M13 NPCs (info, recruit, shopkeeper) flanking the party.

    Lays out one NPC per cardinal so a playtest harness can step toward
    any of them, press ``e``, and drive the dialogue modal. The info
    NPC sits east, the recruit south, the shopkeeper north.
    """

    def populate(room: FixtureRoom, player: EntityId, party: list[EntityId]) -> None:
        px, py = (
            room.world.positions.require(player).x,
            room.world.positions.require(player).y,
        )
        # Spawn at +2 offsets so we don't share the tile with the
        # player, but a companion placed by the YOLO helper might
        # still land on one of these. Evict any occupants of the
        # three target tiles before spawning so collisions surface
        # cleanly (matches the pattern in _build_combat_simple).
        npc_tiles = ((px + 2, py), (px, py + 2), (px, py - 2))
        clear_tiles_for_spawn(
            room.world,
            tiles=npc_tiles,
            movable=tuple(party[1:]),
            bounds=room.floor_bounds,
            avoid=((px, py),),
        )
        spawn_info_npc(
            room.world,
            *npc_tiles[0],
            name="Old Gerda",
            line="The dungeon lies east of town -- mind the dark.",
        )
        spawn_recruit_npc(
            room.world,
            *npc_tiles[1],
            name="Karn the Wanderer",
            ask_text="I can swing for coin if you'll have me. Join up?",
            accept_text="Then you have my blade.",
        )
        spawn_shopkeeper(
            room.world,
            *npc_tiles[2],
            name="Quartermaster",
            gold=200,
            stock=(
                "weapon.shortsword",
                "armor.leather",
                "consumable.healing_potion",
            ),
        )

    return make_fixture_app(rng=rng if rng is not None else _fixture_rng(), populate=populate)


def _build_shop_basic(_app: App, rng: random.Random | None = None) -> App:
    def populate(room: FixtureRoom, player: EntityId, party: list[EntityId]) -> None:
        # Shopkeeper one tile east. Inventory carries a club and a
        # leather suit — both cheap enough for the standard 25g
        # starter wallet to buy at least one item. The shop itself is
        # never an enemy so the play mode stays in ``explore``.
        # Companion-placement may have parked someone at ``(px+1, py)``
        # so we evict them before placing the shopkeeper (issue #77).
        px = room.world.positions.require(player).x
        py = room.world.positions.require(player).y
        shop_tile = (px + 1, py)
        clear_tiles_for_spawn(
            room.world,
            tiles=(shop_tile,),
            movable=tuple(party[1:]),
            bounds=room.floor_bounds,
            avoid=((px, py),),
        )
        spawn_shopkeeper(
            room.world,
            shop_tile[0],
            shop_tile[1],
            name="Quartermaster",
            gold=200,
            stock=(
                "weapon.club",
                "weapon.shortsword",
                "armor.leather",
                "consumable.healing_potion",
            ),
        )

    return make_fixture_app(rng=rng if rng is not None else _fixture_rng(), populate=populate)


def _build_vision_corridor(_app: App, rng: random.Random | None = None) -> App:
    # Long-and-thin room: 31 tiles wide, 5 tall. The LOS radius is 10,
    # so a hostile at the far end is just outside vision at spawn —
    # the corridor is the canonical fixture for testing M19 LOS clipping.
    #
    # Note: ``hostiles_requiring_battle`` (the trigger for forced
    # turn-based mode) is global — any hostile anywhere flips the play
    # mode regardless of LOS. Visual reveal mid-walk is therefore *not*
    # the autowalk interrupt reason today; the COMBAT_STARTED guard
    # fires first. The fixture documents the geometry; the test asserts
    # what the engine actually does (turn-based at t=0, hostile hidden
    # in the snapshot).
    def populate(room: FixtureRoom, player: EntityId, party: list[EntityId]) -> None:
        # Hostile parked at the east end of the corridor.
        hostile_x = room.floor_bounds[2]
        corridor_y = (room.floor_bounds[1] + room.floor_bounds[3]) // 2
        spawn_kobold(room.world, hostile_x, corridor_y)
        # Move the leader to the west end; park companions along the
        # north wall so they don't crowd the autowalk lane.
        leader_x = room.floor_bounds[0]
        room.world.positions.require(player).x = leader_x
        room.world.positions.require(player).y = corridor_y
        north_y = room.floor_bounds[1]
        for index, entity in enumerate(party[1:], start=1):
            pos = room.world.positions.require(entity)
            pos.x = leader_x + index
            pos.y = north_y

    return make_fixture_app(
        width=31,
        height=5,
        rng=rng if rng is not None else _fixture_rng(),
        populate=populate,
        # Party position is set inside populate; the helper still needs
        # a starting cell so it doesn't trip the "off-map" guard.
        party_position=(2, 2),
    )


def _build_hostile_far(_app: App, rng: random.Random | None = None) -> App:
    # Square room large enough that a hostile parked in the far corner
    # is outside LOS at spawn. Autowalk-toward-the-corner reveals them.
    def populate(room: FixtureRoom, player: EntityId, _party: list[EntityId]) -> None:
        px, py = room.world.positions.require(player).x, room.world.positions.require(player).y
        # Drop a kobold near a far corner (room is 30x30 so distance > 10).
        far_x = room.floor_bounds[2]
        far_y = room.floor_bounds[3]
        spawn_kobold(room.world, far_x, far_y)
        # Push the party into the opposite corner for a clean walk.
        room.world.positions.require(player).x = room.floor_bounds[0]
        room.world.positions.require(player).y = room.floor_bounds[1]

    return make_fixture_app(
        width=30,
        height=30,
        rng=rng if rng is not None else _fixture_rng(),
        populate=populate,
        party_position=(2, 2),
    )


def _build_spell_encounter(_app: App, rng: random.Random | None = None) -> App:
    """Wizard leader with two kobolds within spell range.

    Exercises the M11 cast path end-to-end: the leader has a full
    spell list and slot ledger (from the Wizard sheet flowing through
    :func:`_assign_character_sheet`), and the two kobolds give the
    tester an obvious offensive-spell target plus an AOE candidate.
    """

    def populate(room: FixtureRoom, player: EntityId, _party: list[EntityId]) -> None:
        px, py = room.world.positions.require(player).x, room.world.positions.require(player).y
        # Two kobolds: one two tiles east (safe distance for Fire Bolt),
        # one diagonal so Burning Hands at the cursor cell catches both.
        spawn_kobold(room.world, px + 2, py)
        spawn_kobold(room.world, px + 3, py + 1)

    rng = rng if rng is not None else _fixture_rng()
    return make_fixture_app(
        rng=rng,
        populate=populate,
        sheet=force_wizard_sheet(rng),
    )


def _build_open_terrain(_app: App, rng: random.Random | None = None) -> App:
    # Large empty room for autowalk-to-bound: no hostiles, no doors.
    # The companions are explicitly parked along the south wall so the
    # leader has a clear west-east lane for a long autowalk — without
    # this the deterministic ``_nearby_open_position`` helper drops a
    # companion immediately east of the player and the walk halts on
    # the very first displacement ("You displaced X.").
    def populate(room: FixtureRoom, player: EntityId, party: list[EntityId]) -> None:
        left = room.floor_bounds[0]
        # Player at west wall, mid-height (away from companion row).
        room.world.positions.require(player).x = left
        room.world.positions.require(player).y = room.floor_bounds[1] + 1
        # Park each companion along the south wall, equally spaced.
        south_row = room.floor_bounds[3]
        companions = party[1:]
        for index, entity in enumerate(companions, start=1):
            position = room.world.positions.require(entity)
            position.x = left + index
            position.y = south_row

    return make_fixture_app(
        width=30,
        height=10,
        rng=rng if rng is not None else _fixture_rng(),
        populate=populate,
        party_position=(2, 4),
    )


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------

# Order matters for help discovery and CATALOG iteration but not for
# behaviour. Grouped by category: combat first, then interactions, then
# vision/movement.
_FIXTURES: tuple[tuple[str, str, Callable[[App], App], tuple[str, ...]], ...] = (
    (
        "combat_simple",
        "Two melee kobolds adjacent to the party; forces turn-based.",
        _build_combat_simple,
        ("kobold", "kobold"),
    ),
    (
        "combat_archer",
        "One ranged kobold archer parked six tiles east; tests M10 RANGED AI.",
        _build_combat_archer,
        ("kobold archer",),
    ),
    (
        "door_locked",
        "Locked door blocking the east exit; party rolls a Rogue for the M9 pick.",
        _build_door_locked,
        ("locked door",),
    ),
    (
        "trap_armed",
        "Armed trap two tiles east; M9 + M26 disarm-check exercise.",
        _build_trap_armed,
        ("trap",),
    ),
    (
        "container_loot",
        "Closed chest containing a dagger, a healing potion, and 25gp.",
        _build_container_loot,
        ("chest",),
    ),
    (
        "shop_basic",
        "Stationary shopkeeper with a stocked inventory; M12 buy/sell smoke.",
        _build_shop_basic,
        ("Quartermaster",),
    ),
    (
        "npc_dialogue",
        "Three M13 NPCs (info, recruit, shopkeeper) around the party; dialogue modal smoke.",
        _build_npc_dialogue,
        ("Old Gerda", "Karn the Wanderer", "Quartermaster"),
    ),
    (
        "vision_corridor",
        "Long thin corridor with a hostile at the east end; LOS + autowalk reveal.",
        _build_vision_corridor,
        ("kobold",),
    ),
    (
        "hostile_far",
        "Big room with a hostile parked outside LOS; autowalk reveals them.",
        _build_hostile_far,
        ("kobold",),
    ),
    (
        "open_terrain",
        "Empty 30x10 room; tests autowalk-to-bound (out_of_steps / blocked).",
        _build_open_terrain,
        (),
    ),
    (
        "spell_encounter",
        "Wizard leader plus two kobolds in spell range; M11 cast path smoke.",
        _build_spell_encounter,
        ("kobold", "kobold"),
    ),
)


def _register_all() -> tuple[str, ...]:
    """Register every fixture in :data:`_FIXTURES`, idempotently.

    The harness test suite repeatedly imports the package; we therefore
    only register a fixture once per process. Subsequent imports skip
    rather than raise on duplicate names — the registry's own
    duplicate guard would otherwise turn a benign reload into a
    ``ValueError``.
    """
    names: list[str] = []
    for name, description, builder, entities in _FIXTURES:
        names.append(name)
        if name in SCENARIOS:
            continue
        register(
            Scenario(
                name=name,
                builder=builder,
                description=description,
                expected_entities=entities,
            )
        )
    return tuple(names)


CATALOG: tuple[str, ...] = _register_all()
