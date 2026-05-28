# Loot, corpses, and dropped items (M30)

This file describes the loot pipeline introduced in M30. The agentic
playtester (M37) and the future `?` help integration (M39) read this
file directly.

## Player-facing keys

| Key | Mode      | Effect |
| --- | ---       | --- |
| `,` | play      | Pick up everything on the tile under the active actor — gold, dropped items, the contents of any corpse on the tile, and any open ground-drop pile. |
| `d` | inventory | Drop the first non-equipped stack from the active actor's inventory onto the actor's tile. |
| `e` | play      | Interact with the adjacent tile (M9). For containers, this opens them in place; their contents stay accessible to `,` once standing on top. |

Pickup and drop are full actions. In turn-based play (`Battle`, `Turn`)
they consume the active actor's action just like an attack or an
interaction. In explore mode they are free, but they still tick the
world clock by one turn so scheduled effects stay aligned with the
player's pace.

## Drop tables

Drop tables live alongside the creature catalog
(`src/core/creatures.py`). Each `CreatureSpec` carries a
`DropTable`; an empty `DropTable()` means "no loot — corpse only".
Entries are typed:

- `GoldDrop(amount_min, amount_max)` — always fires; the amount is
  uniform in the range.
- `ItemDrop(item_id, probability, quantity_min, quantity_max)` — fires
  with `probability`; quantity is uniform in the range.

Rolls go through `roll_loot(table, rng)` and consume entropy from the
caller-supplied `random.Random`. The app threads a single
`App.loot_rng` through every kill so seeded playtest fixtures see
deterministic drops.

## Death flow

1. Combat (or any other damage source) emits `KillEntity(victim)`.
2. The `EffectApplier`'s kill handler checks for a `LootDrop`
   component on the victim. If present, it rolls the table once and
   spawns a corpse entity at the victim's tile with an `Inventory`
   carrying the rolled gold + items.
3. The corpse is non-blocking, presented as `%`, and named after the
   creature kind (e.g. `goblin corpse`). It persists even after being
   looted so the kill stays visible on the map.

## Ground items

"Loose" piles created by `DropItemAttempt` are independent entities at
the actor's tile with an `Inventory` and no `Corpse`/`Container`
marker. Dropping additional items onto the same tile merges into the
existing pile; corpses are NOT merged into so loot piles stay distinct
from kill records. Loose piles are removed automatically when they go
empty via pickup.

## Save-friendliness

The whole pipeline writes only into existing component stores
(`positions`, `presentations`, `names`, `inventories`, plus new
`corpses` and `loot_drops`). The save layer (#16) walks every
`ComponentStore` on `World`, so loot state round-trips without any
schema-specific code.
