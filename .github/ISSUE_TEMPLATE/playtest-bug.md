---
name: Playtest bug
about: Bug report filed by the standing /playtest agent (or a human playtester).
title: "[playtest] "
labels: bug, playtest, needs-triage
assignees: ''
---

<!--
Filed by the /playtest agent. Keep every section so triage stays mechanical.
See docs/playtest-workflow.md for the full filing discipline and severity rubric.
-->

## Scenario / fixture

<!-- Name of the registered scenario (e.g. open_field) or a short description of the starting state. -->

## Seed

<!-- The integer seed passed to PlaytestHarness (or `--seed` on the launcher). -->

## Build

<!-- Commit sha of `main` (or the PR branch) the session ran against. Use `git rev-parse HEAD`. -->

## Command sequence

<!-- The exact M36 command-script the harness ran. Keep it short and replayable. -->

```
<command1>
<command2>
...
```

## Expected

<!-- What should have happened, ideally referencing the spec/help that documents the behavior. -->

## Actual

<!-- What actually happened. One sentence symptom first, then detail. -->

## Last structured observation

<!-- `harness.observe().to_dict()` JSON dump right before the failure. -->

```json
{
  "...": "..."
}
```

## Messages / log excerpts

<!-- Player-visible message lines, banners, or relevant tracebacks. -->

```
<lines>
```

## Suspected subsystem

<!-- Module or feature area (e.g. `src.ui.turn`, save/load, autowalk, light system). -->

## Reproducibility

<!-- Pick one: always | usually | flaky | once -->

## Severity

<!-- Pick one: critical | high | medium | low. See docs/playtest-workflow.md for the rubric. -->
