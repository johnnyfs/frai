# Agent commands and observation (M35)

This file describes the read-only state surface that the agentic
playtester (M37) consumes. Agents do not read the curses framebuffer;
they consume a structured `Observation` snapshot.

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

## Harness usage (M37 hook)

The harness should call `observe(app)` after every command and diff
against the previous observation. Suggested flow:

```python
last = observe(app)
while not app.terminated:
    command = agent.decide(last)
    apply_command(app, command)  # via App.run_debug_command or input
    current = observe(app)
    agent.update_from_delta(last, current)
    last = current
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
