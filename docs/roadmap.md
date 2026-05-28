# FRAI Vertical Slice Roadmap

This roadmap is the lead-agent milestone DAG for turning the current Python terminal RPG into a playable vertical slice. It should be updated after each milestone merge.

## M0 Baseline

Validation: `uv run pytest` on 2026-05-27: 55 passed.

Current architecture:

- `src/app.py`: top-level runtime state, input handling, effect application, party turn rotation, enemy activations, app restart, curses loop.
- `src/core/world.py`: ECS-like world with typed component stores and tile grid.
- `src/core/components.py`: save-relevant components such as position, presentation, blockers, character, combat stats, weapon, armor, faction.
- `src/core/actions.py`: structured action attempts/commands.
- `src/core/effects.py`: structured effects applied by `App`.
- `src/core/modes.py`: UI/game modes, currently start choice, normal, quit confirm, character creation, game over, inventory.
- `src/systems/*`: dispatcher systems for start, character creation, quit, inventory, movement, combat, game over, rendering, messages, obstruction.
- `src/map/room_builder.py`: handcrafted room plus side passages, two frogs, player placement.
- `src/systems/render_system.py`: terminal projection and modal rendering.
- `tests/*`: small unit/integration tests for current app flow, character creation, combat, input mapping, rendering helpers, and systems.

Current gameplay summary:

- Python terminal-only UI using curses.
- Start screen supports character creation or YOLO.
- Character creation already exposes all D&D 5.1 SRD classes and core races with minimal class/race data.
- Game world is a room-like dungeon map with side passages.
- Turn-based battle mode is automatically triggered whenever any hostile with combat stats exists.
- Explore mode returns when all hostiles are gone.
- Party currently contains the player plus one generated companion.
- Outside battle, the player leads and party members follow previous positions.
- In battle, each player-controlled party member gets a turn, then enemies act.
- Battle movement consumes feet from a per-activation movement budget.
- Attacking consumes the current activation action.
- Movement into a hostile converts into an attack attempt.
- Party members can be displaced by moving into their tile.
- Enemies use hardcoded chase-and-melee behavior.
- Inventory UI only projects equipped armor/weapon.

Major current gaps:

- No voluntary turn-based mode.
- No explicit bonus action, reaction, or extra-action resources.
- Turn/action state currently lives directly on `App`, not in a reusable turn model.
- Enemy AI is hardcoded in `App.run_enemy_activations`.
- Map/content is a single room world, not overworld/town/forest/dungeon levels.
- Terrain has blocking only, no movement cost/color projection distinction.
- No NPCs, dialogue, recruitment interactions, shops, traps, locks, doors, containers, quest state, spells/effects, inventory items, gold, or save/restore.
- Rendering already avoids mutating world state but has no color use yet.
- Persistence is absent; future state needs stable IDs and explicit schema/defaulting.

Architecture direction:

- Keep world logic independent from terminal rendering.
- Keep input mapping separate from action resolution.
- Keep content definitions typed and data-driven where repeated.
- Treat actions as structured attempts and effects/events as first-class runtime objects.
- Keep saveable state explicit and avoid raw unserializable references.
- Preserve current testability with tiny deterministic fixtures.
- Do not deepen full SRD class mechanics until action economy, spells/effects, and persistence boundaries are stable.

## Milestone DAG

| ID | Title | Dependencies | Owner / Branch | Status | Validation Target | PR |
| --- | --- | --- | --- | --- | --- | --- |
| M0 | Repository reconnaissance and baseline | none | lead / `main` | complete | `uv run pytest` | n/a |
| M1 | Abstraction discipline pass | M0 | lead / `agent/m1-abstraction-pass` | complete | `uv run pytest` | #1 |
| M2 | Test harness and tiny world fixtures | M0 | Cicero / `agent/m2-test-fixtures` | complete | `uv run pytest` | #2 |
| M3 | Voluntary turn-based mode | M1, M2 | lead / `agent/m3-voluntary-turn-mode` | complete | `uv run pytest` | #3 |
| M4 | Action economy expansion | M1, M2 | unassigned | pending | action/move/bonus/reaction reset tests | pending |
| M5 | Race/class creation foundation | M1, M2 | unassigned | pending | all race/class level-1 creation tests | pending |
| M6 | Party composition/adaptive companions | M5 | unassigned | pending | coverage logic tests | pending |
| M7 | Map/terrain model expansion | M1, M2 | unassigned | pending | terrain movement/color projection tests | pending |
| M8 | World content skeleton | M7 | unassigned | pending | connected town/forest/dungeon tests | pending |
| M9 | Interaction primitives | M4, M7 | unassigned | pending | door/lock/trap/container interaction tests | pending |
| M10 | Combat variety and AI behaviors | M4, M7 | unassigned | pending | AI legal-action tests | pending |
| M11 | Basic spells and effects | M4, M5 | unassigned | pending | resource/attack/save/heal/area tests | pending |
| M12 | Items/equipment/shop | M5, M7 | unassigned | pending | buy/sell/equip tests | pending |
| M13 | NPC and minimal dialogue | M6, M8 | unassigned | pending | info/recruit/shopkeeper interaction tests | pending |
| M14 | Quest path | M8, M10, M11, M12, M13 | unassigned | pending | quest accept/boss/treasure/victory tests | pending |
| M15 | Boss/villain and dungeon balancing | M10, M11, M14 | unassigned | pending | boss/content/balance smoke tests | pending |
| M16 | Save/restore architecture | M1, M5, M8, M12 | unassigned | pending | save/load and old-save default tests | pending |
| M17 | UI polish for playtest | M3, M4, M8, M12, M13, M16 | unassigned | pending | modal/message/status/render tests | pending |
| M18 | End-to-end integration confidence | M14, M15, M16, M17 | unassigned | pending | character-to-victory integration tests | pending |

## Milestone Detail

M1 - Abstraction discipline pass:

- Clarify `App` boundaries versus reusable game/turn/session state.
- Preserve explore vs battle semantics.
- Preserve party following and battle party turn rotation.
- Separate input command, world intent, action resource checks, rendering projection, and effect application as much as possible without feature explosion.

M2 - Test harness and tiny world fixtures:

- Add deterministic tiny map fixture helpers.
- Add actor/party/enemy fixture helpers.
- Add action-resolution helper tests.
- Keep terminal rendering excluded from core tests.

M3 - Voluntary turn-based mode:

- Add player command for entering/exiting turn-based mode when legal.
- Preserve hostile-triggered battle mode.
- Ensure exit rules do not bypass nearby hostile combat.

M4 - Action economy expansion:

- Extend activation resource tracking to action, movement, bonus action, reaction, and extra-action hooks.
- Keep movement/action consumption distinct.
- Reset resources at correct turn boundaries.

M5 - Race/class creation foundation:

- Keep all SRD races/classes selectable.
- Promote class/race data enough to support level-1 HP, basic proficiencies, starting equipment, and later spell/resource hooks.
- Do not attempt full SRD class mechanics in this milestone.

M6 - Party composition/adaptive companions:

- Create classic party of four based on player class role coverage.
- Player rogue should get martial/divine/arcane support.
- Player cleric should get rogue/martial/arcane support.
- All player classes should produce four named members.

M7 - Map/terrain model expansion:

- Add terrain catalog for overworld, forest, town, dungeon, walls, water/blocked, and difficult terrain.
- Add movement costs and color/render token projection.
- Keep color as rendering projection, not game state mutation.

M8 - World content skeleton:

- Build connected overworld, town, forest, dungeon entrance, and three dungeon levels.
- Handcrafted content is acceptable if it remains generation-compatible.

M9 - Interaction primitives:

- Add generic interaction attempts for doors, locks, traps, and containers.
- Consume appropriate action resource.
- Keep data-driven interaction state in components/config.

M10 - Combat variety and AI behaviors:

- Move enemy AI out of `App`.
- Add chase, flee, wander/random, and simple ranged/caster-ish behavior where feasible.
- Ensure AI chooses legal actions and does not crash when blocked.

M11 - Basic spells and effects:

- Add representative spell action path: attack spell, saving throw spell, healing spell, area/control effect.
- Consume spell resources through the action system.
- Avoid one-off UI special cases.

M12 - Items/equipment/shop:

- Add inventory items, gold, equipment slots, weapon/armor stats, consumables, and shop buy/sell/equip paths.
- Keep shop inventory and player inventory separate.

M13 - NPC and minimal dialogue:

- Add info NPCs, recruitable adventurers, and shopkeeper interaction.
- Human NPCs render as `@`; joined party members render as `#`.
- Dialogue remains simple deterministic state, not rendering hardcode.

M14 - Quest path:

- Add a town/tavern mission hook leading to dungeon boss treasure.
- Add quest acceptance and victory flag.

M15 - Boss/villain and dungeon balancing:

- Add level-1-targeted boss-like creature, meaningful encounters, and significant treasure.
- Keep balance assumptions testable.

M16 - Save/restore architecture:

- Add schema version, stable IDs, migration/defaulting strategy, and JSON-safe save data.
- Verify old minimal save defaults and loaded games can continue.

M17 - UI polish for playtest:

- Add usable status bar, message log/more prompt behavior, modal menus, inventory/shop/dialogue screens, and color pass.
- Keep UI commands distinct from world intents.

M18 - End-to-end integration confidence:

- Add small integration tests covering character creation, party creation, shop, recruit, dungeon entry, sample combat, boss defeat/victory, and mid-run save/load.
