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
| 2    | Dungeon Level 1     | Kobold scouts (glyph `k`, WANDER) guard the approach. Watch for a pressure plate trap on the path. |
| 3    | Dungeon Level 2     | Kobold soldiers (glyph `k`, CHASE) hold the barracks. Higher-DC trap; locked strongbox with a longsword. |
| 4    | Dungeon Level 3     | A kobold elite escort guards the throne. Defeat the warlord boss (`K`) — his corpse drops the golden chalice. |
| 5    | Anywhere            | Pick up the golden chalice (press `,` on the corpse tile). The quest flips to **completed**.        |
| 6    | (automatic)         | Each party member receives 100 gold and 200 XP; the level-up modal pops if the threshold is crossed (M25). |

The dungeon levels escalate: scouts → soldiers → elite + boss; trap
and lock DCs go 8/10 → 12/12 → 15/15; loot scales from a single
healing potion + a few gp on L1 to two healing potions + 40 gp + the
chalice in the warlord's strongbox on L3. The warlord himself is
tuned so 4 level-1 PCs reliably win the fight in 4-6 rounds when they
spend a healing potion or two; without burning resources he can down
a PC, which is the M15 acceptance bar.

The completion check fires the instant both conditions hold. Picking
up the chalice off the corpse triggers it because the corpse loot
spawns when the boss dies; if you somehow loot the chalice before the
boss is dead (e.g. via debug spawn), the quest waits on the kill.

The quest giver's dialogue tracks the quest state: before accepting,
Captain Tane delivers the pitch. After accepting, returning to him
surfaces the in-flight reminder ("The Sunken Gate lies east...") so
the pitch is not repeated. After completing the quest, he greets the
party with an acknowledgment of the finished work.

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
