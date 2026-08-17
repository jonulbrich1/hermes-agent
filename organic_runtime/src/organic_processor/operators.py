from __future__ import annotations

import ast
import heapq
import itertools
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


def collect_partial_order(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    nodes: set[str] = set()
    edges: set[tuple[str, str]] = set()
    for constraint in task.constraints:
        if constraint.get("kind") != "precedes":
            raise OperatorError("Unsupported partial-order constraint")
        before = str(constraint.get("before") or "").strip()
        after = str(constraint.get("after") or "").strip()
        if not before or not after or before == after:
            raise OperatorError("Invalid partial-order edge")
        nodes.update((before, after))
        edges.add((before, after))
    if not edges or len(nodes) > 128 or len(edges) > 512:
        raise OperatorError("Partial-order task exceeds the processor budget")
    return {**state, "nodes": sorted(nodes), "edges": sorted(edges)}


def topological_linearize(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    del task
    nodes = [str(item) for item in state.get("nodes") or []]
    edges = [(str(left), str(right)) for left, right in state.get("edges") or []]
    incoming = {node: 0 for node in nodes}
    outgoing = {node: [] for node in nodes}
    for before, after in edges:
        incoming[after] += 1
        outgoing[before].append(after)
    ready = [node for node, degree in incoming.items() if degree == 0]
    heapq.heapify(ready)
    ordered: list[str] = []
    while ready:
        node = heapq.heappop(ready)
        ordered.append(node)
        for successor in sorted(outgoing[node]):
            incoming[successor] -= 1
            if incoming[successor] == 0:
                heapq.heappush(ready, successor)
    if len(ordered) != len(nodes):
        raise OperatorError("Partial-order constraints contain a cycle")
    return {**state, "ordered": ordered, "order_valid": True}


def emit_order(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    del task
    ordered = state.get("ordered")
    if not isinstance(ordered, list):
        raise OperatorError("No partial-order model exists")
    return {**state, "answer": list(ordered)}


def enumerate_unknown_assignments(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    facts: dict[tuple[str, str], bool | None] = {}
    relations: list[dict[str, str]] = []
    query: dict[str, Any] | None = None
    for constraint in task.constraints:
        kind = constraint.get("kind")
        if kind == "entity_property":
            entity = str(constraint.get("entity") or "").strip()
            property_name = str(constraint.get("property") or "").strip()
            value = constraint.get("value")
            if not entity or not property_name or value not in {True, False, None}:
                raise OperatorError("Invalid entity-property premise")
            facts[(entity, property_name)] = value
        elif kind == "directed_relation":
            relation = {
                "subject": str(constraint.get("subject") or "").strip(),
                "predicate": str(constraint.get("predicate") or "").strip(),
                "object": str(constraint.get("object") or "").strip(),
            }
            if not all(relation.values()):
                raise OperatorError("Invalid directed-relation premise")
            relations.append(relation)
        elif kind == "exists_relation_by_property":
            query = dict(constraint)
        else:
            raise OperatorError("Unsupported Boolean case-analysis constraint")
    unknowns = sorted(key for key, value in facts.items() if value is None)
    if len(unknowns) > 10:
        raise OperatorError("Unknown assignment space exceeds the processor budget")
    assignments: list[dict[str, bool]] = []
    for values in itertools.product((False, True), repeat=len(unknowns)):
        assignment = {
            f"{entity}|{property_name}": bool(value)
            for (entity, property_name), value in facts.items()
            if value is not None
        }
        assignment.update(
            {
                f"{entity}|{property_name}": bool(value)
                for (entity, property_name), value in zip(unknowns, values)
            }
        )
        assignments.append(assignment)
    if query is None or not relations or not assignments:
        raise OperatorError("Boolean case-analysis task is incomplete")
    return {
        **state,
        "assignments": assignments,
        "unknowns": [f"{entity}|{property_name}" for entity, property_name in unknowns],
        "relations": relations,
        "query": query,
    }


def evaluate_existential_relation(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    del task
    query = state.get("query") or {}
    predicate = str(query.get("predicate") or "")
    subject_property = str(query.get("subject_property") or "")
    object_property = str(query.get("object_property") or "")
    subject_value = bool(query.get("subject_value"))
    object_value = bool(query.get("object_value"))
    case_results: list[dict[str, Any]] = []
    for assignment in state.get("assignments") or []:
        witnesses: list[dict[str, str]] = []
        for relation in state.get("relations") or []:
            if relation.get("predicate") != predicate:
                continue
            subject = str(relation.get("subject") or "")
            obj = str(relation.get("object") or "")
            if (
                assignment.get(f"{subject}|{subject_property}") is subject_value
                and assignment.get(f"{obj}|{object_property}") is object_value
            ):
                witnesses.append({"subject": subject, "object": obj})
        case_results.append(
            {
                "assignment": dict(assignment),
                "satisfied": bool(witnesses),
                "witnesses": witnesses,
            }
        )
    return {
        **state,
        "case_results": case_results,
        "entailed": bool(case_results) and all(item["satisfied"] for item in case_results),
        "entailment_valid": True,
    }


def emit_entailment(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    del task
    if "entailed" not in state:
        raise OperatorError("No Boolean entailment model exists")
    return {**state, "answer": bool(state["entailed"])}


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
    "COLLECT_PARTIAL_ORDER": collect_partial_order,
    "TOPOLOGICAL_LINEARIZE": topological_linearize,
    "EMIT_ORDER": emit_order,
    "ENUMERATE_UNKNOWN_ASSIGNMENTS": enumerate_unknown_assignments,
    "EVALUATE_EXISTENTIAL_RELATION": evaluate_existential_relation,
    "EMIT_ENTAILMENT": emit_entailment,
}
