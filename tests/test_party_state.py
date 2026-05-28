"""Tests for the PartyState world abstraction (M45)."""

from __future__ import annotations

import pytest

from src.app import create_app
from src.core.entity import EntityId
from src.core.party_state import PartyState


def _members(*ids: int) -> list[EntityId]:
    return [EntityId(i) for i in ids]


# ---------------------------------------------------------------------------
# Default app wiring
# ---------------------------------------------------------------------------


def test_create_app_builds_party_state_of_size_four() -> None:
    app = create_app()
    assert isinstance(app.party, PartyState)
    assert app.party.size == 4
    assert app.party.members[0] == app.player


# ---------------------------------------------------------------------------
# Membership operations
# ---------------------------------------------------------------------------


def test_from_members_populates_members_and_follow_order() -> None:
    party = PartyState.from_members(_members(1, 2, 3))
    assert party.members == _members(1, 2, 3)
    assert party.follow_order == _members(1, 2, 3)
    assert party.active_index == 0
    assert party.focused_index is None


def test_recruit_adds_member_and_extends_follow_order() -> None:
    party = PartyState.from_members(_members(1, 2))
    party.recruit(EntityId(3))
    assert party.members == _members(1, 2, 3)
    assert party.follow_order == _members(1, 2, 3)


def test_recruit_is_idempotent() -> None:
    party = PartyState.from_members(_members(1, 2))
    party.recruit(EntityId(2))
    assert party.members == _members(1, 2)


def test_is_member_reports_membership() -> None:
    party = PartyState.from_members(_members(1, 2, 3))
    assert party.is_member(EntityId(2))
    assert not party.is_member(EntityId(99))


def test_dismiss_removes_member() -> None:
    party = PartyState.from_members(_members(1, 2, 3))
    party.dismiss(EntityId(2))
    assert party.members == _members(1, 3)
    assert party.follow_order == _members(1, 3)


def test_dismiss_unknown_entity_is_noop() -> None:
    party = PartyState.from_members(_members(1, 2))
    party.dismiss(EntityId(99))
    assert party.members == _members(1, 2)


def test_dismiss_shifts_active_index_when_earlier_member_leaves() -> None:
    party = PartyState.from_members(_members(1, 2, 3))
    party.active_index = 2
    party.dismiss(EntityId(1))
    # The previously-active entity (id 3) should remain active even
    # though its index dropped.
    assert party.members == _members(2, 3)
    assert party.active_index == 1
    assert party.active_member() == EntityId(3)


def test_dismiss_clamps_active_index_when_active_member_leaves() -> None:
    party = PartyState.from_members(_members(1, 2, 3))
    party.active_index = 2
    party.dismiss(EntityId(3))
    assert party.members == _members(1, 2)
    assert party.active_index == 1


def test_dismiss_last_member_resets_indexes() -> None:
    party = PartyState.from_members(_members(1))
    party.focused_index = 0
    party.dismiss(EntityId(1))
    assert party.is_empty
    assert party.active_index == 0
    assert party.focused_index is None
    assert party.active_member() is None


# ---------------------------------------------------------------------------
# Active / focus
# ---------------------------------------------------------------------------


def test_active_member_returns_member_at_active_index() -> None:
    party = PartyState.from_members(_members(1, 2, 3))
    party.active_index = 1
    assert party.active_member() == EntityId(2)


def test_lead_defaults_to_active_member() -> None:
    party = PartyState.from_members(_members(1, 2, 3))
    party.active_index = 2
    assert party.lead() == EntityId(3)


def test_lead_follows_focused_index_when_set() -> None:
    party = PartyState.from_members(_members(1, 2, 3))
    party.active_index = 0
    party.focused_index = 2
    assert party.lead() == EntityId(3)


def test_lead_on_empty_party_raises() -> None:
    party = PartyState()
    with pytest.raises(LookupError):
        party.lead()


# ---------------------------------------------------------------------------
# Swap active
# ---------------------------------------------------------------------------


def test_swap_active_wraps_forward() -> None:
    party = PartyState.from_members(_members(1, 2, 3))
    party.active_index = 2
    assert party.swap_active(+1) == EntityId(1)
    assert party.active_index == 0


def test_swap_active_wraps_backward() -> None:
    party = PartyState.from_members(_members(1, 2, 3))
    party.active_index = 0
    assert party.swap_active(-1) == EntityId(3)
    assert party.active_index == 2


def test_swap_active_on_empty_party_returns_none() -> None:
    party = PartyState()
    assert party.swap_active(+1) is None


# ---------------------------------------------------------------------------
# Save / load
# ---------------------------------------------------------------------------


def test_to_dict_produces_json_safe_payload() -> None:
    party = PartyState.from_members(_members(1, 2, 3))
    party.active_index = 1
    party.focused_index = 2

    snapshot = party.to_dict()

    assert snapshot == {
        "members": [1, 2, 3],
        "active_index": 1,
        "focused_index": 2,
        "follow_order": [1, 2, 3],
    }


def test_from_dict_round_trips() -> None:
    original = PartyState.from_members(_members(7, 8, 9))
    original.active_index = 2
    original.focused_index = 1
    original.follow_order = _members(9, 7, 8)

    rebuilt = PartyState.from_dict(original.to_dict())

    assert rebuilt == original
    assert rebuilt.members == original.members
    assert rebuilt.active_index == original.active_index
    assert rebuilt.focused_index == original.focused_index
    assert rebuilt.follow_order == original.follow_order


def test_from_dict_defaults_follow_order_to_members() -> None:
    rebuilt = PartyState.from_dict(
        {"members": [4, 5], "active_index": 0, "focused_index": None}
    )
    assert rebuilt.follow_order == _members(4, 5)
