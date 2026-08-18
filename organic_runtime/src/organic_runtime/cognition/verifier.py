from __future__ import annotations

import ast
import heapq
import itertools
import math
import operator
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

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


def _expected_partial_order(task: StructuralTask) -> list[str] | None:
    nodes: set[str] = set()
    edges: set[tuple[str, str]] = set()
    for constraint in task.constraints:
        if constraint.get("kind") != "precedes":
            return None
        before = str(constraint.get("before") or "")
        after = str(constraint.get("after") or "")
        if not before or not after or before == after:
            return None
        nodes.update((before, after))
        edges.add((before, after))
    incoming = {node: 0 for node in nodes}
    outgoing = {node: [] for node in nodes}
    for before, after in edges:
        incoming[after] += 1
        outgoing[before].append(after)
    ready = [node for node, degree in incoming.items() if degree == 0]
    heapq.heapify(ready)
    answer: list[str] = []
    while ready:
        node = heapq.heappop(ready)
        answer.append(node)
        for successor in sorted(outgoing[node]):
            incoming[successor] -= 1
            if incoming[successor] == 0:
                heapq.heappush(ready, successor)
    return answer if len(answer) == len(nodes) else None


def _expected_boolean_entailment(task: StructuralTask) -> tuple[bool | None, int]:
    facts: dict[tuple[str, str], bool | None] = {}
    relations: list[tuple[str, str, str]] = []
    query: dict[str, Any] | None = None
    for constraint in task.constraints:
        kind = constraint.get("kind")
        if kind == "entity_property":
            facts[(str(constraint.get("entity") or ""), str(constraint.get("property") or ""))] = (
                constraint.get("value")
            )
        elif kind == "directed_relation":
            relations.append(
                (
                    str(constraint.get("subject") or ""),
                    str(constraint.get("predicate") or ""),
                    str(constraint.get("object") or ""),
                )
            )
        elif kind == "exists_relation_by_property":
            query = dict(constraint)
        else:
            return None, 0
    unknowns = sorted(key for key, value in facts.items() if value is None)
    if query is None or len(unknowns) > 10:
        return None, 0
    outcomes: list[bool] = []
    for values in itertools.product((False, True), repeat=len(unknowns)):
        assigned = dict(facts)
        assigned.update(dict(zip(unknowns, values)))
        outcomes.append(
            any(
                predicate == query.get("predicate")
                and assigned.get((subject, str(query.get("subject_property") or "")))
                is bool(query.get("subject_value"))
                and assigned.get((obj, str(query.get("object_property") or "")))
                is bool(query.get("object_value"))
                for subject, predicate, obj in relations
            )
        )
    return bool(outcomes) and all(outcomes), len(outcomes)


def _expected_truth_lie_question(task: StructuralTask) -> tuple[dict[str, str] | None, int]:
    destinations = next(
        (item for item in task.constraints if item.get("kind") == "binary_destination"),
        None,
    )
    unknown = next(
        (item for item in task.constraints if item.get("kind") == "unknown_responder_home"),
        None,
    )
    rules = {
        str(item.get("home") or ""): item.get("truthful")
        for item in task.constraints
        if item.get("kind") == "responder_rule"
    }
    if not destinations or not unknown:
        return None, 0
    target = str(destinations.get("target") or "").strip()
    alternative = str(destinations.get("alternative") or "").strip()
    options = [str(item).strip() for item in (unknown.get("options") or [])]
    if (
        not target
        or not alternative
        or target == alternative
        or set(options) != {target, alternative}
        or rules.get(target) is not True
        or rules.get(alternative) is not False
    ):
        return None, 0
    outcomes = []
    for home in options:
        indicated = home if rules[home] is True else (alternative if home == target else target)
        outcomes.append(indicated == target)
    if not outcomes or not all(outcomes):
        return None, len(outcomes)
    return (
        {
            "question_id": "ask_home_road",
            "question": "Which road leads to the city where you live?",
            "follow": "take_indicated_road",
        },
        len(outcomes),
    )


def _independent_linear_result(task: StructuralTask) -> dict[str, Any] | None:
    """Solve again outside processor operators and verify every original equation."""
    if any(
        item.get("kind") not in {"linear_equation", "linear_target"}
        for item in task.constraints
    ):
        return None
    equations = [item for item in task.constraints if item.get("kind") == "linear_equation"]
    targets = [item for item in task.constraints if item.get("kind") == "linear_target"]
    if not equations or len(targets) != 1:
        return None
    variables = sorted(
        {
            str(name)
            for item in (*equations, targets[0])
            for name in (item.get("coefficients") or {})
        }
    )
    if not variables or len(equations) != len(variables) or len(variables) > 16:
        return None
    try:
        matrix = [
            [
                *[
                    Fraction(str((item.get("coefficients") or {}).get(name, 0)))
                    for name in variables
                ],
                Fraction(str(item.get("constant", 0))),
            ]
            for item in equations
        ]
        size = len(variables)
        for column in range(size):
            pivot = next((row for row in range(column, size) if matrix[row][column]), None)
            if pivot is None:
                return None
            matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
            divisor = matrix[column][column]
            matrix[column] = [value / divisor for value in matrix[column]]
            for row in range(size):
                if row == column:
                    continue
                factor = matrix[row][column]
                matrix[row] = [
                    value - factor * pivot_value
                    for value, pivot_value in zip(matrix[row], matrix[column])
                ]
        exact = {name: matrix[index][-1] for index, name in enumerate(variables)}
        if not all(
            sum(
                (
                    Fraction(str(coefficient)) * exact[name]
                    for name, coefficient in (equation.get("coefficients") or {}).items()
                ),
                Fraction(0),
            )
            == Fraction(str(equation.get("constant", 0)))
            for equation in equations
        ):
            return None
        target = Fraction(str(targets[0].get("constant", 0))) + sum(
            (
                Fraction(str(coefficient)) * exact[name]
                for name, coefficient in (targets[0].get("coefficients") or {}).items()
            ),
            Fraction(0),
        )
    except (KeyError, ValueError, ZeroDivisionError):
        return None
    plain = lambda value: int(value) if value.denominator == 1 else float(value)
    return {
        "values": {name: plain(value) for name, value in exact.items()},
        "target": plain(target),
    }


def verify_trace(task: StructuralTask, trace: ProcessTrace) -> TraceVerification:
    kinds = {str(item.get("kind") or "") for item in task.constraints}

    if task.goal == "solve_linear_target" and kinds <= {"linear_equation", "linear_target"}:
        expected = _independent_linear_result(task)
        shaped = (
            isinstance(trace.answer, dict)
            and isinstance(trace.answer.get("values"), dict)
            and isinstance(trace.answer.get("target"), (int, float))
            and not isinstance(trace.answer.get("target"), bool)
        )
        matches = shaped and expected is not None and trace.answer == expected
        checks = {
            "answer_has_variable_values": shaped,
            "all_original_equations_satisfied": bool(matches),
            "target_independently_evaluated": bool(matches),
            "unique_solution_verified": expected is not None,
            "external_resources_used": False,
        }
        return TraceVerification(
            accepted=bool(matches),
            reward=1.0 if matches else -0.65,
            result_code="verified_symbolic_linear_constraints"
            if matches
            else "rejected_linear_candidate",
            checks=checks,
            expected=expected,
        )

    if task.goal == "min_distinct_count" and kinds <= {
        "exists_count_before",
        "exists_count_after",
        "exists_middle",
    }:
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

    if task.goal == "evaluate_expression" and kinds == {"expression"}:
        expressions = [
            item.get("expression") for item in task.constraints if item.get("kind") == "expression"
        ]
        expected = _evaluate_expression(str(expressions[0])) if len(expressions) == 1 else None
        numeric = isinstance(trace.answer, (int, float)) and not isinstance(trace.answer, bool)
        matches = (
            numeric
            and expected is not None
            and math.isclose(float(trace.answer), expected, rel_tol=1e-12, abs_tol=1e-12)
        )
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

    if task.goal == "select_supported_items" and kinds == {"grounded_candidate"}:
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

    if task.goal == "linearize_order" and kinds == {"precedes"}:
        expected = _expected_partial_order(task)
        list_result = isinstance(trace.answer, list) and all(
            isinstance(item, str) and item for item in trace.answer
        )
        matches = list_result and expected is not None and trace.answer == expected
        checks = {
            "answer_is_ordered_item_list": list_result,
            "all_precedence_constraints_hold": matches,
            "acyclic_model_verified": expected is not None,
            "external_resources_used": False,
        }
        return TraceVerification(
            accepted=bool(matches),
            reward=1.0 if matches else -0.65,
            result_code="verified_partial_order" if matches else "rejected_partial_order",
            checks=checks,
            expected=expected,
        )

    if task.goal == "prove_existential_relation" and kinds <= {
        "entity_property",
        "directed_relation",
        "exists_relation_by_property",
    }:
        expected, case_count = _expected_boolean_entailment(task)
        boolean_result = isinstance(trace.answer, bool)
        matches = boolean_result and expected is not None and trace.answer is expected
        checks = {
            "answer_is_boolean": boolean_result,
            "all_unknown_assignments_evaluated": case_count > 0,
            "entailment_matches_independent_case_analysis": matches,
            "external_resources_used": False,
        }
        return TraceVerification(
            accepted=bool(matches),
            reward=1.0 if matches else -0.65,
            result_code=(
                "verified_boolean_entailment" if matches else "rejected_boolean_entailment"
            ),
            checks=checks,
            expected=expected,
        )

    if task.goal == "identify_truth_road" and kinds <= {
        "binary_destination",
        "responder_rule",
        "unknown_responder_home",
    }:
        expected, case_count = _expected_truth_lie_question(task)
        shaped = (
            isinstance(trace.answer, dict)
            and isinstance(trace.answer.get("question"), str)
            and bool(trace.answer.get("question"))
            and trace.answer.get("follow") == "take_indicated_road"
        )
        matches = shaped and expected is not None and trace.answer == expected
        checks = {
            "answer_contains_actionable_question": shaped,
            "both_responder_types_evaluated": case_count == 2,
            "indicated_road_is_truth_in_all_cases": bool(matches),
            "external_resources_used": False,
        }
        return TraceVerification(
            accepted=bool(matches),
            reward=1.0 if matches else -0.65,
            result_code=(
                "verified_truth_lie_navigation" if matches else "rejected_truth_lie_question"
            ),
            checks=checks,
            expected=expected,
        )

    return TraceVerification(
        accepted=False,
        reward=-0.25,
        result_code="verifier_capability_gap",
        checks={"supported_family": False, "external_resources_used": False},
    )
