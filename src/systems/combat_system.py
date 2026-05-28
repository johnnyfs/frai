from dataclasses import dataclass, field
import random

from src.core.actions import Action, AttackAttempt
from src.core.combat import ability_modifier
from src.core.conditions import ConditionKind
from src.core.dispatcher import DispatchResult
from src.core.effects import DamageEntity, EmitMessage, EndCondition, KillEntity
from src.core.entity import EntityId
from src.core.stealth import NoiseLevel, propagate_noise
from src.core.world import World


@dataclass(slots=True)
class CombatSystem:
    rng: random.Random = field(default_factory=random.Random)

    def handle(self, action: Action, world: World) -> DispatchResult:
        if not isinstance(action, AttackAttempt):
            return DispatchResult()
        return DispatchResult(effects=self.resolve_attack(action, world), cancel=True)

    def resolve_attack(
        self,
        action: AttackAttempt,
        world: World,
        target_hit_points: int | None = None,
    ) -> list[DamageEntity | EmitMessage | KillEntity | EndCondition]:
        actor_stats = world.combat_stats.require(action.actor)
        target_stats = world.combat_stats.require(action.target)
        weapon = world.weapons.require(action.actor)
        ability_score = actor_stats.strength
        if weapon.finesse:
            ability_score = max(actor_stats.strength, actor_stats.dexterity)
        elif weapon.ability == "DEX":
            ability_score = actor_stats.dexterity

        ability_mod = ability_modifier(ability_score)
        attack_roll = self.rng.randint(1, 20)
        attack_total = attack_roll + ability_mod + actor_stats.proficiency_bonus
        actor_name = world.name_for(action.actor)
        target_name = world.name_for(action.target)
        actor_subject = "You" if actor_name == "you" else f"The {actor_name}"
        hit_verb = _attack_verb(action.actor, weapon.name, weapon.damage_type, world)

        # An attack is unambiguously loud. Ramp every hostile within
        # earshot before we resolve the roll so a "miss" still alerts
        # nearby foes — they heard the swing either way. The actor's
        # own hidden tag also clears on attack (per SRD: attacking
        # breaks stealth).
        propagate_noise(world, action.actor, NoiseLevel.LOUD)
        effects: list[DamageEntity | EmitMessage | KillEntity | EndCondition] = []
        actor_conditions = world.conditions.get(action.actor)
        if actor_conditions is not None and actor_conditions.has(ConditionKind.HIDDEN):
            effects.append(EndCondition(action.actor, ConditionKind.HIDDEN))

        if attack_roll == 1 or (attack_roll != 20 and attack_total < target_stats.armor_class):
            effects.append(EmitMessage(f"{actor_subject} {hit_verb}! Miss."))
            return effects

        damage = max(1, self.rng.randint(1, weapon.damage_die) + ability_mod)
        effects.extend([
            DamageEntity(action.target, damage),
            EmitMessage(f"{actor_subject} {hit_verb}! {damage} damage."),
        ])
        if damage >= (target_hit_points if target_hit_points is not None else target_stats.hit_points):
            effects.append(EmitMessage("You die." if target_name == "you" else f"The {target_name} dies."))
            effects.append(KillEntity(action.target))
        return effects


def _attack_verb(entity: EntityId, weapon_name: str, damage_type: str, world: World) -> str:
    creature = world.creatures.get(entity)
    if creature is not None:
        return creature.attack_verb
    if weapon_name in {"dagger", "rapier", "shortsword"}:
        return "stabs"
    if damage_type == "slashing":
        return "slashes"
    if damage_type == "piercing":
        return "stabs"
    return "hits"
