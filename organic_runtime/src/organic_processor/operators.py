from __future__ import annotations

import ast
import math
import operator
from typing import Any, Callable

from .types import StructuralTask


class OperatorError(RuntimeError):
    pass


def _counts(task: StructuralTask) -> tuple[list[int], list[int], bool]:
    before: list[int] = []
    after: list[int] = []
    middle = False
    for constraint in task.constraints:
        kind = constraint.get("kind")
        if kind == "exists_count_before":
            before.append(int(constraint["count"]))
        elif kind == "exists_count_after":
            after.append(int(constraint["count"]))
        elif kind == "exists_middle":
            middle = True
    return before, after, middle


def sum_constraint_mentions(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    before, after, middle = _counts(task)
    return {**state, "scalar": sum(before) + sum(after) + int(middle)}


def max_constraint_only(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    before, after, middle = _counts(task)
    values = before + after + ([1] if middle else [])
    return {**state, "scalar": max(values or [0])}


def infer_minimum_line_bound(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    before, after, middle = _counts(task)
    lower = max([1, *[value + 1 for value in before], *[value + 1 for value in after]])
    if middle:
        lower = max(lower, 3)
    return {**state, "lower_bound": lower}


def construct_linear_model(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    del task
    return {**state, "model": list(range(int(state.get("lower_bound", 1))))}


def _model_satisfies(task: StructuralTask, count: int) -> bool:
    if count <= 0:
        return False
    positions = range(count)
    for constraint in task.constraints:
        kind = constraint.get("kind")
        if kind == "exists_count_before":
            if not any(position == int(constraint["count"]) for position in positions):
                return False
        elif kind == "exists_count_after":
            if not any(count - 1 - position == int(constraint["count"]) for position in positions):
                return False
        elif kind == "exists_middle":
            if count < 3 or count % 2 == 0:
                return False
        else:
            return False
    return True


def repair_until_valid(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    count = max(1, len(list(state.get("model") or [])))
    for _ in range(64):
        if _model_satisfies(task, count):
            return {**state, "model": list(range(count)), "model_valid": True}
        count += 1
    raise OperatorError("No valid bounded linear model was found")


def emit_scalar(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    del task
    return {**state, "answer": state.get("scalar")}


def emit_model_cardinality(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    del task
    model = state.get("model")
    if not isinstance(model, list):
        raise OperatorError("No structural model exists to count")
    return {**state, "answer": len(model)}


def evaluate_bounded_expression(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    expressions = [item.get("expression") for item in task.constraints if item.get("kind") == "expression"]
    if len(expressions) != 1 or not isinstance(expressions[0], str):
        raise OperatorError("Exactly one arithmetic expression is required")
    expression = expressions[0]
    if len(expression) > 160:
        raise OperatorError("Expression exceeds the processor budget")
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
            value = binary[type(node.op)](evaluate(node.left), evaluate(node.right))
            if not math.isfinite(value) or abs(value) > 1e15:
                raise OperatorError("Arithmetic result exceeds the processor budget")
            return value
        raise OperatorError("Unsupported arithmetic syntax")

    value = evaluate(tree)
    answer: int | float = int(value) if value.is_integer() else value
    return {**state, "answer": answer, "expression_valid": True}


def select_grounded_candidates(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    selected: list[tuple[float, float, int]] = []
    limits: list[int] = []
    for constraint in task.constraints:
        if constraint.get("kind") != "grounded_candidate":
            raise OperatorError("Unsupported grounded-evidence constraint")
        retrieval = float(constraint.get("retrieval_score", 0.0))
        confidence = float(constraint.get("confidence", 0.0))
        threshold = float(constraint.get("selection_threshold", 0.45))
        limits.append(max(1, min(8, int(constraint.get("selection_limit", 4)))))
        if retrieval >= threshold and confidence >= 0.45:
            selected.append((retrieval, confidence, int(constraint["index"])))
    selected.sort(key=lambda item: (-item[0], -item[1], item[2]))
    limit = min(limits or [4])
    return {
        **state,
        "answer": [index for _retrieval, _confidence, index in selected[:limit]],
        "selection_valid": True,
    }


OPERATORS = {
    "SUM_CONSTRAINT_MENTIONS": sum_constraint_mentions,
    "MAX_CONSTRAINT_ONLY": max_constraint_only,
    "INFER_MINIMUM_LINE_BOUND": infer_minimum_line_bound,
    "CONSTRUCT_LINEAR_MODEL": construct_linear_model,
    "REPAIR_UNTIL_VALID": repair_until_valid,
    "EMIT_SCALAR": emit_scalar,
    "EMIT_MODEL_CARDINALITY": emit_model_cardinality,
    "EVALUATE_BOUNDED_EXPRESSION": evaluate_bounded_expression,
    "SELECT_GROUNDED_CANDIDATES": select_grounded_candidates,
}
