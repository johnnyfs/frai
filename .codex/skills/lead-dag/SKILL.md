# /lead-dag - Lead Agent Checklist/DAG Skill

Use this continuously.

## Rules

1. Maintain a milestone DAG.
2. Every task has:
   - id
   - title
   - dependencies
   - owner/branch
   - status
   - validation target
   - PR link if applicable
3. Assign all unblocked work up to available parallelism.
4. Do not assign overlapping files/abstractions in parallel unless necessary.
5. Prefer architecture/refactor foundations before feature fan-out.
6. After each merge, recompute unblocked work.
7. Spawn reviewer for every PR.
8. Track remaining checklist until empty.
9. Do not stop when "playable enough" unless all required milestones are done or explicitly blocked.
10. If blocked, write:
    - blocker
    - why it blocks
    - proposed unblock task
    - smallest fallback

## Lead Loop

- Inspect repo.
- Create/update DAG.
- Assign unblocked tasks.
- Monitor PRs.
- Review via reviewer subagent.
- Merge validated work.
- Update DAG.
- Repeat until complete.

## Milestone DAG

M0 - Repository reconnaissance and baseline

Deps: none

Goal: understand current architecture, tests, run commands, major gaps.

Outputs:

- architecture map
- current gameplay summary
- test baseline
- milestone DAG committed as `docs/roadmap.md`

M1 - Abstraction discipline pass

Deps: M0

Goal: clean current core concepts without feature explosion.

Targets:

- App/game state boundaries
- explore vs turn-based mode
- party state
- actor/action state
- input command vs world intent
- rendering projection boundaries

Tests:

- current behavior preserved

M2 - Test harness and tiny world fixtures

Deps: M0

Goal: make future systems testable.

Targets:

- tiny map fixture
- actor/party fixture
- enemy encounter fixture
- action-resolution helper tests
- terminal rendering excluded from core tests

M3 - Voluntary turn-based mode

Deps: M1, M2

Goal: player can enter/exit turn-based mode when legal.

Tests:

- voluntary mode with no enemies
- enemy-triggered mode still works
- exit rules
- party turns remain coherent

M4 - Action economy expansion

Deps: M1, M2

Goal: strict turn model with action, movement, bonus action, reaction/extra-action hooks.

Tests:

- movement consumed
- action consumed
- bonus action consumed
- invalid double-use blocked
- turn reset works

M5 - Race/class creation foundation

Deps: M1, M2

Goal: every SRD race/class selectable with minimal mechanically meaningful data.

Targets:

- race catalog
- class catalog
- character draft/factory
- starting abilities/proficiencies/HP/basic equipment

Tests:

- every race/class creates valid level 1 character

M6 - Party composition/adaptive companions

Deps: M5

Goal: generate classic four-person party coverage based on player class.

Tests:

- rogue player gets martial/divine/arcane support
- cleric player gets rogue/martial/arcane support
- all player classes produce party of four
- names assigned

M7 - Map/terrain model expansion

Deps: M1, M2

Goal: terrain types sufficient for overworld, forest, dungeon, movement restrictions.

Targets:

- terrain catalog
- movement cost/blocking
- color projection
- overworld/dungeon rendering distinction

Tests:

- blocked terrain blocks
- difficult terrain consumes correctly
- colors/render tokens projected without mutating world

M8 - World content skeleton

Deps: M7

Goal: explorable overworld, town, forest, dungeon entrance, 3 dungeon levels.

Notes:

- Can be handcrafted now, generation-compatible later.

Tests:

- locations connected
- dungeon levels connected
- party can reach each required area

M9 - Interaction primitives

Deps: M4, M7

Goal: doors, locks, traps, containers, interact action.

Tests:

- locked door blocks
- pickable lock can open
- failed pick remains locked
- trap triggers
- trap can damage
- trap can be disarmed if supported

M10 - Combat variety and AI behaviors

Deps: M4, M7

Goal: extensible enemy AI types.

Targets:

- chase
- flee
- wander/random
- simple ranged/caster if feasible

Tests:

- each behavior chooses legal action
- enemies act during enemy phase
- no AI action crashes when blocked

M11 - Basic spells and effects

Deps: M4, M5

Goal: representative 5.1 spell mechanics.

Targets:

- attack spell
- saving throw spell
- healing spell
- area/control effect
- concentration/status hook if feasible

Tests:

- spell slots/resources consumed
- spell attack resolves
- save resolves
- healing applies
- area targets multiple entities

M12 - Items/equipment/shop

Deps: M5, M7

Goal: simple identifiable equipment and buy/sell.

Targets:

- inventory
- equipment slots
- weapon/armor stats
- consumables
- shopkeeper interaction

Tests:

- buy transfers item/gold
- sell transfers item/gold
- equip changes stats
- invalid equip blocked

M13 - NPC and minimal dialogue

Deps: M6, M8

Goal: simple deterministic interactions.

Targets:

- info NPC says one useful thing
- recruitable adventurer asks to join
- shopkeeper enters shop UI

Tests:

- dialogue option produces expected state
- recruit adds party member
- NPC render rules @ vs joined #

M14 - Quest path

Deps: M8, M10, M11, M12, M13

Goal: simple quest from town/tavern to dungeon boss treasure.

Tests:

- quest can be accepted
- dungeon boss exists
- treasure exists
- victory condition can be met

M15 - Boss/villain and dungeon balancing

Deps: M10, M11, M14

Goal: level-1-targeted 3-level dungeon with boss-like creature.

Tests:

- boss has distinct behavior/stats
- dungeon has meaningful encounters
- treasure reward significant
- encounters are not obviously impossible by rules assumptions

M16 - Save/restore architecture

Deps: M1, M5, M8, M12

Goal: backward-compatible save design.

Targets:

- schema/version field
- stable ids
- migration/defaulting strategy
- avoid unserializable raw object links

Tests:

- save then load preserves current game
- older minimal save can load with defaults
- loaded game can continue

M17 - UI polish for playtest

Deps: M3, M4, M8, M12, M13, M16

Goal: terminal experience usable enough.

Targets:

- status bar
- message log / more prompt
- modal menus
- inventory/shop/dialogue screens
- color pass

Tests:

- modal captures input
- messages paginate/block where needed
- viewport/status render without core mutation

M18 - End-to-end integration confidence

Deps: M14, M15, M16, M17

Goal: tests prove likely playable quest path.

Tests:

- character creation to town state
- party creation
- shop transaction
- recruit interaction
- enter dungeon
- resolve sample combat
- boss defeat/victory flag
- save/load mid-run

## Prompt Correction

Do not assign "all classes/races" and "full SRD fidelity" before abstractions are cleaned. The DAG must force minimal valid representation for all classes/races first, then deepen mechanics class by class only after action economy, spells, and effects are stable.
