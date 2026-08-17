from __future__ import annotations

import ast
import math
import operator
from dataclasses import dataclass
from typing import Any, Callable

from organic_processor import ProcessTrace, StructuralTask


@dataclass(frozen=True)
class TraceVerification:
    accepted: bool
    reward: float
    result_code: str
    checks: dict[str, bool]
    expected: Any = None


def _line_satisfies(task: StructuralTask, count: int) -> bool:
    if count <= 0:
        return False
    for constraint in task.constraints:
        kind = constraint.get("kind")
        if kind == "exists_count_before":
            if int(constraint["count"]) >= count:
                return False
        elif kind == "exists_count_after":
            if int(constraint["count"]) >= count:
                return False
        elif kind == "exists_middle":
            if count < 3 or count % 2 == 0:
                return False
        else:
            return False
    return True


def _minimum_line_count(task: StructuralTask) -> int | None:
    return next((count for count in range(1, 65) if _line_satisfies(task, count)), None)


def _evaluate_expression(expression: str) -> float:
    tree = ast.parse(expression, mode="eval")
    binary: dict[type[ast.operator], Callable[[float, float], float]] = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
    }

    def evaluate(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            return binary[type(node.op)](evaluate(node.left), evaluate(node.right))
        raise ValueError("unsupported expression")

    value = evaluate(tree)
    if not math.isfinite(value):
        raise ValueError("non-finite result")
    return value


def verify_trace(task: StructuralTask, trace: ProcessTrace) -> TraceVerification:
    if task.family == "order_cardinality" and task.goal == "min_distinct_count":
        expected = _minimum_line_count(task)
        answer_is_integer = isinstance(trace.answer, int) and not isinstance(trace.answer, bool)
        constraints_hold = answer_is_integer and _line_satisfies(task, int(trace.answer))
        minimal = constraints_hold and trace.answer == expected
        checks = {
            "answer_is_integer": answer_is_integer,
            "all_constraints_satisfied": constraints_hold,
            "minimal_model_verified": minimal,
            "external_resources_used": False,
        }
        accepted = answer_is_integer and constraints_hold and minimal and expected is not None
        return TraceVerification(
            accepted=accepted,
            reward=1.0 if accepted else -0.65,
            result_code="verified_minimum_model" if accepted else "rejected_structural_candidate",
            checks=checks,
            expected=expected,
        )

    if task.family == "bounded_arithmetic" and task.goal == "evaluate_expression":
        expressions = [item.get("expression") for item in task.constraints if item.get("kind") == "expression"]
        expected = _evaluate_expression(str(expressions[0])) if len(expressions) == 1 else None
        numeric = isinstance(trace.answer, (int, float)) and not isinstance(trace.answer, bool)
        matches = numeric and expected is not None and math.isclose(float(trace.answer), expected, rel_tol=1e-12, abs_tol=1e-12)
        checks = {
            "answer_is_numeric": numeric,
            "independent_evaluation_matches": matches,
            "external_resources_used": False,
        }
        return TraceVerification(
            accepted=bool(matches),
            reward=1.0 if matches else -0.65,
            result_code="verified_arithmetic" if matches else "rejected_arithmetic_candidate",
            checks=checks,
            expected=expected,
        )

    if task.family == "grounded_evidence_selection" and task.goal == "select_supported_items":
        qualifying = [
            (
                float(item.get("retrieval_score", 0.0)),
                float(item.get("confidence", 0.0)),
                int(item["index"]),
            )
            for item in task.constraints
            if item.get("kind") == "grounded_candidate"
            and float(item.get("retrieval_score", 0.0))
            >= float(item.get("selection_threshold", 0.45))
            and float(item.get("confidence", 0.0)) >= 0.45
        ]
        qualifying.sort(key=lambda item: (-item[0], -item[1], item[2]))
        limits = [
            max(1, min(8, int(item.get("selection_limit", 4))))
            for item in task.constraints
            if item.get("kind") == "grounded_candidate"
        ]
        selection_limit = min(limits or [4])
        expected = [index for _retrieval, _confidence, index in qualifying[:selection_limit]]
        list_result = isinstance(trace.answer, list) and all(
            isinstance(item, int) and not isinstance(item, bool) for item in trace.answer
        )
        matches = list_result and trace.answer == expected
        checks = {
            "answer_is_index_list": list_result,
            "selection_matches_grounding_policy": matches,
            "external_resources_used": False,
        }
        return TraceVerification(
            accepted=bool(matches),
            reward=1.0 if matches else -0.65,
            result_code="verified_grounded_selection" if matches else "rejected_grounded_selection",
            checks=checks,
            expected=expected,
        )

    return TraceVerification(
        accepted=False,
        reward=-0.25,
        result_code="verifier_capability_gap",
        checks={"supported_family": False, "external_resources_used": False},
    )
