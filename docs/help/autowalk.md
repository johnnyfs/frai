# Auto-walk (M22)

Auto-walk repeats single-step movement in one direction until something
interesting happens. It is the NetHack-style "G<dir>" / shifted-letter
behaviour: press one key, walk down the corridor.

## Key bindings

Auto-walk is initiated by pressing a *capital* direction letter while
`UIMode.play` is active. The standard Rogue/NetHack letter set is used
so the keymap matches the manual single-step movement bindings:

| Key | Direction       |
| --- | ---             |
| `H` | west            |
| `J` | south           |
| `K` | north           |
| `L` | east            |
| `Y` | north-west      |
| `U` | north-east      |
| `B` | south-west      |
| `N` | south-east      |

The lowercase forms (`h`, `j`, ...) still mean "take one step" — the
autowalk keys are a strict capital-letter superset and have no effect
outside the play screen. There is no other key combination today; the
prefix-key (`G<dir>`) form is reserved for a follow-up so the predicate
stays the source of truth.

By default an auto-walk is capped at 100 steps. The cap exists so a
buggy interrupt predicate can never freeze the input loop.

## Interrupts

An auto-walk continues until any of these is true. The first match wins;
the order below is the order the predicate (`src.core.autowalk.step_autowalk`)
checks them in:

| Reason                  | Trigger                                                                                  |
| ---                     | ---                                                                                      |
| `out_of_steps`          | The configured `max_steps` budget was consumed.                                          |
| `modal_opened`          | `UIMode` left `play` (a modal screen, message pager, or game-over screen took focus).   |
| `combat_started`        | Hostile presence flipped on, forcing turn-based play.                                    |
| `new_hostile_visible`   | A hostile entity is in the party's current visible set (M19 vision).                     |
| `blocked`               | The latest step failed — a wall, door, or other blocker stopped us.                      |
| `event_message`         | The message log holds a non-trivial message (anything other than `Blocked.` or empty).   |
| `low_hp`                | Reserved for M24 (conditions / statuses). Never fires today.                             |

When the walk stops, a short message identifies the reason. For
information-bearing reasons (`out_of_steps`, `new_hostile_visible`,
`combat_started`) the autowalk banner is always emitted. For reasons
that the game has already messaged about (e.g. an event triggered a
message), the existing message is preserved instead of being clobbered.

## Save / load

Auto-walk is transient runtime state. A save written mid-walk drops the
walk; the player resumes at the current position with no pending walk.

## Architectural notes

- `src.core.autowalk` exposes a single pure predicate, `step_autowalk`,
  plus the `AutowalkRequest` and `InterruptReason` types. The predicate
  does not mutate any state — the App runs the move dispatch and then
  asks the predicate whether to take another step.
- M36 command-scripting (`<N><dir>` repeated moves) reuses the same
  predicate. A `<N><dir>` form is conceptually an autowalk with
  `max_steps = N` and the identical interrupt list, so a fix in one
  benefits the other.
- The "new hostile visible" check consults `app.memory.visible` (the
  M19 party vision set), so it is line-of-sight aware. Remembered
  hostiles do not interrupt; only ones currently in sight do.
