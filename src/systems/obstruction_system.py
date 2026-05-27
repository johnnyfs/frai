from dataclasses import dataclass

from src.core.world import BlockerRef, World


@dataclass(frozen=True, slots=True)
class ObstructionResult:
    allowed: bool
    blockers: list[BlockerRef]


class ObstructionSystem:
    def movement_allowed(self, world: World, x: int, y: int) -> ObstructionResult:
        blockers = world.blockers_at(x, y)
        return ObstructionResult(allowed=not blockers, blockers=blockers)
