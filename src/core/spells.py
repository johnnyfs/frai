"""Spells and spell catalog (M11).

Spells are typed data describing a representative action path through
the engine: attack-like (Magic Missile, Fire Bolt), saving-throw
(Burning Hands), healing (Cure Wounds), and concentration / buff
(Bless). Each spell flows through the M46 phased resolver as a
:class:`~src.core.actions.CastSpellAttempt` action, consuming a slot
in ``PRE_CHECK`` so a failed resolve doesn't burn the resource.

Design constraints
------------------

- **Pure data.** :class:`Spell` is a frozen dataclass. The catalog is a
  module-level mapping by ``spell_id`` (lowercase ``"magic_missile"``)
  so save data round-trips a stable string identifier rather than a
  reference to the catalog entry.
- **Save-friendly.** :class:`SpellSlots` and :class:`SpellList` are
  ordinary component stores. Both round-trip to JSON via the world
  serialization in ``src/core/world.py``. Slot levels are integer
  keys; lists are tuples of ``spell_id`` strings.
- **No App coupling.** The catalog and the resolver (in
  ``src/systems/spell_system.py``) consume world state only. Targeting
  semantics are described declaratively here (``target_kind``,
  ``range``), and the App's spell menu reads those fields to build a
  :class:`~src.core.targeting.TargetingState` when needed.

Spell catalog
-------------

The five spells implemented here are the minimum representative set
the M11 acceptance criteria demand:

================ ============= ============== ==========================
spell_id         category      target_kind    notes
================ ============= ============== ==========================
magic_missile    auto-hit      single_entity  3 missiles, 1d4+1 force
firebolt         attack roll   single_entity  cantrip, 1d10 fire
cure_wounds      heal          friendly       1d8 + caster_mod
burning_hands    area save     area_radius    DEX save, 3d6 fire, r=1
bless            buff          friendly_x3    concentration, +d4 attack
================ ============= ============== ==========================

Forward seams
-------------

- **M14 quest path.** The catalog is the natural attachment point for
  quest spells (Detect Magic, Knock, etc.). Adding a quest spell is a
  one-entry change here plus an effect-builder in the spell system.
- **M15 boss / villain.** Boss creatures will hold a ``SpellList`` and
  cast through the same :class:`~src.core.actions.CastSpellAttempt`
  the player uses. The AI system can therefore reuse the resolver.
- **M25 leveling.** ``level`` on the catalog entry tracks the SRD
  spell level. Leveling will grow ``SpellSlots`` per the SRD table and
  unlock new entries in ``SpellList`` via the leveling system.
- **M24 concentration.** :func:`concentration_condition` produces the
  ``concentrating`` condition the spell system applies for spells
  flagged ``concentration``. The reaction hook in ``src.app``
  watches for damage on a concentrating caster and ends the condition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.core.entity import EntityId


# ---------------------------------------------------------------------------
# Spell shape
# ---------------------------------------------------------------------------


class SpellTargetKind(str, Enum):
    """How a spell selects its target(s).

    Today the engine recognises three modes; the M20 targeting modal
    composes a different :class:`~src.core.targeting.TargetingState`
    per kind.
    """

    SINGLE_ENTITY = "single_entity"
    """A single entity at the cursor (Magic Missile, Fire Bolt, Cure Wounds)."""

    AREA_RADIUS = "area_radius"
    """Every entity within ``area_radius`` of the cursor (Burning Hands)."""

    FRIENDLY_GROUP = "friendly_group"
    """Up to ``group_size`` friendly entities (Bless)."""


class SpellSchool(str, Enum):
    """The arcane / divine / etc. school. Informational only today."""

    ABJURATION = "abjuration"
    CONJURATION = "conjuration"
    DIVINATION = "divination"
    ENCHANTMENT = "enchantment"
    EVOCATION = "evocation"
    ILLUSION = "illusion"
    NECROMANCY = "necromancy"
    TRANSMUTATION = "transmutation"


@dataclass(frozen=True, slots=True)
class Spell:
    """A typed spell entry.

    Fields:

    - ``spell_id``: stable string identifier used in save data and
      :class:`SpellList`.
    - ``name``: player-facing label.
    - ``level``: SRD spell level (0 = cantrip).
    - ``school``: :class:`SpellSchool`.
    - ``range``: maximum cursor Chebyshev distance from the caster
      (in tiles). 0 means "self / touch".
    - ``casting_time``: human label ("1 action", "1 bonus action").
      The engine charges one Action today regardless — the field is
      surfaced so M11 follow-ups (bonus-action spells) can branch.
    - ``duration``: human label ("Instantaneous", "1 minute", ...).
    - ``target_kind``: :class:`SpellTargetKind`.
    - ``area_radius``: radius in tiles for :class:`SpellTargetKind.AREA_RADIUS`.
    - ``group_size``: maximum target count for
      :class:`SpellTargetKind.FRIENDLY_GROUP`.
    - ``concentration``: whether the spell applies ``concentrating``
      while it lasts. Mutually compatible with any ``target_kind``.
    - ``save_ability``: when set, targets roll a save with this
      ability (e.g. ``"DEX"``); a successful save halves the damage.
    - ``attack_roll``: when True, the caster makes a spell attack
      roll against the target's AC (Fire Bolt). Cannot be combined
      with ``save_ability`` — pick one.
    - ``damage_dice``: ``(count, die_size, bonus)``; e.g. ``(3, 6, 0)``
      for ``3d6``. ``count == 0`` means "no damage" (heal-only or
      buff spell).
    - ``damage_type``: SRD damage type string ("fire", "force", ...).
    - ``healing_dice``: ``(count, die_size, bonus)`` for healing
      (Cure Wounds). ``count == 0`` means "no healing".
    - ``missiles``: number of independent damage rolls (Magic Missile
      is 3). Each missile rolls ``damage_dice`` separately.
    """

    spell_id: str
    name: str
    level: int
    school: SpellSchool
    range: int
    casting_time: str
    duration: str
    target_kind: SpellTargetKind
    area_radius: int = 0
    group_size: int = 1
    concentration: bool = False
    save_ability: str | None = None
    attack_roll: bool = False
    damage_dice: tuple[int, int, int] = (0, 0, 0)
    damage_type: str = ""
    healing_dice: tuple[int, int, int] = (0, 0, 0)
    missiles: int = 1


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


SPELL_CATALOG: dict[str, Spell] = {
    "magic_missile": Spell(
        spell_id="magic_missile",
        name="Magic Missile",
        level=1,
        school=SpellSchool.EVOCATION,
        range=12,
        casting_time="1 action",
        duration="Instantaneous",
        target_kind=SpellTargetKind.SINGLE_ENTITY,
        damage_dice=(1, 4, 1),  # 1d4 + 1 per missile
        damage_type="force",
        missiles=3,
    ),
    "firebolt": Spell(
        spell_id="firebolt",
        name="Fire Bolt",
        level=0,
        school=SpellSchool.EVOCATION,
        range=12,
        casting_time="1 action",
        duration="Instantaneous",
        target_kind=SpellTargetKind.SINGLE_ENTITY,
        attack_roll=True,
        damage_dice=(1, 10, 0),
        damage_type="fire",
    ),
    "cure_wounds": Spell(
        spell_id="cure_wounds",
        name="Cure Wounds",
        level=1,
        school=SpellSchool.EVOCATION,
        range=1,  # touch (1-tile reach)
        casting_time="1 action",
        duration="Instantaneous",
        target_kind=SpellTargetKind.SINGLE_ENTITY,
        healing_dice=(1, 8, 0),  # 1d8 + caster ability mod
    ),
    "burning_hands": Spell(
        spell_id="burning_hands",
        name="Burning Hands",
        level=1,
        school=SpellSchool.EVOCATION,
        range=1,  # cursor sits adjacent to the caster
        casting_time="1 action",
        duration="Instantaneous",
        target_kind=SpellTargetKind.AREA_RADIUS,
        area_radius=1,
        save_ability="DEX",
        damage_dice=(3, 6, 0),
        damage_type="fire",
    ),
    "bless": Spell(
        spell_id="bless",
        name="Bless",
        level=1,
        school=SpellSchool.ENCHANTMENT,
        range=5,
        casting_time="1 action",
        duration="1 minute",
        target_kind=SpellTargetKind.FRIENDLY_GROUP,
        group_size=3,
        concentration=True,
    ),
}


def spell_for_id(spell_id: str) -> Spell:
    """Look up a spell by id. Raises ``KeyError`` for unknown ids.

    Centralised so spell-system code consults a single seam if the
    catalog grows external sources (data files, scripted content) in
    later milestones.
    """

    return SPELL_CATALOG[spell_id]


# ---------------------------------------------------------------------------
# SpellSlots
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SpellSlots:
    """Per-level spell slot ledger.

    Keys are SRD spell levels (1-9; cantrips have no slot). Values are
    remaining slots at that level. Empty / missing keys mean zero
    remaining. Maxima live alongside remaining counts so a future rest
    system can refill via :meth:`reset_to_max` without losing the
    per-class progression baked in by the leveling system (M25).

    ``slots_by_level`` and ``max_by_level`` are kept as plain ``dict``
    so save/load can round-trip them as JSON objects without custom
    serialization.
    """

    slots_by_level: dict[int, int] = field(default_factory=dict)
    max_by_level: dict[int, int] = field(default_factory=dict)

    @classmethod
    def from_pairs(cls, pairs: dict[int, int]) -> "SpellSlots":
        """Construct from a ``{level: count}`` mapping; ``max`` mirrors counts.

        The natural constructor for catalog data: a wizard at level 1
        gets ``SpellSlots.from_pairs({1: 2})`` (per SRD).
        """

        slots = dict(pairs)
        return cls(slots_by_level=slots, max_by_level=dict(slots))

    def remaining(self, level: int) -> int:
        """Slots remaining at ``level``."""

        return int(self.slots_by_level.get(level, 0))

    def has_slot(self, level: int) -> bool:
        """Convenience: true iff at least one slot at ``level`` is left.

        Cantrips (level 0) are always considered available; the engine
        does not gate them on slot count.
        """

        if level <= 0:
            return True
        return self.remaining(level) > 0

    def consume(self, level: int) -> bool:
        """Spend one slot at ``level``.

        Cantrips (level 0) consume nothing and always succeed.
        Returns ``True`` when a slot was successfully consumed (or
        when the spell is a cantrip), ``False`` when no slot was
        available.
        """

        if level <= 0:
            return True
        remaining = self.remaining(level)
        if remaining <= 0:
            return False
        self.slots_by_level[level] = remaining - 1
        return True

    def reset_to_max(self) -> None:
        """Refill all levels to their recorded maxima (long rest seam)."""

        for level, maximum in self.max_by_level.items():
            self.slots_by_level[level] = maximum

    # -- serialization --------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "slots_by_level": {str(k): int(v) for k, v in self.slots_by_level.items()},
            "max_by_level": {str(k): int(v) for k, v in self.max_by_level.items()},
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SpellSlots":
        slots = {
            int(k): int(v) for k, v in payload.get("slots_by_level", {}).items()
        }
        maxima = {int(k): int(v) for k, v in payload.get("max_by_level", {}).items()}
        return cls(slots_by_level=slots, max_by_level=maxima)


# ---------------------------------------------------------------------------
# SpellList
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SpellList:
    """The spells an entity has prepared / can cast.

    ``known`` is a tuple of ``spell_id`` strings — order is the
    player-facing menu order. Cantrips and leveled spells share the
    list; the slot ledger gates whether a leveled spell can be cast.
    """

    known: tuple[str, ...] = ()

    def has(self, spell_id: str) -> bool:
        return spell_id in self.known

    # -- serialization --------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {"known": list(self.known)}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SpellList":
        return cls(known=tuple(str(item) for item in payload.get("known", ())))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def spellcasting_ability_modifier(world: Any, actor: EntityId) -> int:
    """Return the caster's spellcasting ability modifier.

    Looks up the actor's :class:`~src.core.character_creation.CharacterSheet`,
    consults the class's ``spellcasting_ability``, and returns the
    standard ``(score - 10) // 2`` modifier. Falls back to ``0`` when
    the actor has no sheet or the class has no spellcasting ability
    (e.g. a debug-spawned caster). This is the conservative answer:
    spells still resolve, but bonus damage / healing / save DC is
    minimal.
    """

    from src.core.character_creation import require_class
    from src.core.combat import ability_modifier

    character = world.characters.get(actor)
    if character is None:
        return 0
    try:
        class_option = require_class(character.sheet.character_class)
    except KeyError:
        return 0
    ability = class_option.spellcasting_ability
    if ability is None:
        return 0
    score = character.sheet.attributes.get(ability)
    if score is None:
        return 0
    return ability_modifier(score)


def spell_save_dc(world: Any, actor: EntityId) -> int:
    """Standard SRD save DC: 8 + proficiency + spellcasting modifier.

    Falls back to 10 for non-character actors (debug spawns, etc.)
    so the engine never raises on a save check against a sourceless
    spell.
    """

    stats = world.combat_stats.get(actor)
    if stats is None:
        return 10
    return 8 + stats.proficiency_bonus + spellcasting_ability_modifier(world, actor)


def spell_attack_bonus(world: Any, actor: EntityId) -> int:
    """Spell attack bonus: proficiency + spellcasting modifier.

    Used by attack-roll spells (Fire Bolt). Mirrors the save DC
    formula minus the flat 8.
    """

    stats = world.combat_stats.get(actor)
    if stats is None:
        return 0
    return stats.proficiency_bonus + spellcasting_ability_modifier(world, actor)


def starting_spell_loadout_for_class(character_class: str) -> tuple[tuple[str, ...], dict[int, int]]:
    """Default ``(known_spell_ids, slot_pairs)`` for a fresh level-1 caster.

    Returns the M11 representative loadout: every catalog spell is
    known so a fresh playtest can exercise the full action path. Slot
    counts mirror the SRD "level 1 caster" baseline (two level-1
    slots) for spellcasting classes; non-casters get an empty pair.
    """

    if character_class in {"Wizard", "Sorcerer", "Cleric", "Druid", "Bard", "Warlock"}:
        return tuple(SPELL_CATALOG.keys()), {1: 2}
    if character_class in {"Paladin", "Ranger"}:
        # Half-casters: one slot at level 1 in the SRD's strictest
        # reading, but we give them two so M11 playtests don't have to
        # rest after a single cast.
        return tuple(SPELL_CATALOG.keys()), {1: 2}
    return (), {}


__all__ = [
    "SPELL_CATALOG",
    "Spell",
    "SpellList",
    "SpellSchool",
    "SpellSlots",
    "SpellTargetKind",
    "spell_attack_bonus",
    "spell_for_id",
    "spell_save_dc",
    "spellcasting_ability_modifier",
    "starting_spell_loadout_for_class",
]
