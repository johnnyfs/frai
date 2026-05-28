from dataclasses import dataclass, field
import random

from src.core.actions import Action, InteractAttempt
from src.core.checks import AdvantageState, Check, roll_check
from src.core.combat import ability_modifier
from src.core.dispatcher import DispatchResult
from src.core.effects import (
    DamageEntity,
    DisarmTrap,
    Effect,
    EmitMessage,
    OpenEntity,
    RemoveBlocker,
    TriggerTrap,
    UnlockEntity,
)
from src.core.entity import EntityId
from src.core.world import World


# Skill profile for a check-required interaction: which ability and which
# skill name (per `character_creation.SKILLS`) confers proficiency.
_LOCK_ABILITY = "DEX"
_LOCK_SKILL = "Sleight of Hand"
_TRAP_ABILITY = "DEX"
_TRAP_SKILL = "Sleight of Hand"


@dataclass(slots=True)
class InteractionSystem:
    """Resolves `InteractAttempt` actions.

    The system owns a `random.Random` source used only for skill/DC checks
    that the actor implicitly triggers via the public `e` path. Callers that
    want determinism (tests, playtest fixtures) construct the system with
    `InteractionSystem(rng=random.Random(seed))`; production wiring lets it
    default to an unseeded instance.
    """

    rng: random.Random = field(default_factory=random.Random)

    def handle(self, action: Action, world: World) -> DispatchResult:
        if not isinstance(action, InteractAttempt):
            return DispatchResult()

        position = world.positions.require(action.actor)
        target_x = position.x + action.dx
        target_y = position.y + action.dy
        target = _interaction_target(world, target_x, target_y)
        if target is None:
            return DispatchResult(effects=[EmitMessage("Nothing to interact with.")], cancel=True)

        effects = _resolve_interaction(world, action, target, self.rng)
        return DispatchResult(effects=effects, cancel=True)


def _interaction_target(world: World, x: int, y: int) -> EntityId | None:
    for entity in world.entities_at(x, y):
        if (
            world.doors.has(entity)
            or world.traps.has(entity)
            or world.containers.has(entity)
        ):
            return entity
    return None


def _resolve_interaction(
    world: World,
    action: InteractAttempt,
    target: EntityId,
    rng: random.Random,
) -> list[Effect]:
    trap = world.traps.get(target)
    if trap is not None and trap.is_armed:
        passed = _resolve_check(
            world,
            action,
            dc=trap.disarm_dc,
            ability=_TRAP_ABILITY,
            skill=_TRAP_SKILL,
            rng=rng,
        )
        if passed is None:
            return [EmitMessage("You sense danger - you need a way to disarm it.")]
        if passed:
            return [DisarmTrap(target), EmitMessage("Trap disarmed.")]
        return [
            DamageEntity(action.actor, trap.damage),
            EmitMessage("Trap triggered."),
            TriggerTrap(target),
        ]

    lock = world.locks.get(target)
    if lock is not None and lock.is_locked:
        passed = _resolve_check(
            world,
            action,
            dc=lock.pick_dc,
            ability=_LOCK_ABILITY,
            skill=_LOCK_SKILL,
            rng=rng,
        )
        if passed is None:
            return [EmitMessage("It's locked. You need a way to pick it.")]
        if not passed:
            return [EmitMessage("Lock pick failed.")]
        return [
            UnlockEntity(target),
            OpenEntity(target),
            RemoveBlocker(target),
            EmitMessage("Unlocked and opened."),
        ]

    if world.doors.has(target):
        door = world.doors.require(target)
        if door.is_open:
            return [EmitMessage("Already open.")]
        return [OpenEntity(target), RemoveBlocker(target), EmitMessage("Opened.")]

    if world.containers.has(target):
        container = world.containers.require(target)
        if container.is_open:
            return [EmitMessage("Already open.")]
        return [OpenEntity(target), EmitMessage("Opened.")]

    return [EmitMessage("Nothing happens.")]


def _resolve_check(
    world: World,
    action: InteractAttempt,
    *,
    dc: int,
    ability: str,
    skill: str,
    rng: random.Random,
) -> bool | None:
    """Return whether the actor passes the DC check.

    Resolution order:
      1. Explicit `action.check_result` (legacy/test path) - comparison
         against `dc` matches the historical M9 semantics.
      2. Actor has a character sheet -> roll an implicit `Check` using
         that sheet's ability modifier and proficiency.
      3. Otherwise return `None`, leaving the caller to emit the M9
         refusal message.

    With the player party always assembled via character creation (M5/M6),
    case 3 is essentially unused in the live game; it remains so that
    bare-bones test fixtures and any future non-character actors still get
    the M9 refusal rather than a crash.
    """
    if action.check_result is not None:
        return action.check_result >= dc
    character = world.characters.get(action.actor)
    if character is None:
        return None
    sheet = character.sheet
    score = sheet.attributes.get(ability)
    if score is None:
        return None
    stats = world.combat_stats.get(action.actor)
    proficiency_bonus = stats.proficiency_bonus if stats is not None else 2
    check = Check(
        actor=action.actor,
        ability=ability,
        ability_modifier=ability_modifier(score),
        dc=dc,
        proficiency=skill in sheet.skills,
        proficiency_bonus=proficiency_bonus,
        advantage_state=AdvantageState.NORMAL,
    )
    return roll_check(check, rng).success
