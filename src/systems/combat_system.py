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
        is_second_person = actor_name == "you"
        actor_subject = "You" if is_second_person else f"The {actor_name}"
        hit_verb = _attack_verb(
            action.actor,
            weapon.name,
            weapon.damage_type,
            world,
            second_person=is_second_person,
        )

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


def _attack_verb(
    entity: EntityId,
    weapon_name: str,
    damage_type: str,
    world: World,
    *,
    second_person: bool = False,
) -> str:
    """Pick the verb shown in the attack message.

    When ``second_person`` is true, the subject is "You" and the verb
    needs the bare form ("you hit", "you slash"). Otherwise the subject
    is a third-person singular ("The frog ...") and the verb takes the
    ``-s`` form ("bites", "slashes"). The verb lexicon is stored as
    third-person singular (the dominant case at runtime — every enemy
    attack); the second-person form is derived by stripping a trailing
    ``-es``/``-s`` when present.
    """

    creature = world.creatures.get(entity)
    if creature is not None:
        verb = creature.attack_verb
    elif weapon_name in {"dagger", "rapier", "shortsword"}:
        verb = "stabs"
    elif damage_type == "slashing":
        verb = "slashes"
    elif damage_type == "piercing":
        verb = "stabs"
    else:
        verb = "hits"
    if second_person:
        return _to_bare_verb(verb)
    return verb


def _to_bare_verb(third_person: str) -> str:
    """Return the bare ("you ___") form of a third-person singular verb.

    Handles the regular English ``-s``/``-es`` patterns we use in the
    creature/weapon lexicon: ``slashes`` -> ``slash``, ``stabs`` ->
    ``stab``, ``bites`` -> ``bite``, ``hits`` -> ``hit``. Verbs ending
    in ``-shes``, ``-ches``, ``-xes``, ``-zes`` drop the trailing ``es``;
    ``-ies`` becomes ``-y``; everything else drops a single trailing
    ``s``. Verbs that don't end in ``s`` are returned as-is so future
    lexicon entries authored in bare form keep working.
    """

    if not third_person.endswith("s"):
        return third_person
    if third_person.endswith(("shes", "ches", "xes", "zes")):
        return third_person[:-2]
    if third_person.endswith("ies") and len(third_person) > 3:
        return third_person[:-3] + "y"
    return third_person[:-1]
