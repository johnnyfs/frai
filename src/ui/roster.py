"""Party roster modal projection (M-feature: roster).

Pure projection over ``world`` + ``party_state``. Builds a list of
:class:`RosterEntry` records the renderer and observation layer can
display without re-reading component stores. The modal is render-only:
opening or scrolling does not mutate any world state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from src.core.conditions import ConditionStore
from src.core.entity import EntityId
from src.core.world import World


@dataclass(frozen=True, slots=True)
class RosterEntry:
    """One row in the roster list.

    Fields are projected from the world component stores at the moment
    the modal is built. Reads are by-value where possible so a partial
    refresh (e.g. after a hostile attack) doesn't render stale halves.
    """

    entity: EntityId
    name: str
    character_class: str
    level: int
    hp: int
    max_hp: int
    conditions: tuple[str, ...]


@dataclass(slots=True)
class RosterState:
    """Transient state for the open roster modal."""

    entries: tuple[RosterEntry, ...]
    cursor: int = 0
    previous_mode: str | None = None

    def move_cursor(self, delta: int) -> None:
        if not self.entries:
            return
        self.cursor = max(0, min(len(self.entries) - 1, self.cursor + delta))

    def selected(self) -> RosterEntry | None:
        if not self.entries:
            return None
        return self.entries[self.cursor]


def build_roster(world: World, members: Iterable[EntityId]) -> tuple[RosterEntry, ...]:
    """Project ``members`` against ``world`` into roster entries.

    Members without a Position component (i.e. removed from the world)
    are skipped — the roster only shows actors who exist in the world.
    """

    entries: list[RosterEntry] = []
    for member in members:
        position = world.positions.get(member)
        if position is None:
            continue
        name = world.name_for(member)
        character = world.characters.get(member)
        if character is not None:
            character_class = character.sheet.character_class
            level = character.sheet.level
        else:
            character_class = "?"
            level = 1
        stats = world.combat_stats.get(member)
        if stats is not None:
            hp = stats.hit_points
            max_hp = stats.max_hit_points
        else:
            hp = 0
            max_hp = 0
        conditions = _conditions_for(world.conditions.get(member))
        entries.append(
            RosterEntry(
                entity=member,
                name=name,
                character_class=character_class,
                level=level,
                hp=hp,
                max_hp=max_hp,
                conditions=conditions,
            )
        )
    return tuple(entries)


def _conditions_for(store: ConditionStore | None) -> tuple[str, ...]:
    if store is None:
        return ()
    return tuple(condition.kind.value for condition in store.conditions)


def roster_line(entry: RosterEntry, *, selected: bool = False) -> str:
    """Format a single roster row for display.

    The leading marker (``>`` for the cursor) is reserved for the
    renderer; this helper formats only the row body so tests can assert
    against a stable string.
    """

    class_part = f"{entry.character_class} L{entry.level}"
    hp_part = f"HP {entry.hp}/{entry.max_hp}"
    cond_part = ""
    if entry.conditions:
        cond_part = "  [" + ",".join(entry.conditions) + "]"
    marker = ">" if selected else " "
    return f"{marker} {entry.name:<16} {class_part:<18} {hp_part}{cond_part}"
