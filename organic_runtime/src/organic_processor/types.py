from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class StructuralTask:
    """Domain-neutral input accepted by the bounded Organic Processor."""

    family: str
    goal: str
    constraints: tuple[dict[str, Any], ...]

    def signature(self) -> str:
        kinds = sorted(str(item.get("kind", "")) for item in self.constraints)
        return f"{self.family}|{self.goal}|{'/'.join(kinds)}"


@dataclass
class ProcessTrace:
    pathway: str
    operators: list[str]
    signature: str
    intermediate: list[dict[str, Any]] = field(default_factory=list)
    answer: Any = None
    valid: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProcessResult:
    status: str
    answer: Any = None
    confidence: float = 0.0
    trace: ProcessTrace | None = None
    alternatives: list[ProcessTrace] = field(default_factory=list)
    capability_gap: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
