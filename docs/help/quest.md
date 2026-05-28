# Quests (M14)

The quest layer wires content together for the vertical slice. A quest
is typed configuration plus per-party progress: there is no scripting
language and no in-game quest log UI yet (M17 / M25 will surface it).
The single quest the build ships with is **The Sunken Gate** — the
hook that connects the town, the dungeon, and the boss.

## The Sunken Gate

| Step | Where               | What happens                                                                                        |
| ---  | ---                 | ---                                                                                                 |
| 1    | Town (tavern)       | Captain Tane offers the quest. Choose "Yes, I'll take it." to accept.                               |
| 2    | Dungeon Level 3     | Defeat the kobold warlord (boss creature, glyph `K`). His corpse drops the golden chalice.          |
| 3    | Anywhere            | Pick up the golden chalice (press `,` on the corpse tile). The quest flips to **completed**.        |
| 4    | (automatic)         | Each party member receives 100 gold and 200 XP; the level-up modal pops if the threshold is crossed (M25). |

The completion check fires the instant both conditions hold. Picking
up the chalice off the corpse triggers it because the corpse loot
spawns when the boss dies; if you somehow loot the chalice before the
boss is dead (e.g. via debug spawn), the quest waits on the kill.

## Quest states

| State          | Meaning                                                                              |
| ---            | ---                                                                                  |
| `not_offered`  | The party has not heard about the quest. Implicit default; not stored explicitly.    |
| `offered`      | The party has been pitched but has not yet accepted. Reserved for future content.    |
| `accepted`     | The party committed to the quest. Completion criteria are checked on every kill/pickup. |
| `completed`    | All criteria satisfied. Rewards applied. Terminal.                                   |
| `failed`       | Reserved — no current quest can fail.                                                |

## Player-facing keys

The quest path uses the existing controls — no new keys.

| Key | Mode      | Effect (quest-relevant)                                            |
| --- | ---       | ---                                                                |
| `e` | play      | Talk to Captain Tane (or any other quest giver). Offers the quest. |
| `,` | play      | Pick up the chalice from the warlord's corpse.                     |

## Save / load

Quest state lives on the party (`PartyState.quests`, a
`PartyQuestLog` mapping quest id to state). It serializes cleanly via
the existing party JSON round-trip, so saving mid-quest and reloading
preserves the exact same state.

## Architectural notes

- `src/core/quest.py` defines the typed model (`Quest`,
  `QuestObjective`, `QuestReward`, `PartyQuestLog`, `QuestRegistry`).
  Module-level `QUESTS` holds every quest the build knows about.
- Quest accept rides on the M13 dialogue bus: the
  `AcceptQuestEffect(quest_id)` dialogue option effect is resolved by
  `App._apply_accept_quest_effect`, which flips the log and emits the
  accept message + victory condition.
- Quest completion checks live in `src/core/effects_applier.py`:
  `_apply_kill_entity` re-evaluates progress after a boss-marked
  entity dies; `_apply_transfer_inventory` re-evaluates after a
  pickup that contained the treasure item.
- Boss creatures carry a `BossMarker` component (a stable string
  token) that the kill hook matches against `QuestObjective.boss_marker`.
- Rewards are emitted as world effects via the same effect applier so
  save/load and the message log stay consistent.
