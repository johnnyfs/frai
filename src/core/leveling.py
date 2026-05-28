"""Leveling, XP tables, and level-up application (M25).

Leveling is a tiny, deterministic policy layer the engine consults when
:class:`ExperiencePoints` mutates or a player confirms a pending
level-up. The shape of the policy is borrowed from D&D 5.1 SRD-lite and
deliberately scoped to the levels the vertical slice actually visits
(1, 2, and 3). Higher levels can be added by extending
:data:`XP_THRESHOLDS` without touching code.

Design notes
------------

- **Pure data + pure helpers.** The threshold table is a module-level
  list, and every helper (``threshold_for``, ``next_threshold``,
  ``level_for_xp``) is a pure function over ints. The applier in
  ``src.core.effects_applier`` calls these helpers; the App never
  reaches in.
- **CR-to-XP table** lives here so the combat XP-grant hook can ask
  "how much XP is this kill worth?" without grokking the loot drop
  table. Values mirror the SRD CR table for the encounters the
  vertical slice ships (CR 1/8 to CR 2).
- **Per-class HP gain.** On level up the actor gets ``hit_die +
  CON modifier`` HP (taking the SRD's "fixed gain" option instead of
  rolling), with a floor of 1.
- **Per-class spell-slot gains.** The SRD's level-2/level-3 slot growth
  is encoded in :data:`SPELL_SLOT_PROGRESSION` keyed by class. Half
  casters (Paladin, Ranger) get their first level-1 slot at level 2 in
  the SRD; full casters get a second level-1 slot at level 2 and a
  level-2 slot at level 3.

Quest XP trajectory
-------------------

The M14 quest grants 200 XP per party member. The level-2 threshold is
300 XP; on its own the quest reward will not be enough to ding the
party, but combined with a single mid-tier kill on the way out it
will. This is intentional — the quest reward is the milestone payoff,
not the level-up trigger.
"""

from __future__ import annotations

from typing import Mapping


# ---------------------------------------------------------------------------
# XP thresholds (D&D 5.1 SRD-lite)
# ---------------------------------------------------------------------------

# ``XP_THRESHOLDS[i]`` is the total XP required to be level ``i+1``.
# Index 0 (level 1) is zero; index 1 (level 2) is 300; index 2
# (level 3) is 900. Higher entries fall off the slice but are
# deliberately precomputed so a content author can extend the cap
# without code review.
XP_THRESHOLDS: tuple[int, ...] = (
    0,        # level 1
    300,      # level 2
    900,      # level 3
    2_700,    # level 4 (placeholder, not surfaced)
    6_500,    # level 5 (placeholder)
)


MAX_LEVEL: int = 3
"""The highest level the leveling system actually applies effects for.

Levels beyond 3 are not in scope for the vertical slice. The applier
silently caps `LevelUp` at MAX_LEVEL so a future content batch that
hands out extreme XP can't accidentally drive a partial implementation.
"""


def threshold_for(level: int) -> int:
    """Return the cumulative XP needed to reach ``level``.

    ``level == 1`` returns 0. Levels above the table cap clamp to the
    last known threshold; this keeps callers safe when the XP table
    grows out of step with code that consults it.
    """

    if level <= 1:
        return 0
    index = level - 1
    if index >= len(XP_THRESHOLDS):
        return XP_THRESHOLDS[-1]
    return XP_THRESHOLDS[index]


def next_threshold(current_level: int) -> int | None:
    """Return the XP needed to reach ``current_level + 1``, or ``None`` at the cap.

    A return of ``None`` means "the actor is at the max level the
    engine knows how to apply" and the threshold check should stop.
    """

    if current_level >= MAX_LEVEL:
        return None
    return threshold_for(current_level + 1)


def level_for_xp(xp: int) -> int:
    """Return the highest level whose threshold is ``<= xp``.

    Clamped to :data:`MAX_LEVEL`. The leveling system uses this to
    decide whether a freshly granted XP value crosses one or more
    thresholds — a multi-level jump still surfaces one
    :class:`LevelUpAvailable` at a time (the next pending level).
    """

    if xp <= 0:
        return 1
    level = 1
    for candidate in range(2, MAX_LEVEL + 1):
        if xp >= threshold_for(candidate):
            level = candidate
        else:
            break
    return level


# ---------------------------------------------------------------------------
# Combat XP by CR (SRD-lite)
# ---------------------------------------------------------------------------

# ``XP_BY_CR`` maps a coarse CR bucket to the XP the party earns per
# kill. The CR values stay small (the slice ships CR 1/8 to CR 2) and
# the table is keyed on the CR string so content authors can extend
# without touching numerical glue. ``CR_BY_CREATURE_KIND`` maps each
# creature key in :data:`src.core.creatures.CREATURES` to a CR; new
# creatures must add an entry or fall through the default.
XP_BY_CR: Mapping[str, int] = {
    "0": 10,
    "1/8": 25,
    "1/4": 50,
    "1/2": 100,
    "1": 200,
    "2": 450,
    "3": 700,
}


CR_BY_CREATURE_KIND: Mapping[str, str] = {
    # Common dungeon dressing / wildlife — low CR.
    "frog": "0",
    "rat": "0",
    "bat": "0",
    # Standard humanoid mooks.
    "kobold": "1/8",
    "kobold_archer": "1/8",
    "goblin": "1/4",
    # M14 quest boss. Tuned so a clean kill plus the quest reward is
    # enough for the party to ding level 2.
    "boss_kobold_warlord": "2",
}


DEFAULT_KILL_XP: int = 25
"""Fallback XP for a kill whose creature kind we don't have a CR for.

Keeping a non-zero default means a creature whose CR was missed in a
content PR still rewards the player slightly, so playtesters notice
the omission instead of silently getting nothing.
"""


def xp_for_kill(creature_kind: str) -> int:
    """Return the total XP the party earns for killing ``creature_kind``.

    The grant is split equally across living party members at the
    effect-application layer; this helper returns the *pool* value so
    the splitting policy lives in one place.
    """

    cr = CR_BY_CREATURE_KIND.get(creature_kind)
    if cr is None:
        return DEFAULT_KILL_XP
    return XP_BY_CR.get(cr, DEFAULT_KILL_XP)


# ---------------------------------------------------------------------------
# Per-class HP / spell-slot progression
# ---------------------------------------------------------------------------


def hp_gain_for_level_up(hit_die: int, constitution: int) -> int:
    """Standard SRD "fixed gain": ``(hit_die // 2 + 1) + CON_mod``.

    Floored at 1 so a level up never goes backwards. Constitution mod
    is the usual ``(score - 10) // 2``.
    """

    from src.core.combat import ability_modifier

    con_mod = ability_modifier(constitution)
    # The SRD "average HP" option is hit_die/2 + 1 rounded up; for
    # even-sided dice this is exactly hit_die // 2 + 1.
    base = hit_die // 2 + 1
    return max(1, base + con_mod)


# Per-class slot progression for the levels we care about. Each entry
# is keyed on the level the actor *just attained*; the value is the
# slot map that should become the actor's new max. We treat this as the
# absolute ledger after level-up (not a diff) so a save-load that lost
# track of intermediate state still lands on the right ledger.
#
# Sources: D&D 5.1 SRD spellcasting table for full casters (Wizard,
# Sorcerer, Cleric, Druid, Bard, Warlock — Warlock's pact magic uses
# its own table, but for the slice we treat it like a full caster's
# levels 2/3 for simplicity) and half casters (Paladin, Ranger).
SPELL_SLOT_PROGRESSION: Mapping[str, Mapping[int, Mapping[int, int]]] = {
    # Full casters
    "Wizard":   {2: {1: 3}, 3: {1: 4, 2: 2}},
    "Sorcerer": {2: {1: 3}, 3: {1: 4, 2: 2}},
    "Cleric":   {2: {1: 3}, 3: {1: 4, 2: 2}},
    "Druid":    {2: {1: 3}, 3: {1: 4, 2: 2}},
    "Bard":     {2: {1: 3}, 3: {1: 4, 2: 2}},
    # Warlock pact-magic slots; SRD treats them differently but we
    # follow the same shape here so the renderer stays simple.
    "Warlock":  {2: {1: 2}, 3: {2: 2}},
    # Half casters
    "Paladin":  {2: {1: 2}, 3: {1: 3}},
    "Ranger":   {2: {1: 2}, 3: {1: 3}},
}


def slot_progression_for(character_class: str, new_level: int) -> dict[int, int]:
    """Return the post-level-up spell-slot ledger for the class.

    Empty dict for non-casters, classes the progression table doesn't
    know about, or levels with no slot change. Callers that want
    "slot gained" diffs derive them by comparing against the actor's
    current ledger.
    """

    table = SPELL_SLOT_PROGRESSION.get(character_class)
    if table is None:
        return {}
    progression = table.get(new_level)
    if progression is None:
        return {}
    return dict(progression)


__all__ = [
    "CR_BY_CREATURE_KIND",
    "DEFAULT_KILL_XP",
    "MAX_LEVEL",
    "SPELL_SLOT_PROGRESSION",
    "XP_BY_CR",
    "XP_THRESHOLDS",
    "hp_gain_for_level_up",
    "level_for_xp",
    "next_threshold",
    "slot_progression_for",
    "threshold_for",
    "xp_for_kill",
]
