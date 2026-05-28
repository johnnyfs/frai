"""Online help registry and modal state (M31 + M39).

The help system is data-driven from ``docs/help/`` — every markdown file
under that directory auto-registers as a topic, keyed by the file stem.
Plus a set of synthetic, hand-curated overview topics ("Movement",
"Combat", "Inventory", "Dialogue") that summarise the play-mode key
bindings the player needs to know on day one.

Rendering is intentionally minimal: the title bar shows the topic name,
the body is wrapped to the current playfield width, and Esc / q pops
back to the index. The modal is render-only; opening it does not mutate
the world or advance any clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


# Directory containing the markdown topic files. Resolved once at import
# time so a process started from any working directory finds the docs.
HELP_DOCS_DIR: Path = Path(__file__).resolve().parents[2] / "docs" / "help"


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HelpTopic:
    """A single help entry.

    ``topic_id`` is the stable identifier used by tests / scripts.
    ``title`` is the human-readable label shown in the index.
    ``body`` is the markdown contents (overview topics are pre-rendered;
    file-backed topics carry the raw file text).
    """

    topic_id: str
    title: str
    body: str


# Synthetic overview topics. Hand-curated because they need to mirror
# the *currently bound* keys; the lint-style test below asserts that
# every key wired through ``input_system.map_key`` appears in this list
# (or one of the autoloaded docs).
_OVERVIEW_TOPICS: tuple[HelpTopic, ...] = (
    HelpTopic(
        topic_id="movement",
        title="Movement",
        body=(
            "# Movement\n"
            "\n"
            "Rogue-style direction keys move the active actor one tile.\n"
            "Capital letters auto-walk in that direction until interrupted.\n"
            "\n"
            "  h / left          step west\n"
            "  l / right         step east\n"
            "  k / up            step north\n"
            "  j / down          step south\n"
            "  y                 step northwest\n"
            "  u                 step northeast\n"
            "  b                 step southwest\n"
            "  n                 step southeast\n"
            "  H J K L Y U B N   auto-walk in that direction\n"
            "  Space             end turn (turn-based only)\n"
            "  t                 toggle voluntary turn-based\n"
            "\n"
            "See docs/help/autowalk.md for the interrupt list.\n"
        ),
    ),
    HelpTopic(
        topic_id="combat",
        title="Combat",
        body=(
            "# Combat\n"
            "\n"
            "Bump into a hostile to attack with your equipped weapon.\n"
            "Forced turn-based mode kicks in whenever a hostile is in\n"
            "sight; movement and actions cost from per-turn budgets.\n"
            "\n"
            "  (bump)            melee attack on adjacent hostile\n"
            "  s                 open spell menu (casters only)\n"
            "  z                 attempt to enter stealth\n"
            "  p                 perception sweep for hidden foes\n"
            "  Space             end turn\n"
            "  t                 toggle voluntary turn-based\n"
            "\n"
            "See docs/help/spells.md, docs/help/stealth.md, and\n"
            "docs/help/death.md for deeper coverage.\n"
        ),
    ),
    HelpTopic(
        topic_id="inventory",
        title="Inventory",
        body=(
            "# Inventory\n"
            "\n"
            "  i                 open / close inventory\n"
            "  ,                 pick up items on your tile\n"
            "  d (in inventory)  drop the first unequipped stack\n"
            "  q / Esc           close inventory\n"
            "\n"
            "See docs/help/loot.md for ground drops and corpse handling.\n"
        ),
    ),
    HelpTopic(
        topic_id="party",
        title="Party",
        body=(
            "# Party and characters\n"
            "\n"
            "  P                 open the party roster\n"
            "  Enter (roster)    drill into the selected character sheet\n"
            "  Esc (roster)      back out to play\n"
            "\n"
            "The character sheet shows ability scores, conditions,\n"
            "equipment, inventory, known spells / slots, faction,\n"
            "and current position.\n"
        ),
    ),
    HelpTopic(
        topic_id="interaction",
        title="Interaction",
        body=(
            "# Interaction\n"
            "\n"
            "  e                 interact with the facing tile\n"
            "  x or ;            examine cursor (look at any tile)\n"
            "  r                 open the rest menu\n"
            "  ?                 open this help screen\n"
            "  q                 quit (from play)\n"
            "\n"
            "Bumping into an NPC opens dialogue. See docs/help/dialogue.md\n"
            "and docs/help/examine.md for details.\n"
        ),
    ),
)


def _load_doc_topics(docs_dir: Path = HELP_DOCS_DIR) -> list[HelpTopic]:
    """Load every ``*.md`` under ``docs_dir`` as a topic.

    The topic_id is the file stem; the title is derived from the first
    ``# ...`` heading if present, falling back to the stem with the
    first letter capitalised.
    """

    if not docs_dir.is_dir():
        return []
    topics: list[HelpTopic] = []
    for path in sorted(docs_dir.glob("*.md")):
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue
        title = _extract_title(body) or path.stem.capitalize()
        topics.append(
            HelpTopic(topic_id=path.stem, title=title, body=body)
        )
    return topics


def _extract_title(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return None


def build_help_topics(
    docs_dir: Path = HELP_DOCS_DIR,
) -> tuple[HelpTopic, ...]:
    """Return the overview topics followed by every autoloaded doc."""

    return tuple(_OVERVIEW_TOPICS) + tuple(_load_doc_topics(docs_dir))


# Module-level cache. Tests that want a hand-crafted set inject through
# :func:`build_help_topics` directly; the App always uses the cache.
HELP_TOPICS: tuple[HelpTopic, ...] = build_help_topics()


def topic_for(topic_id: str) -> HelpTopic | None:
    """Look up a topic by id. ``None`` for unknown ids."""

    for topic in HELP_TOPICS:
        if topic.topic_id == topic_id:
            return topic
    return None


# ---------------------------------------------------------------------------
# Modal state
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HelpState:
    """Transient state for the open help modal.

    ``cursor`` is the selected index in the topic list. ``viewing`` is
    the topic the player has drilled into (``None`` means we are on the
    index screen). ``scroll`` is the first body line displayed when
    viewing — Esc drops both viewing and scroll back to zero.
    """

    cursor: int = 0
    viewing: HelpTopic | None = None
    scroll: int = 0
    previous_mode: str | None = None
    topics: tuple[HelpTopic, ...] = field(default_factory=lambda: HELP_TOPICS)

    def move_cursor(self, delta: int) -> None:
        if not self.topics:
            return
        self.cursor = max(0, min(len(self.topics) - 1, self.cursor + delta))

    def select_current(self) -> None:
        if not self.topics:
            return
        self.viewing = self.topics[self.cursor]
        self.scroll = 0

    def back_to_index(self) -> bool:
        """Pop out of a viewed topic. Returns True if we did pop.

        When already on the index, returns False so the App knows to
        close the modal entirely.
        """

        if self.viewing is None:
            return False
        self.viewing = None
        self.scroll = 0
        return True

    def scroll_by(self, delta: int) -> None:
        if self.viewing is None:
            return
        self.scroll = max(0, self.scroll + delta)


# ---------------------------------------------------------------------------
# Text wrapping
# ---------------------------------------------------------------------------


def wrap_body(body: str, width: int) -> list[str]:
    """Wrap ``body`` to ``width`` columns, preserving blank lines.

    Markdown is rendered effectively as plain text: no syntax stripping,
    no bold / italic rendering. The wrapping is whitespace-only so a
    code block stays readable. Tabs are expanded to four spaces.
    """

    if width <= 0:
        return [line.rstrip() for line in body.splitlines()]
    out: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.expandtabs(4).rstrip()
        if not line:
            out.append("")
            continue
        # Don't reflow lines that already fit.
        if len(line) <= width:
            out.append(line)
            continue
        out.extend(_wrap_one(line, width))
    return out


def _wrap_one(line: str, width: int) -> list[str]:
    words = line.split(" ")
    pieces: list[str] = []
    current = ""
    for word in words:
        if not current:
            current = word
            continue
        candidate = current + " " + word
        if len(candidate) <= width:
            current = candidate
        else:
            pieces.append(current)
            current = word
    if current:
        pieces.append(current)
    # Hard-break any single word longer than the wrap width.
    final: list[str] = []
    for piece in pieces:
        while len(piece) > width:
            final.append(piece[:width])
            piece = piece[width:]
        final.append(piece)
    return final


# ---------------------------------------------------------------------------
# Coverage helpers (used by the lint-style test)
# ---------------------------------------------------------------------------


def collect_help_text(topics: Iterable[HelpTopic] | None = None) -> str:
    """Return all topic titles + bodies joined into a single blob.

    The lint-style test that asserts every input-system key is mentioned
    in help reads this blob and searches for the bound character.
    Lowercase so the search is case-insensitive.
    """

    if topics is None:
        topics = HELP_TOPICS
    parts: list[str] = []
    for topic in topics:
        parts.append(topic.title)
        parts.append(topic.body)
    return "\n".join(parts).lower()
