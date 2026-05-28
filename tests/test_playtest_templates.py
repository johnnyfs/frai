"""Smoke tests for the M40 playtest issue templates and workflow doc.

These are process-level guards: if someone deletes the templates or
strips a required section, the standing /playtest agent loses its
ability to file actionable reports. We assert the files exist and
contain the required structural markers.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

BUG_TEMPLATE = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "playtest-bug.md"
IMPROVEMENT_TEMPLATE = (
    REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "playtest-improvement.md"
)
WORKFLOW_DOC = REPO_ROOT / "docs" / "playtest-workflow.md"
AGENT_HELP = REPO_ROOT / "docs" / "help" / "agent.md"


def _read(path: Path) -> str:
    assert path.exists(), f"missing required file: {path}"
    return path.read_text(encoding="utf-8")


def test_bug_template_has_required_sections():
    body = _read(BUG_TEMPLATE)
    # YAML frontmatter
    assert body.startswith("---\n"), "bug template must start with YAML frontmatter"
    assert "name: Playtest bug" in body
    assert "labels: bug, playtest, needs-triage" in body
    assert 'title: "[playtest] "' in body
    # Required body sections
    for header in (
        "## Scenario / fixture",
        "## Seed",
        "## Build",
        "## Command sequence",
        "## Expected",
        "## Actual",
        "## Last structured observation",
        "## Messages / log excerpts",
        "## Suspected subsystem",
        "## Reproducibility",
        "## Severity",
    ):
        assert header in body, f"bug template missing section: {header}"


def test_improvement_template_has_required_sections():
    body = _read(IMPROVEMENT_TEMPLATE)
    assert body.startswith("---\n")
    assert "name: Playtest improvement" in body
    assert "labels: enhancement, playtest, needs-triage" in body
    for header in (
        "## Friction observed",
        "## Why it matters",
        "## Proposed behavior",
        "## Priority",
    ):
        assert header in body, f"improvement template missing section: {header}"


def test_workflow_doc_covers_required_topics():
    body = _read(WORKFLOW_DOC)
    for marker in (
        "Overview of the /playtest role",
        "Filing a bug",
        "Filing an improvement",
        "Reproduction discipline",
        "Severity rubric",
        "Lead triage flow",
    ):
        assert marker in body, f"workflow doc missing topic: {marker}"
    # Links back to the templates
    assert "playtest-bug.md" in body
    assert "playtest-improvement.md" in body


def test_agent_help_links_to_templates():
    body = _read(AGENT_HELP)
    assert "Filing playtest reports" in body
    assert "playtest-bug.md" in body
    assert "playtest-improvement.md" in body
    assert "docs/playtest-workflow.md" in body
