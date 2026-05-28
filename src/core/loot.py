"""Drop tables and loot rolls (M30).

Drop tables are typed config that describe what a monster (or container)
might leave behind on death/open. Each ``DropTable`` is a list of
``LootEntry`` rows; rolling the table produces a concrete ``LootRoll``
with a gold amount and item stacks that callers can stuff into an
``Inventory`` component (the same model used everywhere else).

Determinism is required — every test and the AI death pipeline pass an
explicit ``random.Random`` so seeded fixtures roll the same loot every
time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from random import Random
from typing import TypeAlias


@dataclass(frozen=True, slots=True)
class ItemDrop:
    """A single item line on a drop table.

    ``probability`` is the chance in ``[0, 1]`` that this entry fires when
    the table is rolled. When it fires, the quantity is uniformly sampled
    in ``[quantity_min, quantity_max]``.
    """

    item_id: str
    probability: float = 1.0
    quantity_min: int = 1
    quantity_max: int = 1


@dataclass(frozen=True, slots=True)
class GoldDrop:
    """A gold line on a drop table.

    Always fires when rolled (no probability), but the amount is
    uniformly sampled. Multiple ``GoldDrop`` entries on the same table
    are summed.
    """

    amount_min: int
    amount_max: int


LootEntry: TypeAlias = ItemDrop | GoldDrop


@dataclass(frozen=True, slots=True)
class DropTable:
    """Typed config: zero or more loot rows.

    Empty tables are legal and roll to no gold and no items — convenient
    for monsters that should hold a corpse on death but no contents.
    """

    entries: tuple[LootEntry, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class LootRoll:
    """The concrete outcome of one ``roll_loot`` call."""

    gold: int
    items: tuple[tuple[str, int], ...]


def roll_loot(table: DropTable, rng: Random) -> LootRoll:
    """Roll ``table`` once with ``rng`` and return the concrete drops.

    Item entries are validated against the items catalog so a bad
    ``item_id`` fails fast at roll time rather than silently producing a
    pile of nothing. Quantity ranges with ``min > max`` are also
    rejected.
    """

    # Imported lazily to avoid an items -> components -> loot import
    # cycle (items references InventoryStack on the components module
    # that imports DropTable from this file).
    from src.core.items import require_item

    gold = 0
    items: list[tuple[str, int]] = []
    for entry in table.entries:
        if isinstance(entry, GoldDrop):
            if entry.amount_max < entry.amount_min:
                raise ValueError(
                    f"GoldDrop has amount_max < amount_min: {entry}"
                )
            gold += rng.randint(entry.amount_min, entry.amount_max)
            continue
        # ItemDrop
        require_item(entry.item_id)
        if entry.quantity_max < entry.quantity_min:
            raise ValueError(
                f"ItemDrop has quantity_max < quantity_min: {entry}"
            )
        if entry.probability >= 1.0 or rng.random() < entry.probability:
            quantity = rng.randint(entry.quantity_min, entry.quantity_max)
            if quantity > 0:
                items.append((entry.item_id, quantity))
    return LootRoll(gold=gold, items=tuple(items))
