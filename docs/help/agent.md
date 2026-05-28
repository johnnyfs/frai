# Agent commands and observation (M35, M36)

This file describes the read-only state surface that the agentic
playtester (M37) consumes and the command-script language agents use to
drive the App. Agents do not read the curses framebuffer; they consume a
structured `Observation` snapshot and send commands either as parsed
`Command` records or as a single compact script string.

The `?` help integration (M39) will surface this file under a debug
section once that milestone lands. Until then, this file is reference
material for harness authors.

## `observe(app)` API

```python
from src.app import create_app
from src.ui.observation import observe

obs = observe(app)
payload = obs.to_dict()           # JSON-compatible
restored = Observation.from_dict(payload)
```

- `observe(app)` is pure: it never mutates `app`, `app.world`, or any
  component store. Two calls back-to-back with no input return equal
  observations.
- The result is a frozen `Observation` dataclass; `to_dict()` returns a
  plain-Python tree that survives `json.dumps`.

## Observation field surface

| Field               | Shape                                                                       | Notes |
| ---                 | ---                                                                         | --- |
| `mode`              | `{ui_mode: str, play_mode: str | None}`                                    | `play_mode` is `None` outside `UIMode.play`. |
| `active_actor`      | `ActorSummary | None`                                                       | `{id, name, hp, max_hp, position, faction, glyph}`. |
| `party`             | `list[ActorSummary]`                                                        | Skips members without a `Position`. |
| `visible_entities`  | `list[VisibleEntity]`                                                       | Anything within 10 tiles of the active actor, party excluded. Sorted by distance then id. |
| `exits`             | `list[str]`                                                                 | Compass directions with no movement blocker on the adjacent tile. |
| `recent_messages`   | `list[str]`                                                                 | Current + pending message pages (capped at 10). No log history yet. |
| `combat`            | `CombatSnapshot | None`                                                     | Present only in turn-based play. |
| `available_actions` | `list[str]`                                                                 | Best-effort; consult `app.turn` once M44 lands. |
| `modal`             | `ModalSnapshot | None`                                                      | Present whenever `ui_mode != play`. |
| `world_time`        | `{seconds, rounds, minutes, hours}`                                         | Mirrors `app.world.clock`. |

### Mode strings

`ui_mode` is one of: `start`, `character_creation`, `play`, `inventory`,
`dialogue`, `shop`, `targeting`, `examine`, `help`, `message_pager`,
`quit_confirm`, `game_over`.

`play_mode` is one of: `explore`, `turn_based`, `voluntary_turn`.

## Visibility

The visible-entity list is filtered through M19 party memory:
`app.memory.visible` is a frozenset of `(x, y)` cells. Any entity whose
position is in that set is reported; everything else is omitted, so
agents do not peek through walls or fog.

If a harness fixture builds an `App`-like object that omits the
`memory` attribute, the filter falls back to a Chebyshev radius of
`DEFAULT_VISIBILITY_RADIUS = 10` from the active actor. Both call sites
go through the same `_visible_filter` helper.

## Combat snapshot

When `play_mode` is `turn_based` or `voluntary_turn`, `combat` reports:

- `round` — `app.world.clock.rounds`.
- `active_index` — index into `party` of the actor whose turn it is.
- `action_remaining`, `bonus_remaining`, `reaction_remaining` — booleans
  derived from `ActivationState`.
- `movement_remaining`, `movement_total` — feet of movement left/total.

Once M44 (`TurnController`) lands the full initiative roster will be
added; agents should treat the field set as additive.

## Command-script language (M36)

Agents send commands as a single text "script". The script grammar is
deliberately compact so a single-line decision can carry up to a few
dozen keystrokes' worth of intent without escaping headaches.

### Quick reference

```python
from src.app import create_app
from src.ui.script_runner import run_script

app = create_app()
app.handle_key(ord("y"))                  # YOLO into play.
outcomes = run_script(app, "5l;,;i")      # walk 5 east, pick up, open inventory
for outcome in outcomes:
    print(outcome.command, outcome.steps_taken, outcome.interrupt_reason)
```

### Grammar

| Token              | Meaning                                          |
| ---                | ---                                              |
| `h j k l y u b n`  | Rogue-style movement (west/south/north/east + diagonals). |
| `<N><dir>`         | Repeat-movement: walk up to `N` tiles. Only valid before a movement letter. |
| `e`                | Interact with the facing tile.                   |
| `,`                | Pick up items on the actor's tile.               |
| `i`                | Open/close inventory.                            |
| `r`                | Short rest. No-op until M34 lands.               |
| `x`                | Examine. No-op until M41 lands.                  |
| `?`                | Open help. No-op until M39 lands.                |
| `.`                | Wait one tick.                                   |
| `Enter`            | Confirm the current modal.                       |
| `Esc`              | Cancel the current modal.                        |
| `;` or newline     | Command separator.                               |
| `# ...`            | Whole-line comment (must start the line).        |

The parser raises `CommandScriptError` for any malformed token; the
caller (harness or `--script` flag) should surface the error and not
mutate further state. Whitespace around tokens is ignored, as are
blank and comment lines.

### Repeat-movement and interrupts

A `<N><dir>` move is conceptually identical to an M22 auto-walk with
`max_steps = N`. Internally the runner sets `app.autowalk` and lets
`App._run_autowalk` drive the loop, which consults the same
`step_autowalk` predicate the keyboard auto-walk uses. The interrupt
vocabulary is identical (see `docs/help/autowalk.md`):

| Reason                  | Trigger                                          |
| ---                     | ---                                              |
| `out_of_steps`          | `N` budget consumed.                             |
| `modal_opened`          | A modal stole focus (`UIMode != play`).          |
| `combat_started`        | Hostile presence flipped on.                     |
| `new_hostile_visible`   | A hostile entered the party's vision.            |
| `blocked`               | A step was refused (wall, door, occupant).       |
| `event_message`         | The game emitted a player-relevant message.      |
| `low_hp`                | Reserved for M24 conditions/statuses.            |

`run_command`/`run_script` returns a list of `CommandOutcome` records
exposing `steps_taken`, `interrupt_reason`, and a fresh
`observation_after` snapshot.

## Harness usage (M37 hook)

The harness should call `observe(app)` after every command and diff
against the previous observation. With the M36 script runner the loop is:

```python
from src.ui.observation import observe
from src.ui.script_runner import run_script

last = observe(app)
while not app.terminated:
    script = agent.decide(last)                  # e.g. "5l;i"
    outcomes = run_script(app, script)
    for outcome in outcomes:
        agent.update_from_delta(last, outcome.observation_after)
        last = outcome.observation_after
        if outcome.interrupt_reason is not None:
            agent.note_interrupt(outcome.interrupt_reason)
```

`observe()` never throws, never mutates, and never depends on the
curses screen. It is safe to call from any thread, including ones that
do not own the terminal.

## Limitations and follow-ups

- No log of past messages. When a `MessageLog` lands (likely with
  M32 error/message discipline), `recent_messages` should pull from it.
- `available_actions` is heuristic. M44 will give us an authoritative
  per-actor action set.
- Visibility radius is a placeholder for M19.
- The harness must own delta computation; `observe()` is intentionally
  stateless.
