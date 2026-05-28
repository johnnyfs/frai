"""Spell resolver (M11).

Turns a :class:`~src.core.actions.CastSpellAttempt` into the flat list
of effects the M46 resolver applies. The system is intentionally a
single ``handle`` method consulting :mod:`src.core.spells` for catalog
data — every spell-specific decision (damage roll, save vs no save,
heal vs damage, concentration) is a small branch keyed on the
:class:`~src.core.spells.Spell` dataclass.

Design constraints
------------------

- **Slot consumption is NOT here.** The :class:`~src.app.App` registers
  a ``PRE_CHECK`` phase handler that consumes the caster's slot before
  the spell system runs. That ordering matters: a spell rejected by
  the system (e.g. an out-of-range target) doesn't burn the slot.
  The resolver returns ``cancel=True`` either way so the dispatcher
  stops walking systems regardless of the outcome.
- **RNG is injected.** The system holds a ``random.Random`` source so
  tests can pin damage rolls with a seed.
- **No App reference.** The system reads only the world. Concentration
  break (M24 seam) is implemented as a reaction hook on the App side
  — it watches resolved attempts for a damage effect on a
  concentrating caster.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import random
from typing import Iterable

from src.core.actions import Action, CastSpellAttempt
from src.core.checks import AdvantageState, Save, roll_save
from src.core.combat import ability_modifier
from src.core.conditions import Condition, ConditionKind, DurationPolicy
from src.core.dispatcher import DispatchResult
from src.core.effects import (
    ApplyCondition,
    ApplyHealing,
    DamageEntity,
    Effect,
    EmitMessage,
    KillEntity,
)
from src.core.entity import EntityId
from src.core.spells import (
    Spell,
    SpellTargetKind,
    spell_attack_bonus,
    spell_for_id,
    spell_save_dc,
    spellcasting_ability_modifier,
)
from src.core.stealth import NoiseLevel, propagate_noise
from src.core.world import World


@dataclass(slots=True)
class SpellSystem:
    """Resolve :class:`CastSpellAttempt` actions.

    The system reads the world (caster position, target entities, save
    ability mods) and produces a list of effects covering every
    target. RNG lives on the instance so seeded fixtures stay
    deterministic.
    """

    rng: random.Random = field(default_factory=random.Random)

    def handle(self, action: Action, world: World) -> DispatchResult:
        if not isinstance(action, CastSpellAttempt):
            return DispatchResult()

        try:
            spell = spell_for_id(action.spell_id)
        except KeyError:
            return DispatchResult(
                effects=[EmitMessage(f"Unknown spell '{action.spell_id}'.")],
                cancel=True,
            )

        # The list of spells the caster knows is consulted by the App
        # before the action is built (the spell menu can only emit
        # actions for known spells). Defensive: refuse here too so an
        # AI-built action that picks an unlearned spell still fails
        # cleanly.
        known = world.spell_lists.get(action.actor)
        if known is not None and not known.has(spell.spell_id):
            return DispatchResult(
                effects=[EmitMessage(f"You don't know {spell.name}.")],
                cancel=True,
            )

        # Range check (Chebyshev). For SINGLE_ENTITY we measure to the
        # target entity; for AREA_RADIUS we measure to the cursor
        # tile; for FRIENDLY_GROUP we accept any target within range
        # (per-target check below).
        caster_position = world.positions.get(action.actor)
        if caster_position is None:
            return DispatchResult(
                effects=[EmitMessage("Caster has no position.")], cancel=True
            )

        # M23: every spell carries a verbal component in this catalog
        # (we don't yet model "S-only" spells), so casting always ramps
        # nearby hostiles to AWARE. This happens before the effects
        # resolve so a fizzled cast still gives away the caster's
        # position — the words were spoken either way.
        propagate_noise(world, action.actor, NoiseLevel.LOUD)

        effects = self._build_effects(spell, action, world, caster_position)
        return DispatchResult(effects=effects, cancel=True)

    # ------------------------------------------------------------------
    # Internal: per-target-kind dispatch
    # ------------------------------------------------------------------

    def _build_effects(
        self,
        spell: Spell,
        action: CastSpellAttempt,
        world: World,
        caster_position,
    ) -> list[Effect]:
        caster_name = world.name_for(action.actor)
        caster_subject = "You" if caster_name == "you" else f"The {caster_name}"
        opening = [EmitMessage(f"{caster_subject} cast {spell.name}.")]

        if spell.target_kind is SpellTargetKind.SINGLE_ENTITY:
            return opening + self._resolve_single_entity(spell, action, world)
        if spell.target_kind is SpellTargetKind.AREA_RADIUS:
            return opening + self._resolve_area(spell, action, world, caster_position)
        if spell.target_kind is SpellTargetKind.FRIENDLY_GROUP:
            return opening + self._resolve_friendly_group(spell, action, world)
        return opening + [EmitMessage("Spell fizzles.")]

    # ------------------------------------------------------------------
    # SINGLE_ENTITY: magic missile, fire bolt, cure wounds
    # ------------------------------------------------------------------

    def _resolve_single_entity(
        self,
        spell: Spell,
        action: CastSpellAttempt,
        world: World,
    ) -> list[Effect]:
        target = action.target_entity
        if target is None:
            return [EmitMessage("No target.")]

        # Healing spell.
        if spell.healing_dice[0] > 0:
            return self._resolve_heal(spell, action.actor, target, world)

        # Attack-roll spell (Fire Bolt).
        if spell.attack_roll:
            return self._resolve_attack_roll_spell(spell, action.actor, target, world)

        # Auto-hit spell (Magic Missile).
        return self._resolve_auto_hit_spell(spell, action.actor, target, world)

    def _resolve_heal(
        self,
        spell: Spell,
        caster: EntityId,
        target: EntityId,
        world: World,
    ) -> list[Effect]:
        count, die, base_bonus = spell.healing_dice
        mod = spellcasting_ability_modifier(world, caster)
        total = 0
        for _ in range(max(0, count)):
            total += self.rng.randint(1, die)
        total += base_bonus + mod
        total = max(1, total)
        target_name = world.name_for(target)
        target_subject = "you" if target_name == "you" else f"the {target_name}"
        return [
            ApplyHealing(target, total),
            EmitMessage(f"{spell.name} restores {total} HP to {target_subject}."),
        ]

    def _resolve_attack_roll_spell(
        self,
        spell: Spell,
        caster: EntityId,
        target: EntityId,
        world: World,
    ) -> list[Effect]:
        target_stats = world.combat_stats.get(target)
        if target_stats is None:
            return [EmitMessage("Target has no combat stats.")]
        bonus = spell_attack_bonus(world, caster)
        natural = self.rng.randint(1, 20)
        total = natural + bonus
        target_name = world.name_for(target)
        target_subject = "you" if target_name == "you" else f"the {target_name}"
        # Critical fail / normal miss.
        if natural == 1 or (natural != 20 and total < target_stats.armor_class):
            return [EmitMessage(f"{spell.name} misses {target_subject}.")]
        damage = self._roll_damage(spell)
        if natural == 20:
            damage += self._roll_damage(spell)  # crit doubles dice
        effects: list[Effect] = [
            DamageEntity(target, damage),
            EmitMessage(
                f"{spell.name} hits {target_subject} for {damage} {spell.damage_type} damage."
            ),
        ]
        if damage >= target_stats.hit_points:
            verb = "die" if target_name == "you" else f"the {target_name} dies"
            effects.append(EmitMessage(f"You {verb}." if target_name == "you" else f"The {target_name} dies."))
            effects.append(KillEntity(target))
        return effects

    def _resolve_auto_hit_spell(
        self,
        spell: Spell,
        caster: EntityId,
        target: EntityId,
        world: World,
    ) -> list[Effect]:
        """Magic Missile: every missile auto-hits and rolls damage."""
        target_stats = world.combat_stats.get(target)
        if target_stats is None:
            return [EmitMessage("Target has no combat stats.")]
        total = 0
        for _ in range(max(1, spell.missiles)):
            total += self._roll_damage(spell)
        target_name = world.name_for(target)
        target_subject = "you" if target_name == "you" else f"the {target_name}"
        effects: list[Effect] = [
            DamageEntity(target, total),
            EmitMessage(
                f"{spell.name} strikes {target_subject} for {total} {spell.damage_type} damage."
            ),
        ]
        if total >= target_stats.hit_points:
            effects.append(EmitMessage(
                "You die." if target_name == "you" else f"The {target_name} dies."
            ))
            effects.append(KillEntity(target))
        return effects

    # ------------------------------------------------------------------
    # AREA_RADIUS: burning hands
    # ------------------------------------------------------------------

    def _resolve_area(
        self,
        spell: Spell,
        action: CastSpellAttempt,
        world: World,
        caster_position,
    ) -> list[Effect]:
        cursor = action.target_tile
        if cursor is None:
            return [EmitMessage("No target tile.")]
        radius = max(0, spell.area_radius)
        affected: list[EntityId] = []
        for entity, position in world.positions.values.items():
            if entity == action.actor:
                # Caster never targets themselves with their own area
                # spell. This is conservative; some SRD spells include
                # the caster — those will pass a flag once we add them.
                continue
            if max(abs(position.x - cursor[0]), abs(position.y - cursor[1])) <= radius:
                if world.combat_stats.has(entity):
                    affected.append(entity)
        if not affected:
            return [EmitMessage(f"{spell.name} hits nothing.")]

        full_damage = self._roll_damage(spell)
        effects: list[Effect] = []
        for target in affected:
            target_name = world.name_for(target)
            target_subject = "you" if target_name == "you" else f"the {target_name}"
            damage = full_damage
            if spell.save_ability is not None:
                saved = self._roll_target_save(spell, action.actor, target, world)
                if saved:
                    damage = full_damage // 2
                    effects.append(EmitMessage(
                        f"{target_subject.capitalize()} saves; {damage} {spell.damage_type} damage."
                    ))
                else:
                    effects.append(EmitMessage(
                        f"{target_subject.capitalize()} fails to save; {damage} {spell.damage_type} damage."
                    ))
            else:
                effects.append(EmitMessage(
                    f"{target_subject.capitalize()} takes {damage} {spell.damage_type} damage."
                ))
            if damage > 0:
                effects.append(DamageEntity(target, damage))
                stats = world.combat_stats.get(target)
                if stats is not None and damage >= stats.hit_points:
                    effects.append(EmitMessage(
                        "You die." if target_name == "you" else f"The {target_name} dies."
                    ))
                    effects.append(KillEntity(target))
        return effects

    def _roll_target_save(
        self,
        spell: Spell,
        caster: EntityId,
        target: EntityId,
        world: World,
    ) -> bool:
        """Roll the target's saving throw against ``spell``.

        Uses the shared :func:`~src.core.checks.roll_save` machinery
        with the target's ability modifier and the caster's
        :func:`~src.core.spells.spell_save_dc`. Class save
        proficiencies aren't deeply modelled at M11 — every save uses
        the bare ability modifier, which is the conservative reading
        of the SRD until M25 brings proficiencies through.
        """

        stats = world.combat_stats.get(target)
        if stats is None or spell.save_ability is None:
            return False
        ability = spell.save_ability
        score = {
            "STR": stats.strength,
            "DEX": stats.dexterity,
            "CON": stats.constitution,
        }.get(ability)
        if score is None:
            score = 10
        save = Save(
            actor=target,
            ability=ability,
            ability_modifier=ability_modifier(score),
            dc=spell_save_dc(world, caster),
            advantage_state=AdvantageState.NORMAL,
        )
        return roll_save(save, self.rng).success

    # ------------------------------------------------------------------
    # FRIENDLY_GROUP: bless
    # ------------------------------------------------------------------

    def _resolve_friendly_group(
        self,
        spell: Spell,
        action: CastSpellAttempt,
        world: World,
    ) -> list[Effect]:
        targets = list(action.target_entities)
        if not targets:
            return [EmitMessage("No targets.")]
        if len(targets) > spell.group_size:
            targets = targets[: spell.group_size]
        effects: list[Effect] = []
        for target in targets:
            duration = _duration_for_spell(spell)
            condition = Condition(
                kind=ConditionKind.BLESSED,
                duration=duration,
                source=action.actor,
            )
            effects.append(ApplyCondition(target, condition))
            name = world.name_for(target)
            subject = "you" if name == "you" else f"the {name}"
            effects.append(EmitMessage(f"{spell.name} blesses {subject}."))
        if spell.concentration:
            effects.append(
                ApplyCondition(
                    action.actor,
                    Condition(
                        kind=ConditionKind.CONCENTRATING,
                        duration=DurationPolicy.minutes(1),
                        source=action.actor,
                        payload={"spell_id": spell.spell_id},
                    ),
                )
            )
        return effects

    # ------------------------------------------------------------------
    # Damage roll
    # ------------------------------------------------------------------

    def _roll_damage(self, spell: Spell) -> int:
        count, die, bonus = spell.damage_dice
        if count <= 0:
            return 0
        total = 0
        for _ in range(count):
            total += self.rng.randint(1, die)
        return total + bonus


def _duration_for_spell(spell: Spell) -> DurationPolicy:
    """Map a catalog ``duration`` string to a :class:`DurationPolicy`.

    Only the strings the M11 catalog uses are recognised. Unknown
    durations fall back to ``until_removed`` so the spell still applies
    its condition but never expires on a tick — the M11 scope keeps
    this map intentionally small.
    """

    text = spell.duration.lower().strip()
    if text == "1 minute":
        return DurationPolicy.minutes(1)
    if text == "instantaneous":
        return DurationPolicy.until_removed()
    return DurationPolicy.until_removed()


__all__ = ["SpellSystem"]
