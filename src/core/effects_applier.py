"""Apply typed Effect dataclasses against an App/World.

Centralizes the dispatch that used to live in ``App.apply_effects`` so the
matching logic is no longer a long ``isinstance`` chain inline in the App.

Handlers are grouped by domain (movement, combat, interaction, lifecycle, UI,
messages). Today they all live in this single module; once the seams are
stable they can be split into per-domain modules.

A few handlers are not pure ``World`` mutations — they reach into ``App`` state
(``mode``, ``running``, ``party``, ``restart``). Those handlers take the App as
a ``host`` argument. They are documented below and are the seams for the next
refactor pass.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from src.core.components import (
    Corpse,
    ExperiencePoints,
    GodMode,
    Inventory,
    LevelUpAvailable,
    Name,
    Position,
    Presentation,
)
from src.core.conditions import apply_condition, end_condition
from src.core.items import has_item
from src.core.leveling import (
    hp_gain_for_level_up,
    level_for_xp,
    next_threshold,
    slot_progression_for,
    xp_for_kill,
)
from src.core.dialogue import mark_quest_completed_in_tree
from src.core.quest import QUESTS, Quest, QuestState
from src.core.effects import (
    ApplyCondition,
    ApplyHealing,
    ConsumeSpellSlot,
    DamageEntity,
    DisarmTrap,
    DropToGround,
    Effect,
    EmitMessage,
    EndCondition,
    GrantGold,
    GrantItem,
    GrantXP,
    KillEntity,
    LevelUp,
    MoveEntity,
    OpenEntity,
    QuitGame,
    RemoveBlocker,
    RestartGame,
    SetCharacterSheet,
    SetGodMode,
    SetMode,
    SpawnCorpse,
    SpawnEntity,
    TransferInventory,
    TriggerTrap,
    UnlockEntity,
)
from src.core.modes import UIMode

if TYPE_CHECKING:
    from src.app import App


class _AppHost(Protocol):
    """Subset of ``App`` that effect handlers depend on.

    Documents the coupling between effect handlers and App state so that the
    seams are visible. Anything outside this protocol must go through ``world``.
    """

    # Direct attributes the handlers mutate.
    ui_mode: UIMode
    character_creation_state: object
    running: bool
    party: object  # PartyState; typed loosely to avoid a circular import.
    active_party_index: int
    player: object
    loot_rng: object  # random.Random; used by the kill-loot pipeline.

    # Methods the handlers invoke.
    def restart(self) -> None: ...


class EffectApplier:
    """Dispatch a batch of Effects against the world and the host App.

    Construct with the owning App; ``apply_all`` consumes a list of effects in
    order, mutating the world and (where necessary) the host App state.
    """

    __slots__ = ("_host",)

    def __init__(self, host: "App") -> None:
        self._host = host

    def apply_all(self, effects: list[Effect]) -> None:
        messages: list[str] = []
        for effect in effects:
            self._dispatch(effect, messages)
        if messages:
            self._host.messages.emit(" ".join(message for message in messages if message))

    def _dispatch(self, effect: Effect, messages: list[str]) -> None:
        # Movement
        if isinstance(effect, MoveEntity):
            _apply_move_entity(self._host, effect)
            return

        # Combat
        if isinstance(effect, DamageEntity):
            _apply_damage_entity(self._host, effect)
            return
        if isinstance(effect, KillEntity):
            _apply_kill_entity(self._host, effect)
            return

        # Interaction
        if isinstance(effect, OpenEntity):
            _apply_open_entity(self._host, effect)
            return
        if isinstance(effect, UnlockEntity):
            _apply_unlock_entity(self._host, effect)
            return
        if isinstance(effect, DisarmTrap):
            _apply_disarm_trap(self._host, effect)
            return
        if isinstance(effect, TriggerTrap):
            _apply_trigger_trap(self._host, effect)
            return
        if isinstance(effect, RemoveBlocker):
            _apply_remove_blocker(self._host, effect)
            return

        # Lifecycle
        if isinstance(effect, SetCharacterSheet):
            _apply_set_character_sheet(self._host, effect)
            return
        if isinstance(effect, RestartGame):
            _apply_restart_game(self._host, effect)
            return
        if isinstance(effect, QuitGame):
            _apply_quit_game(self._host, effect)
            return

        # UI
        if isinstance(effect, SetMode):
            _apply_set_mode(self._host, effect)
            return

        # Debug (M33)
        if isinstance(effect, SetGodMode):
            _apply_set_god_mode(self._host, effect)
            return
        if isinstance(effect, SpawnEntity):
            _apply_spawn_entity(self._host, effect)
            return
        if isinstance(effect, GrantGold):
            _apply_grant_gold(self._host, effect)
            return
        if isinstance(effect, GrantItem):
            _apply_grant_item(self._host, effect)
            return
        if isinstance(effect, GrantXP):
            _apply_grant_xp(self._host, effect, messages)
            return
        if isinstance(effect, LevelUp):
            _apply_level_up(self._host, effect, messages)
            return

        # Conditions (M24)
        if isinstance(effect, ApplyCondition):
            _apply_apply_condition(self._host, effect)
            return
        if isinstance(effect, EndCondition):
            _apply_end_condition(self._host, effect)
            return

        # Spells (M11)
        if isinstance(effect, ApplyHealing):
            _apply_apply_healing(self._host, effect)
            return
        if isinstance(effect, ConsumeSpellSlot):
            _apply_consume_spell_slot(self._host, effect)
            return

        # Loot / pickup / drop (M30)
        if isinstance(effect, TransferInventory):
            _apply_transfer_inventory(self._host, effect, messages)
            return
        if isinstance(effect, SpawnCorpse):
            _apply_spawn_corpse(self._host, effect)
            return
        if isinstance(effect, DropToGround):
            _apply_drop_to_ground(self._host, effect, messages)
            return

        # Messages
        if isinstance(effect, EmitMessage):
            messages.append(effect.text)
            return


# ---------------------------------------------------------------------------
# MovementEffects
# ---------------------------------------------------------------------------


def _apply_move_entity(host: "App", effect: MoveEntity) -> None:
    """Pure world mutation."""
    position = host.world.positions.require(effect.entity)
    position.x = effect.x
    position.y = effect.y


# ---------------------------------------------------------------------------
# CombatEffects
# ---------------------------------------------------------------------------


def _apply_damage_entity(host: "App", effect: DamageEntity) -> None:
    """Pure world mutation. Missing combat_stats is a no-op (preserved behavior).

    Entities with an enabled `GodMode` component (M33 debug `god on`) ignore
    all damage. This is intentionally checked here, not in the combat system,
    so anything that emits `DamageEntity` (combat, traps, future effects) is
    automatically respected.

    M29: damage that reduces a *player-controlled* combat-statted actor
    to 0 HP applies the SRD ``unconscious`` condition and seeds a
    :class:`DeathSaves` row instead of killing the actor outright.
    Damage on an actor already at 0 HP is interpreted as an automatic
    death-save failure. Massive damage (an amount that would overflow
    the actor's ``max_hit_points`` pool) routes through real death —
    :class:`KillEntity` is enqueued for the next dispatch.

    NPC enemies (no ``PlayerControlled``) follow the legacy path: HP
    clamps at 0 and the subsequent ``KillEntity`` finalises the kill.
    The M28 distinction means hostile creatures die outright when their
    HP runs out — only PCs (and recruited companions, which carry
    ``PlayerControlled``) earn death saves.
    """
    from src.core.death_saves import begin_downed, record_damage_failure

    god = host.world.god_modes.get(effect.entity)
    if god is not None and god.enabled:
        return
    stats = host.world.combat_stats.get(effect.entity)
    if stats is None:
        return

    is_pc = host.world.player_controlled.has(effect.entity)
    previous_hit_points = stats.hit_points

    if not is_pc:
        # Legacy behavior for non-PC combatants: clamp at 0; combat /
        # spell systems still emit KillEntity to finalise the kill.
        stats.hit_points = max(0, previous_hit_points - effect.amount)
        return

    # Damage landing on an already-downed PC is a death-save failure.
    if previous_hit_points <= 0 and host.world.death_saves.get(effect.entity) is not None:
        followups = record_damage_failure(host.world, effect.entity, effect.amount)
        if followups:
            host.effect_applier.apply_all(list(followups))
        return

    new_hit_points = previous_hit_points - effect.amount
    # Massive damage (per SRD): if the blow would drive the actor below
    # the negative of their max HP, it's an outright kill — bypass the
    # downed pipeline and route through KillEntity directly.
    if new_hit_points <= -stats.max_hit_points:
        stats.hit_points = 0
        host.effect_applier.apply_all([KillEntity(effect.entity)])
        return

    stats.hit_points = max(0, new_hit_points)
    if stats.hit_points == 0 and previous_hit_points > 0:
        # Newly downed PC: apply unconscious + DeathSaves row. Follow-up
        # effects are routed through the normal applier so the condition
        # store mutates through the standard ApplyCondition handler.
        followups = begin_downed(host.world, effect.entity)
        if followups:
            host.effect_applier.apply_all(list(followups))
        _check_party_wipe(host)


def _apply_kill_entity(host: "App", effect: KillEntity) -> None:
    """Reaches into App state: if the player dies we flip to UIMode.game_over.

    For non-player kills, if the dying entity has a ``LootDrop`` we roll
    its drop table (via the host's ``loot_rng`` so seeded fixtures stay
    deterministic) and spawn a corpse at the same tile carrying the
    rolled inventory. The dying entity is then removed.

    Before the kill is finalised we capture the dying entity's
    :class:`~src.core.components.BossMarker` (if any) so the post-kill
    quest hook can credit the boss token even after the entity row
    has been wiped from the component stores.

    M29: a KillEntity that arrives while a *player-controlled* actor
    is in the freshly downed state (DeathSaves present, fewer than
    ``FAILURES_TO_DIE`` failures) is treated as a downed transition
    rather than a real death. This preserves existing combat behavior —
    CombatSystem and SpellSystem still emit KillEntity when an attack
    reduces HP to 0, and the kill handler reinterprets the effect
    against the actor's current state. Real deaths (death-save failure
    tally, massive damage) bypass this guard because the death-save
    driver / damage handler pop the DeathSaves row before emitting
    KillEntity. NPC enemies follow the legacy hard-kill path.
    """
    from src.core.death_saves import FAILURES_TO_DIE

    is_pc = host.world.player_controlled.has(effect.entity)
    saves = host.world.death_saves.get(effect.entity)
    if is_pc and saves is not None and saves.failures < FAILURES_TO_DIE:
        # Freshly downed PC — preserve the unconscious state. The
        # earlier DamageEntity already wired the condition; this branch
        # defends against a stray double-kill from a system that emits
        # both DamageEntity and KillEntity in the same batch.
        return
    if effect.entity == host.player:
        host.ui_mode = UIMode.game_over
        host.character_creation_state = None
        return
    # A non-player party member has died for real. Check whether the
    # whole party is now down so we can flip to the game-over screen.
    if is_pc:
        # The entity is about to be removed below; do the wipe check
        # against the post-removal world view by including the
        # to-be-removed entity in the "down" tally manually.
        _maybe_trigger_party_wipe_after_kill(host, effect.entity)
    world = host.world
    loot_drop = world.loot_drops.get(effect.entity)
    position = world.positions.get(effect.entity)
    creature = world.creatures.get(effect.entity)
    # Snapshot the boss token before the entity is removed; the M14
    # progress hook only needs the token, not the entity itself.
    boss_marker = world.boss_markers.get(effect.entity)
    boss_token = boss_marker.token if boss_marker is not None else None
    if loot_drop is not None and position is not None:
        from src.core.loot import roll_loot

        roll = roll_loot(loot_drop.table, host.loot_rng)
        if roll.gold or roll.items:
            _spawn_corpse_entity(
                world,
                x=position.x,
                y=position.y,
                creature_kind=creature.kind if creature is not None else "",
                gold=roll.gold,
                items=roll.items,
            )
        else:
            # Empty roll: still leave a bare corpse so the kill is visible.
            _spawn_corpse_entity(
                world,
                x=position.x,
                y=position.y,
                creature_kind=creature.kind if creature is not None else "",
                gold=0,
                items=(),
            )
    # Combat XP grant (M25): split the kill's XP pool across living
    # party members BEFORE removing the entity so the creature kind is
    # still readable. Only creatures grant XP — non-creature kills
    # (corpses re-killed, traps, etc.) are silent.
    if creature is not None and effect.entity != host.player:
        from src.core.effects import GrantXP

        xp_pool = xp_for_kill(creature.kind)
        if xp_pool > 0:
            living = _living_party_members(host)
            if living:
                share = max(1, xp_pool // len(living))
                xp_effects: list[Effect] = [
                    GrantXP(member, share) for member in living
                ]
                host.effect_applier.apply_all(xp_effects)

    world.remove_entity(effect.entity)
    if boss_token is not None:
        _on_boss_killed(host, boss_token)


def _living_party_members(host: "App") -> list:
    """Return the party members that still have positive HP (M25)."""

    living: list = []
    for member in host.party.members:
        stats = host.world.combat_stats.get(member)
        if stats is None:
            continue
        if stats.hit_points <= 0:
            continue
        living.append(member)
    return living


def _check_party_wipe(host: "App") -> None:
    """Flip to ``UIMode.game_over`` when every party member is down (M29)."""
    from src.core.death_saves import party_wiped

    party = getattr(host, "party", None)
    members = getattr(party, "members", None) if party is not None else None
    if not members:
        return
    if party_wiped(host.world, list(members)):
        host.ui_mode = UIMode.game_over
        host.character_creation_state = None


def _maybe_trigger_party_wipe_after_kill(host: "App", dying: object) -> None:
    """Anticipating ``dying`` being removed, decide if party is wiped (M29)."""
    from src.core.death_saves import is_unconscious

    party = getattr(host, "party", None)
    members = getattr(party, "members", None) if party is not None else None
    if not members:
        return
    world = host.world
    for member in members:
        if member == dying:
            continue
        if not world.positions.has(member):
            continue
        if is_unconscious(world, member):
            continue
        stats = world.combat_stats.get(member)
        if stats is None:
            return
        if stats.hit_points > 0:
            return
    host.ui_mode = UIMode.game_over
    host.character_creation_state = None


def _creature_display_name(creature_kind: str) -> str:
    """Resolve a creature kind key into its player-visible name.

    The kind key is the registry key (e.g. ``boss_kobold_warlord``)
    which is convenient for content code but not what the player should
    see in messages. The registry stores a friendly ``name``
    (``kobold warlord``) — use it when available, falling back to the
    raw key for keys not in the registry so missing content doesn't
    crash corpse naming (issue #114).
    """
    from src.core.creatures import CREATURES

    spec = CREATURES.get(creature_kind)
    if spec is None:
        return creature_kind
    return spec.name


def _spawn_corpse_entity(
    world,
    *,
    x: int,
    y: int,
    creature_kind: str,
    gold: int,
    items: tuple[tuple[str, int], ...],
):
    """Create a corpse entity at (x, y) with the rolled inventory.

    Corpses are non-blocking ground entities (you can walk onto a
    corpse to loot it with ``,``). They carry an ``Inventory`` so the
    same pickup/transfer code works for corpses, dropped items, and
    open containers alike.
    """
    from src.core.items import add_item

    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation("%"))
    name = f"{_creature_display_name(creature_kind)} corpse" if creature_kind else "corpse"
    world.names.add(entity, Name(name))
    world.corpses.add(entity, Corpse(creature_kind=creature_kind))
    inventory = Inventory(gold=gold)
    for item_id, quantity in items:
        add_item(inventory, item_id, quantity=quantity)
    world.inventories.add(entity, inventory)
    return entity


# ---------------------------------------------------------------------------
# InteractionEffects
# ---------------------------------------------------------------------------


def _apply_open_entity(host: "App", effect: OpenEntity) -> None:
    """Pure world mutation. Opens doors and containers for the entity.

    Containers always have an ``Inventory`` component as their authoritative
    contents store. Opening a container ensures the inventory exists (empty by
    default) so callers can rely on ``world.inventories.get(container)`` once
    the open effect has been applied.
    """
    world = host.world
    if world.doors.has(effect.entity):
        world.doors.require(effect.entity).is_open = True
    if world.containers.has(effect.entity):
        world.containers.require(effect.entity).is_open = True
        if not world.inventories.has(effect.entity):
            world.inventories.add(effect.entity, Inventory())


def _apply_unlock_entity(host: "App", effect: UnlockEntity) -> None:
    """Pure world mutation."""
    lock = host.world.locks.get(effect.entity)
    if lock is not None:
        lock.is_locked = False


def _apply_disarm_trap(host: "App", effect: DisarmTrap) -> None:
    """Pure world mutation."""
    trap = host.world.traps.get(effect.entity)
    if trap is not None:
        trap.is_armed = False


def _apply_trigger_trap(host: "App", effect: TriggerTrap) -> None:
    """Pure world mutation. Non-reusable traps disarm themselves on trigger."""
    trap = host.world.traps.get(effect.entity)
    if trap is not None and not trap.reusable:
        trap.is_armed = False


def _apply_remove_blocker(host: "App", effect: RemoveBlocker) -> None:
    """Pure world mutation."""
    host.world.blockers.values.pop(effect.entity, None)


# ---------------------------------------------------------------------------
# LifecycleEffects
# ---------------------------------------------------------------------------


def _apply_set_character_sheet(host: "App", effect: SetCharacterSheet) -> None:
    """Reaches into App state when the player sheet changes (rebuilds party).

    Imports here are local to avoid a hard import cycle with ``src.app``.
    """
    from src.app import _assign_character_sheet, _replace_companions_for_player_sheet

    _assign_character_sheet(host.world, effect.entity, effect.sheet)
    if effect.entity == host.player:
        new_members = _replace_companions_for_player_sheet(
            host.world,
            host.player,
            host.party.members,
            effect.sheet,
        )
        # Mutate PartyState in place so the TurnController and any
        # other holders see the updated roster without rewiring.
        host.party.members = new_members
        host.party.follow_order = list(new_members)
        host.party.focused_index = None
        host.party.active_index = 0


def _apply_restart_game(host: "App", effect: RestartGame) -> None:
    """Reaches into App state: delegates to ``App.restart``."""
    host.restart()


def _apply_quit_game(host: "App", effect: QuitGame) -> None:
    """Reaches into App state: flips the running flag."""
    host.running = False


# ---------------------------------------------------------------------------
# UIEffects
# ---------------------------------------------------------------------------


def _apply_set_mode(host: "App", effect: SetMode) -> None:
    """Reaches into App state: swaps the active UI mode.

    When switching to `UIMode.character_creation` the effect carries the
    creation state to install; for every other UIMode the field is
    cleared so stale state cannot leak across screen transitions.
    """
    host.ui_mode = effect.mode
    if effect.mode is UIMode.character_creation:
        host.character_creation_state = effect.character_creation_state
    else:
        host.character_creation_state = None


# ---------------------------------------------------------------------------
# DebugEffects (M33)
# ---------------------------------------------------------------------------


def _apply_set_god_mode(host: "App", effect: SetGodMode) -> None:
    """Toggle the GodMode component on the target.

    `enabled=True` adds (or refreshes) a GodMode; `enabled=False` clears it.
    """
    store = host.world.god_modes
    if effect.enabled:
        store.add(effect.entity, GodMode(enabled=True))
    else:
        store.values.pop(effect.entity, None)


def _apply_spawn_entity(host: "App", effect: SpawnEntity) -> None:
    """Materialize a debug-catalog entity at (x, y).

    The catalog lives in `src.systems.debug_system`; we import lazily so the
    core effects module stays free of system-layer imports.
    """
    from src.systems.debug_system import DEBUG_SPAWN_CATALOG

    spawner = DEBUG_SPAWN_CATALOG.get(effect.kind)
    if spawner is None:
        return
    spawner(host.world, effect.x, effect.y)


def _apply_grant_gold(host: "App", effect: GrantGold) -> None:
    """Add gold to `entity`'s inventory; no-op if none."""
    inventory = host.world.inventories.get(effect.entity)
    if inventory is None:
        return
    inventory.gold += effect.amount


def _apply_grant_item(host: "App", effect: GrantItem) -> None:
    """Add `quantity` of `item_id` to `entity`'s inventory; no-op if no inventory."""
    from src.core.items import add_item

    inventory = host.world.inventories.get(effect.entity)
    if inventory is None:
        return
    add_item(inventory, effect.item_id, quantity=effect.quantity)


# ---------------------------------------------------------------------------
# Leveling effects (M25)
# ---------------------------------------------------------------------------


def _apply_grant_xp(host: "App", effect: GrantXP, messages: list[str]) -> None:
    """Add ``effect.amount`` XP to the entity's ledger and check thresholds (M25).

    Creates an :class:`ExperiencePoints` component on first grant. After
    adding, if the new total crosses the next level threshold and the
    actor has no pending :class:`LevelUpAvailable`, attaches the marker
    and emits an "X is ready to level up!" message so the player knows
    the modal is waiting. Multi-level jumps surface the next pending
    level only; the player consumes one level at a time through the
    level-up modal.
    """

    if effect.amount <= 0:
        return
    world = host.world
    store = world.experience_points
    sheet_component = world.characters.get(effect.entity)
    current_level = sheet_component.sheet.level if sheet_component is not None else 1
    xp = store.get(effect.entity)
    if xp is None:
        xp = ExperiencePoints(value=0, level=current_level)
        store.add(effect.entity, xp)
    xp.value += effect.amount
    # Refresh level mirror in case the sheet was leveled outside the
    # ledger (defensive — no current code path does so).
    xp.level = current_level
    pending = world.level_up_pending.get(effect.entity)
    if pending is not None:
        return
    threshold = next_threshold(current_level)
    if threshold is None:
        return
    if xp.value < threshold:
        return
    new_level = level_for_xp(xp.value)
    if new_level <= current_level:
        return
    target = min(current_level + 1, new_level)
    world.level_up_pending.add(effect.entity, LevelUpAvailable(target_level=target))
    name = world.name_for(effect.entity)
    messages.append(f"{name} is ready to level up!")


def _apply_level_up(host: "App", effect: LevelUp, messages: list[str]) -> None:
    """Resolve the pending level-up on ``effect.entity`` (M25).

    Bumps the character sheet's level, recomputes proficiency, adds
    HP gain to both current and max, and installs the post-level-up
    spell-slot maxima (for caster classes). The
    :class:`LevelUpAvailable` marker is cleared. No-op when the actor
    has no pending level-up.
    """

    world = host.world
    pending = world.level_up_pending.get(effect.entity)
    if pending is None:
        return
    sheet_component = world.characters.get(effect.entity)
    if sheet_component is None:
        # Defensive: clear the marker so a debug-spawned actor without
        # a sheet doesn't leave a stuck pending level-up.
        world.level_up_pending.values.pop(effect.entity, None)
        return
    from dataclasses import replace as _replace

    from src.core.character_creation import require_class
    from src.core.combat import proficiency_bonus_for_level
    from src.core.spells import SpellSlots

    old_sheet = sheet_component.sheet
    new_level = pending.target_level
    new_sheet = _replace(old_sheet, level=new_level)
    sheet_component.sheet = new_sheet

    class_option = None
    try:
        class_option = require_class(old_sheet.character_class)
    except KeyError:
        pass

    stats = world.combat_stats.get(effect.entity)
    if stats is not None and class_option is not None:
        hp_gain = hp_gain_for_level_up(class_option.hit_die, stats.constitution)
        stats.max_hit_points += hp_gain
        stats.hit_points = min(stats.max_hit_points, stats.hit_points + hp_gain)
        stats.proficiency_bonus = proficiency_bonus_for_level(new_level)
    else:
        hp_gain = 0

    # Spell-slot growth. Replace the ledger's maxima for the levels the
    # progression table specifies and refill the remaining counts so a
    # newly granted slot is usable immediately (the player should not
    # need to long-rest right after ding).
    progression = slot_progression_for(old_sheet.character_class, new_level)
    if progression:
        slots = world.spell_slots.get(effect.entity)
        if slots is None:
            slots = SpellSlots.from_pairs(progression)
            world.spell_slots.add(effect.entity, slots)
        else:
            for level, maximum in progression.items():
                slots.max_by_level[level] = maximum
                slots.slots_by_level[level] = maximum

    # Sync the XP component's level mirror so subsequent grants probe
    # the new threshold.
    xp = world.experience_points.get(effect.entity)
    if xp is not None:
        xp.level = new_level

    world.level_up_pending.values.pop(effect.entity, None)
    name = world.name_for(effect.entity)
    messages.append(f"{name} reaches level {new_level}!")


# ---------------------------------------------------------------------------
# ConditionEffects (M24)
# ---------------------------------------------------------------------------


def _apply_apply_condition(host: "App", effect: ApplyCondition) -> None:
    """Attach the condition to the target via :func:`apply_condition`.

    The helper handles concentration handoff (the new ``concentrating``
    condition replaces any prior one), seeds round/turn countdowns, and
    resolves clock-driven expiry against ``world.clock``.
    """
    apply_condition(host.world, effect.entity, effect.condition)


def _apply_end_condition(host: "App", effect: EndCondition) -> None:
    """Remove every condition of ``effect.kind`` from the target."""
    end_condition(host.world, effect.entity, effect.kind)


# ---------------------------------------------------------------------------
# Spell effects (M11)
# ---------------------------------------------------------------------------


def _apply_apply_healing(host: "App", effect: ApplyHealing) -> None:
    """Restore HP up to the entity's recorded maximum.

    No-op when the entity has no combat stats. The healing is applied
    after damage in the same effect batch (because batches dispatch in
    list order), so a spell that heals and immediately resolves a
    counterattack still ends up at the right HP.

    M29: any positive heal applied to an unconscious actor revives
    them. The unconscious condition is ended and the :class:`DeathSaves`
    row is cleared so the actor goes back to normal play. The revival
    effects are dispatched through the standard EffectApplier so
    save/load and observation stay consistent.
    """
    from src.core.death_saves import revive_with_healing

    stats = host.world.combat_stats.get(effect.entity)
    if stats is None:
        return
    if effect.amount <= 0:
        return
    was_downed = stats.hit_points <= 0
    stats.hit_points = min(stats.max_hit_points, stats.hit_points + effect.amount)
    if was_downed and stats.hit_points > 0:
        followups = revive_with_healing(host.world, effect.entity)
        if followups:
            host.effect_applier.apply_all(list(followups))


def _apply_consume_spell_slot(host: "App", effect: ConsumeSpellSlot) -> None:
    """Spend one slot at ``effect.level`` from the caster's ledger.

    Cantrips and casters with no ledger are silent no-ops — slot
    accounting is decided at cast time, not at apply time, so this
    handler only mirrors the decision.
    """

    if effect.level <= 0:
        return
    slots = host.world.spell_slots.get(effect.entity)
    if slots is None:
        return
    slots.consume(effect.level)


# ---------------------------------------------------------------------------
# Loot effects (M30)
# ---------------------------------------------------------------------------


def _apply_transfer_inventory(
    host: "App", effect: TransferInventory, messages: list[str]
) -> None:
    """Move every item and all gold from ``source`` into ``destination``.

    Empty loose drops (anything that owns Inventory + Position but is
    not a Container and not a Corpse) are removed from the world after
    the transfer. Corpses and chests stay on the ground so the player
    has a visible record.
    """
    from src.core.items import add_item

    world = host.world
    source_inventory = world.inventories.get(effect.source)
    destination_inventory = world.inventories.get(effect.destination)
    if source_inventory is None or destination_inventory is None:
        return

    transferred_names: list[str] = []
    picked_item_ids: list[str] = []
    if source_inventory.gold:
        destination_inventory.gold += source_inventory.gold
        transferred_names.append(f"{source_inventory.gold} gold")
        source_inventory.gold = 0
    for stack in list(source_inventory.items):
        add_item(destination_inventory, stack.item_id, quantity=stack.quantity)
        transferred_names.append(
            f"{stack.quantity}x {stack.item_id}"
            if stack.quantity != 1
            else stack.item_id
        )
        picked_item_ids.append(stack.item_id)
        source_inventory.items.remove(stack)

    if transferred_names:
        source_name = world.name_for(effect.source)
        messages.append(
            f"Picked up {', '.join(transferred_names)} from {source_name}."
        )

    # Loose ground drops (no container, no corpse) are removed when empty.
    is_container = world.containers.has(effect.source)
    is_corpse = world.corpses.has(effect.source)
    if not is_container and not is_corpse:
        world.remove_entity(effect.source)

    if picked_item_ids:
        _on_items_picked_up(host, picked_item_ids, messages)


# ---------------------------------------------------------------------------
# Quest progress hooks (M14)
# ---------------------------------------------------------------------------


def _on_boss_killed(host: "App", boss_token: str) -> None:
    """Re-evaluate every active quest whose objective matches ``boss_token``.

    The check is conservative: a quest only progresses if it's currently
    in :class:`QuestState.ACCEPTED`. The chalice-possession side of the
    objective is checked by the same helper, so killing the boss when
    the party already has the chalice flips the quest to ``completed``
    immediately. Killing the boss without the chalice still leaves the
    quest accepted; the pickup hook will re-check on the next pickup.
    """

    log = host.party.quests
    for quest in QUESTS.all():
        if quest.objective.boss_marker != boss_token:
            continue
        _try_complete_quest(host, quest)


def _on_items_picked_up(
    host: "App", item_ids: list[str], messages: list[str]
) -> None:
    """Re-evaluate quests after a pickup that may satisfy a treasure clause.

    Walks every accepted quest whose ``treasure_item_id`` appears in
    the pickup batch. Quests that complete have their completion
    message and reward effects emitted into the same message list so
    the player sees the pickup + completion announcement together.
    """

    log = host.party.quests
    relevant = set(item_ids)
    for quest in QUESTS.all():
        if quest.objective.treasure_item_id not in relevant:
            continue
        _try_complete_quest(host, quest, message_sink=messages)


def _try_complete_quest(
    host: "App",
    quest: Quest,
    *,
    message_sink: list[str] | None = None,
) -> None:
    """Promote ``quest`` to ``completed`` if both criteria are satisfied.

    Both criteria must hold simultaneously: the boss must be dead AND
    the party must hold the treasure item. The kill side is recorded
    implicitly — once a creature with the matching boss marker is
    removed from the world, no other live entity carries that marker,
    so "no live entity with this marker exists" is the post-kill
    invariant. The treasure side is a direct inventory walk.

    Messages emitted by this helper land on ``message_sink`` if
    provided (used by the pickup hook so the completion text rides
    along with the "Picked up ..." line). Without a sink (the kill
    hook), the helper collects its own messages into a local list and
    emits them as a single combined string at the end so the completion
    announcement and reward summary don't overwrite each other in the
    message pager (issue #112).
    """

    log = host.party.quests
    if log.state_of(quest.id) is not QuestState.ACCEPTED:
        return
    if not _quest_objective_satisfied(host, quest):
        return
    log.set_state(quest.id, QuestState.COMPLETED)
    _rebind_quest_dialogues_to_completed(host, quest.id)
    owned_sink = message_sink is None
    sink: list[str] = [] if owned_sink else message_sink  # type: ignore[assignment]
    _emit_completion(host, quest, sink)
    _apply_quest_reward(host, quest, sink)
    if owned_sink and sink:
        host.messages.emit(" ".join(sink))


def _rebind_quest_dialogues_to_completed(host: "App", quest_id: str) -> None:
    """Walk NPC dialogue trees and re-bind any with ``quest_id`` (#113).

    On the COMPLETED transition the quest giver's dialogue should
    surface the completion follow-up instead of the original pitch on
    the next interaction. Trees that don't carry the matching
    ``quest_id`` (or that don't declare a ``completed_node_key``) are
    skipped, so the helper is safe to call on every completion.
    """
    world = host.world
    for npc_dialogue in world.npc_dialogues.values.values():
        tree = npc_dialogue.tree
        if tree.quest_id != quest_id:
            continue
        mark_quest_completed_in_tree(tree)


def _quest_objective_satisfied(host: "App", quest: Quest) -> bool:
    world = host.world
    # Boss check: no live entity in the world carries the matching
    # boss-marker token. Once the boss is killed, the marker is gone
    # (removed with the entity), so the "no live boss" predicate is
    # the same as "the boss is dead".
    boss_marker_token = quest.objective.boss_marker
    for marker in world.boss_markers.values.values():
        if marker.token == boss_marker_token:
            return False
    # Treasure check: any party member's inventory holds at least one
    # of the treasure item.
    item_id = quest.objective.treasure_item_id
    for member in host.party.members:
        inventory = world.inventories.get(member)
        if inventory is None:
            continue
        if has_item(inventory, item_id, 1):
            return True
    return False


def _emit_completion(
    host: "App", quest: Quest, message_sink: list[str]
) -> None:
    message_sink.append(quest.completion_message)


def _apply_quest_reward(
    host: "App", quest: Quest, message_sink: list[str]
) -> None:
    """Apply the quest's reward effects (M14, XP wired M25).

    Gold is added to each living party member's inventory; XP is
    granted via :class:`GrantXP` so the ledger updates, the
    threshold check fires, and any "ready to level up!" message
    flows through the standard pipeline. Members without an inventory
    are skipped silently rather than spawning one mid-completion.
    """
    reward = quest.reward
    granted: list[str] = []
    if reward.gold_per_member > 0:
        for member in host.party.members:
            inventory = host.world.inventories.get(member)
            if inventory is None:
                continue
            inventory.gold += reward.gold_per_member
        granted.append(f"{reward.gold_per_member} gold each")
    if reward.xp_per_member > 0:
        xp_effects: list[Effect] = [
            GrantXP(member, reward.xp_per_member)
            for member in host.party.members
        ]
        host.effect_applier.apply_all(xp_effects)
        granted.append(f"{reward.xp_per_member} XP each")
    if granted:
        text = f"Quest reward: {', '.join(granted)}."
        message_sink.append(text)


def _apply_spawn_corpse(host: "App", effect: SpawnCorpse) -> None:
    """Create a corpse entity at (x, y) with the rolled loot inventory."""
    _spawn_corpse_entity(
        host.world,
        x=effect.x,
        y=effect.y,
        creature_kind=effect.creature_kind,
        gold=effect.gold,
        items=effect.items,
    )


def _apply_drop_to_ground(
    host: "App", effect: DropToGround, messages: list[str]
) -> None:
    """Move ``quantity`` of ``item_id`` (or gold) from source to a ground entity.

    Merges into any existing loose ground-drop entity on the tile (one
    that has Inventory + Position and is not a container/corpse). A
    corpse on the tile is not merged into — dropping onto a corpse
    creates a new pile next to it (semantically still at the same tile
    but rendered as a fresh entity).
    """
    from src.core.items import add_item, has_item, remove_item

    world = host.world
    source_inventory = world.inventories.get(effect.source)
    if source_inventory is None:
        return

    if effect.item_id is None:
        # Gold drop.
        if effect.gold <= 0 or source_inventory.gold < effect.gold:
            return
        source_inventory.gold -= effect.gold
        target = _ground_drop_at(world, effect.x, effect.y)
        if target is None:
            target = _spawn_ground_drop(world, effect.x, effect.y)
        target_inventory = world.inventories.require(target)
        target_inventory.gold += effect.gold
        messages.append(f"Dropped {effect.gold} gold.")
        return

    if effect.quantity <= 0:
        return
    if not has_item(source_inventory, effect.item_id, effect.quantity):
        return
    remove_item(source_inventory, effect.item_id, effect.quantity)
    target = _ground_drop_at(world, effect.x, effect.y)
    if target is None:
        target = _spawn_ground_drop(world, effect.x, effect.y)
    target_inventory = world.inventories.require(target)
    add_item(target_inventory, effect.item_id, quantity=effect.quantity)
    qty = f"{effect.quantity}x " if effect.quantity != 1 else ""
    messages.append(f"Dropped {qty}{effect.item_id}.")


def _ground_drop_at(world, x: int, y: int):
    """Return an existing loose-drop entity at (x, y), if any.

    Containers and corpses are intentionally NOT merged into — dropping
    onto a corpse leaves a separate pile so the player can still tell
    "stuff I dropped" from "stuff the monster carried".
    """
    for entity in world.entities_at(x, y):
        if not world.inventories.has(entity):
            continue
        if world.containers.has(entity):
            continue
        if world.corpses.has(entity):
            continue
        if world.creatures.has(entity):
            continue
        if world.player_controlled.has(entity):
            continue
        return entity
    return None


def _spawn_ground_drop(world, x: int, y: int):
    """Create a fresh loose-drop ground entity (Inventory + Position)."""
    entity = world.create_entity()
    world.positions.add(entity, Position(x=x, y=y))
    world.presentations.add(entity, Presentation("*"))
    world.names.add(entity, Name("items"))
    world.inventories.add(entity, Inventory())
    return entity
