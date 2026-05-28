"""Dialogue data model (M13).

Dialogue is typed data — there is no scripting language. A
``DialogueTree`` is a rooted tree of :class:`DialogueNode`. Each node
carries the speaker's :class:`DialogueLine` (a literal string of text)
plus an ordered list of :class:`DialogueOption` entries. Choosing an
option may emit a structured :class:`DialogueEffect`, advance to a
linked child node, or close the dialogue.

The :class:`DialogueState` object is what an :class:`App` instance holds
while the dialogue modal is open. It tracks which NPC the player is
talking to and the current node within the tree. The model is small
enough that save/load is a straightforward dict round-trip (the
:meth:`to_dict` / :meth:`from_dict` helpers below).

Why typed data and not a tiny scripting language? The roadmap calls
this out explicitly (M13 "Simple deterministic state — no scripting
language; just typed data"). Two reasons:

- Save-friendliness: every option/effect is a known dataclass so a
  save can encode the conversation without a code re-eval.
- Reviewability: content authors look at a list of options and effect
  tags, not at a DSL hidden behind a string parser.

The effects (:class:`OpenShopEffect`, :class:`RecruitEffect`,
:class:`CloseDialogueEffect`) are gameplay verbs that the app resolves
when an option is selected. They are deliberately distinct from
:mod:`src.core.effects` (which is the world-mutation effect bus): a
dialogue effect is a UI verb that may *then* produce world effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypeAlias

from src.core.entity import EntityId


# ---------------------------------------------------------------------------
# Effects produced by dialogue options
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CloseDialogueEffect:
    """Close the dialogue modal without further side effects.

    Implicit on any option whose ``effect`` is ``None`` and whose
    ``next_node`` is ``None`` — kept as an explicit tag so save/load
    can serialize "the option that ends the conversation" without a
    sentinel.
    """


@dataclass(frozen=True, slots=True)
class RecruitEffect:
    """Add the NPC the player is talking to into the party.

    The actual entity id is not stored here — :class:`DialogueState`
    knows which NPC is speaking. The app resolves the effect by
    calling :meth:`PartyState.recruit` on that entity and then removing
    the NPC from the world (since it's now a party member, not a
    standing NPC).
    """


@dataclass(frozen=True, slots=True)
class OpenShopEffect:
    """Switch the UI to the shop screen for the current speaker.

    The shop screen itself is M12; this effect is just the dialogue
    -> shop hand-off so the player presses one number key (or arrow +
    Enter) instead of leaving the conversation and pressing ``e``
    again on the same tile.
    """


@dataclass(frozen=True, slots=True)
class AcceptQuestEffect:
    """Mark a quest as accepted in the party's quest log (M14).

    The ``quest_id`` is a key into :data:`src.core.quest.QUESTS`. The
    App's dialogue resolver applies the accept (which emits the quest's
    accept message and victory condition) when the player picks an
    option carrying this effect. The dialogue navigation rules then
    follow ``next_node`` as usual; the modal does not implicitly
    close, so a quest accept can chain into a thank-you node before
    the player dismisses the conversation.
    """

    quest_id: str


DialogueEffect: TypeAlias = (
    CloseDialogueEffect
    | RecruitEffect
    | OpenShopEffect
    | AcceptQuestEffect
)


# ---------------------------------------------------------------------------
# Lines, options, nodes, trees
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DialogueLine:
    """A single utterance: who says it and what they say.

    ``speaker_id`` is a free-form tag, not an entity id. It exists so
    a single tree can have multiple speakers (e.g. an NPC vs the party
    reply line) without us needing to thread entity ids through the
    tree data. The :class:`DialogueState` separately tracks the world
    entity that owns the tree.
    """

    speaker_id: str
    text: str


@dataclass(frozen=True, slots=True)
class DialogueOption:
    """One choice the player can select.

    ``label`` is the prompt shown to the player. ``next_node`` is the
    follow-up node (a key into the tree's ``nodes`` map); ``None``
    means the option closes the dialogue. ``effect`` is an optional
    :class:`DialogueEffect` that fires when the option is selected; it
    is independent of node navigation — an option can both recruit
    the speaker *and* navigate to a thank-you node.
    """

    label: str
    next_node: str | None = None
    effect: DialogueEffect | None = None


@dataclass(frozen=True, slots=True)
class DialogueNode:
    """A single utterance + the options the player has from here.

    A node with an empty ``options`` list is a terminal node — the
    only valid input is Esc/Enter to close. The renderer surfaces
    ``"[Press Enter or Esc to close]"`` in that case.
    """

    line: DialogueLine
    options: tuple[DialogueOption, ...] = ()


@dataclass(frozen=True, slots=True)
class DialogueTree:
    """A complete conversation.

    ``root`` is the key of the node the conversation starts on.
    ``nodes`` is the full node map. The tree dataclass itself is
    frozen, but the ``nodes`` dict is mutable so the tree can re-bind
    its entry node when quest state changes — see
    :meth:`DialogueState.advance_to` and ``sync_quest_dialogue`` for the
    issue #113 quest-aware re-entry path.

    ``quest_id``, ``accepted_node_key``, and ``completed_node_key`` are
    the optional quest-aware fields. When set, navigating to a node
    whose key matches one of the keyed entries re-binds ``nodes["root"]``
    so subsequent ``begin`` calls start the player on that node instead
    of repeating the pitch.
    """

    root: str
    nodes: dict[str, DialogueNode] = field(default_factory=dict)
    quest_id: str | None = None
    accepted_node_key: str | None = None
    completed_node_key: str | None = None

    def node(self, key: str) -> DialogueNode:
        return self.nodes[key]

    def _rebind_root_to(self, node_key: str) -> None:
        """Make subsequent ``begin`` calls land on ``node_key``.

        Mutates the entry stored under the ``root`` key — the
        ``root`` field itself is frozen, but the ``nodes`` dict is
        not. Idempotent on repeated calls with the same key.
        """
        if node_key not in self.nodes:
            return
        self.nodes[self.root] = self.nodes[node_key]

    # ------------------------------------------------------------------
    # Save / load
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "root": self.root,
            "nodes": {
                key: _node_to_dict(node) for key, node in self.nodes.items()
            },
        }
        if self.quest_id is not None:
            payload["quest_id"] = self.quest_id
        if self.accepted_node_key is not None:
            payload["accepted_node_key"] = self.accepted_node_key
        if self.completed_node_key is not None:
            payload["completed_node_key"] = self.completed_node_key
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DialogueTree":
        root = str(data.get("root", ""))
        nodes_raw = data.get("nodes", {}) or {}
        nodes = {
            str(key): _node_from_dict(value) for key, value in nodes_raw.items()
        }
        quest_id_raw = data.get("quest_id")
        accepted_raw = data.get("accepted_node_key")
        completed_raw = data.get("completed_node_key")
        return cls(
            root=root,
            nodes=nodes,
            quest_id=None if quest_id_raw is None else str(quest_id_raw),
            accepted_node_key=(
                None if accepted_raw is None else str(accepted_raw)
            ),
            completed_node_key=(
                None if completed_raw is None else str(completed_raw)
            ),
        )


# ---------------------------------------------------------------------------
# Per-session dialogue state
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DialogueState:
    """The active conversation.

    The app pushes one of these onto itself when the dialogue modal
    opens and clears it when the modal closes. ``current_node`` walks
    the tree; ``speaker`` is the world entity speaking (so the
    recruit effect knows who to add and so the renderer can render
    the speaker name).

    ``previous_mode`` records which :class:`UIMode` the player was on
    when the modal opened; closing the modal restores it. We deliberately
    don't store the :class:`UIMode` enum on the dataclass directly — it
    would force this module to import :mod:`src.core.modes`, which in
    turn would risk circular imports as dialogue is wired into the App
    later. We carry the raw enum value (a string).
    """

    speaker: EntityId
    tree: DialogueTree
    current_node: str
    previous_mode: str | None = None

    @classmethod
    def begin(
        cls,
        speaker: EntityId,
        tree: DialogueTree,
        *,
        previous_mode: str | None = None,
    ) -> "DialogueState":
        return cls(
            speaker=speaker,
            tree=tree,
            current_node=tree.root,
            previous_mode=previous_mode,
        )

    def node(self) -> DialogueNode:
        return self.tree.node(self.current_node)

    def advance_to(self, node_key: str) -> None:
        if node_key not in self.tree.nodes:
            raise KeyError(f"DialogueTree has no node {node_key!r}")
        self.current_node = node_key
        # Quest-aware trees re-bind their entry node when the player
        # transitions into the accepted branch so the next ``begin``
        # call lands on the follow-up instead of repeating the pitch
        # (issue #113). Completion-side mutation lives in
        # :func:`sync_completed_quest_dialogue` because the COMPLETED
        # transition fires from the effect-applier hook (kill + chalice
        # pickup), not from a dialogue option.
        tree = self.tree
        accepted_key = tree.accepted_node_key
        if accepted_key is not None and node_key == accepted_key:
            tree._rebind_root_to(node_key)


# ---------------------------------------------------------------------------
# Common single-line builders
# ---------------------------------------------------------------------------


def info_tree(
    speaker_id: str,
    text: str,
    *,
    close_label: str = "Goodbye.",
) -> DialogueTree:
    """Build a tree that shows one line and offers a single close option.

    Used by every info NPC. Saves content code from typing the same
    one-line tree boilerplate.
    """

    node = DialogueNode(
        line=DialogueLine(speaker_id=speaker_id, text=text),
        options=(
            DialogueOption(label=close_label, next_node=None, effect=None),
        ),
    )
    return DialogueTree(root="root", nodes={"root": node})


def recruit_tree(
    speaker_id: str,
    ask_text: str,
    accept_text: str,
    *,
    accept_label: str = "Join us.",
    decline_label: str = "Not right now.",
) -> DialogueTree:
    """Build a two-node recruit dialogue.

    The root asks; the ``accept`` option fires :class:`RecruitEffect`
    and lands on a thank-you node whose only option closes the
    dialogue. The ``decline`` option closes immediately without any
    effect.
    """

    root = DialogueNode(
        line=DialogueLine(speaker_id=speaker_id, text=ask_text),
        options=(
            DialogueOption(
                label=accept_label,
                next_node="joined",
                effect=RecruitEffect(),
            ),
            DialogueOption(
                label=decline_label,
                next_node=None,
                effect=None,
            ),
        ),
    )
    joined = DialogueNode(
        line=DialogueLine(speaker_id=speaker_id, text=accept_text),
        options=(DialogueOption(label="Onwards.", next_node=None, effect=None),),
    )
    return DialogueTree(root="root", nodes={"root": root, "joined": joined})


def quest_offer_tree(
    speaker_id: str,
    quest_id: str,
    *,
    pitch: str,
    accept_response: str,
    decline_response: str,
    completion_response: str | None = None,
    accept_label: str = "Yes, I'll take it.",
    decline_label: str = "Not now.",
    completion_label: str = "Farewell.",
) -> DialogueTree:
    """Build a quest-offer dialogue tree (M14, #113 re-entry).

    Four nodes:

    - ``root`` (the pitch + accept/decline options) — the initial
      conversation entry.
    - ``accepted`` — the response shown in-session right after the
      accept option fires, AND the entry on subsequent visits while
      the quest is ACCEPTED (so the NPC reminds the party of the
      objective instead of replaying the pitch).
    - ``declined`` (in-session follow-up after the player refuses).
    - ``completed`` (entry once the quest is COMPLETED) — set from
      ``completion_response``.

    ``accepted_node_key`` is wired to ``accepted`` so
    :meth:`DialogueState.advance_to` re-binds ``nodes["root"]`` to the
    accept response as soon as the accept option fires.
    ``completed_node_key`` is wired to ``completed`` so the
    effect-applier completion hook can rebind the entry once the quest
    finishes (#113).
    """

    if completion_response is None:
        completion_response = "Thank you. The work is done."
    root = DialogueNode(
        line=DialogueLine(speaker_id=speaker_id, text=pitch),
        options=(
            DialogueOption(
                label=accept_label,
                next_node="accepted",
                effect=AcceptQuestEffect(quest_id=quest_id),
            ),
            DialogueOption(
                label=decline_label,
                next_node="declined",
                effect=None,
            ),
        ),
    )
    accepted = DialogueNode(
        line=DialogueLine(speaker_id=speaker_id, text=accept_response),
        options=(DialogueOption(label="Farewell.", next_node=None, effect=None),),
    )
    declined = DialogueNode(
        line=DialogueLine(speaker_id=speaker_id, text=decline_response),
        options=(DialogueOption(label="Farewell.", next_node=None, effect=None),),
    )
    completed = DialogueNode(
        line=DialogueLine(speaker_id=speaker_id, text=completion_response),
        options=(
            DialogueOption(label=completion_label, next_node=None, effect=None),
        ),
    )
    return DialogueTree(
        root="root",
        nodes={
            "root": root,
            "accepted": accepted,
            "declined": declined,
            "completed": completed,
        },
        quest_id=quest_id,
        accepted_node_key="accepted",
        completed_node_key="completed",
    )


def mark_quest_completed_in_tree(tree: DialogueTree) -> None:
    """Re-bind ``tree``'s entry node to its ``completed`` follow-up.

    Used by the effects-applier completion hook (issue #113) when the
    boss-kill + chalice-pickup pair flips the quest to ``COMPLETED``.
    Trees without a ``completed_node_key`` are left untouched so the
    helper is safe to call on any dialogue tree.
    """
    completed_key = tree.completed_node_key
    if completed_key is None:
        return
    tree._rebind_root_to(completed_key)


def shopkeeper_tree(
    speaker_id: str,
    greeting: str,
    *,
    open_shop_label: str = "Let me see your wares.",
    close_label: str = "Maybe later.",
) -> DialogueTree:
    """Build a shopkeeper greeting tree with a single open-shop option.

    The shopkeeper greets the party; the open-shop option fires
    :class:`OpenShopEffect`. The close option leaves the modal.
    """

    root = DialogueNode(
        line=DialogueLine(speaker_id=speaker_id, text=greeting),
        options=(
            DialogueOption(
                label=open_shop_label,
                next_node=None,
                effect=OpenShopEffect(),
            ),
            DialogueOption(label=close_label, next_node=None, effect=None),
        ),
    )
    return DialogueTree(root="root", nodes={"root": root})


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


_EFFECT_KINDS: dict[str, type[DialogueEffect]] = {
    "close": CloseDialogueEffect,
    "recruit": RecruitEffect,
    "open_shop": OpenShopEffect,
    "accept_quest": AcceptQuestEffect,
}


def _effect_to_dict(effect: DialogueEffect | None) -> dict[str, Any] | None:
    if effect is None:
        return None
    if isinstance(effect, AcceptQuestEffect):
        return {"kind": "accept_quest", "quest_id": effect.quest_id}
    for tag, cls in _EFFECT_KINDS.items():
        if cls is AcceptQuestEffect:
            continue
        if isinstance(effect, cls):
            return {"kind": tag}
    raise TypeError(f"Unknown DialogueEffect type: {type(effect)!r}")


def _effect_from_dict(payload: dict[str, Any] | None) -> DialogueEffect | None:
    if payload is None:
        return None
    kind = payload.get("kind")
    if kind == "accept_quest":
        return AcceptQuestEffect(quest_id=str(payload.get("quest_id", "")))
    cls = _EFFECT_KINDS.get(str(kind))
    if cls is None:
        return None
    if cls is AcceptQuestEffect:
        return AcceptQuestEffect(quest_id=str(payload.get("quest_id", "")))
    return cls()


def _option_to_dict(option: DialogueOption) -> dict[str, Any]:
    return {
        "label": option.label,
        "next_node": option.next_node,
        "effect": _effect_to_dict(option.effect),
    }


def _option_from_dict(payload: dict[str, Any]) -> DialogueOption:
    next_node_raw = payload.get("next_node")
    return DialogueOption(
        label=str(payload.get("label", "")),
        next_node=None if next_node_raw is None else str(next_node_raw),
        effect=_effect_from_dict(payload.get("effect")),
    )


def _node_to_dict(node: DialogueNode) -> dict[str, Any]:
    return {
        "line": {
            "speaker_id": node.line.speaker_id,
            "text": node.line.text,
        },
        "options": [_option_to_dict(option) for option in node.options],
    }


def _node_from_dict(payload: dict[str, Any]) -> DialogueNode:
    line_raw = payload.get("line", {}) or {}
    line = DialogueLine(
        speaker_id=str(line_raw.get("speaker_id", "")),
        text=str(line_raw.get("text", "")),
    )
    options_raw = payload.get("options", []) or []
    return DialogueNode(
        line=line,
        options=tuple(_option_from_dict(option) for option in options_raw),
    )
