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


DialogueEffect: TypeAlias = (
    CloseDialogueEffect
    | RecruitEffect
    | OpenShopEffect
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
    ``nodes`` is the full node map. Trees are immutable; per-session
    state lives on :class:`DialogueState`.
    """

    root: str
    nodes: dict[str, DialogueNode] = field(default_factory=dict)

    def node(self, key: str) -> DialogueNode:
        return self.nodes[key]

    # ------------------------------------------------------------------
    # Save / load
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "nodes": {
                key: _node_to_dict(node) for key, node in self.nodes.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DialogueTree":
        root = str(data.get("root", ""))
        nodes_raw = data.get("nodes", {}) or {}
        nodes = {
            str(key): _node_from_dict(value) for key, value in nodes_raw.items()
        }
        return cls(root=root, nodes=nodes)


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
}


def _effect_to_dict(effect: DialogueEffect | None) -> dict[str, Any] | None:
    if effect is None:
        return None
    for tag, cls in _EFFECT_KINDS.items():
        if isinstance(effect, cls):
            return {"kind": tag}
    raise TypeError(f"Unknown DialogueEffect type: {type(effect)!r}")


def _effect_from_dict(payload: dict[str, Any] | None) -> DialogueEffect | None:
    if payload is None:
        return None
    kind = payload.get("kind")
    cls = _EFFECT_KINDS.get(str(kind))
    if cls is None:
        return None
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
