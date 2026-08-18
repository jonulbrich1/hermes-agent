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
_NAME = r"[A-Z][A-Za-z0-9_-]*"


def _number(value: str) -> int:
    lowered = value.lower()
    return int(lowered) if lowered.isdigit() else _NUMBERS[lowered]


def _infer_partial_order(text: str) -> dict[str, Any] | None:
    if not re.search(r"\b(?:finishing\s+order|finish(?:ed|ing)?\s+order|what\s+was\s+the\s+order)\b", text, re.IGNORECASE):
        return None
    edges: list[tuple[str, str]] = []
    before_pattern = re.compile(
        rf"\b(?P<subject>{_NAME})\s+(?:(?i:finish(?:ed|es|ing)?|is)\s+)?"
        rf"(?i:before)\s+(?P<before>{_NAME})"
        rf"(?:\s*,?\s*(?i:but\s+behind)\s+(?P<behind>{_NAME}))?",
    )
    for match in before_pattern.finditer(text):
        subject = match.group("subject")
        edges.append((subject, match.group("before")))
        if match.group("behind"):
            edges.append((match.group("behind"), subject))
    after_pattern = re.compile(
        rf"\b(?P<subject>{_NAME})\s+(?:(?i:finish(?:ed|es|ing)?|is)\s+)?"
        rf"(?i:after|behind)\s+(?P<after>{_NAME})",
    )
    for match in after_pattern.finditer(text):
        edges.append((match.group("after"), match.group("subject")))
    unique_edges = list(dict.fromkeys(edges))
    if len(unique_edges) < 2:
        return None
    return {
        "closed_world": True,
        "sufficient_premises": True,
        "reasoning_family": "partial_order",
        "reasoning_goal": "linearize_order",
        "required_operations": ["CREATE_RELATION", "TOPOLOGICAL_ORDER", "STOP_IF_VERIFIED"],
        "structural_constraints": [
            {"kind": "precedes", "before": before, "after": after}
            for before, after in unique_edges
        ],
    }


def _infer_boolean_case_analysis(text: str) -> dict[str, Any] | None:
    lowered = text.lower()
    if not (
        "married person" in lowered
        and "unmarried person" in lowered
        and "looking at" in lowered
    ):
        return None
    relations = [
        {"kind": "directed_relation", "subject": subject, "predicate": "looking_at", "object": obj}
        for subject, obj in re.findall(
            rf"\b({_NAME})\s+is\s+looking\s+at\s+({_NAME})\b",
            text,
        )
    ]
    unknowns = set(
        re.findall(
            rf"\b(?:we\s+)?(?:do\s+not|don['\u2019]?t)\s+know\s+if\s+({_NAME})\s+is\s+married\b",
            text,
            re.IGNORECASE,
        )
    )
    negatives = set(
        re.findall(
            rf"\b({_NAME})\s+is\s+not(?:\s+married)?\b",
            text,
        )
    )
    positives = set(
        re.findall(rf"\b({_NAME})\s+is\s+married\b", text)
    ) - unknowns - negatives
    properties = [
        {"kind": "entity_property", "entity": entity, "property": "married", "value": True}
        for entity in sorted(positives)
    ]
    properties.extend(
        {"kind": "entity_property", "entity": entity, "property": "married", "value": False}
        for entity in sorted(negatives)
    )
    properties.extend(
        {"kind": "entity_property", "entity": entity, "property": "married", "value": None}
        for entity in sorted(unknowns)
    )
    if len(relations) < 1 or len(properties) < 2:
        return None
    return {
        "closed_world": True,
        "sufficient_premises": True,
        "reasoning_family": "boolean_case_analysis",
        "reasoning_goal": "prove_existential_relation",
        "required_operations": [
            "ENUMERATE_CASES",
            "TEST_ENTAILMENT",
            "VERIFY_ALL_CASES",
            "STOP_IF_VERIFIED",
        ],
        "structural_constraints": [
            *relations,
            *properties,
            {
                "kind": "exists_relation_by_property",
                "predicate": "looking_at",
                "subject_property": "married",
                "subject_value": True,
                "object_property": "married",
                "object_value": False,
            },
        ],
    }


def _infer_truth_lie_navigation(text: str) -> dict[str, Any] | None:
    lowered = text.lower()
    required_markers = (
        "city of lies",
        "city of truth",
        "always lies",
        "always tells the truth",
    )
    if not all(marker in lowered for marker in required_markers):
        return None
    if "road" not in lowered or not re.search(r"\b(?:ask|question)\b", lowered):
        return None
    return {
        "closed_world": True,
        "sufficient_premises": True,
        "reasoning_family": "truth_lie_navigation",
        "reasoning_goal": "identify_truth_road",
        "required_operations": [
            "ENUMERATE_CASES",
            "TEST_ENTAILMENT",
            "VERIFY_ALL_CASES",
            "STOP_IF_VERIFIED",
        ],
        "structural_constraints": [
            {
                "kind": "binary_destination",
                "target": "City of Truth",
                "alternative": "City of Lies",
            },
            {
                "kind": "responder_rule",
                "home": "City of Truth",
                "truthful": True,
            },
            {
                "kind": "responder_rule",
                "home": "City of Lies",
                "truthful": False,
            },
            {
                "kind": "unknown_responder_home",
                "options": ["City of Truth", "City of Lies"],
            },
        ],
    }


def _infer_truth_role_assignment(text: str) -> dict[str, Any] | None:
    """Compile bounded knight/knave-style role puzzles without solving them."""
    truth_policies: dict[str, bool | None] = {}
    policy_patterns = (
        (r"\bthe\s+([a-z][a-z-]*)\s+always\s+tells?\s+the\s+truth\b", True),
        (r"\bthe\s+([a-z][a-z-]*)\s+always\s+lies\b", False),
        (
            (
                r"\bthe\s+([a-z][a-z-]*)\s+can\s+(?:either\s+)?"
                r"(?:lie|tell\s+the\s+truth)(?:\s+or\s+(?:lie|tell\s+the\s+truth))?\b"
            ),
            None,
        ),
    )
    for pattern, truthful in policy_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            truth_policies[match.group(1).lower()] = truthful
    if len(truth_policies) < 2 or True not in truth_policies.values() or False not in truth_policies.values():
        return None

    quoted_statements = re.compile(
        rf"\b(?P<speaker>{_NAME})\s+says\s*:\s*[\"\u201c'](?P<body>.*?)[\"\u201d']",
        re.IGNORECASE,
    )
    statements: list[dict[str, str]] = []
    for match in quoted_statements.finditer(text):
        speaker = match.group("speaker")
        body = match.group("body").strip().rstrip(".?!").strip()
        proposition = re.fullmatch(
            rf"(?P<subject>I|{_NAME})\s+(?:am|is)\s+(?:a|an|the)\s+"
            r"(?P<role>[a-z][a-z-]*)",
            body,
            re.IGNORECASE,
        )
        if proposition is None:
            return None
        subject = proposition.group("subject")
        role = proposition.group("role").lower()
        if subject.lower() == "i":
            subject = speaker
        statements.append(
            {
                "kind": "role_statement",
                "speaker": speaker,
                "subject": subject,
                "role": role,
            }
        )
    if not statements:
        return None

    entities: list[str] = []
    introduction = re.search(
        rf"\bthere\s+are\s+{_COUNT}\s+people\s*\((?P<names>[^)]+)\)",
        text,
        re.IGNORECASE,
    )
    if introduction:
        for name in re.split(r"\s*(?:,|\band\b)\s*", introduction.group("names"), flags=re.IGNORECASE):
            clean = name.strip()
            if re.fullmatch(_NAME, clean) and clean not in entities:
                entities.append(clean)
    for statement in statements:
        for key in ("speaker", "subject"):
            entity = statement[key]
            if entity not in entities:
                entities.append(entity)

    roles = list(truth_policies)
    if (
        len(entities) != len(roles)
        or not 2 <= len(entities) <= 8
        or any(statement["role"] not in truth_policies for statement in statements)
        or any(entity not in {item["speaker"] for item in statements} for entity in entities)
    ):
        return None

    return {
        "closed_world": True,
        "sufficient_premises": True,
        "reasoning_family": "truth_role_assignment",
        "reasoning_goal": "identify_role_assignment",
        "required_operations": [
            "ENUMERATE_CASES",
            "TEST_ENTAILMENT",
            "VERIFY_ALL_CASES",
            "STOP_IF_VERIFIED",
        ],
        "structural_constraints": [
            {
                "kind": "assignment_domain",
                "entities": entities,
                "roles": roles,
                "bijection": True,
            },
            *[
                {
                    "kind": "role_truth_policy",
                    "role": role,
                    "truthful": truthful,
                }
                for role, truthful in truth_policies.items()
            ],
            *statements,
        ],
    }


def infer_structural_fields(request: str) -> dict[str, Any] | None:
    """Compile recognized task-local language without solving the task."""
    text = " ".join(request.strip().split())
    lowered = text.lower()
    partial_order = _infer_partial_order(text)
    if partial_order:
        return partial_order
    truth_lie = _infer_truth_lie_navigation(text)
    if truth_lie:
        return truth_lie
    truth_roles = _infer_truth_role_assignment(text)
    if truth_roles:
        return truth_roles
    boolean_case = _infer_boolean_case_analysis(text)
    if boolean_case:
        return boolean_case
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
            "required_operations": ["PROPAGATE_CONSTRAINT", "COMPARE_VALUES", "VERIFY_SOLUTION"],
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
            "required_operations": ["EVALUATE_EXPRESSION", "VERIFY_SOLUTION"],
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
        required_operations=tuple(envelope.required_operations),
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
