# Playtest workflow (M40)

This doc is the operating manual for the standing `/playtest` agent and any
human playtester filing issues against the terminal RPG. It pairs with the
GitHub issue templates in `.github/ISSUE_TEMPLATE/` and the `/playtest` skill
(`.claude/skills/playtest/SKILL.md`).

## Overview of the /playtest role

The lead keeps exactly one `/playtest` agent active whenever the prerequisites
(observation API, command-script runner, harness, deterministic seed,
playable start) are met. Each session targets a recent change, a fragile
subsystem, an acceptance-criteria gap, or a fix PR awaiting verification.

The agent's job is to:

- Drive `PlaytestHarness` (M37) with M36 command scripts.
- Read the M35 structured `Observation` snapshot — never the ANSI framebuffer.
- File reproducible issues using the templates described below.
- Verify fix PRs by re-running the same script against the PR branch.

Confusion is a bug or an improvement, not noise. Don't silently work around
weirdness. File it.

## Filing a bug

Use **`.github/ISSUE_TEMPLATE/playtest-bug.md`** (the "Playtest bug" template
on the New Issue picker) when behavior diverges from the spec, help text, or
basic player expectations: crashes, rule incoherence, stuck modals, lost
input, save/load drift, autowalk that fails to interrupt, UI states the
observation can't reach, etc.

Every bug report must include:

- **Scenario / fixture** — name of the registered scenario or a short
  description of the starting state.
- **Seed** — the integer passed to `PlaytestHarness(seed=...)`.
- **Build** — commit sha of the branch you tested against (`git rev-parse
  HEAD`).
- **Command sequence** — the exact M36 script that reproduces the failure.
- **Expected / Actual** — one short sentence each.
- **Last structured observation** — the JSON dump from
  `harness.observe().to_dict()` immediately before the failure.
- **Messages / log excerpts** — relevant message-log lines or tracebacks.
- **Suspected subsystem** — best guess at the module or feature area.
- **Reproducibility** — `always | usually | flaky | once`.
- **Severity** — `critical | high | medium | low` (see rubric below).

If any of these fields is impossible to fill in, that itself is a finding —
file it as an improvement against the harness/observation surface.

## Filing an improvement

Use **`.github/ISSUE_TEMPLATE/playtest-improvement.md`** (the "Playtest
improvement" template) when something isn't broken but degrades playability
or agentic testing: missing feedback, awkward modal flow, observation field
you wished existed, help text drift, command-script syntax friction, etc.

Every improvement report must include:

- **Friction observed** — what was confusing, slow, painful, or hard to
  script.
- **Why it matters** — both angles: human player impact *and* agentic test
  impact.
- **Proposed behavior** — concrete enough that an implementer could open a
  milestone.
- **Priority** — `high | medium | low`.

## Reproduction discipline

Reproducibility is the lever that makes playtest reports actionable. Every
report must let any other agent or human:

1. Construct a harness with the recorded seed and scenario.
2. Paste the command script.
3. See the same observation diff and the same failure.

That requires:

- **Always record the seed.** Sessions without a seed cannot file reports.
- **Always record the scenario or fixture builder.** "Default `create_app`"
  is fine but say so.
- **Always record the full command script**, including any debug commands
  (M33) you used to set up state. Use the exact M36 syntax.
- **Capture the structured observation, not the screen.** Paste the
  `to_dict()` JSON, not a curses screenshot.
- **Note the build sha.** Bugs that are real on `main` but already gone on a
  fix branch need both shas.

## Severity rubric

Mirrors the `/playtest` skill's severity guide:

- **Critical** — crash, save corruption, impossible progression, hung main
  loop, party permanently invalid.
- **High** — rules incoherence, stuck modal, invalid combat state,
  impossible quest step, lost input, broken save/load round-trip.
- **Medium** — misleading UI, missing feedback, awkward-but-recoverable
  interaction, repeated-movement that fails to interrupt safely.
- **Low** — copy/help polish, color/rendering nit, minor message ordering.

If you're unsure, pick the higher of two and let triage downgrade it.

## Lead triage flow

When a `[playtest]` issue lands, the lead applies this checklist:

1. **Duplicate?** Search open and recently-closed issues for the same
   subsystem and symptom. If yes, link and close, copying the new repro
   commands into the existing issue if they add coverage.
2. **Real bug or expected behavior?** Cross-check spec, help text, and the
   relevant tests. If expected, close with a one-line explanation and
   suggest a help-text update if the misunderstanding was reasonable.
3. **Bug vs improvement?** A divergence from documented behavior is a bug.
   A complaint about documented-but-unpleasant behavior is an improvement.
4. **Priority.** For bugs, severity sets baseline priority. Critical and
   high open a milestone immediately; medium and low queue for the next
   sweep. For improvements, use the priority field plus impact on agentic
   testing — anything that blocks `/playtest` itself is treated as high.
5. **Assignment.** Route to the smallest milestone that covers the fix.
   Add `needs-help-update` if the fix changes player-facing surface so the
   implementer remembers the `/implement` checklist.
6. **Verify.** When the fix PR lands, re-dispatch `/playtest` with the
   original seed/script to confirm the bug is dead before closing.

## See also

- `.claude/skills/playtest/SKILL.md` — the standing-agent skill.
- `docs/help/agent.md` — observation surface, command-script grammar, and
  harness API the templates assume.
- `.github/ISSUE_TEMPLATE/playtest-bug.md`
- `.github/ISSUE_TEMPLATE/playtest-improvement.md`
