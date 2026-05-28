# Repo-Local Agent Skills

Project-specific skills for agents working on the Python terminal RPG vertical slice (johnnyfs/frai).

- `/spec` — object model and system design before significant implementation.
- `/implement` — implementation workflow for milestone/subtask authors; enforces help/observation/playtest updates.
- `/review` — focused PR review workflow with help/observation/playtest checks.
- `/assign` — lead-agent work-picker; maximizes safe parallelism, staffs the standing playtester.
- `/playtest` — dedicated agentic playtester; uses command scripts and structured observations, files reproducible bug/improvement reports.
- `/lead-dag` — lead-agent coordination loop; maintains the milestone DAG, the always-playable `main` invariant, and the standing playtester role until all unblocked work is complete.

A mirror of these skills lives in `.codex/skills/` for the Codex agent.
