from __future__ import annotations

import re
from typing import Any

from organic_processor import StructuralTask
from organic_runtime.contracts import (
    ActiveWeave,
    ActiveWeaveItem,
    ActiveWeaveScope,
    ActiveWeaveTrust,
    IntentEnvelope,
)


_NUMBERS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_COUNT = r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|\d+)"


def _number(value: str) -> int:
    lowered = value.lower()
    return int(lowered) if lowered.isdigit() else _NUMBERS[lowered]


def infer_structural_fields(request: str) -> dict[str, Any] | None:
    """Compile recognized task-local language without solving the task."""
    text = " ".join(request.strip().split())
    lowered = text.lower()
    front = re.search(rf"\b(?P<n>{_COUNT})\s+[a-z][a-z-]*s?\s+in front of\b", lowered)
    behind = re.search(rf"\b(?P<n>{_COUNT})\s+[a-z][a-z-]*s?\s+behind\b", lowered)
    middle = re.search(r"\b(?:a|an|one)\s+[a-z][a-z-]*\s+in the middle\b", lowered)
    asks_count = bool(re.search(r"\bhow many\b", lowered))
    if front and behind and middle and asks_count:
        return {
            "closed_world": True,
            "sufficient_premises": True,
            "reasoning_family": "order_cardinality",
            "reasoning_goal": "min_distinct_count",
            "structural_constraints": [
                {"kind": "exists_count_before", "count": _number(front.group("n"))},
                {"kind": "exists_count_after", "count": _number(behind.group("n"))},
                {"kind": "exists_middle"},
            ],
        }

    expression = re.search(
        r"(?<!\w)(-?\d+(?:\.\d+)?(?:\s*[+*/-]\s*-?\d+(?:\.\d+)?)+)",
        text,
    )
    if expression:
        return {
            "closed_world": True,
            "sufficient_premises": True,
            "reasoning_family": "bounded_arithmetic",
            "reasoning_goal": "evaluate_expression",
            "structural_constraints": [
                {"kind": "expression", "expression": expression.group(1).strip()}
            ],
        }
    return None


def compile_structural_task(envelope: IntentEnvelope) -> StructuralTask | None:
    if not envelope.closed_world or not envelope.sufficient_premises:
        return None
    if not envelope.reasoning_family or not envelope.reasoning_goal:
        return None
    constraints = tuple(dict(item) for item in envelope.structural_constraints)
    if not constraints:
        return None
    return StructuralTask(
        family=envelope.reasoning_family,
        goal=envelope.reasoning_goal,
        constraints=constraints,
    )


def build_active_weave(envelope: IntentEnvelope, task: StructuralTask) -> ActiveWeave:
    items = [
        ActiveWeaveItem(
            role="premise",
            item_type=str(constraint.get("kind") or "constraint"),
            scope=ActiveWeaveScope.TASK_LOCAL_PREMISE,
            trust=ActiveWeaveTrust.USER_SUPPLIED,
            provenance={"request_id": envelope.request_id, "source": "current_user_turn"},
            confidence=1.0,
            uncertainty=0.0,
            payload=dict(constraint),
        )
        for constraint in task.constraints
    ]
    return ActiveWeave(
        request_id=envelope.request_id,
        family=task.family,
        goal=task.goal,
        items=items,
        durable_memory_allowed=False,
        external_resources_allowed=False,
    )
