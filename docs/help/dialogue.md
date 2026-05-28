# Dialogue (M13)

Dialogue mode is the UI modal that opens when the player presses `e`
on a tile adjacent to a non-hostile NPC. The town in the M8 world
skeleton ships with three kinds of NPC and a deterministic line / set
of options for each. The system is intentionally small — typed data,
no scripting language — so save / load is a plain JSON round-trip.

## NPC kinds

| Kind          | Glyph | Faction | Purpose                                                          |
| ---           | ---   | ---     | ---                                                              |
| `info`        | `@`   | `town`  | Says one useful line (a directional hint, a rumour, a warning).  |
| `recruit`     | `@`   | `town`  | Offers to join the party. Accepting adds them to `PartyState`.   |
| `shopkeeper`  | `@`   | `town`  | Greets the party and offers to open the shop screen (M12 / M17). |

The Hearthgate town also hosts **Captain Tane** in the tavern corner
(south-east of the town anchor). Tane is tagged as an info NPC but
his dialogue carries an `AcceptQuestEffect` for "The Sunken Gate"
(M14). Picking the accept option flips the quest log to `accepted`
and emits the victory condition into the message log. See
[`quest.md`](quest.md) for the full quest pipeline.

A joined party member renders as their numbered party slot
(`1`, `2`, ...) instead of `@`. The renderer projects the lead as
`@` and follower N as the digit `N` (up to nine companions); a tenth
or later companion falls back to `#`. The recruited NPC keeps its
position, character sheet, weapon, armor, and combat stats so the new
party member fights from the next tick.

## Entering the modal

Pressing `e` while facing an adjacent tile that holds an entity with
the `NPC` marker opens the dialogue modal. The dialogue check runs
*before* the standard interaction path (doors / locks / traps /
containers), so an NPC standing on top of a chest still gets the
dialogue rather than the chest. Opening the modal is a pure UI
event:

- It does **not** consume the actor's action even in turn-based mode
  (the dialogue is a modal, not a world action).
- It does **not** advance the world clock in explore mode.

## Key bindings

| Key             | Effect                                          |
| ---             | ---                                             |
| `1`..`9`        | Select the matching option (1 == first listed). |
| `Enter` / Space | Select option 1, or close on a terminal node.   |
| `Esc` / `q`     | Close the modal with no effect.                 |

The renderer shows the speaker's name on the first row, the current
line on the next, and a numbered list of options below. A node with
no options (e.g. the thank-you node after recruiting) renders the
hint `"[Press Enter or Esc to close]"`.

## Option effects

Each option carries an optional `DialogueEffect`. The supported tags
are deliberately small:

| Effect                 | Behaviour                                                                       |
| ---                    | ---                                                                             |
| `CloseDialogueEffect`  | Equivalent to "no effect, no next node" — closes the modal.                     |
| `RecruitEffect`        | Adds the current speaker to the party and removes the NPC marker / dialogue.    |
| `OpenShopEffect`       | Switches the UI to the shop screen and remembers the speaker as `shop_partner`. |
| `AcceptQuestEffect`    | Flips the party quest log entry to `accepted` (M14). Does NOT close the modal — navigates to the option's `next_node` so the quest giver can show a thank-you line. The accept also re-binds the tree's entry node to that follow-up so the next visit to the quest giver shows the in-flight reminder instead of replaying the pitch. |

Options that set `next_node` navigate to that node within the same
tree; options with `next_node=None` close the modal once the effect
fires.

## Recruit semantics

The `RecruitEffect` calls `PartyState.recruit(speaker)`, switches the
NPC's faction to `player_party`, marks them as `PlayerControlled`,
and removes the `NPC` / `NPCDialogue` components. The entity itself
stays in the world — same position, same combat stats — so combat
and movement systems pick the new member up automatically. The
player sees a `"<name> joined your party."` message in the log.

## Shop hand-off

The `OpenShopEffect` flips the UI to `UIMode.shop` and stores the
shopkeeper entity on `App.shop_partner`. The shop screen itself
(M17 follow-up) drives buy / sell against that entity's `Shop`
component and `Inventory`. Closing the shop screen is the shop
screen's responsibility; this dialogue path is one-way.

Until the full M17 buy / sell UI lands, the shop modal still owns
its own keys so the player isn't trapped:

| Key         | Effect                                       |
| ---         | ---                                          |
| `Esc` / `q` | Close the shop and return to play.           |
| `b`         | Reserved for buy; placeholder message today. |
| `s`         | Reserved for sell; placeholder message today.|

Closing the modal clears `App.shop_partner`.

## Save / load

`DialogueTree` is a typed dataclass with a JSON round-trip
(`to_dict` / `from_dict`). The tree is persisted as part of the
`NPCDialogue` component on each NPC entity, so reloading a save
restores the full conversation graph. The transient
`DialogueState` (which node the player is currently on) is
**not** persisted — a save written mid-conversation drops the
modal; loading lands the player back in the play screen with no
dialogue pending.

## Architectural notes

- `src/core/dialogue.py` owns the data types and the helper
  builders (`info_tree`, `recruit_tree`, `shopkeeper_tree`). It
  does not import the App, the renderer, or the world.
- The App handles dialogue input directly in
  `_handle_dialogue_key` rather than routing through `map_key` —
  the same pattern the M20 targeting modal uses. This is so a
  stray inventory / quit key in dialogue mode cannot leak through
  and open another modal.
- The interaction system (`src/systems/interaction_system.py`) is
  unchanged — the NPC short-circuit lives in `App._handle_interaction`
  before the dispatcher sees the action. That keeps the dialogue
  modal out of the action / effect pipeline (it is a UI mode, not
  a world mutation).
