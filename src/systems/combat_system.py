from dataclasses import dataclass, field
import random

from src.core.actions import Action, AttackAttempt
from src.core.combat import ability_modifier
from src.core.dispatcher import DispatchResult
from src.core.effects import DamageEntity, EmitMessage, KillEntity
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
    ) -> list[DamageEntity | EmitMessage | KillEntity]:
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
        actor_subject = "You" if actor_name == "you" else actor_name
        attack_verb = "attack" if actor_name == "you" else "attacks"
        hit_verb = "hit" if actor_name == "you" else "hits"

        if attack_roll == 1 or (attack_roll != 20 and attack_total < target_stats.armor_class):
            return [
                EmitMessage(
                    f"{actor_subject} {attack_verb} {target_name} with {weapon.name}: miss ({attack_total} vs AC {target_stats.armor_class})."
                )
            ]

        damage = max(1, self.rng.randint(1, weapon.damage_die) + ability_mod)
        effects = [
            DamageEntity(action.target, damage),
            EmitMessage(
                f"{actor_subject} {hit_verb} {target_name} with {weapon.name} for {damage} {weapon.damage_type} damage."
            ),
        ]
        if damage >= (target_hit_points if target_hit_points is not None else target_stats.hit_points):
            effects.append(EmitMessage("You die." if target_name == "you" else f"{target_name} dies."))
            effects.append(KillEntity(action.target))
        return effects
