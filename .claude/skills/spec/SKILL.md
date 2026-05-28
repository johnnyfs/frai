# /spec - Object Model and System Design Skill

Use this before significant implementation or refactor work.

## Purpose

Create or update the project's explicit mental model before writing code.

## Heuristic

1. Identify the feature in gameplay terms.
2. Identify the runtime facts it requires.
3. Classify each fact as:
   - persistent world state
   - transient runtime state
   - UI state
   - content/config data
   - derived/query state
4. Classify each behavior as:
   - system algorithm
   - action-resolution phase
   - event/signal reaction
   - rendering projection
   - input mapping
   - persistence concern
5. Decide what should be:
   - class/type
   - dataclass/config object
   - enum
   - component-like data
   - system/service
   - event/message/signal
   - prefab/content definition
6. Check for serialization impact:
   - must this be saved?
   - can this be reconstructed?
   - will old saves tolerate future added fields?
7. Check for testability:
   - can the rule be tested without terminal rendering?
   - can the system be tested on a tiny world fixture?
8. Check action timing:
   - when does this occur?
   - can it be interrupted?
   - can it trigger reactions?
   - does it consume action, bonus action, movement, reaction, or time?
9. Check UI routing:
   - is this world intent or UI command?
   - does it require a modal?
   - does it block input?
10. Write the spec as a small delta:
   - existing model
   - proposed model
   - new/changed types
   - invariants
   - tests required

## Biases

- Prefer explicit enums/messages/events over stringly behavior.
- Prefer data-driven content where repeated.
- Prefer hardcoded flow where making it generic would obscure the model.
- Do not add abstraction just because it sounds reusable.
- Add abstraction when it protects timing, serialization, testability, or rules consistency.

## Output

- Short design note.
- List of files likely touched.
- Invariants.
- Test plan.
