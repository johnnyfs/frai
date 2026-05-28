"""Quest model — typed config + per-party progress (M14).

The quest layer is deliberately tiny for the vertical slice. A
:class:`Quest` is typed configuration: id, display strings, completion
criteria, and rewards. Per-party progress lives on
:class:`PartyQuestLog`, a save-friendly mapping from quest id to
:class:`QuestState`. The :class:`QuestRegistry` (and the module-level
:data:`QUESTS` instance) hold every quest the game knows about so the
content shape stays declarative.

Why typed config and not a scripting hook? Same reasoning as M13
dialogue: quests are content the reviewer should be able to skim, and
quest state has to round-trip through JSON saves cleanly. The accept
verb and completion check are reusable helpers in :mod:`src.core.quest`
itself; the App wires them in through the dialogue effect bus and the
effect applier hooks for kill / pickup.

A quest's completion criteria are a small data record
(:class:`QuestObjective`) describing the boss-marker token the party
must kill and the item id they must possess. The applier checks both
conditions on every kill / pickup that could move progress forward; the
quest flips to ``completed`` only when both are satisfied. Rewards are
a flat list of :class:`QuestReward` rows that the applier translates
into world effects (gold + XP grant).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class QuestState(str, Enum):
    """Lifecycle of a quest for the party.

    ``not_offered`` is the implicit default for a quest the party
    hasn't heard about; the log never stores it explicitly so saves
    stay terse. ``offered`` is set when the player opens dialogue with
    the quest giver (today the only quest, "The Sunken Gate", is
    offered as soon as the player talks to Captain Tane). ``accepted``
    is the active state — completion criteria are checked. ``completed``
    is the terminal success state; rewards have been applied.
    ``failed`` is reserved for future quests (no current quest can
    fail).
    """

    NOT_OFFERED = "not_offered"
    OFFERED = "offered"
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class QuestObjective:
    """Completion criteria for "The Sunken Gate"-shaped quests (M14).

    The vertical slice uses a single objective shape: kill a creature
    with the given ``boss_marker`` token AND have the party hold at
    least one of ``treasure_item_id`` in any member's inventory. The
    boss marker is checked via :class:`BossMarker` on the kill effect;
    the treasure check walks party + container inventories at the time
    of any progress event.
    """

    boss_marker: str
    treasure_item_id: str


@dataclass(frozen=True, slots=True)
class QuestReward:
    """Reward to apply when a quest completes.

    Gold is granted to every party member's inventory (matching the M30
    per-actor purse model — the project has not unified to a shared
    purse yet). ``xp_per_member`` is reserved for the M25 leveling
    pass; M14 emits a message announcing the XP grant but does not yet
    mutate XP fields (no XP component exists). Persisting the value
    here means M25 can wire it up without touching content.
    """

    gold_per_member: int = 0
    xp_per_member: int = 0


@dataclass(frozen=True, slots=True)
class Quest:
    """Typed quest definition (M14).

    Identified by ``id`` (the registry key). ``name`` and
    ``description`` are surfaced to the player by the future quest log
    UI; ``accept_message`` and ``completion_message`` are emitted into
    the message log when the player accepts and completes the quest.
    ``victory_condition`` is the player-visible description of "how to
    finish" — surfaced on accept so the player knows what to do next.
    """

    id: str
    name: str
    description: str
    accept_message: str
    completion_message: str
    victory_condition: str
    objective: QuestObjective
    reward: QuestReward = field(default_factory=QuestReward)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


SUNKEN_GATE_QUEST_ID = "sunken_gate"


def _sunken_gate_quest() -> Quest:
    return Quest(
        id=SUNKEN_GATE_QUEST_ID,
        name="The Sunken Gate",
        description=(
            "Captain Tane has heard whispers of a kobold warlord ruling "
            "the depths beneath the Sunken Gate, hoarding a golden "
            "chalice stolen from the old chapel."
        ),
        accept_message=(
            "You agree to help Captain Tane. The Sunken Gate awaits."
        ),
        completion_message=(
            "You return triumphant: the warlord is dead and the golden "
            "chalice is yours. Captain Tane will be pleased."
        ),
        victory_condition=(
            "Defeat the boss at the dungeon's deepest level and bring "
            "back the golden chalice."
        ),
        objective=QuestObjective(
            boss_marker="sunken_gate_warlord",
            treasure_item_id="treasure.golden_chalice",
        ),
        reward=QuestReward(gold_per_member=100, xp_per_member=200),
    )


class QuestRegistry:
    """Mapping from quest id to :class:`Quest`.

    The registry is a thin wrapper so future content can ``register``
    quests at import time without touching the catalog directly.
    """

    __slots__ = ("_quests",)

    def __init__(self, quests: Mapping[str, Quest] | None = None) -> None:
        self._quests: dict[str, Quest] = dict(quests or {})

    def register(self, quest: Quest) -> None:
        if quest.id in self._quests:
            raise ValueError(f"Quest id already registered: {quest.id!r}")
        self._quests[quest.id] = quest

    def get(self, quest_id: str) -> Quest | None:
        return self._quests.get(quest_id)

    def require(self, quest_id: str) -> Quest:
        quest = self._quests.get(quest_id)
        if quest is None:
            raise KeyError(f"Unknown quest id: {quest_id!r}")
        return quest

    def all(self) -> tuple[Quest, ...]:
        return tuple(self._quests.values())

    def __contains__(self, quest_id: object) -> bool:
        return quest_id in self._quests


QUESTS: QuestRegistry = QuestRegistry({_sunken_gate_quest().id: _sunken_gate_quest()})


# ---------------------------------------------------------------------------
# Per-party progress
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PartyQuestLog:
    """Per-party quest progress (M14).

    Stored on :class:`~src.core.party_state.PartyState` so save/load
    walks it via the existing party serializer. The mapping holds the
    explicit state for every quest the party has touched; quests not
    in the map are implicitly :attr:`QuestState.NOT_OFFERED`.
    """

    states: dict[str, QuestState] = field(default_factory=dict)

    def state_of(self, quest_id: str) -> QuestState:
        return self.states.get(quest_id, QuestState.NOT_OFFERED)

    def set_state(self, quest_id: str, state: QuestState) -> None:
        self.states[quest_id] = state

    def is_completed(self, quest_id: str) -> bool:
        return self.state_of(quest_id) is QuestState.COMPLETED

    def is_accepted(self, quest_id: str) -> bool:
        return self.state_of(quest_id) is QuestState.ACCEPTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "states": {qid: state.value for qid, state in self.states.items()},
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "PartyQuestLog":
        log = cls()
        if not payload:
            return log
        for qid, state_raw in (payload.get("states") or {}).items():
            try:
                log.states[str(qid)] = QuestState(state_raw)
            except ValueError:
                # Unknown state token — drop quietly. Forward-compat with
                # quests that introduced new lifecycle states.
                continue
        return log


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------


def offer_quest(log: PartyQuestLog, quest_id: str) -> bool:
    """Mark ``quest_id`` as offered if it has not been accepted yet.

    Returns ``True`` if the state actually changed. Quests that are
    already accepted, completed, or failed are left alone.
    """
    current = log.state_of(quest_id)
    if current is QuestState.NOT_OFFERED:
        log.set_state(quest_id, QuestState.OFFERED)
        return True
    return False


def accept_quest(log: PartyQuestLog, quest_id: str) -> bool:
    """Mark ``quest_id`` as accepted if it hasn't already been.

    Returns ``True`` if the state actually changed. Quests that are
    completed or failed are not re-accepted; quests that are not yet
    offered are auto-offered then accepted (covers the test case where
    the player jumps straight from `not_offered` to `accepted`).
    """
    current = log.state_of(quest_id)
    if current in (QuestState.COMPLETED, QuestState.FAILED):
        return False
    if current is QuestState.ACCEPTED:
        return False
    log.set_state(quest_id, QuestState.ACCEPTED)
    return True
