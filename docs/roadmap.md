# FRAI Vertical Slice Roadmap

This roadmap is the lead-agent milestone DAG for turning the current Python terminal RPG into a playable vertical slice. It must be updated after each milestone merge.

## Standing policies

1. **`main` is always playable.** `uv run pytest` is green and `uv run python -m src.main` reaches the start screen without crashing. If either breaks after a merge, the next assignment is the fix.
2. **Lead keeps `main` current.** Before reviewing, merging, or assigning, the lead has run `git fetch --all --prune` and `git pull` on `main`.
3. **One `/playtest` agent runs at all times** once the agentic-playtest prerequisites (#13 observation, #29 cmdscripts, #30 harness, ≥1 fixture from #31) are met. Playtester rotates targets; the role is standing, not one-off.
4. **Help and observation are kept current.** Every PR that changes player-facing commands, modes, or agent-visible state must update `?` help and the structured observation output. Reviewers enforce this.
5. **Container/Inventory unification (#35)** scheduled after PR #10 lands.

## M0 Baseline

Validation: `uv run pytest` on 2026-05-27: 250 passed.

Current architecture (post M0-M10 + skill rewrite):

- `src/app.py`: top-level runtime state, input handling, effect application, party turn rotation, enemy activations, app restart, curses loop.
- `src/core/world.py`: ECS-like world with typed component stores and tile grid.
- `src/core/components.py`: save-relevant components including position, presentation, blockers, character, combat stats, weapon, armor, faction, door/lock/trap/container.
- `src/core/actions.py`: structured action attempts/commands.
- `src/core/effects.py`: structured effects applied by `App`.
- `src/core/modes.py`: UI/game modes.
- `src/core/party.py`: deterministic four-member party composition based on player class role coverage.
- `src/systems/*`: dispatcher systems for start, character creation, quit, inventory, movement, combat, game over, rendering, messages, obstruction, interaction, AI.
- `src/map/room_builder.py` and world content: handcrafted overworld, town, forest, dungeon entrance, three dungeon levels.
- `src/systems/render_system.py`: terminal projection and modal rendering with color.
- `tests/*`: small unit/integration tests covering current systems.

Current gameplay summary:

- Python terminal-only UI using curses.
- Start screen supports character creation or YOLO.
- Character creation exposes all D&D 5.1 SRD classes and core races with minimal class/race data.
- Adaptive four-member party generated based on player class role coverage.
- Overworld + town + forest + dungeon (3 levels) skeleton.
- Terrain catalog with movement cost and color projection.
- Turn-based mode triggered by enemy presence OR voluntary entry.
- Action economy: action, movement, bonus action, reaction, extra-action hooks.
- Enemy AI behaviors: chase, flee, wander, simple ranged/caster.
- Interaction primitives: doors, locks, traps, containers via public `e` key with refusal messages for check-required interactions.
- M12 in review (PR #10): inventory, equipment, shop buy/sell.

Major remaining gaps (now tracked as issues #12–#35):

- No vision/LOS/memory rendering (#17).
- No targeting/examine/auto-walk (#22, #24, #23).
- No conditions/duration system (#19).
- No skill check machinery (#15).
- No time/clocks model (#14).
- No faction model (#16).
- No downed/death-saves loop (#26).
- No leveling (#27).
- No loot/corpses on ground (#20).
- No help screen / `?` online help (#28, #32).
- No agent-readable observation mode (#13), no command scripting (#29), no playtest harness (#30), no fixtures (#31).
- No rest/shelter zones (#21).
- Save/restore in review (M16) — JSON save/load via `src.core.save`.

Architecture direction:

- Keep world logic independent from terminal rendering.
- Keep input mapping separate from action resolution.
- Keep content definitions typed and data-driven where repeated.
- Treat actions as structured attempts and effects/events as first-class runtime objects.
- Keep saveable state explicit and avoid raw unserializable references.
- Preserve current testability with tiny deterministic fixtures.
- Do not deepen full SRD class mechanics until action economy, spells/effects, and persistence boundaries are stable.

## Milestone DAG

| ID  | Title                                              | Dependencies                | Issue / PR        | Status     |
| --- | -------------------------------------------------- | --------------------------- | ----------------- | ---------- |
| M0  | Repository reconnaissance and baseline             | none                        | n/a               | complete   |
| M1  | Abstraction discipline pass                        | M0                          | #1                | complete   |
| M2  | Test harness and tiny world fixtures               | M0                          | #2                | complete   |
| M3  | Voluntary turn-based mode                          | M1, M2                      | #3                | complete   |
| M4  | Action economy expansion                           | M1, M2                      | #6                | complete   |
| M5  | Race/class creation foundation                     | M1, M2                      | #5                | complete   |
| M6  | Party composition/adaptive companions              | M5                          | #9                | complete   |
| M7  | Map/terrain model expansion                        | M1, M2                      | #4                | complete   |
| M8  | World content skeleton                             | M7                          | #8                | complete   |
| M9  | Interaction primitives                             | M4, M7                      | #7                | complete   |
| M10 | Combat variety and AI behaviors                    | M4, M7                      | #11               | complete   |
| M11 | Basic spells and effects                           | M4, M5                      | pending           | complete   |
| M12 | Items/equipment/shop                               | M5, M7                      | #10               | complete   |
| M13 | NPC and minimal dialogue                           | M6, M8                      | pending           | complete   |
| M14 | Quest path                                         | M8, M10, M11, M12, M13      | pending           | pending    |
| M15 | Boss/villain and dungeon balancing                 | M10, M11, M14               | pending           | in review  |
| M16 | Save/restore architecture                          | M1, M5, M8, M12             | #67               | complete   |
| M17 | UI polish for playtest                             | M3, M4, M8, M12, M13, M16   | pending           | pending    |
| M18 | End-to-end integration confidence                  | M14, M15, M16, M17          | pending           | pending    |
| M19 | Vision, lighting, LOS, and memory rendering        | M1, M2, M7                  | #54               | complete   |
| M20 | Targeting mode                                     | M4, M19                     | #75               | complete   |
| M21 | Examine and look command                           | M19, M20                    | #24               | complete   |
| M22 | Pathing / auto-walk                                | M3, M7, M19                 | #62               | complete   |
| M23 | Stealth, noise, and perception                     | M4, M19, M26, M28           | #25               | in review  |
| M24 | Conditions, statuses, and durations                | M4, M27                     | #66               | complete   |
| M25 | Leveling, XP, and rewards                          | M5, M10, M14                | #27               | in review  |
| M26 | Skill checks and DC checks                         | M5                          | #50               | complete   |
| M27 | Time and clocks                                    | M4                          | #49               | complete   |
| M28 | Faction and hostility model                        | M3, M6, M13                 | #72               | complete   |
| M29 | Downed, unconscious, death saves, and recovery     | M24, M34                    | #26               | in review  |
| M30 | Loot containers, corpses, and dropped items        | M9, M12                     | #63               | complete   |
| M31 | Command help and keybinding screen                 | M17                         | #28               | unassigned |
| M32 | Error and message discipline                       | M17                         | #18               | unassigned |
| M33 | Debug/dev tools                                    | M2                          | #55               | complete   |
| M34 | Rest system and shelter zones                      | M4, M7, M11, M27            | #21               | complete   |
| M35 | Agent-readable observation mode                    | (foundation)                | #58               | complete   |
| M36 | Command scripting and agent input mode             | M22, M35                    | #68               | complete   |
| M37 | Playtest harness                                   | M35, M36                    | #71               | complete   |
| M38 | Scenario fixtures for playtesting                  | M37                         | #76               | complete   |
| M39 | Online help (`?`)                                  | M31                         | #32               | unassigned |
| M40 | Playtest bug-report workflow                       | M35, M37                    | #74               | complete   |
| M41 | Maintain-one-playtester process                    | M35, M36, M37, M38          | #34               | unassigned |
| M42 | Unify Container with Inventory                     | M12                         | #53               | complete   |
| M43 | Extract EffectApplier / WorldMutator               | M1                          | #47               | complete   |
| M44 | Extract TurnController / ActivationSystem          | M4, M43                     | #57               | complete   |
| M45 | PartyState world abstraction                       | M1, M6, M44                 | #60               | complete   |
| M46 | ActionContext / ResolvedAttempt                    | M43, M44                    | #39               | complete   |
| M47 | Split UIMode and PlayMode                          | M1                          | #51               | complete   |
| M48 | AwarenessSystem query service                      | M1                          | #46               | complete   |
| M49 | GameState container                                | M43, M44, M45, M47          | #61               | complete   |

## Architectural refactor priority

An external review (2026-05-27) identified that `src/app.py` is becoming a god object and the dispatcher lacks phases. Feature work (M19-M42) should not pile on more `App` complexity. Refactor milestones (M43-M49) are therefore TOP priority and partly front-loaded.

**Refactor wave 1 (dispatch in parallel — minimal file collisions):**

- **#36 M43 EffectApplier / WorldMutator** (gut the `App.apply_effects` isinstance chain). Sequential within this list — biggest scope.
- **#40 M47 Split UIMode and PlayMode** (independent, small).
- **#41 M48 AwarenessSystem query service** (independent, small).

**Refactor wave 2 (after M43 lands):**

- **#37 M44 TurnController / ActivationSystem** (extracts turn semantics from `App`; rebases on M43).
- **#38 M45 PartyState** (after M44).
- **#39 M46 ActionContext / ResolvedAttempt** (after M43, M44).

**Refactor wave 3 (after M44, M45, M47 land):**

- **#42 M49 GameState container** (consolidates the runtime aggregate; precondition for save/load and observation).

## Feature work unblocked after refactor stabilizes

Once refactor wave 1 is in, the following feature foundations can run safely in parallel:

- **#12 M33 Debug/dev tools** (M2 done; uses extracted EffectApplier).
- **#13 M35 Observation mode** (no hard refactor deps but cleaner after M49; can start with thin shim).
- **#14 M27 Time and clocks** (M4 done; integrates with M44).
- **#15 M26 Skill checks** (M5 done; resolves M9's check-required refusal path).
- **#17 M19 Vision/LOS** (M1, M2, M7 done; benefits from M48 AwarenessSystem).
- **#18 M32 Error/message discipline** (UI polish track).
- **#20 M30 Loot/corpses** (M9, M12 done).
- **#35 M42 Container/Inventory unification** (M12 just landed).

## M12 follow-ups (small, can be assigned to junior agents)

- **#43** M12 follow-up: `unequip_item` primitive.
- **#44** M12 follow-up: enforce or remove `ItemDefinition.max_stack`.
- **#45** M12 follow-up: replace `weapon_name`/`armor_name` string coupling.

## Milestone Detail

(See each issue body for full scope, non-goals, acceptance tests, and architectural notes.)

M1 - Abstraction discipline pass: Clarify `App` boundaries vs reusable game/turn/session state. Preserve explore vs battle semantics. Preserve party following and battle party turn rotation. Separate input command, world intent, action resource checks, rendering projection, and effect application.

M2 - Test harness and tiny world fixtures: deterministic tiny map / actor / party / enemy fixture helpers; action-resolution helper tests; terminal rendering excluded from core tests.

M3 - Voluntary turn-based mode: player command for entering/exiting turn-based mode when legal; preserves hostile-triggered combat.

M4 - Action economy: action, movement, bonus action, reaction, extra-action; reset at correct turn boundaries.

M5 - Race/class creation foundation: every SRD race/class selectable; minimal mechanical data for level 1.

M6 - Party composition: adaptive four-person party covering martial / divine / arcane / expert roles based on player class.

M7 - Map/terrain model: terrain catalog with movement cost, blocking, color projection.

M8 - World content skeleton: overworld, town, forest, dungeon entrance, three dungeon levels.

M9 - Interaction primitives: doors, locks, traps, containers via generic `InteractAttempt` action. Public `e` path emits refusal messages for check-required interactions until skill-check machinery (M26) is in.

M10 - Combat variety and AI: chase, flee, wander, simple ranged/caster behaviors.

M11 - Basic spells and effects: representative attack spell, save spell, healing spell, area/control effect. Consumes spell resources through action system.

M12 - Items/equipment/shop: inventory, equipment slots, weapon/armor stats, consumables, shop buy/sell/equip. PR #10 in review.

M13 - NPC and minimal dialogue: info NPCs, recruitable adventurers, shopkeeper interaction. Human NPCs render as `@`, joined party as `#`.

M14 - Quest path: town/tavern hook → dungeon boss treasure → victory flag.

M15 - Boss/villain and dungeon balancing: level-1-targeted three-level dungeon with boss creature, meaningful encounters, significant treasure.

M16 - Save/restore architecture: schema version, stable ids, migration/defaulting, JSON-safe save data.

M17 - UI polish for playtest: status bar, message log/more prompt, modal menus, inventory/shop/dialogue screens, color pass.

M18 - End-to-end integration: character creation → party creation → shop → recruit → dungeon entry → sample combat → boss defeat → victory → mid-run save/load.

M19–M42: see each linked issue.

## Roadmap update protocol

After every PR merge, the lead must:

1. Flip the row's Status to `complete` (or note follow-up issues).
2. Add the PR number.
3. Mark newly unblocked milestones in the "Unblocked work right now" section.
4. Re-run `/assign` to dispatch the next batch.
5. Trigger `/playtest` smoke on the merged change once prerequisites exist.
