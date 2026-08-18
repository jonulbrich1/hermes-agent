from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class StructuralTask:
    """Domain-neutral input accepted by the bounded Organic Processor."""

    family: str
    goal: str
    constraints: tuple[dict[str, Any], ...]
    required_operations: tuple[str, ...] = ()

    def signature(self) -> str:
        operations = sorted({str(item).upper() for item in self.required_operations if str(item)})
        kinds = sorted(str(item.get("kind", "")) for item in self.constraints)
        return f"{self.family}|{self.goal}|{'/'.join(kinds)}|{'/'.join(operations)}"

    def legacy_signature(self) -> str:
        kinds = sorted(str(item.get("kind", "")) for item in self.constraints)
        return f"{self.family}|{self.goal}|{'/'.join(kinds)}"

    def capability_signature(self) -> str:
        kinds = sorted({str(item.get("kind", "")) for item in self.constraints})
        operations = sorted({str(item).upper() for item in self.required_operations if str(item)})
        return f"{self.goal}|{'/'.join(kinds)}|{'/'.join(operations)}"

    def legacy_capability_signature(self) -> str:
        kinds = sorted({str(item.get("kind", "")) for item in self.constraints})
        return f"{self.family}|{self.goal}|{'/'.join(kinds)}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "goal": self.goal,
            "constraints": [dict(item) for item in self.constraints],
            "required_operations": list(self.required_operations),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> StructuralTask:
        constraints = value.get("constraints")
        if not isinstance(constraints, list):
            raise TypeError("Structural task constraints must be a list")
        return cls(
            family=str(value.get("family") or ""),
            goal=str(value.get("goal") or ""),
            constraints=tuple(dict(item) for item in constraints if isinstance(item, dict)),
            required_operations=tuple(
                str(item) for item in (value.get("required_operations") or []) if str(item)
            ),
        )


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
