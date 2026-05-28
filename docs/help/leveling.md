# Leveling (M25)

Experience points (XP), level thresholds, and the level-up modal turn
clean kills and quest rewards into stronger party members. The system
is a tiny SRD-lite layer: thresholds match the D&D 5.1 SRD up to
level 3, and per-class HP and spell-slot growth follow the SRD
"fixed gain" option.

## XP table

| Level | Total XP to reach |
| ----- | ----------------- |
| 1     | 0 (starting)      |
| 2     | 300               |
| 3     | 900               |

`MAX_LEVEL` is 3 for the vertical slice. Higher entries exist in the
table (`src/core/leveling.XP_THRESHOLDS`) so a future content batch
can lift the cap without touching code, but the engine clamps
level-up application to 3.

## How XP is granted

| Source                 | When                                    | Amount                              |
| ---                    | ---                                     | ---                                 |
| Combat kill            | Any enemy creature dies                 | CR-based pool, split across living party members |
| Quest reward           | A quest flips to `completed`            | `xp_per_member` from the quest's reward          |

The CR-to-XP table lives in `src/core/leveling.XP_BY_CR`. Current
encounters bucket as:

| Creature kind         | CR    | XP pool (party share at 4) |
| ---                   | ---   | ---                        |
| frog / rat / bat      | 0     | 10 (2 each)                |
| kobold / kobold_archer | 1/8  | 25 (6 each)                |
| goblin                | 1/4   | 50 (12 each)               |
| boss_kobold_warlord   | 2     | 450 (112 each)             |

Unrecognised kinds fall back to a small default (25) so playtesters
notice the omission rather than getting nothing.

### Quest trajectory

The M14 quest "The Sunken Gate" rewards 200 XP per party member on
completion. On its own that is below the level-2 threshold (300), so
the party will need to clear a few stragglers on the way out before
they ding. The boss kill itself awards ~112 XP per member at a party
of four; combined with the quest reward that comfortably crosses
level 2 and edges toward level 3.

## Level-up modal

When a member's XP crosses the next threshold, an
`m` "<name> is ready to level up!" message fires and the level-up
modal (`UIMode.level_up`) pops the next time the play screen has
focus. The modal shows:

- The member's name and class.
- Current HP → projected HP (with HP gain in parentheses).
- Current proficiency bonus → projected proficiency bonus.
- New spell-slot ledger for casters (or "no change" for martials).

| Key       | Effect                                                     |
| ---       | ---                                                        |
| `y`       | Confirm. Applies the `LevelUp` effect; HP/slots/proficiency update. |
| `Enter`   | Same as `y`.                                               |
| `q` / `Esc` | Dismiss. Marker stays attached; modal reopens on the next cue. |

If multiple party members are pending simultaneously, the modal
reopens after each confirm so the player consumes them one at a time
in recruitment order.

## Components

| Component             | Purpose                                                          |
| ---                   | ---                                                              |
| `ExperiencePoints`    | Per-actor XP ledger; mirrors `CharacterSheet.level`.             |
| `LevelUpAvailable`    | Marker that `target_level` is unlocked and waiting on a confirm. |

Both round-trip through `World.to_dict` / `from_dict` via the
component-store registry. Pre-M25 saves load cleanly because the
stores default to empty.

## Effects

| Effect      | Behaviour                                                    |
| ---         | ---                                                          |
| `GrantXP`   | Adds XP, creates the ledger if missing, attaches `LevelUpAvailable` on threshold cross. |
| `LevelUp`   | Bumps the sheet level, adds HP gain, refreshes proficiency, installs new spell slots. |

The quest reward applier and the kill applier both emit `GrantXP`
into the standard pipeline so save/load and the message log stay
consistent with every other gameplay effect.

## Observation surface

Per-actor fields added to the M35 observation snapshot:

| Field              | Meaning                                                     |
| ---                | ---                                                         |
| `level`            | Current character level.                                    |
| `xp`               | Current XP total.                                           |
| `xp_to_next`       | XP needed to reach the next known threshold (`None` at cap).|
| `level_up_pending` | The pending `target_level`, or `None`.                      |

Modal kind `level_up` surfaces the pending member, name, and target
level via the `options` list so an agentic playtester can plan its
confirm without inspecting component stores directly.

## Architectural notes

- `src/core/leveling.py` owns the threshold table, CR-to-XP table,
  per-class HP gain helper, and per-class spell-slot progression.
- `src/core/effects_applier.py` wires `GrantXP` and `LevelUp` into the
  standard `EffectApplier`. Combat XP fires inside `_apply_kill_entity`
  so any death (player, AI, debug command) earns the party XP.
- `src/app.py` opens the level-up modal at the tail of `apply_effects`
  so any effect batch that grants XP and crosses a threshold
  immediately surfaces the modal.
- Help / observation / playtest impact: yes (this file, observation
  fields, modal projection); a playtest fixture can exercise the
  pipeline by killing several mooks and watching the modal fire.
