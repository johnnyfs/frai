# Spells and spell slots (M11)

This document describes the M11 representative spell action path. The
agentic playtester (M37) and the future `?` help integration (M39)
read this file directly.

## Key bindings

| Key                 | Effect                                                |
| ---                 | ---                                                   |
| `s` (in play)       | Open the spell menu for the active actor              |
| `a`..`z` (menu)     | Pick the spell at that letter slot                    |
| `q` / `Esc` (menu)  | Close the menu; no slot is consumed                   |

When a spell needs a target the menu hands off to the M20 targeting
modal; the standard cursor keys (`h j k l y u b n`) move the cursor,
`Enter` or `Space` confirms, and `q` or `Esc` cancels without
consuming a slot.

## Target validity

Single-entity spell targets are checked by
`make_spell_target_predicate` (`src/core/targeting.py`):

- **Damage spells** (`magic_missile`, `firebolt`) require a *hostile*
  target with combat stats. Friendly party members are rejected (no
  friendly-fire). The caster's own tile is also rejected — confirming
  on the caster's tile would otherwise self-target whenever the cursor
  opens on the caster.
- **Friendly / heal spells** (`cure_wounds`) require a non-hostile
  target. The caster's own tile is allowed only when the spell
  declares `allow_self_target=True` (Cure Wounds does).

An illegal confirm emits `"Invalid target."`, keeps the modal open,
and consumes no slot.

## Catalog

The current catalog (`src/core/spells.py`) is the M11 representative
set:

| Spell id        | Name            | Level | Target          | Effect                                                              |
| ---             | ---             | ---   | ---             | ---                                                                 |
| `magic_missile` | Magic Missile   | 1     | single entity   | Auto-hit. 3 missiles × (1d4 + 1) force damage.                      |
| `firebolt`      | Fire Bolt       | 0     | single entity   | Cantrip; spell attack vs AC. 1d10 fire on hit (doubles on a crit).  |
| `cure_wounds`   | Cure Wounds     | 1     | single entity   | Restores 1d8 + spellcasting modifier HP.                            |
| `burning_hands` | Burning Hands   | 1     | area (r=1)      | 3d6 fire to every entity at the cursor tile and its 8 neighbours. DEX save halves. |
| `bless`         | Bless           | 1     | up to 3 allies  | Applies `BLESSED` for 1 minute. Caster gains `CONCENTRATING`.       |

Cantrips (`level == 0`) consume no slot. Leveled spells consume one
slot at their level when cast.

## Components

- **`SpellList(known)`** — the spells an actor has prepared, in menu
  order. Stored on the entity; round-trips through save/load.
- **`SpellSlots(slots_by_level, max_by_level)`** — the actor's slot
  ledger. Per-level remaining and maximum counts are tracked
  separately so the rest system (M34) can refill maxima without
  losing the per-class progression.

Both components are JSON-serialisable and live alongside the rest of
the world's component stores.

## Action flow

1. The player presses `s` in `UIMode.play`. The input layer emits
   `SpellMenuRequest(actor=...)` and the App switches to
   `UIMode.spell_menu`.
2. The player presses a letter. The App resolves the letter to a
   `spell_id` from the active actor's `SpellList` and either:
   - opens a `TargetingState` (single-entity / area spells), or
   - dispatches the cast immediately (friendly-group spells target
     the first N party members within range).
3. On confirm, the targeting modal builds a `CastSpellAttempt` and
   hands it to `app.resolve_action`.
4. The M46 resolver walks the phases:
   - **`PRE_CHECK`** — the App's spell pre-check looks up the spell in
     the catalog, checks slot availability, and either cancels with
     `"No spell slot available."` or emits a typed `ConsumeSpellSlot`
     effect. Cantrips skip the slot check entirely.
   - **`RESOLVE`** — the `SpellSystem` produces the damage / heal /
     condition effects according to the spell's `target_kind`,
     `damage_dice`, `healing_dice`, etc.
   - **Reaction hook** — the App watches every resolved attempt for
     `DamageEntity` effects whose target is currently
     `CONCENTRATING`; when it finds one, it appends an
     `EndCondition(CONCENTRATING)` to break concentration. This is
     the M24 seam for "damage breaks concentration".

A failed pre-check short-circuits the dispatcher so the spell system
never runs and the slot is preserved.

## Concentration

`bless` flags `concentration=True` in the catalog. When the spell
system applies the buff, it also emits an
`ApplyCondition(CONCENTRATING, Minutes(1))` for the caster. Because
the `apply_condition` helper special-cases `CONCENTRATING`, applying
a second concentration condition automatically ends the prior one —
the caster can only concentrate on one spell at a time.

The reaction hook breaks concentration on any damage taken in the
same resolved attempt. M11 intentionally keeps this loose: the SRD
"DC 10 or half damage, whichever is higher" save is M24 follow-up
work. The current behaviour is "any damage breaks", which is
conservative and easy to test.

## Saving throws

Save-vs-spell uses the M26 `Save` machinery. The DC is
`8 + proficiency_bonus + spellcasting_modifier` (computed by
`spell_save_dc`), and the target rolls a normal-advantage save with
its raw ability modifier. M25 (leveling) will add save proficiencies;
until then the bare ability modifier is used.

## Spell attack rolls

Attack-roll spells (Fire Bolt) use `spell_attack_bonus =
proficiency_bonus + spellcasting_modifier`. Natural 1 always misses;
natural 20 always hits and doubles the damage dice (the SRD crit
rule). Misses emit no damage effect.

## Save / load

`SpellList` and `SpellSlots` are JSON-serialisable. They round-trip
through `World.to_dict` like any other component store. A save
written mid-cast (before pre-check fires) does not consume a slot;
saves written between pre-check and applier are not a concern in
practice because the resolver is synchronous.

## Forward seams

- **M14 quest path** — the catalog is the natural attachment point
  for quest spells.
- **M15 boss / villain** — boss creatures will hold a `SpellList`
  and cast via the same `CastSpellAttempt`. The AI system can reuse
  the resolver.
- **M25 leveling** — leveling will grow `SpellSlots` per SRD
  progression and unlock new entries in `SpellList`. The current
  `starting_spell_loadout_for_class` helper is the seam.
- **M34 rest** — `SpellSlots.reset_to_max()` is the long-rest entry
  point.
