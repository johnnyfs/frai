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

### Faction values

The `faction` field on `ActorSummary` and `VisibleEntity` carries the
canonical M28 `FactionId` value (`player_party`, `town`, `dungeon`,
`wildlife`) for content spawned through the post-M28 path. Pre-M28
fixtures and saves may still emit the legacy strings `player` and
`enemy`; both are resolved to the same relations internally. See
`docs/help/factions.md` for the full catalog and relation matrix.

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
| `e`                | Interact with the facing tile (opens dialogue on adjacent NPC, M13). |
| `,`                | Pick up items on the actor's tile.               |
| `i`                | Open/close inventory.                            |
| `r`                | Short rest. No-op until M34 lands.               |
| `x`                | Open examine cursor (M21). `;` is an alias.      |
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

## Harness usage (M37)

`src.testing.PlaytestHarness` is the headless wrapper an agentic
playtester drives. It hides the difference between "construct an App,
seed RNG, call observe, dispatch a script, save/load" behind a small
class so a session reads top-to-bottom.

```python
from src.core.modes import UIMode
from src.testing import PlaytestHarness

harness = PlaytestHarness(seed=42)        # dev_mode=True by default
print(harness.observe().mode)              # {'ui_mode': 'start', 'play_mode': None}

# Drop into play, then drive an M36 script:
harness.app.ui_mode = UIMode.play
outcomes = harness.run("5l;,;i")           # walk 5 east, pick up, open inventory
for outcome in outcomes:
    print(outcome.command, outcome.steps_taken, outcome.interrupt_reason)

# Inspect or assert via raw App access — predicates take the App so
# they can reach into component stores the Observation doesn't expose:
harness.assert_predicate(
    lambda app: app.party.size == 4,
    "party should still have four members",
)

# M33 debug commands (gated by FRAI_DEV, flipped on by dev_mode=True):
banner = harness.debug("tp 5 5")
print(banner)                              # "Teleported to (5, 5)."

# M16 save/load round-trip:
path = harness.save()                      # writes to a temp file
harness.load(path)                         # swap self.app in place
```

### API surface

| Method                                    | Returns                  | Notes |
| ---                                       | ---                      | --- |
| `__init__(scenario_name, seed, dev_mode)` | —                        | Constructs an App via `create_app(rng=Random(seed))`. `scenario_name` resolves through `SCENARIOS` (empty in M37; M38 populates). |
| `run(script: str)`                        | `list[CommandOutcome]`   | Forwards to M36 `run_script`. |
| `observe()`                               | `Observation`            | Forwards to M35 `observe`. Pure. |
| `debug(command: str)`                     | `str`                    | Runs an M33 debug command; returns the resulting banner. |
| `save(path: Path \| None)`                | `Path`                   | Wraps `src.core.save.save_game`. Default path lands in a per-harness tempdir. |
| `load(path: Path)`                        | `None`                   | Wraps `load_game`; replaces `self.app`. |
| `assert_predicate(fn, msg)`               | `None`                   | Raises `PredicateAssertionError` on a falsey predicate. |
| `messages()`                              | `list[str]`              | Current + pending message-log lines. |
| `load_scenario(name: str)`                | `None`                   | Rebuild against a registered scenario without re-constructing the harness. |

### Determinism contract

`PlaytestHarness(seed=N)` produces an App whose loot rolls, interaction
checks, and YOLO character roll are all driven by `random.Random(N)`.
Two harnesses created with the same seed and driven by the same
command script must produce **bit-identical observation sequences** —
the M37 test suite asserts this.

`yolo_sheet(rng=...)` and `initial_character_creation_state(rng=...)`
both accept an optional RNG so the harness can pin the starting party.
Interactive launches still pass `None` and get fresh rolls.

### Fixture catalog (M38)

`src.testing.fixtures` registers a curated set of scenarios at import
time. The `/playtest` standing agent picks targets from this catalog,
and CI integration tests pin to specific names. All fixtures load via
`PlaytestHarness(scenario_name="<name>")` and surface a four-member
party in the play screen at t=0.

| Name                | Exercises                                | Geometry / payload                                                     |
| ---                 | ---                                      | ---                                                                    |
| `combat_simple`     | Adjacent melee, forced turn-based mode    | Two kobolds adjacent to the player at (player+1, 0) and (0, player+1). |
| `combat_archer`     | M10 RANGED AI behaviour, ranged attacks   | One kobold archer six tiles east; ranged AI with preferred_range=4.    |
| `door_locked`       | M9 doors + M26 skill-check lock pick      | Locked door one tile east; party is Rogue with Sleight of Hand.        |
| `trap_armed`        | M9 trap + disarm-check path                | Armed trap two tiles east; party is Rogue with Sleight of Hand.        |
| `container_loot`    | M9 OpenEntity + M30 ground pickup         | Closed chest two tiles east holding dagger, healing potion, 25gp.      |
| `shop_basic`        | M12 buy/sell                               | NPC shopkeeper one tile east; club, shortsword, leather armor, potion. |
| `vision_corridor`   | M19 LOS clipping                          | 31x5 corridor; kobold at the east end is outside the radius-10 LOS.    |
| `hostile_far`       | LOS hiding non-visible hostile             | 30x30 room; kobold in the far corner > 10 tiles from the party.        |
| `open_terrain`      | M22 autowalk to step bound                 | 30x10 empty room; companions parked along the south wall.              |

#### Picking a fixture

- For *combat correctness regressions*: `combat_simple` (every melee
  attack path runs) or `combat_archer` (ranged attack + AI distancing).
- For *interaction primitives (M9)*: `door_locked` for the lock-pick
  branch, `trap_armed` for the disarm branch, `container_loot` for
  the open-container branch.
- For *economy/shop (M12)*: `shop_basic` — the only fixture that
  surfaces a `Shop` component, the only one that stays in `explore`
  with a populated NPC inventory.
- For *vision/autowalk (M19, M22)*: `vision_corridor`,
  `hostile_far`, `open_terrain`. Note that
  `hostiles_requiring_battle` is currently global, so the autowalk
  interrupt for the first two fires as `combat_started` rather than
  `new_hostile_visible` — the geometry is set up so a future M28
  faction/awareness refactor can switch the trigger without
  rewriting the fixtures.

#### Adding a new fixture

Register a `Scenario` in
`src.testing.fixtures.scenarios._FIXTURES` (or via a side-effect
import that calls `register(Scenario(...))`). The builder receives the
default-seeded App and should return a replacement App built via
`make_fixture_app` in `_helpers.py` — that keeps the seed contract
clean. Tiny worlds (≤ 30x30) are preferred; the playtester loops over
the catalog and a giant world per fixture would slow CI noticeably.

```python
from src.testing.scenarios import Scenario, register

def my_builder(app):
    """Custom smoke fixture: drop straight into play mode."""
    from src.core.modes import UIMode
    app.ui_mode = UIMode.play
    return None  # mutates app in place; returning None keeps it.

register(Scenario(
    name="my_smoke",
    builder=my_builder,
    description="Default world, in play mode from t=0.",
))
```

### Notes on environment

- The harness sets `FRAI_DEV=1` when `dev_mode=True` (the default) so
  `harness.debug(...)` actually runs commands. Pass `dev_mode=False`
  to assert that the disabled path returns a refusal banner.
- `save()` with no argument writes to
  `$TMPDIR/frai-playtest-harness/harness-seed<N>.json`. Pass an
  explicit path for CI integration tests that need a known location.
- `load(path)` keeps `self` identity stable; any caller holding a
  reference to the harness keeps working after the swap.

### Headless guarantees

`PlaytestHarness.__init__` does not import `src.ui.screen`, does not
call `curses.wrapper`, and does not require a TTY. Construction in a
pytest subprocess with no controlling terminal is part of the test
matrix.

### Direct (non-harness) usage

If you want to skip the harness wrapper and drive the lower layers
directly, the original observe/script-runner loop still works:

```python
from src.ui.observation import observe
from src.ui.script_runner import run_script

last = observe(app)
while app.running:
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

## Filing playtest reports

The standing `/playtest` agent files bugs and improvement requests via the
GitHub issue templates committed to this repo. Use the GitHub "New Issue"
picker and pick one of:

- **Playtest bug** — `.github/ISSUE_TEMPLATE/playtest-bug.md`. Labels
  `bug, playtest, needs-triage`. Required fields: scenario/fixture, seed,
  build sha, command sequence, expected, actual, last structured
  observation (`harness.observe().to_dict()` JSON dump), messages/log
  excerpts, suspected subsystem, reproducibility, severity.
- **Playtest improvement** — `.github/ISSUE_TEMPLATE/playtest-improvement.md`.
  Labels `enhancement, playtest, needs-triage`. Required fields:
  friction observed, why it matters (playability + agentic testing),
  proposed behavior, priority.

See `docs/playtest-workflow.md` for the full filing discipline, severity
rubric, and the lead's triage flow.

## Limitations and follow-ups

- No log of past messages. When a `MessageLog` lands (likely with
  M32 error/message discipline), `recent_messages` should pull from it.
- `available_actions` is heuristic. M44 will give us an authoritative
  per-actor action set.
- Visibility radius is a placeholder for M19.
- The harness must own delta computation; `observe()` is intentionally
  stateless.
