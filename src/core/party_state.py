"""PartyState — first-class world abstraction for the player party.

Before M45 the party lived as a bare ``list[EntityId]`` directly on
``App``, with a separate ``active_party_index`` and a hand-rolled
``focus`` derivation tucked into the turn controller. That worked while
recruitment was static and there was no formation, but every milestone
that touches party composition or per-member camera state needs the
same data: who is in the party, who is currently acting, who the camera
follows, and what the marching order is in explore mode.

This module promotes those concerns into ``PartyState``:

- ``members``: the canonical list of party entity ids, in recruitment
  order. The head of the list is treated as the player by everything
  downstream that doesn't care about turn-based mode.
- ``active_index``: who is currently acting in turn-based combat. The
  ``TurnController`` reads this directly via ``active_member()``.
- ``focused_index``: who the camera/UI is following. Defaults to the
  active member but can be steered independently (e.g. cycling through
  party views while it's not your turn).
- ``follow_order``: the formation queue in explore mode. When the lead
  moves, followers shuffle into the lead's previous tile. Today it
  mirrors ``members`` but is split out so M13 recruitment and future
  formation commands can reorder it without disturbing recruitment
  order.

Forward seams
-------------

- M13 recruitment goes through ``recruit()`` / ``dismiss()``; it owns
  whatever world-side bookkeeping (placement, dialogue) is needed and
  hands the resulting entity to ``PartyState``.
- M28 faction model will store party-side faction relations next to
  ``members`` (or as a sibling structure that takes ``members`` as a
  view) — ``PartyState`` is the obvious home for "the party trusts
  faction X".
- M49 ``GameState`` consumes ``PartyState`` directly via composition
  rather than going through ``App``.

Gold decision
-------------

Per-actor gold stays on ``Inventory.gold`` for now. A future milestone
may move to a shared party purse, but doing it here would require
touching shop, loot, and quest reward flows and is out of scope.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import overload

from src.core.entity import EntityId
from src.core.quest import PartyQuestLog


@dataclass(slots=True)
class PartyState:
    """The player party: members, active actor, camera focus, formation.

    The data shape is deliberately small. Methods on this class only
    mutate party-shaped state; they do not touch the world or the
    activation map. Mode/turn logic lives in ``TurnController``.
    """

    members: list[EntityId] = field(default_factory=list)
    active_index: int = 0
    focused_index: int | None = None
    follow_order: list[EntityId] = field(default_factory=list)
    quests: PartyQuestLog = field(default_factory=PartyQuestLog)

    # ------------------------------------------------------------------
    # Membership
    # ------------------------------------------------------------------

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def is_empty(self) -> bool:
        return not self.members

    def is_member(self, entity: EntityId) -> bool:
        return entity in self.members

    def lead(self) -> EntityId:
        """Camera-followed entity, defaulting to the active member.

        Used by rendering and movement: in explore mode it determines
        which tile the camera centers on and which entity leads the
        follow-the-leader displacement. In turn-based mode the focused
        member is typically the same as the active member, but the UI
        may override (e.g. preview another party member's threatened
        tiles).
        """
        if not self.members:
            raise LookupError("PartyState has no members.")
        if self.focused_index is not None and 0 <= self.focused_index < len(self.members):
            return self.members[self.focused_index]
        return self.members[self.active_index]

    def active_member(self) -> EntityId | None:
        if not self.members:
            return None
        if not 0 <= self.active_index < len(self.members):
            return None
        return self.members[self.active_index]

    def recruit(self, entity: EntityId) -> None:
        """Add ``entity`` to the party. No-op if already a member."""
        if entity in self.members:
            return
        self.members.append(entity)
        self.follow_order.append(entity)

    def dismiss(self, entity: EntityId) -> None:
        """Remove ``entity`` from the party.

        If the dismissed entity was the active actor, the next member
        (or 0 if the party becomes empty) becomes active. The focused
        index follows the same rule.
        """
        if entity not in self.members:
            return
        index = self.members.index(entity)
        self.members.pop(index)
        if entity in self.follow_order:
            self.follow_order.remove(entity)
        if not self.members:
            self.active_index = 0
            self.focused_index = None
            return
        if self.active_index >= len(self.members):
            self.active_index = max(0, len(self.members) - 1)
        elif index < self.active_index:
            self.active_index -= 1
        if self.focused_index is not None:
            if self.focused_index >= len(self.members):
                self.focused_index = max(0, len(self.members) - 1)
            elif index < self.focused_index:
                self.focused_index -= 1

    def swap_active(self, direction: int) -> EntityId | None:
        """Rotate the active actor by ``direction`` (typically +1 or -1).

        Returns the new active member, or ``None`` if the party is
        empty. Wraps around the member list.
        """
        if not self.members:
            return None
        self.active_index = (self.active_index + direction) % len(self.members)
        return self.members[self.active_index]

    # ------------------------------------------------------------------
    # Transitional sequence protocol
    # ------------------------------------------------------------------
    #
    # Many call sites still consume the party as a ``Sequence[EntityId]``
    # (renderer, AI, awareness queries). Delegating these operations to
    # ``members`` keeps the migration diff small. New code should prefer
    # the explicit ``party.members`` or the named methods above.

    def __iter__(self) -> Iterator[EntityId]:
        return iter(self.members)

    def __len__(self) -> int:
        return len(self.members)

    def __contains__(self, entity: object) -> bool:
        return entity in self.members

    @overload
    def __getitem__(self, index: int) -> EntityId: ...
    @overload
    def __getitem__(self, index: slice) -> list[EntityId]: ...
    def __getitem__(self, index: int | slice) -> EntityId | list[EntityId]:
        return self.members[index]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, PartyState):
            return (
                self.members == other.members
                and self.active_index == other.active_index
                and self.focused_index == other.focused_index
                and self.follow_order == other.follow_order
            )
        if isinstance(other, list):
            # Transitional convenience for tests/comparisons against the
            # raw member list. New code should compare ``party.members``
            # explicitly.
            return self.members == other
        return NotImplemented

    def __hash__(self) -> int:  # pragma: no cover - PartyState is mutable
        raise TypeError("PartyState is mutable and not hashable")

    # ------------------------------------------------------------------
    # Save / load
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "members": [int(entity) for entity in self.members],
            "active_index": self.active_index,
            "focused_index": self.focused_index,
            "follow_order": [int(entity) for entity in self.follow_order],
            "quests": self.quests.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PartyState":
        members = [EntityId(int(value)) for value in data.get("members", [])]
        follow_order_raw = data.get("follow_order")
        if follow_order_raw is None:
            follow_order = list(members)
        else:
            follow_order = [EntityId(int(value)) for value in follow_order_raw]
        focused = data.get("focused_index")
        return cls(
            members=members,
            active_index=int(data.get("active_index", 0)),
            focused_index=None if focused is None else int(focused),
            follow_order=follow_order,
            quests=PartyQuestLog.from_dict(data.get("quests")),
        )

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_members(cls, members: Iterable[EntityId]) -> "PartyState":
        member_list = list(members)
        return cls(members=member_list, follow_order=list(member_list))
